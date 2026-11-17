"""Public pages under `/u/{slug}/`: the university comes from the URL (P2.7)."""

from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..models import University
from . import service as tenancy


def university_page(db: Session, slug: str) -> University:
    university = tenancy.active_university(db, slug)
    if university is None:
        raise HTTPException(404)
    return university


def to_university_page(db: Session, page: str) -> RedirectResponse:
    """Pre-v10 public URLs: straight to the page when there is one university,
    otherwise to the home page, which lists them (P2.7)."""
    universities = tenancy.active_universities(db)
    if len(universities) == 1:
        return RedirectResponse(f"/u/{universities[0].slug}/{page}", 303)
    return RedirectResponse("/#universities", 303)
