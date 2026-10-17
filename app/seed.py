from .config import settings
from .models import Faculty,Department,Institute,Program,Teacher,Template,AIProfile,AdminUser
from .security import hash_password

def seed(db):
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
        db.add(AIProfile(program_id=bsc.id,submission_type='Project Report',service_url='http://127.0.0.1:8001',base_model=settings.ollama_model,embedding_model='BAAI/bge-m3',status='Active'))
    if db.query(AdminUser).filter(AdminUser.role=='SUPER_ADMIN').count()==0:
        db.add(AdminUser(name='System Super Admin',email='admin@unisubmit.local',password_hash=hash_password('Admin@123'),role='SUPER_ADMIN'))
    db.commit()
