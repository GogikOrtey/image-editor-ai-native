"""Expose the model catalogue and the image-edit proxy used by the editor UI.

The browser never sees the CloseRouter key: both routes read it from the server
environment. ``/api/models`` returns a trimmed catalogue, and ``/api/inpaint`` accepts the
frontend's multipart contract and answers with binary image bytes.

The upstream API has no mask parameter, so the mask travels to the model only as guidance
text plus coverage statistics; the client restores unmarked pixels after generation.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.services.providers import (
    MAX_IMAGE_BYTES,
    MAX_PROMPT_LENGTH,
    ProviderRequestError,
    build_edit_prompt,
    fetch_catalog,
    fetch_result_image,
    mask_summary,
    request_image_edit,
)

router = APIRouter()

_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
_MAX_MODEL_LENGTH = 200
# Only image-capable models are offered in the picker; the catalogue also lists chat models.
_CATALOG_FIELDS = ("id", "name", "aliases")


def _clean_text(value: str, name: str, max_length: int) -> str:
    """Reject blank form fields while keeping provider requests predictable."""

    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail=f"{name} must not be blank")
    if len(cleaned) > max_length:
        raise HTTPException(status_code=422, detail=f"{name} exceeds the maximum length")
    return cleaned


def _validate_mime_type(value: str, field_name: str) -> str:
    """Allow only image media types accepted by the edit endpoint."""

    normalized = value.lower().strip()
    if normalized not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be one of: {', '.join(sorted(_ALLOWED_IMAGE_TYPES))}",
        )
    return normalized


async def _read_upload(upload: UploadFile, field_name: str) -> bytes:
    """Read one upload in memory and enforce the provider's 20 MB per-file limit."""

    data = await upload.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail=f"{field_name} must not be empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"{field_name} exceeds the 20 MB limit")
    return data


@router.get("/models")
async def list_models() -> dict[str, list[dict[str, object]]]:
    """Return the live image-model catalogue so the UI never hard-codes model ids."""

    try:
        payload = await fetch_catalog()
    except ProviderRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    models = []
    for entry in payload.get("data", []):
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        # Skip entries without an image capability marker when the catalogue says so.
        if "image_operations" in entry and not entry["image_operations"]:
            continue
        models.append({field: entry.get(field) for field in _CATALOG_FIELDS})
    return {"models": models}


@router.post("/inpaint")
async def inpaint(
    image: Annotated[UploadFile, File(description="Source image")],
    prompt: Annotated[str, Form()],
    model: Annotated[str, Form()],
    mask: Annotated[UploadFile | None, File(description="Optional PNG mask")] = None,
) -> Response:
    """Forward one edit request to CloseRouter and stream the resulting image back."""

    image_type = _validate_mime_type(image.content_type or "", "image content type")
    prompt = _clean_text(prompt, "prompt", MAX_PROMPT_LENGTH)
    model = _clean_text(model, "model", _MAX_MODEL_LENGTH)
    image_bytes = await _read_upload(image, "image")

    summary = {"editable": 0, "total": 0, "percent": 0}
    if mask is not None:
        mask_bytes = await _read_upload(mask, "mask")
        try:
            summary = mask_summary(mask_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        payload = await request_image_edit(
            model=model,
            prompt=build_edit_prompt(prompt, summary),
            image_bytes=image_bytes,
            image_content_type=image_type,
        )
        content, content_type = await fetch_result_image(payload)
    except ProviderRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return Response(
        content=content,
        media_type=content_type,
        headers={"X-Mask-Coverage": str(summary["percent"])},
    )