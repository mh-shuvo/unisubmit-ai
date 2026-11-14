"""Universities, the tenants, and the accounts that stand above a single unit.

A Platform Admin creates a university together with its first University
Admin and can switch the university off, which blocks every login in it.
"""

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AdminRole, AdminUser, University
from .security import hash_password

MIN_ADMIN_PASSWORD = 12
MAX_SLUG = 60  # universities.slug
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TenancyError(ValueError):
    """A request the caller can correct; the message is shown to them."""


def _check_password(password: str) -> None:
    if len(password) < MIN_ADMIN_PASSWORD:
        raise TenancyError(f"The password must be at least {MIN_ADMIN_PASSWORD} characters.")


def _check_email_free(db: Session, email: str) -> None:
    if db.scalar(select(AdminUser.id).where(AdminUser.email == email)):
        raise TenancyError(f"An admin with email {email} already exists.")


def _check_name_and_slug(db: Session, name: str, slug: str, university_id: int | None) -> None:
    if not name:
        raise TenancyError("The university needs a name.")
    if not SLUG.match(slug) or len(slug) > MAX_SLUG:
        raise TenancyError("The short name may contain lower-case letters, digits and hyphens.")
    clash = db.scalar(
        select(University.id).where(
            (func.lower(University.name) == name.lower()) | (University.slug == slug),
            University.id != (university_id or 0),
        )
    )
    if clash:
        raise TenancyError("Another university already uses that name or short name.")


def create_platform_admin(db: Session, name: str, email: str, password: str) -> AdminUser:
    email = email.strip().lower()
    _check_password(password)
    _check_email_free(db, email)
    admin = AdminUser(
        name=name, email=email, password_hash=hash_password(password), role=AdminRole.PLATFORM_ADMIN
    )
    db.add(admin)
    db.commit()
    return admin


def create_university(
    db: Session,
    name: str,
    slug: str,
    *,
    admin_name: str,
    admin_email: str,
    admin_password: str,
) -> University:
    """A university and its first University Admin, in one transaction."""
    name, slug, admin_email = name.strip(), slug.strip().lower(), admin_email.strip().lower()
    _check_name_and_slug(db, name, slug, None)
    _check_password(admin_password)
    _check_email_free(db, admin_email)
    university = University(name=name, slug=slug, is_active=True)
    db.add(university)
    db.flush()
    db.add(
        AdminUser(
            name=admin_name.strip(),
            email=admin_email,
            password_hash=hash_password(admin_password),
            role=AdminRole.UNIVERSITY_ADMIN,
            university_id=university.id,
        )
    )
    db.commit()
    return university


def rename_university(db: Session, university_id: int, name: str, slug: str) -> University:
    university = db.get(University, university_id)
    if university is None:
        raise TenancyError("No such university.")
    name, slug = name.strip(), slug.strip().lower()
    _check_name_and_slug(db, name, slug, university_id)
    university.name, university.slug = name, slug
    db.commit()
    return university


def set_university_active(db: Session, university_id: int, active: bool) -> University:
    """Users of an inactive university are signed out on their next request."""
    university = db.get(University, university_id)
    if university is None:
        raise TenancyError("No such university.")
    university.is_active = active
    db.commit()
    return university


def active_universities(db: Session) -> list[University]:
    return list(
        db.scalars(select(University).where(University.is_active).order_by(University.name))
    )


def active_university(db: Session, slug: str) -> University | None:
    return db.scalar(select(University).where(University.slug == slug, University.is_active))
