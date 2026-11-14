"""Bootstrap data, created deliberately through the CLI rather than on every
web startup: `unisubmit seed-demo` and `unisubmit create-super-admin`.

The web startup used to call `seed()`, which created `admin@unisubmit.local`
with the password `Admin@123`, published in the README (DEF-028).
"""

from sqlalchemy.orm import Session

from .core.config import get_settings
from .models import AdminUser, AIProfile, Department, Faculty, Program, Teacher, Template
from .security import hash_password

MIN_ADMIN_PASSWORD = 12


def seed_demo_data(db: Session) -> None:
    """Sample academic structure for a demo. Creates no accounts."""
    if db.query(Faculty).count() == 0:
        sci = Faculty(name="Faculty of Science", code="SCI")
        db.add(sci)
        db.flush()
        cse = Department(name="Computer Science and Engineering", code="CSE", faculty_id=sci.id)
        db.add(cse)
        db.flush()
        bsc = Program(
            name="B.Sc. in CSE", code="BSC-CSE", level="Undergraduate", department_id=cse.id
        )
        msc = Program(name="M.Sc. in CSE", code="MSC-CSE", level="Graduate", department_id=cse.id)
        db.add_all([bsc, msc])
        db.flush()
        db.add_all(
            [
                Teacher(
                    name="Dr. Ayesha Rahman",
                    designation="Professor",
                    email="ayesha.rahman@example.edu",
                    department_id=cse.id,
                ),
                Teacher(
                    name="Dr. Mahmud Hasan",
                    designation="Associate Professor",
                    email="mahmud.hasan@example.edu",
                    department_id=cse.id,
                ),
            ]
        )
        db.add(
            Template(
                title="BSc CSE Project Report", submission_type="Project Report", program_id=bsc.id
            )
        )
        db.add(
            AIProfile(
                program_id=bsc.id,
                submission_type="Project Report",
                service_url=get_settings().ai_service_url,
                base_model=get_settings().ollama_model,
                embedding_model="BAAI/bge-m3",
                status="Active",
            )
        )
    db.commit()


def create_super_admin(db: Session, name: str, email: str, password: str) -> AdminUser:
    email = email.strip().lower()
    if len(password) < MIN_ADMIN_PASSWORD:
        raise ValueError(f"The password must be at least {MIN_ADMIN_PASSWORD} characters.")
    if db.query(AdminUser).filter(AdminUser.email == email).first():
        raise ValueError(f"An admin with email {email} already exists.")
    admin = AdminUser(
        name=name, email=email, password_hash=hash_password(password), role="SUPER_ADMIN"
    )
    db.add(admin)
    db.commit()
    return admin
