"""Talk to the CloseRouter image API on behalf of the browser client.

The router asks this module for two things: the live model catalogue and one image edit.
Both use a single server-side key loaded from ``backend/.env``; the key never reaches the
browser and is never included in logs, error messages, or responses.

Response shape differs per provider: Gemini and Qwen return a short-lived resource URL,
while GPT Image returns inline base64. ``fetch_result_image`` normalizes both to bytes so
the API route can always answer with a binary image.
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image

DEFAULT_BASE_URL = "https://api.closerouter.dev"
BASE_URL_ENV = "CLOSEROUTER_BASE_URL"
API_KEY_ENV = "CLOSEROUTER_API_KEY"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PROMPT_LENGTH = 8_000
REQUEST_TIMEOUT_SECONDS = 240.0
CATALOG_TIMEOUT_SECONDS = 20.0


@dataclass(slots=True)
class ProviderResponse:
    """A minimal response envelope that is safe to pass through the API route."""

    status_code: int
    content: bytes
    content_type: str


class ProviderRequestError(RuntimeError):
    """Describe a provider failure without retaining the secret used for the request."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def load_api_key() -> str:
    """Read the CloseRouter key from the environment, failing loudly when it is absent."""

    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise ProviderRequestError(
            "Server is not configured: set CLOSEROUTER_API_KEY in backend/.env.",
            status_code=503,
        )
    return key


def load_base_url() -> str:
    """Return the configured API host, defaulting to the public CloseRouter gateway."""

    return validate_base_url(os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL))


def validate_base_url(value: str, *, default: str = DEFAULT_BASE_URL) -> str:
    """Validate and normalize a provider base URL."""

    candidate = (value or default).strip().rstrip("/")
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("base_url must be an absolute HTTP or HTTPS URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("base_url must not contain credentials, query parameters, or fragments")
    return candidate


def mask_summary(mask_bytes: bytes) -> dict[str, int]:
    """Report how much of the frame the mask covers, used to build the model instruction."""

    try:
        with Image.open(io.BytesIO(mask_bytes)) as source:
            alpha = source.convert("L")
            pixels = list(alpha.getdata())
    except (OSError, ValueError) as exc:
        raise ValueError("mask must be a valid image") from exc
    editable = sum(1 for value in pixels if value > 0)
    total = len(pixels) or 1
    return {"editable": editable, "total": total, "percent": round(editable * 100 / total)}


def build_edit_prompt(prompt: str, summary: dict[str, int]) -> str:
    """Frame the user prompt so the model changes only the marked region.

    The upstream API has no mask parameter, so scope is communicated in text and the
    untouched area is later restored on the client by compositing through the mask.
    """

    if summary["editable"] == 0:
        return prompt
    return (
        "Edit the supplied image. Apply the change only inside the area described below "
        f"and leave the rest of the frame untouched.\n\n"
        f"Requested change: {prompt}"
    )


def _to_data_url(image_bytes: bytes, content_type: str) -> str:
    """Encode image bytes as an inline data URL, the form the edit route accepts."""

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type or 'image/png'};base64,{encoded}"


def _upstream_error(status: int, body: bytes) -> ProviderRequestError:
    """Translate an upstream status into a useful message, keeping upstream detail short."""

    detail = ""
    try:
        import json

        payload = json.loads(body.decode("utf-8"))
        error = payload.get("error") or payload
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")[:300]
        elif isinstance(error, str):
            detail = error[:300]
    except (ValueError, AttributeError, TypeError):
        detail = ""

    if status in {401, 403}:
        message = "Provider authentication failed; check CLOSEROUTER_API_KEY in backend/.env."
    elif status == 404:
        message = "Provider endpoint or model was not found; check the model id."
    elif status == 429:
        message = "Provider rate limit or quota reached; try again later."
    elif status == 402:
        message = "Provider reports insufficient balance for this request."
    elif 400 <= status < 500:
        message = f"Provider rejected the request. {detail}".strip()
    else:
        message = f"Provider returned an upstream error (HTTP {status})."
    return ProviderRequestError(message, status_code=502)


async def fetch_catalog() -> dict[str, Any]:
    """Return the live image-model catalogue with only the public, useful fields."""

    api_key = load_api_key()
    url = f"{load_base_url()}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=CATALOG_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        raise ProviderRequestError("Could not reach the model catalogue.") from exc
    if response.is_error:
        raise _upstream_error(response.status_code, response.content)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderRequestError("Model catalogue returned malformed JSON.") from exc
    return payload


async def request_image_edit(
    *,
    model: str,
    prompt: str,
    image_bytes: bytes,
    image_content_type: str,
    size: str | None = None,
) -> dict[str, Any]:
    """Send one edit request and return the parsed JSON envelope."""

    api_key = load_api_key()
    url = f"{load_base_url()}/v1/images/edits"
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "image": [_to_data_url(image_bytes, image_content_type)],
        "response_format": "b64_json",
    }
    if size:
        body["size"] = size
    # Gemini is priced by resolution and accepts only the uppercase 1K/2K/4K enum;
    # 2K keeps cost predictable while still beating the default 1K output.
    if model.startswith("google/"):
        body["resolution"] = "2K"
        body.pop("response_format", None)
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise ProviderRequestError("Provider request timed out.") from exc
    except httpx.HTTPError as exc:
        raise ProviderRequestError("Could not connect to the provider.") from exc
    if response.is_error:
        raise _upstream_error(response.status_code, response.content)
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderRequestError("Provider returned malformed JSON.") from exc


async def fetch_result_image(payload: dict[str, Any]) -> tuple[bytes, str]:
    """Resolve a provider envelope to image bytes for a binary HTTP response."""

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ProviderRequestError("Provider returned no image data.")
    first = data[0]
    if not isinstance(first, dict):
        raise ProviderRequestError("Provider returned an unexpected image payload.")

    inline = first.get("b64_json")
    if inline:
        try:
            return base64.b64decode(inline), "image/png"
        except (ValueError, TypeError) as exc:
            raise ProviderRequestError("Provider returned corrupt base64 image data.") from exc

    remote = first.get("url")
    if not remote:
        raise ProviderRequestError("Provider returned neither an image URL nor inline data.")
    parts = urlsplit(remote)
    headers = {}
    if parts.netloc == urlsplit(load_base_url()).netloc:
        headers["Authorization"] = f"Bearer {load_api_key()}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(remote, headers=headers)
    except httpx.HTTPError as exc:
        raise ProviderRequestError("Could not download the generated image.") from exc
    if response.is_error:
        raise ProviderRequestError(f"Generated image download failed (HTTP {response.status_code}).")
    content_type = response.headers.get("content-type", "image/png").split(";")[0]
    if not content_type.startswith("image/"):
        content_type = "image/png"
    return response.content, content_type