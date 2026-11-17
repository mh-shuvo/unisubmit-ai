"""Audit events for admin actions (P4.8): a row in `audit_events` in the same
transaction as the change, and a `security` log line."""

from typing import Any

import structlog
from sqlalchemy.orm import Session

from .core.logging import current_request_id
from .models import AdminUser, AuditEvent

logger = structlog.get_logger("unisubmit.security")


def record(
    db: Session,
    admin: AdminUser | None,
    action: str,
    subject: str,
    *,
    university_id: int | None = None,
    **detail: Any,
) -> None:
    """Record `action` on `subject`; the caller commits. `university_id`
    defaults to the admin's own university."""
    university = (
        university_id if university_id is not None else getattr(admin, "university_id", None)
    )
    db.add(
        AuditEvent(
            university_id=university,
            admin_id=admin.id if admin else None,
            action=action,
            subject=subject,
            detail=detail,
            request_id=current_request_id(),
        )
    )
    logger.info(
        "audit." + action,
        subject=subject,
        admin_id=admin.id if admin else None,
        university_id=university,
        **detail,
    )
