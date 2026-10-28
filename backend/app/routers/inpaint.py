"""Validate inpainting requests and expose provider proxy endpoints.

The OpenAI-compatible route accepts multipart image and mask uploads, while the Gemini
route accepts JSON base64 images. API keys are request fields only and are passed directly
into the provider call without persistence, logging, or inclusion in error responses.
"""

from __future__ import annotations

import base64
import json
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.providers import (
    DEFAULT_GEMINI_BASE_URL,
    DEFAULT_OPENAI_BASE_URL,
    MAX_IMAGE_BYTES,
    ProviderRequestError,
    ProviderResponse,
    decode_base64_image,
    make_openai_alpha_mask,
    request_gemini_inpaint,
    request_openai_inpaint,
    validate_base_url,
)

router = APIRouter()
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
_MAX_PROMPT_LENGTH = 10_000
_MAX_MODEL_LENGTH = 200
_MAX_API_KEY_LENGTH = 512


class GeminiInpaintRequest(BaseModel):
    """JSON contract for the Gemini image-plus-mask request."""

    api_key: str = Field(min_length=1, max_length=_MAX_API_KEY_LENGTH)
    model: str = Field(min_length=1, max_length=_MAX_MODEL_LENGTH)
    prompt: str = Field(min_length=1, max_length=_MAX_PROMPT_LENGTH)
    image_base64: str = Field(min_length=1)
    mask_base64: str = Field(min_length=1)
    image_mime_type: str = "image/png"
    mask_mime_type: str = "image/png"
    base_url: str = Field(default=DEFAULT_GEMINI_BASE_URL, max_length=2_000)


def _clean_text(value: str, name: str, max_length: int) -> str:
    """Reject blank form fields while keeping provider requests predictable."""

    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail=f"{name} must not be blank")
    if len(cleaned) > max_length:
        raise HTTPException(status_code=422, detail=f"{name} exceeds the maximum length")
    return cleaned


def _validate_mime_type(value: str, field_name: str) -> str:
    """Allow only image media types supported by the provider payloads."""

    normalized = value.lower().strip()
    if normalized not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be one of: {', '.join(sorted(_ALLOWED_IMAGE_TYPES))}",
        )
    return normalized


async def _read_upload(upload: UploadFile, field_name: str) -> bytes:
    """Read one upload in memory and enforce a bounded image payload."""

    content_type = _validate_mime_type(upload.content_type or "", field_name + " content type")
    del content_type  # The caller validates the content type separately for the provider.
    data = await upload.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail=f"{field_name} must not be empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"{field_name} exceeds the 20 MB limit")
    return data


def _provider_response(result: ProviderResponse) -> Response:
    """Return provider bytes while forwarding only the safe content type and status."""

    return Response(
        content=result.content,
        status_code=result.status_code,
        headers={"content-type": result.content_type},
    )


def _provider_http_error(exc: ProviderRequestError) -> HTTPException:
    """Convert a provider service error into a stable public API error."""

    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _extract_gemini_image(result: ProviderResponse) -> Response:
    """Extract the first inline image from a Gemini response for the browser client."""

    try:
        payload = json.loads(result.content.decode("utf-8"))
        parts = payload["candidates"][0]["content"]["parts"]
        image_part = next(part for part in parts if "inlineData" in part or "inline_data" in part)
        inline_data = image_part.get("inlineData") or image_part.get("inline_data")
        encoded = inline_data["data"]
        return Response(content=base64.b64decode(encoded), media_type=inline_data.get("mimeType", "image/png"))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderRequestError("Gemini returned no usable image.", status_code=502) from exc


def _extract_openai_image(result: ProviderResponse) -> Response:
    """Normalize OpenAI-style JSON image output to the frontend's binary response contract."""

    if result.content_type.startswith("image/"):
        return Response(content=result.content, media_type=result.content_type)
    try:
        payload = json.loads(result.content.decode("utf-8"))
        image_data = payload["data"][0]
        if image_data.get("b64_json"):
            return Response(content=base64.b64decode(image_data["b64_json"]), media_type="image/png")
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderRequestError("Provider returned no usable image.", status_code=502) from exc
    raise ProviderRequestError("Provider returned a URL-only image response, which is not supported.", status_code=502)


@router.post("")
async def inpaint(
    image: Annotated[UploadFile, File(description="Source image")],
    mask: Annotated[UploadFile, File(description="PNG mask")],
    prompt: Annotated[str, Form()],
    provider: Annotated[str, Form()] = "openai",
    model: Annotated[str, Form()] = "gpt-image-1",
    base_url: Annotated[str, Form()] = "",
    api_key: Annotated[str | None, Header(alias="X-Provider-API-Key")] = None,
) -> Response:
    """Route the frontend's single multipart contract to the selected provider adapter."""

    selected_provider = provider.strip().lower()
    if selected_provider not in {"openai", "gemini", "router"}:
        raise HTTPException(status_code=422, detail="provider must be openai, gemini, or router")
    prompt = _clean_text(prompt, "prompt", _MAX_PROMPT_LENGTH)
    model = _clean_text(model, "model", _MAX_MODEL_LENGTH)
    key = _clean_text(api_key or "", "X-Provider-API-Key", _MAX_API_KEY_LENGTH)
    image_type = _validate_mime_type(image.content_type or "", "image content type")
    mask_type = _validate_mime_type(mask.content_type or "", "mask content type")
    image_bytes = await _read_upload(image, "image")
    mask_bytes = await _read_upload(mask, "mask")
    try:
        if selected_provider == "gemini":
            result = await request_gemini_inpaint(
                api_key=key,
                base_url=validate_base_url(base_url, default=DEFAULT_GEMINI_BASE_URL),
                model=model,
                prompt=prompt,
                image_bytes=image_bytes,
                image_content_type=image_type,
                mask_bytes=mask_bytes,
                mask_content_type=mask_type,
            )
            return _extract_gemini_image(result)
        result = await request_openai_inpaint(
            api_key=key,
            base_url=validate_base_url(base_url, default=DEFAULT_OPENAI_BASE_URL),
            model=model,
            prompt=prompt,
            image_bytes=image_bytes,
            image_filename=image.filename or "image",
            image_content_type=image_type,
            mask_bytes=make_openai_alpha_mask(mask_bytes),
            mask_filename=mask.filename or "mask.png",
            mask_content_type="image/png",
        )
        return _extract_openai_image(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderRequestError as exc:
        raise _provider_http_error(exc) from exc


@router.post("/openai")
async def openai_inpaint(
    image: Annotated[UploadFile, File(description="Source image")],
    mask: Annotated[UploadFile, File(description="PNG mask")],
    prompt: Annotated[str, Form()],
    model: Annotated[str, Form()],
    api_key: Annotated[str, Form()],
    base_url: Annotated[str, Form()] = DEFAULT_OPENAI_BASE_URL,
) -> Response:
    """Proxy an OpenAI-compatible multipart image edit request."""

    prompt = _clean_text(prompt, "prompt", _MAX_PROMPT_LENGTH)
    model = _clean_text(model, "model", _MAX_MODEL_LENGTH)
    api_key = _clean_text(api_key, "api_key", _MAX_API_KEY_LENGTH)
    image_type = _validate_mime_type(image.content_type or "", "image content type")
    mask_type = _validate_mime_type(mask.content_type or "", "mask content type")
    image_bytes = await _read_upload(image, "image")
    mask_bytes = await _read_upload(mask, "mask")
    try:
        base_url = validate_base_url(base_url, default=DEFAULT_OPENAI_BASE_URL)
        result = await request_openai_inpaint(
            api_key=api_key,
            base_url=base_url,
            model=model,
            prompt=prompt,
            image_bytes=image_bytes,
            image_filename=image.filename or "image",
            image_content_type=image_type,
            mask_bytes=make_openai_alpha_mask(mask_bytes),
            mask_filename=mask.filename or "mask.png",
            mask_content_type="image/png",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderRequestError as exc:
        raise _provider_http_error(exc) from exc
    return _provider_response(result)


@router.post("/gemini")
async def gemini_inpaint(request: GeminiInpaintRequest) -> Response:
    """Proxy a Gemini generateContent request using JSON base64 image parts."""

    prompt = _clean_text(request.prompt, "prompt", _MAX_PROMPT_LENGTH)
    model = _clean_text(request.model, "model", _MAX_MODEL_LENGTH)
    api_key = _clean_text(request.api_key, "api_key", _MAX_API_KEY_LENGTH)
    image_type = _validate_mime_type(request.image_mime_type, "image_mime_type")
    mask_type = _validate_mime_type(request.mask_mime_type, "mask_mime_type")
    try:
        image_bytes = decode_base64_image(request.image_base64, "image_base64")
        mask_bytes = decode_base64_image(request.mask_base64, "mask_base64")
        base_url = validate_base_url(request.base_url, default=DEFAULT_GEMINI_BASE_URL)
        result = await request_gemini_inpaint(
            api_key=api_key,
            base_url=base_url,
            model=model,
            prompt=prompt,
            image_bytes=image_bytes,
            image_content_type=image_type,
            mask_bytes=mask_bytes,
            mask_content_type=mask_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderRequestError as exc:
        raise _provider_http_error(exc) from exc
    return _provider_response(result)
