"""Test the public image-edit contract without calling the real provider.

The tests cover request validation, mask handling, and response normalization that the
frontend depends on. Upstream calls are stubbed so no credentials or network are needed.

The upstream API has no mask field, so masks are only summarized into prompt guidance;
these tests lock in that behavior so a refactor cannot silently drop the selection.
"""

import asyncio
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services.providers import (
    ProviderRequestError,
    build_edit_prompt,
    fetch_result_image,
    mask_summary,
)


@pytest.fixture
def client() -> TestClient:
    """Build a client against the production ASGI application."""

    return TestClient(app)


def _png_bytes(mode: str, values: list[int], size: tuple[int, int] = (2, 1)) -> bytes:
    """Build a tiny PNG in memory for mask and validation checks."""

    image = Image.new(mode, size)
    if mode == "L":
        image.putdata(values)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_inpaint_rejects_blank_prompt(client: TestClient) -> None:
    """Blank prompts are rejected before any provider call is attempted."""

    response = client.post(
        "/api/inpaint",
        files={"image": ("source.png", _png_bytes("RGB", [], (1, 1)), "image/png")},
        data={"prompt": "   ", "model": "openai/gpt-image-2.5-flare"},
    )

    assert response.status_code == 422
    assert "prompt" in response.json()["detail"]


def test_inpaint_rejects_blank_model(client: TestClient) -> None:
    """A missing model id is a client error, not a provider error."""

    response = client.post(
        "/api/inpaint",
        files={"image": ("source.png", _png_bytes("RGB", [], (1, 1)), "image/png")},
        data={"prompt": "edit", "model": " "},
    )

    assert response.status_code == 422
    assert "model" in response.json()["detail"]


def test_inpaint_rejects_unsupported_media_type(client: TestClient) -> None:
    """Only image media types accepted by the upstream edit route are allowed."""

    response = client.post(
        "/api/inpaint",
        files={"image": ("source.gif", b"gif-bytes", "image/gif")},
        data={"prompt": "edit", "model": "openai/gpt-image-2.5-flare"},
    )

    assert response.status_code == 422
    assert "image content type" in response.json()["detail"]


def test_mask_summary_counts_editable_pixels() -> None:
    """The mask summary drives prompt scoping and must count painted pixels correctly."""

    summary = mask_summary(_png_bytes("L", [255, 0]))

    assert summary["editable"] == 1
    assert summary["total"] == 2
    assert summary["percent"] == 50


def test_mask_summary_rejects_invalid_image() -> None:
    """Corrupt mask uploads surface as a clear validation error."""

    with pytest.raises(ValueError):
        mask_summary(b"not-an-image")


def test_prompt_leaves_original_text_when_mask_is_empty() -> None:
    """With no selection the user prompt is forwarded unchanged."""

    assert build_edit_prompt("make it blue", {"editable": 0, "total": 4, "percent": 0}) == "make it blue"


def test_prompt_scopes_change_to_selection() -> None:
    """A non-empty mask adds the instruction that keeps the rest of the frame intact."""

    prompt = build_edit_prompt("add a star", {"editable": 2, "total": 4, "percent": 50})

    assert "add a star" in prompt
    assert "untouched" in prompt


def test_result_image_decodes_inline_base64() -> None:
    """Inline b64_json output from GPT Image becomes image bytes."""

    image_bytes = _png_bytes("L", [255, 0])

    import base64

    encoded = base64.b64encode(image_bytes).decode("ascii")
    content, content_type = asyncio.run(
        fetch_result_image({"data": [{"b64_json": encoded}]})
    )

    assert content == image_bytes
    assert content_type == "image/png"


def test_result_image_rejects_empty_payload() -> None:
    """An envelope without image data fails loudly instead of returning empty bytes."""

    with pytest.raises(ProviderRequestError):
        asyncio.run(fetch_result_image({"data": []}))