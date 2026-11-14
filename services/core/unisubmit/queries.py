"""Reads of tenant data, scoped once here so no page can forget the filter.

A University Admin sees one university; a unit admin and a student see one
department or institute, which itself belongs to one university.
"""

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from .models import (
    AdminRole,
    AdminUser,
    AIJob,
    Department,
    Faculty,
    Institute,
    Program,
    StudentUser,
    Submission,
    SubmissionVersion,
    Teacher,
    Template,
    TemplateVersion,
)


def program_in_university(university_id: int) -> ColumnElement[bool]:
    """Programs reach their university through a faculty's department or an institute."""
    return or_(
        Program.department_id.in_(
            select(Department.id)
            .join(Faculty, Department.faculty_id == Faculty.id)
            .where(Faculty.university_id == university_id)
        ),
        Program.institute_id.in_(
            select(Institute.id).where(Institute.university_id == university_id)
        ),
    )


def faculties(db: Session, university_id: int) -> list[Faculty]:
    return list(db.scalars(select(Faculty).where(Faculty.university_id == university_id)))


def departments(db: Session, university_id: int, active_only: bool = False) -> list[Department]:
    query = (
        select(Department)
        .join(Faculty, Department.faculty_id == Faculty.id)
        .where(Faculty.university_id == university_id)
    )
    if active_only:
        query = query.where(Department.active)
    return list(db.scalars(query.order_by(Department.name)))


def institutes(db: Session, university_id: int, active_only: bool = False) -> list[Institute]:
    query = select(Institute).where(Institute.university_id == university_id)
    if active_only:
        query = query.where(Institute.active)
    return list(db.scalars(query.order_by(Institute.name)))


def unit_admins(db: Session, university_id: int) -> list[AdminUser]:
    return list(
        db.scalars(
            select(AdminUser).where(
                AdminUser.university_id == university_id, AdminUser.role == AdminRole.UNIT_ADMIN
            )
        )
    )


def active_guidelines(db: Session, university_id: int) -> list[TemplateVersion]:
    return list(
        db.scalars(
            select(TemplateVersion)
            .join(Template, TemplateVersion.template_id == Template.id)
            .join(Program, Template.program_id == Program.id)
            .where(TemplateVersion.status == "Active", program_in_university(university_id))
            .order_by(TemplateVersion.created_at.desc())
        )
    )


def _in_unit(
    model: type[Program] | type[Teacher], user: AdminUser | StudentUser
) -> ColumnElement[bool] | None:
    if user.department_id:
        return model.department_id == user.department_id
    if user.institute_id:
        return model.institute_id == user.institute_id
    return None


def unit_programs(db: Session, user: AdminUser | StudentUser) -> list[Program]:
    unit = _in_unit(Program, user)
    if unit is None:
        return []
    return list(db.scalars(select(Program).where(Program.active, unit).order_by(Program.name)))


def unit_teachers(db: Session, user: AdminUser | StudentUser) -> list[Teacher]:
    unit = _in_unit(Teacher, user)
    if unit is None:
        return []
    return list(
        db.scalars(
            select(Teacher)
            .where(Teacher.active, unit, Teacher.university_id == user.university_id)
            .order_by(Teacher.name)
        )
    )


def unit_submissions(db: Session, admin: AdminUser) -> list[Submission]:
    unit = _in_unit(Program, admin)
    if unit is None:
        return []
    return list(
        db.scalars(
            select(Submission)
            .join(Program, Submission.program_id == Program.id)
            .where(unit, Submission.university_id == admin.university_id)
            .order_by(Submission.created_at.desc())
        )
    )


def unit_jobs(db: Session, admin: AdminUser) -> list[AIJob]:
    unit = _in_unit(Program, admin)
    if unit is None:
        return []
    return list(
        db.scalars(
            select(AIJob)
            .join(SubmissionVersion, AIJob.submission_version_id == SubmissionVersion.id)
            .join(Submission, SubmissionVersion.submission_id == Submission.id)
            .join(Program, Submission.program_id == Program.id)
            .where(unit, Submission.university_id == admin.university_id)
            .order_by(AIJob.created_at.desc())
        )
    )
