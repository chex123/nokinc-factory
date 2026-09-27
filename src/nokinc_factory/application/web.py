"""Same-origin chat shell; authenticated APIs remain the data boundary."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

_ASSETS = Path(__file__).with_name("static")
_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self'; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def mount_chat(application: FastAPI) -> None:
    application.mount("/static", StaticFiles(directory=_ASSETS), name="chat-assets")

    @application.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/chat", status_code=303)

    @application.get("/chat", include_in_schema=False)
    def chat_page() -> FileResponse:
        return FileResponse(_ASSETS / "chat.html", headers=_HEADERS)