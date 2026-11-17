"""Demo data, created deliberately with `unisubmit seed-demo` rather than on
every web startup.

The web startup used to call `seed()`, which created `admin@unisubmit.local`
with the password `Admin@123`, published in the README (DEF-028).
"""

from sqlalchemy.orm import Session

from .core.config import get_settings
from .models import AIProfile, Department, Faculty, Program, Teacher, Template, University


def seed_demo_data(db: Session) -> None:
    """A demo university with a sample academic structure. Creates no accounts."""
    if db.query(University).count() == 0:
        demo = University(name="Demo University", slug="demo", is_active=True)
        db.add(demo)
        db.flush()
        sci = Faculty(name="Faculty of Science", code="SCI", university_id=demo.id)
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
                    university_id=demo.id,
                    department_id=cse.id,
                ),
                Teacher(
                    name="Dr. Mahmud Hasan",
                    designation="Associate Professor",
                    email="mahmud.hasan@example.edu",
                    university_id=demo.id,
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
