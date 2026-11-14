"""DEF-028: the first admin is created deliberately, with a chosen password."""

import unittest

import pytest

from unisubmit.models import AdminRole, AdminUser, Faculty, University
from unisubmit.security import verify_password
from unisubmit.seed import seed_demo_data
from unisubmit.tenancy import TenancyError, create_platform_admin


class AdminBootstrapTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _database(self, session_factory):
        with session_factory() as db:
            self.db = db
            yield

    def test_demo_data_creates_a_university_and_no_admin_account(self):
        seed_demo_data(self.db)

        self.assertEqual(
            (
                self.db.query(University).one().slug,
                self.db.query(Faculty).one().university.slug,
                self.db.query(AdminUser).count(),
            ),
            ("demo", "demo", 0),
        )

    def test_platform_admin_needs_a_strong_password(self):
        with self.assertRaises(TenancyError):
            create_platform_admin(self.db, "Admin", "admin@example.edu", "Admin@123")

    def test_platform_admin_is_created_with_the_chosen_password(self):
        create_platform_admin(self.db, "Admin", "Admin@Example.edu", "a long chosen passphrase")

        admin = self.db.query(AdminUser).one()
        self.assertEqual(
            (
                admin.email,
                admin.role,
                admin.university_id,
                verify_password("a long chosen passphrase", admin.password_hash),
            ),
            ("admin@example.edu", AdminRole.PLATFORM_ADMIN, None, True),
        )
