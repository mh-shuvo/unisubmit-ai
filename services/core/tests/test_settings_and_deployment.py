"""Regression tests for secrets, the admin bootstrap and the Docker deployment.

DEF-027: the session-signing secret fell back to a published default.
DEF-028: an admin account with a published password was created automatically.
DEF-029: the AI service token is required configuration on both sides.
DEF-030: internal services were published on every host interface with a
         default database password.
DEF-031: the Qdrant image tag floated with `latest`.
DEF-024: the queue worker runs as its own service.
"""

import os
import re
import unittest
from unittest.mock import patch

import support  # noqa: F401  sets the test environment before the app is imported
import yaml
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from support import ROOT
from unisubmit.config import Settings
from unisubmit.db import Base
from unisubmit.models import AdminUser, Faculty

STRONG = "a" * 16 + "b" * 16 + "c" * 8


def settings_from(**env):
    with patch.dict(os.environ, env, clear=True):
        return Settings(_env_file=None)


class SecretSettingsTests(unittest.TestCase):
    def test_missing_app_secret_is_rejected(self):
        """DEF-027"""
        with self.assertRaises(ValidationError):
            settings_from(AI_SERVICE_TOKEN=STRONG)

    def test_published_placeholder_secrets_are_rejected(self):
        """DEF-027: the values shipped in .env.example and compose must not run."""
        for placeholder in (
            "change-this-secret-in-production",
            "replace-with-a-long-random-secret",
        ):
            with self.subTest(placeholder=placeholder), self.assertRaises(ValidationError):
                settings_from(APP_SECRET=placeholder, AI_SERVICE_TOKEN=STRONG)

    def test_short_app_secret_is_rejected(self):
        """DEF-027"""
        with self.assertRaises(ValidationError):
            settings_from(APP_SECRET="short-secret", AI_SERVICE_TOKEN=STRONG)

    def test_missing_ai_service_token_is_rejected(self):
        """DEF-029"""
        with self.assertRaises(ValidationError):
            settings_from(APP_SECRET=STRONG)

    def test_strong_values_are_accepted(self):
        settings = settings_from(APP_SECRET=STRONG, AI_SERVICE_TOKEN=STRONG[::-1])
        self.assertEqual(settings.app_secret, STRONG)


class AdminBootstrapTests(unittest.TestCase):
    """DEF-028: the first admin is created deliberately, with a chosen password."""

    def setUp(self):
        engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)

    def test_demo_data_creates_no_admin_account(self):
        from unisubmit.seed import seed_demo_data

        seed_demo_data(self.db)

        self.assertEqual(
            (self.db.query(Faculty).count() > 0, self.db.query(AdminUser).count()), (True, 0)
        )

    def test_super_admin_needs_a_strong_password(self):
        from unisubmit.seed import create_super_admin

        with self.assertRaises(ValueError):
            create_super_admin(self.db, "Admin", "admin@example.edu", "Admin@123")

    def test_super_admin_is_created_with_the_chosen_password(self):
        from unisubmit.security import verify_password
        from unisubmit.seed import create_super_admin

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


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        self.services = self.compose["services"]

    def test_published_ports_bind_to_loopback_only(self):
        """DEF-030: nothing is reachable from another machine on the network."""
        exposed = [
            f"{name}: {port}"
            for name, service in self.services.items()
            for port in service.get("ports", [])
            if not str(port).startswith("127.0.0.1:")
        ]
        self.assertEqual(exposed, [])

    def test_no_secret_falls_back_to_a_default(self):
        """DEF-027 and DEF-030: secrets must be set, never defaulted."""
        text = (ROOT / "docker-compose.yml").read_text()
        for variable in ("APP_SECRET", "AI_SERVICE_TOKEN", "POSTGRES_PASSWORD"):
            with self.subTest(variable=variable):
                defaulted = re.search(r"\$\{" + variable + r":-", text)
                required = re.search(r"\$\{" + variable + r":\?", text)
                self.assertEqual((bool(defaulted), bool(required)), (False, True))
        self.assertNotIn("unisubmit:unisubmit@", text)

    def test_images_are_pinned(self):
        """DEF-031: an image tag must name a version, never `latest`."""
        floating = [
            f"{name}: {service['image']}"
            for name, service in self.services.items()
            if "image" in service
            and (":" not in service["image"] or service["image"].endswith(":latest"))
        ]
        self.assertEqual(floating, [])

    def test_queue_worker_runs_as_its_own_service(self):
        """DEF-024"""
        self.assertEqual(self.services["worker"]["command"], ["python", "-m", "unisubmit.worker"])


if __name__ == "__main__":
    unittest.main()
