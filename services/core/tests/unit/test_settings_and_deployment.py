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
from pathlib import Path
from unittest.mock import patch

import yaml
from pydantic import ValidationError

from unisubmit.core.config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[4]
STRONG = "a" * 16 + "b" * 16 + "c" * 8
DATABASE = "postgresql+psycopg://unisubmit:pw@127.0.0.1:5433/unisubmit"
VALID = {
    "UNISUBMIT_SECRET_KEY": STRONG,
    "UNISUBMIT_AI_SERVICE_TOKEN": STRONG[::-1],
    "UNISUBMIT_DATABASE_URL": DATABASE,
}


def settings_from(**env):
    with patch.dict(os.environ, env, clear=True):
        return Settings(_env_file=None)


def without(name, **overrides):
    return {key: value for key, value in VALID.items() if key != name} | overrides


class SecretSettingsTests(unittest.TestCase):
    def test_missing_secret_key_is_rejected(self):
        """DEF-027"""
        with self.assertRaises(ValidationError):
            settings_from(**without("UNISUBMIT_SECRET_KEY"))

    def test_published_placeholder_secrets_are_rejected(self):
        """DEF-027: the values shipped in .env.example and compose must not run."""
        for placeholder in (
            "change-this-secret-in-production",
            "replace-with-a-long-random-secret",
        ):
            with self.subTest(placeholder=placeholder), self.assertRaises(ValidationError):
                settings_from(**VALID | {"UNISUBMIT_SECRET_KEY": placeholder})

    def test_short_secret_key_is_rejected(self):
        """DEF-027"""
        with self.assertRaises(ValidationError):
            settings_from(**VALID | {"UNISUBMIT_SECRET_KEY": "short-secret"})

    def test_missing_ai_service_token_is_rejected(self):
        """DEF-029"""
        with self.assertRaises(ValidationError):
            settings_from(**without("UNISUBMIT_AI_SERVICE_TOKEN"))

    def test_strong_values_are_accepted_and_hidden(self):
        settings = settings_from(**VALID)
        self.assertEqual(
            (settings.secret_key.get_secret_value(), STRONG in repr(settings)), (STRONG, False)
        )

    def test_startup_names_every_missing_variable(self):
        """P1.5: a missing secret stops startup with the variable to set."""
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as stop:
            get_settings.cache_clear()
            self.addCleanup(get_settings.cache_clear)
            with patch.dict(Settings.model_config, {"env_file": None}):
                get_settings()
        message = str(stop.exception)
        self.assertEqual(
            [name in message for name in VALID],
            [True, True, True],
        )

    def test_plain_postgresql_url_uses_psycopg_3(self):
        settings = settings_from(**VALID | {"UNISUBMIT_DATABASE_URL": "postgresql://u:p@db/x"})
        self.assertEqual(str(settings.database_url), "postgresql+psycopg://u:p@db/x")

    def test_non_postgres_database_is_rejected(self):
        with self.assertRaises(ValidationError):
            settings_from(**VALID | {"UNISUBMIT_DATABASE_URL": "sqlite:///./unisubmit.db"})


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
