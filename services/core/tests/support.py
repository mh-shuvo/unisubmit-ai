"""`AppTestCase`: the real FastAPI app on a test PostgreSQL database.

Each test runs inside a transaction that is rolled back afterwards; commits in
the code under test become savepoints (see `conftest.py`).
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from factories import PASSWORD, build_world
from unisubmit import main as web
from unisubmit.core.config import get_settings


class AppTestCase(unittest.TestCase):
    Session: sessionmaker[Session]
    tmp: Path

    @pytest.fixture(autouse=True)
    def _database(self, session_factory: sessionmaker[Session], tmp_path: Path) -> None:
        self.Session = session_factory
        self.tmp = tmp_path

    def setUp(self) -> None:
        storage = patch.dict(os.environ, {"UNISUBMIT_STORAGE_DIR": str(self.tmp)})
        storage.start()
        self.addCleanup(storage.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        # Anything the app does at startup lands in this test database, where
        # the tests can see its effect (DEF-024, DEF-028).
        self.app = web.create_app()
        self.app.state.session_factory = self.Session
        with self.Session() as db:
            self.world = build_world(db, self.tmp)

    def client(self) -> TestClient:
        return TestClient(self.app, follow_redirects=False)

    def logged_in_student(self) -> TestClient:
        client = self.client()
        response = client.post(
            "/student/login", data={"email": "student@example.edu", "password": PASSWORD}
        )
        self.assertEqual(response.headers.get("location"), "/student/dashboard")
        return client

    def logged_in_admin(self, email: str = "unit.admin@example.edu") -> TestClient:
        client = self.client()
        response = client.post("/admin/login", data={"email": email, "password": PASSWORD})
        self.assertEqual(response.headers.get("location"), "/admin")
        return client
