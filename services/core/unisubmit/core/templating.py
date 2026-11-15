"""The Jinja templates shared by every router, and redirect messages."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

PACKAGE = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=str(PACKAGE / "templates"))


def flash_url(path: str, message: str = "", error: str = "") -> str:
    """`path` with a success `message` or an `error` for the page to show."""
    q = []
    if message:
        q.append("message=" + message.replace(" ", "%20"))
    if error:
        q.append("error=" + error.replace(" ", "%20"))
    return path + ("?" + "&".join(q) if q else "")
