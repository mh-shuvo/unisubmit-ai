"""DEF-028: the first admin is created deliberately, with a chosen password."""

import unittest

import pytest

from unisubmit.models import AdminUser, Faculty
from unisubmit.security import verify_password
from unisubmit.seed import create_super_admin, seed_demo_data


class AdminBootstrapTests(unittest.TestCase):
    """DEF-028: the first admin is created deliberately, with a chosen password."""

    @pytest.fixture(autouse=True)
    def _database(self, session_factory):
        with session_factory() as db:
            self.db = db
            yield

    def test_demo_data_creates_no_admin_account(self):
        seed_demo_data(self.db)

        self.assertEqual(
            (self.db.query(Faculty).count() > 0, self.db.query(AdminUser).count()), (True, 0)
        )

    def test_super_admin_needs_a_strong_password(self):
        with self.assertRaises(ValueError):
            create_super_admin(self.db, "Admin", "admin@example.edu", "Admin@123")

    def test_super_admin_is_created_with_the_chosen_password(self):
        create_super_admin(self.db, "Admin", "Admin@Example.edu", "a long chosen passphrase")

        admin = self.db.query(AdminUser).one()
        self.assertEqual(
            (
                admin.email,
                admin.role,
                verify_password("a long chosen passphrase", admin.password_hash),
            ),
            ("admin@example.edu", "SUPER_ADMIN", True),
        )
