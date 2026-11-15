"""P5.8: admin lists load what they show up front and page at 100 rows.

Each submission gets its own student and supervisor: lazy loads of a shared
row are answered from the identity map, so only distinct rows expose N+1.
"""

from itertools import count

import sqlalchemy as sa

from support import AppTestCase
from unisubmit.models import (
    Job,
    JobType,
    StudentUser,
    Submission,
    SubmissionVersion,
    Teacher,
)
from unisubmit.queries import PAGE_ROWS

SERIAL = count()


class AdminListTests(AppTestCase):
    def add_submissions(self, number: int, *, with_jobs: bool = False) -> None:
        world = self.world
        with self.Session() as db:
            for _ in range(number):
                n = next(SERIAL)
                student = StudentUser(
                    name=f"Student {n}",
                    student_id=f"N-{n}",
                    email=f"list.student.{n}@example.edu",
                    password_hash="unused",
                    university_id=world.university_id,
                    department_id=world.department_id,
                )
                teacher = Teacher(
                    name=f"Supervisor {n}",
                    designation="Lecturer",
                    email=f"list.supervisor.{n}@example.edu",
                    university_id=world.university_id,
                    department_id=world.department_id,
                )
                db.add_all([student, teacher])
                db.flush()
                submission = Submission(
                    university_id=world.university_id,
                    student_user_id=student.id,
                    title=f"Thesis {n}",
                    program_id=world.program_id,
                    teacher_id=teacher.id,
                    template_version_id=world.template_version_id,
                )
                db.add(submission)
                db.flush()
                if with_jobs:
                    version = SubmissionVersion(
                        submission_id=submission.id, version_no=1, pdf_path=f"{n}.pdf"
                    )
                    db.add(version)
                    db.flush()
                    db.add(
                        Job(
                            university_id=world.university_id,
                            type=JobType.VALIDATE_SUBMISSION,
                            subject_key=f"submission:{submission.id}",
                            payload={"submission_version_id": version.id},
                        )
                    )
            db.commit()

    def statements(self, path: str) -> int:
        client = self.logged_in_admin()
        executed = 0

        def counted(*_: object) -> None:
            nonlocal executed
            executed += 1

        sa.event.listen(sa.Engine, "before_cursor_execute", counted)
        try:
            assert client.get(path).status_code == 200
        finally:
            sa.event.remove(sa.Engine, "before_cursor_execute", counted)
        return executed

    def test_submission_list_query_count_does_not_grow_with_rows(self):
        self.add_submissions(10)
        ten = self.statements("/admin/submissions")
        self.add_submissions(40)

        self.assertEqual(self.statements("/admin/submissions"), ten)

    def test_queue_page_query_count_does_not_grow_with_rows(self):
        self.add_submissions(10, with_jobs=True)
        ten = self.statements("/admin/queue")
        self.add_submissions(40, with_jobs=True)

        self.assertEqual(self.statements("/admin/queue"), ten)

    def test_submission_list_shows_at_most_one_page_of_rows(self):
        self.add_submissions(PAGE_ROWS + 20)  # plus the world's own submission
        client = self.logged_in_admin()

        pages = [client.get(f"/admin/submissions?page={n}").text for n in (1, 2)]

        self.assertEqual(
            [(page.count('data-label="ID"'), "?page=2" in page) for page in pages],
            [(PAGE_ROWS, True), (21, False)],
        )
