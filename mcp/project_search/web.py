"""Project Search API with browser UIs."""

from pathlib import Path

from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .server import app
from . import answer as _answer  # noqa: F401  # Register /answer.


STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount(
    "/project-search-assets",
    StaticFiles(directory=STATIC_DIR),
    name="project-search-assets",
)

@app.get("/ui", include_in_schema=False)
@app.get("/ui/", include_in_schema=False)
@app.get("/search-ui", include_in_schema=False)
@app.get("/search-ui/", include_in_schema=False)
async def retired_custom_chat_ui():
    """Redirect the retired custom chat frontend to Open WebUI."""
    return RedirectResponse(
        url="https://mpllm.tail7e5dfc.ts.net",
        status_code=307,
    )
