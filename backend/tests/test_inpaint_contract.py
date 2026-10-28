"""Test the public inpainting contract without making external provider requests.

The tests cover request validation and image response normalization used by the frontend.
Provider calls are kept out of scope so credentials and network state are never needed.
"""

import base64

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.routers.inpaint import _extract_openai_image
from app.services.providers import ProviderResponse, make_openai_alpha_mask


@pytest.fixture
def client() -> TestClient:
    """Build a client against the production ASGI application."""

    return TestClient(app)


def test_common_route_requires_provider_key(client: TestClient) -> None:
    """The shared route rejects missing request-scoped credentials before proxying."""

    response = client.post(
        "/api/inpaint",
        files={
            "image": ("source.png", b"image", "image/png"),
            "mask": ("mask.png", b"mask", "image/png"),
        },
        data={"prompt": "edit", "provider": "openai", "model": "gpt-image-1"},
    )

    assert response.status_code == 422


def test_common_route_rejects_unknown_provider(client: TestClient) -> None:
    """The shared route accepts only the configured provider families."""

    response = client.post(
        "/api/inpaint",
        headers={"X-Provider-API-Key": "test-key"},
        files={
            "image": ("source.png", b"image", "image/png"),
            "mask": ("mask.png", b"mask", "image/png"),
        },
        data={"prompt": "edit", "provider": "unknown", "model": "test"},
    )

    assert response.status_code == 422
    assert "provider must be" in response.json()["detail"]


def test_white_editable_mask_becomes_transparent_openai_png() -> None:
    """The UI's white editable region maps to transparent OpenAI mask alpha."""

    source = Image.new("L", (2, 1))
    source.putdata([255, 0])
    source_buffer = io.BytesIO()
    source.save(source_buffer, format="PNG")

    converted = Image.open(io.BytesIO(make_openai_alpha_mask(source_buffer.getvalue())))

    assert converted.mode == "RGBA"
    assert list(converted.getchannel("A").getdata()) == [0, 255]


def test_openai_base64_response_becomes_png() -> None:
    """OpenAI-compatible b64_json output is converted to the frontend binary format."""

    image_bytes = b"png-test-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    result = ProviderResponse(
        status_code=200,
        content=(f'{{"data":[{{"b64_json":"{encoded}"}}]}}').encode(),
        content_type="application/json",
    )

    response = _extract_openai_image(result)

    assert response.media_type == "image/png"
    assert response.body == image_bytes
