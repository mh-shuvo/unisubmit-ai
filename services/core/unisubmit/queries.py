"""Reads of tenant data, scoped once here so no page can forget the filter.

A University Admin sees one university; a unit admin and a student see one
department or institute, which itself belongs to one university.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    IN_PROGRESS,
    AdminRole,
    AdminUser,
    Department,
    Faculty,
    GuidelineStatus,
    Institute,
    Job,
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
            .where(
                TemplateVersion.status == GuidelineStatus.ACTIVE,
                program_in_university(university_id),
            )
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


PAGE_ROWS = 100  # the most rows any list page shows at once (P5.8)


@dataclass(frozen=True, slots=True)
class Page[T]:
    rows: list[T]
    number: int
    has_next: bool


def _page[T](rows: list[T], number: int) -> Page[T]:
    """`rows` holds one row more than a page when there is a next page."""
    return Page(rows[:PAGE_ROWS], number, len(rows) > PAGE_ROWS)


def _offset(number: int) -> int:
    return (max(number, 1) - 1) * PAGE_ROWS


def _unit_submissions_query(admin: AdminUser) -> Select[Submission] | None:
    unit = _in_unit(Program, admin)
    if unit is None:
        return None
    return (
        select(Submission)
        .join(Program, Submission.program_id == Program.id)
        .where(unit, Submission.university_id == admin.university_id)
    )


def unit_submissions(db: Session, admin: AdminUser, page: int = 1) -> Page[Submission]:
    """One page of the unit's submissions, newest first, with everything the
    list shows loaded up front: the query count does not grow with the rows."""
    query = _unit_submissions_query(admin)
    if query is None:
        return Page([], 1, False)
    rows = db.scalars(
        query.options(
            selectinload(Submission.student),
            selectinload(Submission.program),
            selectinload(Submission.teacher),
            selectinload(Submission.template_version).selectinload(TemplateVersion.template),
        )
        .order_by(Submission.created_at.desc(), Submission.id.desc())
        .limit(PAGE_ROWS + 1)
        .offset(_offset(page))
    )
    return _page(list(rows), max(page, 1))


def unit_submission_counts(db: Session, admin: AdminUser) -> tuple[int, int]:
    """All of the unit's submissions, and those still queued or validating."""
    query = _unit_submissions_query(admin)
    if query is None:
        return 0, 0
    counted = query.with_only_columns(
        func.count(Submission.id),
        func.count(Submission.id).filter(Submission.status.in_(IN_PROGRESS)),
    )
    total, in_progress = db.execute(counted).one()
    return total, in_progress


def unit_jobs(
    db: Session, admin: AdminUser, page: int = 1
) -> Page[tuple[Job, SubmissionVersion | None, TemplateVersion | None]]:
    """One page of the unit's jobs, newest first, each with the submission
    version or guideline it works on and what the queue page shows of them."""
    unit = _in_unit(Program, admin)
    if unit is None:
        return Page([], 1, False)
    rows = db.execute(
        select(Job, SubmissionVersion, TemplateVersion)
        .outerjoin(
            SubmissionVersion,
            SubmissionVersion.id == Job.payload["submission_version_id"].as_integer(),
        )
        .outerjoin(Submission, Submission.id == SubmissionVersion.submission_id)
        .outerjoin(
            TemplateVersion, TemplateVersion.id == Job.payload["template_version_id"].as_integer()
        )
        .outerjoin(Template, Template.id == TemplateVersion.template_id)
        .join(Program, Program.id == func.coalesce(Submission.program_id, Template.program_id))
        .where(unit, Job.university_id == admin.university_id)
        .options(
            selectinload(SubmissionVersion.submission).selectinload(Submission.student),
            selectinload(SubmissionVersion.submission).selectinload(Submission.program),
            selectinload(TemplateVersion.template).selectinload(Template.program),
        )
        .order_by(Job.id.desc())
        .limit(PAGE_ROWS + 1)
        .offset(_offset(page))
    )
    return _page([(job, version, guideline) for job, version, guideline in rows], max(page, 1))
