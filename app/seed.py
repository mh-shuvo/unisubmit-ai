"""Bootstrap commands, run deliberately rather than on every web startup.

    python -m app.seed demo-data                 # sample faculty, programs, supervisors
    python -m app.seed super-admin --email ...   # first Super Admin, password prompted

The web startup used to call `seed()`, which created `admin@unisubmit.local`
with the password `Admin@123`, published in the README (DEF-028).
"""
import argparse
import getpass
import os
import sys

from .config import settings
from .models import Faculty,Department,Program,Teacher,Template,AIProfile,AdminUser
from .security import hash_password

MIN_ADMIN_PASSWORD = 12


def seed_demo_data(db):
    """Sample academic structure for a demo. Creates no accounts."""
    if db.query(Faculty).count()==0:
        sci=Faculty(name='Faculty of Science',code='SCI'); db.add(sci); db.flush()
        cse=Department(name='Computer Science and Engineering',code='CSE',faculty_id=sci.id); db.add(cse); db.flush()
        bsc=Program(name='B.Sc. in CSE',code='BSC-CSE',level='Undergraduate',department_id=cse.id)
        msc=Program(name='M.Sc. in CSE',code='MSC-CSE',level='Graduate',department_id=cse.id); db.add_all([bsc,msc]); db.flush()
        db.add_all([
            Teacher(name='Dr. Ayesha Rahman',designation='Professor',email='ayesha.rahman@example.edu',department_id=cse.id),
            Teacher(name='Dr. Mahmud Hasan',designation='Associate Professor',email='mahmud.hasan@example.edu',department_id=cse.id),
        ])
        db.add(Template(title='BSc CSE Project Report',submission_type='Project Report',program_id=bsc.id))
        db.add(AIProfile(program_id=bsc.id,submission_type='Project Report',service_url=settings.ai_service_url,base_model=settings.ollama_model,embedding_model='BAAI/bge-m3',status='Active'))
    db.commit()


def create_super_admin(db, name: str, email: str, password: str) -> AdminUser:
    email = email.strip().lower()
    if len(password) < MIN_ADMIN_PASSWORD:
        raise ValueError(f'The password must be at least {MIN_ADMIN_PASSWORD} characters.')
    if db.query(AdminUser).filter(AdminUser.email == email).first():
        raise ValueError(f'An admin with email {email} already exists.')
    admin = AdminUser(name=name, email=email, password_hash=hash_password(password), role='SUPER_ADMIN')
    db.add(admin)
    db.commit()
    return admin


def main(argv=None) -> int:
    from .db import Base, SessionLocal, engine

    parser = argparse.ArgumentParser(prog='python -m app.seed', description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('demo-data', help='create the sample faculty, programs and supervisors')
    admin = commands.add_parser('super-admin', help='create a Super Admin account')
    admin.add_argument('--email', required=True)
    admin.add_argument('--name', default='System Super Admin')
    args = parser.parse_args(argv)

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if args.command == 'demo-data':
            seed_demo_data(db)
            print('Demo academic data is in place.')
            return 0
        # UNISUBMIT_ADMIN_PASSWORD allows a non-interactive run, e.g. in Docker.
        password = os.environ.get('UNISUBMIT_ADMIN_PASSWORD') or getpass.getpass('Password: ')
        if not os.environ.get('UNISUBMIT_ADMIN_PASSWORD') and getpass.getpass('Repeat password: ') != password:
            print('The passwords do not match.', file=sys.stderr)
            return 1
        try:
            create_super_admin(db, args.name, args.email, password)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f'Super Admin {args.email.strip().lower()} created.')
        return 0


if __name__ == '__main__':
    sys.exit(main())
