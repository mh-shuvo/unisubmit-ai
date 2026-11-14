from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .config import settings
from .db import Base

class Faculty(Base):
    __tablename__='faculties'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    name: Mapped[str]=mapped_column(String(200),unique=True)
    code: Mapped[str]=mapped_column(String(50),unique=True)
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    departments=relationship('Department',back_populates='faculty')

class Institute(Base):
    __tablename__='institutes'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    name: Mapped[str]=mapped_column(String(200),unique=True)
    code: Mapped[str]=mapped_column(String(50),unique=True)
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    programs=relationship('Program',back_populates='institute')
    teachers=relationship('Teacher',back_populates='institute')

class Department(Base):
    __tablename__='departments'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    name: Mapped[str]=mapped_column(String(200),unique=True)
    code: Mapped[str]=mapped_column(String(50),unique=True)
    faculty_id: Mapped[int]=mapped_column(ForeignKey('faculties.id'))
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    faculty=relationship('Faculty',back_populates='departments')
    programs=relationship('Program',back_populates='department')
    teachers=relationship('Teacher',back_populates='department')

class Program(Base):
    __tablename__='programs'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    name: Mapped[str]=mapped_column(String(220))
    code: Mapped[str]=mapped_column(String(80))
    level: Mapped[str]=mapped_column(String(80),default='Undergraduate')
    department_id: Mapped[Optional[int]]=mapped_column(ForeignKey('departments.id'),nullable=True)
    institute_id: Mapped[Optional[int]]=mapped_column(ForeignKey('institutes.id'),nullable=True)
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    department=relationship('Department',back_populates='programs')
    institute=relationship('Institute',back_populates='programs')
    templates=relationship('Template',back_populates='program')
    ai_profiles=relationship('AIProfile',back_populates='program')

    @property
    def unit_name(self):
        return self.department.name if self.department else (self.institute.name if self.institute else '')
    @property
    def unit_kind(self):
        return 'department' if self.department_id else 'institute'
    @property
    def unit_id(self):
        return self.department_id or self.institute_id

class AdminUser(Base):
    __tablename__='admin_users'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    name: Mapped[str]=mapped_column(String(180))
    email: Mapped[str]=mapped_column(String(200),unique=True)
    password_hash: Mapped[str]=mapped_column(String(300))
    role: Mapped[str]=mapped_column(String(40),default='UNIT_ADMIN')
    department_id: Mapped[Optional[int]]=mapped_column(ForeignKey('departments.id'),nullable=True)
    institute_id: Mapped[Optional[int]]=mapped_column(ForeignKey('institutes.id'),nullable=True)
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    department=relationship('Department')
    institute=relationship('Institute')

    @property
    def unit_name(self):
        return self.department.name if self.department else (self.institute.name if self.institute else 'Global')

class StudentUser(Base):
    __tablename__='student_users'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    name: Mapped[str]=mapped_column(String(180))
    student_id: Mapped[str]=mapped_column(String(100))
    email: Mapped[str]=mapped_column(String(200),unique=True)
    password_hash: Mapped[str]=mapped_column(String(300))
    department_id: Mapped[Optional[int]]=mapped_column(ForeignKey('departments.id'),nullable=True)
    institute_id: Mapped[Optional[int]]=mapped_column(ForeignKey('institutes.id'),nullable=True)
    verified: Mapped[bool]=mapped_column(Boolean,default=True)
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    department=relationship('Department')
    institute=relationship('Institute')

    @property
    def unit_name(self):
        return self.department.name if self.department else (self.institute.name if self.institute else '')
    @property
    def unit_kind(self):
        return 'department' if self.department_id else 'institute'
    @property
    def unit_id(self):
        return self.department_id or self.institute_id

class Teacher(Base):
    __tablename__='teachers'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    name: Mapped[str]=mapped_column(String(200))
    designation: Mapped[str]=mapped_column(String(120))
    email: Mapped[str]=mapped_column(String(200),unique=True)
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    department_id: Mapped[Optional[int]]=mapped_column(ForeignKey('departments.id'),nullable=True)
    institute_id: Mapped[Optional[int]]=mapped_column(ForeignKey('institutes.id'),nullable=True)
    department=relationship('Department',back_populates='teachers')
    institute=relationship('Institute',back_populates='teachers')

    @property
    def unit_name(self):
        return self.department.name if self.department else (self.institute.name if self.institute else '')

class Template(Base):
    __tablename__='templates'
    __table_args__=(UniqueConstraint('program_id','submission_type',name='uq_program_submission_type'),)
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    submission_type: Mapped[str]=mapped_column(String(80))
    title: Mapped[str]=mapped_column(String(200))
    program_id: Mapped[int]=mapped_column(ForeignKey('programs.id'))
    program=relationship('Program',back_populates='templates')
    versions=relationship('TemplateVersion',back_populates='template',cascade='all, delete-orphan')

class TemplateVersion(Base):
    __tablename__='template_versions'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    template_id: Mapped[int]=mapped_column(ForeignKey('templates.id'))
    version: Mapped[str]=mapped_column(String(50))
    pdf_path: Mapped[str]=mapped_column(String(500))
    extracted_text: Mapped[str]=mapped_column(Text,default='')
    additional_rules_pdf_path: Mapped[str]=mapped_column(String(500),default='')
    additional_rules_text: Mapped[str]=mapped_column(Text,default='')
    effective_from: Mapped[str]=mapped_column(String(20),default='')
    status: Mapped[str]=mapped_column(String(30),default='Draft')
    change_note: Mapped[str]=mapped_column(Text,default='')
    rag_collection: Mapped[str]=mapped_column(String(220),default='')
    rag_status: Mapped[str]=mapped_column(String(40),default='Not Indexed')
    format_profile_json: Mapped[str]=mapped_column(Text,default='{}')
    format_profile_status: Mapped[str]=mapped_column(String(40),default='Not Extracted')
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    template=relationship('Template',back_populates='versions')
    rules=relationship('ValidationRule',back_populates='template_version',cascade='all, delete-orphan')

class ValidationRule(Base):
    __tablename__='validation_rules'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    template_version_id: Mapped[int]=mapped_column(ForeignKey('template_versions.id'))
    name: Mapped[str]=mapped_column(String(200))
    category: Mapped[str]=mapped_column(String(80),default='Additional Restriction')
    severity: Mapped[str]=mapped_column(String(30),default='Mandatory')
    validator: Mapped[str]=mapped_column(String(80),default='searchable_pdf')
    target: Mapped[str]=mapped_column(String(200),default='')
    expected: Mapped[str]=mapped_column(String(200),default='')
    source_page: Mapped[str]=mapped_column(String(40),default='')
    source_text: Mapped[str]=mapped_column(Text,default='')
    enabled: Mapped[bool]=mapped_column(Boolean,default=True)
    template_version=relationship('TemplateVersion',back_populates='rules')

class AIProfile(Base):
    __tablename__='ai_profiles'
    __table_args__=(UniqueConstraint('program_id','submission_type',name='uq_ai_program_type'),)
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    program_id: Mapped[int]=mapped_column(ForeignKey('programs.id'))
    submission_type: Mapped[str]=mapped_column(String(80))
    service_url: Mapped[str]=mapped_column(String(250),default='http://127.0.0.1:8001')
    base_model: Mapped[str]=mapped_column(String(120),default=lambda: settings.ollama_model)
    adapter_name: Mapped[str]=mapped_column(String(160),default='')
    adapter_version: Mapped[str]=mapped_column(String(60),default='')
    embedding_model: Mapped[str]=mapped_column(String(120),default='BAAI/bge-m3')
    rag_collection: Mapped[str]=mapped_column(String(180),default='')
    prompt_version: Mapped[str]=mapped_column(String(80),default='v1')
    training_dataset_version: Mapped[str]=mapped_column(String(80),default='')
    status: Mapped[str]=mapped_column(String(30),default='Draft')
    program=relationship('Program',back_populates='ai_profiles')

class Submission(Base):
    __tablename__='submissions'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    student_user_id: Mapped[int]=mapped_column(ForeignKey('student_users.id'))
    title: Mapped[str]=mapped_column(String(400))
    program_id: Mapped[int]=mapped_column(ForeignKey('programs.id'))
    teacher_id: Mapped[int]=mapped_column(ForeignKey('teachers.id'))
    template_version_id: Mapped[int]=mapped_column(ForeignKey('template_versions.id'))
    ai_profile_id: Mapped[Optional[int]]=mapped_column(ForeignKey('ai_profiles.id'),nullable=True)
    status: Mapped[str]=mapped_column(String(50),default='QUEUED')
    email_status: Mapped[str]=mapped_column(String(50),default='Not Sent')
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    student=relationship('StudentUser')
    program=relationship('Program')
    teacher=relationship('Teacher')
    template_version=relationship('TemplateVersion')
    ai_profile=relationship('AIProfile')
    versions=relationship('SubmissionVersion',back_populates='submission',cascade='all, delete-orphan')

class SubmissionVersion(Base):
    __tablename__='submission_versions'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    submission_id: Mapped[int]=mapped_column(ForeignKey('submissions.id'))
    version_no: Mapped[int]=mapped_column(Integer)
    pdf_path: Mapped[str]=mapped_column(String(500))
    extracted_text: Mapped[str]=mapped_column(Text,default='')
    status: Mapped[str]=mapped_column(String(50),default='QUEUED')
    validation_json: Mapped[str]=mapped_column(Text,default='[]')
    ai_suggestions: Mapped[str]=mapped_column(Text,default='')
    report_path: Mapped[str]=mapped_column(String(500),default='')
    ai_run_reference: Mapped[str]=mapped_column(String(120),default='')
    emailed_at: Mapped[Optional[datetime]]=mapped_column(DateTime,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    submission=relationship('Submission',back_populates='versions')
    job=relationship('AIJob',back_populates='submission_version',uselist=False,cascade='all, delete-orphan')

class AIJob(Base):
    __tablename__='ai_jobs'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    submission_version_id: Mapped[int]=mapped_column(ForeignKey('submission_versions.id'),unique=True)
    status: Mapped[str]=mapped_column(String(40),default='QUEUED')
    priority: Mapped[int]=mapped_column(Integer,default=100)
    attempt_count: Mapped[int]=mapped_column(Integer,default=0)
    error_message: Mapped[str]=mapped_column(Text,default='')
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]]=mapped_column(DateTime,nullable=True)
    completed_at: Mapped[Optional[datetime]]=mapped_column(DateTime,nullable=True)
    submission_version=relationship('SubmissionVersion',back_populates='job')
