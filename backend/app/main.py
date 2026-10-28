"""Create the FastAPI application and its local-development middleware.

The frontend calls the health and inpainting routes mounted below the ``/api`` prefix.
CORS is limited to localhost origins, and no application-level storage is used for API keys.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.inpaint import router as inpaint_router


app = FastAPI(title="Image Editor API", version="0.1.0")

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


app.include_router(inpaint_router, prefix="/api/inpaint", tags=["inpaint"])
