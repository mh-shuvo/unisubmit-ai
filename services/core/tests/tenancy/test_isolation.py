"""P2.11: nothing of one university is visible to, or writable by, another.

Both universities come from the same factory, so their faculties, departments
and programs share names and codes: that alone checks per-university
uniqueness (P2.3, DEF-043). Each test then crosses the boundary once.
"""

from sqlalchemy.exc import IntegrityError

from factories import PASSWORD, add_version, build_world, pdf_bytes
from support import AppTestCase
from unisubmit import queries
from unisubmit.models import AdminRole, AdminUser, Department, StudentUser, Teacher
from unisubmit.tenancy import create_platform_admin, set_university_active

PLATFORM_PASSWORD = "a long chosen passphrase"


class TwoUniversities(AppTestCase):
    def setUp(self):
        super().setUp()
        with self.Session() as db:
            self.other = build_world(db, self.tmp, slug="other")

    def other_admin(self):
        return self.logged_in_admin(f"university.admin@{self.other.domain}")


class OrganisationTests(TwoUniversities):
    def test_university_admin_lists_only_its_own_unit_admins(self):
        page = self.logged_in_admin("university.admin@example.edu").get("/admin/admins").text
        self.assertEqual(
            ("unit.admin@example.edu" in page, f"unit.admin@{self.other.domain}" in page),
            (True, False),
        )

    def test_department_cannot_be_added_to_another_universitys_faculty(self):
        response = self.other_admin().post(
            "/admin/departments",
            data={"faculty_id": self.world.faculty_id, "name": "Physics", "code": "PHY"},
        )
        with self.Session() as db:
            created = db.query(Department).filter_by(code="PHY").count()
        self.assertEqual((response.status_code, created), (404, 0))

    def test_unit_admin_cannot_be_created_in_another_universitys_unit(self):
        response = self.other_admin().post(
            "/admin/admins",
            data={
                "name": "Intruder",
                "email": "intruder@other.example.edu",
                "password": PASSWORD,
                "unit_type": "department",
                "unit_id": self.world.department_id,
            },
        )
        with self.Session() as db:
            created = db.query(AdminUser).filter_by(email="intruder@other.example.edu").count()
        self.assertEqual((response.status_code, created), (404, 0))

    def test_same_supervisor_email_may_exist_in_two_universities(self):
        """DEF-043: a joint appointment is two directory entries."""
        with self.Session() as db:
            db.add(
                Teacher(
                    name="Dr Joint",
                    designation="Professor",
                    email="supervisor@example.edu",
                    university_id=self.other.university_id,
                    department_id=self.other.department_id,
                )
            )
            db.commit()
            count = db.query(Teacher).filter_by(email="supervisor@example.edu").count()
        self.assertEqual(count, 2)


class PublicPageTests(TwoUniversities):
    def test_guidelines_page_lists_only_that_universitys_guidelines(self):
        page = self.client().get("/u/test/guidelines").text
        self.assertEqual(
            (
                f"/guidelines/{self.world.template_version_id}/download" in page,
                f"/guidelines/{self.other.template_version_id}/download" in page,
            ),
            (True, False),
        )

    def test_registration_rejects_another_universitys_unit(self):
        response = self.client().post(
            "/u/test/register",
            data={
                "name": "Crosser",
                "student_id": "X-1",
                "email": "crosser@example.edu",
                "password": PASSWORD,
                "unit_type": "department",
                "unit_id": self.other.department_id,
            },
        )
        with self.Session() as db:
            created = db.query(StudentUser).filter_by(email="crosser@example.edu").count()
        self.assertEqual((response.status_code, created), (400, 0))

    def test_registration_joins_the_university_of_the_page(self):
        self.client().post(
            "/u/other/register",
            data={
                "name": "New",
                "student_id": "N-1",
                "email": "new@other.example.edu",
                "password": PASSWORD,
                "unit_type": "department",
                "unit_id": self.other.department_id,
            },
        )
        with self.Session() as db:
            student = db.query(StudentUser).filter_by(email="new@other.example.edu").one()
        self.assertEqual(student.university_id, self.other.university_id)

    def test_old_registration_url_offers_a_choice_of_universities(self):
        response = self.client().get("/student/register")
        self.assertEqual(response.headers["location"], "/#universities")

    def test_unknown_university_is_not_found(self):
        self.assertEqual(self.client().get("/u/nowhere/register").status_code, 404)


class SubmissionTests(TwoUniversities):
    def test_unit_admin_sees_only_its_own_universitys_submissions(self):
        with self.Session() as db:
            admin = db.get(AdminUser, self.other.admin_id)
            ids = [submission.id for submission in queries.unit_submissions(db, admin)]
        self.assertEqual(ids, [self.other.submission_id])

    def test_unit_admin_sees_only_its_own_universitys_queue(self):
        with self.Session() as db:
            add_version(db, self.world.submission_id, self.tmp, "QUEUED")
            mine = add_version(db, self.other.submission_id, self.tmp, "QUEUED")
            admin = db.get(AdminUser, self.other.admin_id)
            ids = [job.id for job in queries.unit_jobs(db, admin)]
        self.assertEqual(ids, [mine])

    def test_student_cannot_choose_another_universitys_supervisor(self):
        response = self.logged_in_student().post(
            "/submit",
            data={
                "title": "Crossing",
                "program_id": self.world.program_id,
                "teacher_id": self.other.teacher_id,
                "template_version_id": self.world.template_version_id,
            },
            files={"pdf": ("t.pdf", pdf_bytes("Thesis"), "application/pdf")},
        )
        self.assertEqual(response.status_code, 403)

    def test_uploads_are_stored_in_the_universitys_folder(self):
        """P2.9: files live under <root>/<university id>/ and rows hold relative paths."""
        with self.Session() as db:
            add_version(db, self.world.submission_id, self.tmp, "COMPLETED")
        self.logged_in_student().post(
            f"/submission/{self.world.submission_id}/resubmit",
            files={"pdf": ("r.pdf", pdf_bytes("Revised"), "application/pdf")},
        )
        with self.Session() as db:
            from unisubmit.models import SubmissionVersion

            latest = (
                db.query(SubmissionVersion)
                .filter_by(submission_id=self.world.submission_id)
                .order_by(SubmissionVersion.version_no.desc())
                .first()
            )
        self.assertEqual(
            (latest.pdf_path.split("/")[:2], (self.tmp / latest.pdf_path).is_file()),
            ([str(self.world.university_id), "submissions"], True),
        )


class PlatformTests(TwoUniversities):
    def platform(self):
        with self.Session() as db:
            create_platform_admin(db, "Platform", "platform@example.edu", PLATFORM_PASSWORD)
        client = self.client()
        client.post(
            "/admin/login", data={"email": "platform@example.edu", "password": PLATFORM_PASSWORD}
        )
        return client

    def test_university_admin_cannot_open_platform_pages(self):
        client = self.logged_in_admin("university.admin@example.edu")
        self.assertEqual(client.get("/platform/universities").status_code, 403)

    def test_platform_admin_onboards_a_university_with_its_admin(self):
        self.platform().post(
            "/platform/universities",
            data={
                "name": "Third University",
                "slug": "third",
                "admin_name": "Third Admin",
                "admin_email": "admin@third.example.edu",
                "admin_password": PLATFORM_PASSWORD,
            },
        )
        with self.Session() as db:
            admin = db.query(AdminUser).filter_by(email="admin@third.example.edu").one()
            self.assertEqual(
                (admin.role, admin.university.slug), (AdminRole.UNIVERSITY_ADMIN, "third")
            )

    def test_deactivating_a_university_signs_out_its_users(self):
        """P2.6: on their next request, not at their next login."""
        student = self.logged_in_student()
        with self.Session() as db:
            set_university_active(db, self.world.university_id, False)
        response = student.get("/student/dashboard")
        self.assertEqual(response.headers.get("location"), "/student/login")

    def test_deactivated_university_refuses_login(self):
        with self.Session() as db:
            set_university_active(db, self.world.university_id, False)
        response = self.client().post(
            "/admin/login", data={"email": "unit.admin@example.edu", "password": PASSWORD}
        )
        self.assertIn("deactivated", response.headers["location"])

    def test_other_university_is_unaffected_by_a_deactivation(self):
        with self.Session() as db:
            set_university_active(db, self.world.university_id, False)
        self.logged_in_admin(f"unit.admin@{self.other.domain}")

    def test_only_a_platform_admin_may_have_no_university(self):
        """P2.4: the database refuses a University Admin outside every university."""
        with self.Session() as db:
            db.add(
                AdminUser(
                    name="Orphan",
                    email="orphan@example.edu",
                    password_hash="x",
                    role=AdminRole.UNIVERSITY_ADMIN,
                )
            )
            with self.assertRaises(IntegrityError):
                db.commit()
