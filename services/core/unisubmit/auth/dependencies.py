"""Who is signed in: the session's admin or student, checked on every request (P2.6, P4.3)."""

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from ..models import AdminRole, AdminUser, Program, StudentUser

Account = AdminUser | StudentUser


def admin_user(request: Request, db: Session) -> AdminUser | None:
    uid = request.session.get("admin_id")
    return db.get(AdminUser, uid) if isinstance(uid, int) else None


def student_user(request: Request, db: Session) -> StudentUser | None:
    uid = request.session.get("student_id")
    return db.get(StudentUser, uid) if isinstance(uid, int) else None


def university_inactive(user: Account) -> bool:
    """Deactivating a university signs out all its users on their next request (P2.6)."""
    return user.university is not None and not user.university.is_active


def signed_out(request: Request, user: Account) -> bool:
    """A session ends when its user or university is deactivated, or when the
    user's password changed after it began (P4.3)."""
    gone = (
        not user.active
        or university_inactive(user)
        or request.session.get("auth_epoch") != user.auth_epoch
    )
    if gone:
        request.session.clear()
    return gone


def require_admin(request: Request, db: Session) -> AdminUser:
    user = admin_user(request, db)
    if user is None or signed_out(request, user):
        raise HTTPException(401, "Admin login required")
    return user


def require_role(request: Request, db: Session, role: AdminRole) -> AdminUser:
    user = require_admin(request, db)
    if user.role != role:
        raise HTTPException(403, f"{role.replace('_', ' ').title()} access required")
    return user


def require_student(request: Request, db: Session) -> StudentUser:
    user = student_user(request, db)
    if user is None or signed_out(request, user):
        raise HTTPException(401, "Student login required")
    return user


def same_unit(program: Program, user: Account) -> bool:
    """Whether the program belongs to the user's department or institute."""
    return bool(
        (user.department_id and program.department_id == user.department_id)
        or (user.institute_id and program.institute_id == user.institute_id)
    )
