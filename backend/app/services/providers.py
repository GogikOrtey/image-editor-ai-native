"""Proxy image-edit requests to OpenAI-compatible and Gemini providers.

The router supplies request-scoped credentials and bytes to these functions, which use
``httpx`` for one outbound call and return only the provider response to the caller.
Credentials are never logged, cached, written to disk, or placed in process-wide state.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 120.0


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


def validate_base_url(value: str, *, default: str) -> str:
    """Validate and normalize a user-supplied provider base URL."""

    candidate = (value or default).strip().rstrip("/")
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("base_url must be an absolute HTTP or HTTPS URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("base_url must not contain credentials, query parameters, or fragments")
    return candidate


def make_openai_alpha_mask(mask_bytes: bytes) -> bytes:
    """Convert a white-editable mask to OpenAI's transparent-editable PNG convention."""

    try:
        source = Image.open(io.BytesIO(mask_bytes))
        alpha = source.point(lambda value: 0 if value > 0 else 255)
        rgba = Image.new("RGBA", source.size, (255, 255, 255, 255))
        rgba.putalpha(alpha)
        output = io.BytesIO()
        rgba.save(output, format="PNG")
        return output.getvalue()
    except (OSError, ValueError) as exc:
        raise ValueError("mask must be a valid image") from exc


def decode_base64_image(value: str, field_name: str) -> bytes:
    """Decode one JSON image field and reject empty or oversized payloads."""

    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be valid base64 data") from exc
    if not decoded:
        raise ValueError(f"{field_name} must not be empty")
    if len(decoded) > MAX_IMAGE_BYTES:
        raise ValueError(f"{field_name} exceeds the {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit")
    return decoded


def _provider_error(response: httpx.Response) -> ProviderRequestError:
    """Translate an upstream status into a useful message without echoing its body."""

    status = response.status_code
    if status in {401, 403}:
        message = "Provider authentication failed; check the API key."
    elif status == 404:
        message = "Provider endpoint or model was not found; check base_url and model."
    elif status == 429:
        message = "Provider rate limit or quota reached; try again later."
    elif 400 <= status < 500:
        message = "Provider rejected the request; check model, image, mask, and prompt."
    else:
        message = f"Provider returned an upstream error (HTTP {status})."
    return ProviderRequestError(message, status_code=502)


async def _post(
    url: str,
    *,
    api_key: str,
    files: Any = None,
    data: Any = None,
    json: Any = None,
    params: Any = None,
) -> ProviderResponse:
    """Make one outbound request and convert transport/provider failures."""

    headers = {"Authorization": f"Bearer {api_key}"} if files is not None else {}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.post(
                url,
                headers=headers,
                files=files,
                data=data,
                json=json,
                params=params,
            )
    except httpx.TimeoutException as exc:
        raise ProviderRequestError("Provider request timed out.") from exc
    except httpx.RequestError as exc:
        raise ProviderRequestError("Could not connect to the provider.") from exc

    if response.is_error:
        raise _provider_error(response)

    return ProviderResponse(
        status_code=response.status_code,
        content=response.content,
        content_type=response.headers.get("content-type", "application/json"),
    )


async def request_openai_inpaint(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    image_bytes: bytes,
    image_filename: str,
    image_content_type: str,
    mask_bytes: bytes,
    mask_filename: str,
    mask_content_type: str,
) -> ProviderResponse:
    """Send an OpenAI Images Edits-compatible multipart request."""

    endpoint = f"{validate_base_url(base_url, default=DEFAULT_OPENAI_BASE_URL)}/images/edits"
    files = {
        "image": (image_filename or "image", image_bytes, image_content_type),
        "mask": (mask_filename or "mask.png", mask_bytes, mask_content_type),
    }
    data = {"model": model, "prompt": prompt}
    return await _post(endpoint, api_key=api_key, files=files, data=data)


async def request_gemini_inpaint(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    image_bytes: bytes,
    image_content_type: str,
    mask_bytes: bytes,
    mask_content_type: str,
) -> ProviderResponse:
    """Send a Gemini generateContent request with image and mask inline data."""

    endpoint = (
        f"{validate_base_url(base_url, default=DEFAULT_GEMINI_BASE_URL)}"
        f"/models/{model}:generateContent"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Apply the requested modification only inside the indicated mask "
                            "boundaries. Keep the remaining image completely identical.\n\n"
                            f"User prompt: {prompt}"
                        )
                    },
                    {
                        "inline_data": {
                            "mime_type": image_content_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                    {
                        "inline_data": {
                            "mime_type": mask_content_type,
                            "data": base64.b64encode(mask_bytes).decode("ascii"),
                        }
                    },
                ]
            }
        ]
    }
    return await _post(endpoint, api_key=api_key, json=payload, params={"key": api_key})
