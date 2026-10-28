"""Create the FastAPI application and its local-development middleware.

The frontend calls the health, model-catalogue, and inpainting routes mounted below the
``/api`` prefix. CORS is limited to localhost origins. ``backend/.env`` is loaded at import
time so the CloseRouter key stays on the server and is never sent to the browser.
"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.inpaint import router as inpaint_router

# Load secrets before any route reads them; the file is git-ignored and stays local.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = FastAPI(title="Image Editor API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return a lightweight liveness response without contacting providers."""

    return {"status": "ok"}


app.include_router(inpaint_router, prefix="/api", tags=["image"])