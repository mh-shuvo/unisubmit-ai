"""DEF-047: every HTML page renders, for the role that can open it."""

from unittest.mock import patch

from support import AppTestCase
from unisubmit import main as web
from unisubmit.seed import create_super_admin

PUBLIC = [
    "/",
    "/how-it-works",
    "/guidelines",
    "/student/register",
    "/student/login",
    "/admin/login",
]
STUDENT = ["/student/dashboard", "/student/profile", "/submit", "/submission/{submission_id}"]
UNIT_ADMIN = [
    "/admin",
    "/admin/programs",
    "/admin/supervisors",
    "/admin/configuration",
    "/admin/submissions",
    "/admin/queue",
    "/admin/template-version/{template_version_id}",
]
SUPER_ADMIN = ["/admin", "/admin/structure", "/admin/admins", "/admin/ai"]
SUPER_PASSWORD = "a long chosen passphrase"


class PageRenderingTests(AppTestCase):
    def setUp(self):
        super().setUp()
        offline = patch.object(web, "ai_health", return_value={"online": False, "error": "test"})
        offline.start()
        self.addCleanup(offline.stop)

    def statuses(self, client, paths):
        return {path: client.get(path.format(**vars(self.world))).status_code for path in paths}

    def test_public_pages_render(self):
        self.assertEqual(self.statuses(self.client(), PUBLIC), dict.fromkeys(PUBLIC, 200))

    def test_student_pages_render(self):
        client = self.logged_in_student()
        self.assertEqual(self.statuses(client, STUDENT), dict.fromkeys(STUDENT, 200))

    def test_unit_admin_pages_render(self):
        client = self.logged_in_admin()
        self.assertEqual(self.statuses(client, UNIT_ADMIN), dict.fromkeys(UNIT_ADMIN, 200))

    def test_super_admin_pages_render(self):
        with self.Session() as db:
            create_super_admin(db, "Super", "super@example.edu", SUPER_PASSWORD)
        client = self.client()
        client.post("/admin/login", data={"email": "super@example.edu", "password": SUPER_PASSWORD})
        self.assertEqual(self.statuses(client, SUPER_ADMIN), dict.fromkeys(SUPER_ADMIN, 200))
