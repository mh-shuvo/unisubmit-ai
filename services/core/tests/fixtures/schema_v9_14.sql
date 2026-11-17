-- The schema v9.14 built with create_all (app/models.py at 66683c7), before
-- migrations: Postgres named every constraint that the models left unnamed.
-- Generated with pg_dump --schema-only; used to test adoption (DEF-039).
CREATE TABLE admin_users (
    id integer NOT NULL,
    name character varying(180) NOT NULL,
    email character varying(200) NOT NULL,
    password_hash character varying(300) NOT NULL,
    role character varying(40) NOT NULL,
    department_id integer,
    institute_id integer,
    active boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);
CREATE SEQUENCE admin_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE admin_users_id_seq OWNED BY admin_users.id;
CREATE TABLE ai_jobs (
    id integer NOT NULL,
    submission_version_id integer NOT NULL,
    status character varying(40) NOT NULL,
    priority integer NOT NULL,
    attempt_count integer NOT NULL,
    error_message text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    started_at timestamp without time zone,
    completed_at timestamp without time zone
);
CREATE SEQUENCE ai_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE ai_jobs_id_seq OWNED BY ai_jobs.id;
CREATE TABLE ai_profiles (
    id integer NOT NULL,
    program_id integer NOT NULL,
    submission_type character varying(80) NOT NULL,
    service_url character varying(250) NOT NULL,
    base_model character varying(120) NOT NULL,
    adapter_name character varying(160) NOT NULL,
    adapter_version character varying(60) NOT NULL,
    embedding_model character varying(120) NOT NULL,
    rag_collection character varying(180) NOT NULL,
    prompt_version character varying(80) NOT NULL,
    training_dataset_version character varying(80) NOT NULL,
    status character varying(30) NOT NULL
);
CREATE SEQUENCE ai_profiles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE ai_profiles_id_seq OWNED BY ai_profiles.id;
CREATE TABLE departments (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    code character varying(50) NOT NULL,
    faculty_id integer NOT NULL,
    active boolean NOT NULL
);
CREATE SEQUENCE departments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE departments_id_seq OWNED BY departments.id;
CREATE TABLE faculties (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    code character varying(50) NOT NULL,
    active boolean NOT NULL
);
CREATE SEQUENCE faculties_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE faculties_id_seq OWNED BY faculties.id;
CREATE TABLE institutes (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    code character varying(50) NOT NULL,
    active boolean NOT NULL
);
CREATE SEQUENCE institutes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE institutes_id_seq OWNED BY institutes.id;
CREATE TABLE programs (
    id integer NOT NULL,
    name character varying(220) NOT NULL,
    code character varying(80) NOT NULL,
    level character varying(80) NOT NULL,
    department_id integer,
    institute_id integer,
    active boolean NOT NULL
);
CREATE SEQUENCE programs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE programs_id_seq OWNED BY programs.id;
CREATE TABLE student_users (
    id integer NOT NULL,
    name character varying(180) NOT NULL,
    student_id character varying(100) NOT NULL,
    email character varying(200) NOT NULL,
    password_hash character varying(300) NOT NULL,
    department_id integer,
    institute_id integer,
    verified boolean NOT NULL,
    active boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);
CREATE SEQUENCE student_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE student_users_id_seq OWNED BY student_users.id;
CREATE TABLE submission_versions (
    id integer NOT NULL,
    submission_id integer NOT NULL,
    version_no integer NOT NULL,
    pdf_path character varying(500) NOT NULL,
    extracted_text text NOT NULL,
    status character varying(50) NOT NULL,
    validation_json text NOT NULL,
    ai_suggestions text NOT NULL,
    report_path character varying(500) NOT NULL,
    ai_run_reference character varying(120) NOT NULL,
    emailed_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL
);
CREATE SEQUENCE submission_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE submission_versions_id_seq OWNED BY submission_versions.id;
CREATE TABLE submissions (
    id integer NOT NULL,
    student_user_id integer NOT NULL,
    title character varying(400) NOT NULL,
    program_id integer NOT NULL,
    teacher_id integer NOT NULL,
    template_version_id integer NOT NULL,
    ai_profile_id integer,
    status character varying(50) NOT NULL,
    email_status character varying(50) NOT NULL,
    created_at timestamp without time zone NOT NULL
);
CREATE SEQUENCE submissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE submissions_id_seq OWNED BY submissions.id;
CREATE TABLE teachers (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    designation character varying(120) NOT NULL,
    email character varying(200) NOT NULL,
    active boolean NOT NULL,
    department_id integer,
    institute_id integer
);
CREATE SEQUENCE teachers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE teachers_id_seq OWNED BY teachers.id;
CREATE TABLE template_versions (
    id integer NOT NULL,
    template_id integer NOT NULL,
    version character varying(50) NOT NULL,
    pdf_path character varying(500) NOT NULL,
    extracted_text text NOT NULL,
    additional_rules_pdf_path character varying(500) NOT NULL,
    additional_rules_text text NOT NULL,
    effective_from character varying(20) NOT NULL,
    status character varying(30) NOT NULL,
    change_note text NOT NULL,
    rag_collection character varying(220) NOT NULL,
    rag_status character varying(40) NOT NULL,
    format_profile_json text NOT NULL,
    format_profile_status character varying(40) NOT NULL,
    created_at timestamp without time zone NOT NULL
);
CREATE SEQUENCE template_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE template_versions_id_seq OWNED BY template_versions.id;
CREATE TABLE templates (
    id integer NOT NULL,
    submission_type character varying(80) NOT NULL,
    title character varying(200) NOT NULL,
    program_id integer NOT NULL
);
CREATE SEQUENCE templates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE templates_id_seq OWNED BY templates.id;
CREATE TABLE validation_rules (
    id integer NOT NULL,
    template_version_id integer NOT NULL,
    name character varying(200) NOT NULL,
    category character varying(80) NOT NULL,
    severity character varying(30) NOT NULL,
    validator character varying(80) NOT NULL,
    target character varying(200) NOT NULL,
    expected character varying(200) NOT NULL,
    source_page character varying(40) NOT NULL,
    source_text text NOT NULL,
    enabled boolean NOT NULL
);
CREATE SEQUENCE validation_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE validation_rules_id_seq OWNED BY validation_rules.id;
ALTER TABLE ONLY admin_users ALTER COLUMN id SET DEFAULT nextval('admin_users_id_seq'::regclass);
ALTER TABLE ONLY ai_jobs ALTER COLUMN id SET DEFAULT nextval('ai_jobs_id_seq'::regclass);
ALTER TABLE ONLY ai_profiles ALTER COLUMN id SET DEFAULT nextval('ai_profiles_id_seq'::regclass);
ALTER TABLE ONLY departments ALTER COLUMN id SET DEFAULT nextval('departments_id_seq'::regclass);
ALTER TABLE ONLY faculties ALTER COLUMN id SET DEFAULT nextval('faculties_id_seq'::regclass);
ALTER TABLE ONLY institutes ALTER COLUMN id SET DEFAULT nextval('institutes_id_seq'::regclass);
ALTER TABLE ONLY programs ALTER COLUMN id SET DEFAULT nextval('programs_id_seq'::regclass);
ALTER TABLE ONLY student_users ALTER COLUMN id SET DEFAULT nextval('student_users_id_seq'::regclass);
ALTER TABLE ONLY submission_versions ALTER COLUMN id SET DEFAULT nextval('submission_versions_id_seq'::regclass);
ALTER TABLE ONLY submissions ALTER COLUMN id SET DEFAULT nextval('submissions_id_seq'::regclass);
ALTER TABLE ONLY teachers ALTER COLUMN id SET DEFAULT nextval('teachers_id_seq'::regclass);
ALTER TABLE ONLY template_versions ALTER COLUMN id SET DEFAULT nextval('template_versions_id_seq'::regclass);
ALTER TABLE ONLY templates ALTER COLUMN id SET DEFAULT nextval('templates_id_seq'::regclass);
ALTER TABLE ONLY validation_rules ALTER COLUMN id SET DEFAULT nextval('validation_rules_id_seq'::regclass);
ALTER TABLE ONLY admin_users
    ADD CONSTRAINT admin_users_email_key UNIQUE (email);
ALTER TABLE ONLY admin_users
    ADD CONSTRAINT admin_users_pkey PRIMARY KEY (id);
ALTER TABLE ONLY ai_jobs
    ADD CONSTRAINT ai_jobs_pkey PRIMARY KEY (id);
ALTER TABLE ONLY ai_jobs
    ADD CONSTRAINT ai_jobs_submission_version_id_key UNIQUE (submission_version_id);
ALTER TABLE ONLY ai_profiles
    ADD CONSTRAINT ai_profiles_pkey PRIMARY KEY (id);
ALTER TABLE ONLY departments
    ADD CONSTRAINT departments_code_key UNIQUE (code);
ALTER TABLE ONLY departments
    ADD CONSTRAINT departments_name_key UNIQUE (name);
ALTER TABLE ONLY departments
    ADD CONSTRAINT departments_pkey PRIMARY KEY (id);
ALTER TABLE ONLY faculties
    ADD CONSTRAINT faculties_code_key UNIQUE (code);
ALTER TABLE ONLY faculties
    ADD CONSTRAINT faculties_name_key UNIQUE (name);
ALTER TABLE ONLY faculties
    ADD CONSTRAINT faculties_pkey PRIMARY KEY (id);
ALTER TABLE ONLY institutes
    ADD CONSTRAINT institutes_code_key UNIQUE (code);
ALTER TABLE ONLY institutes
    ADD CONSTRAINT institutes_name_key UNIQUE (name);
ALTER TABLE ONLY institutes
    ADD CONSTRAINT institutes_pkey PRIMARY KEY (id);
ALTER TABLE ONLY programs
    ADD CONSTRAINT programs_pkey PRIMARY KEY (id);
ALTER TABLE ONLY student_users
    ADD CONSTRAINT student_users_email_key UNIQUE (email);
ALTER TABLE ONLY student_users
    ADD CONSTRAINT student_users_pkey PRIMARY KEY (id);
ALTER TABLE ONLY submission_versions
    ADD CONSTRAINT submission_versions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY submissions
    ADD CONSTRAINT submissions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY teachers
    ADD CONSTRAINT teachers_email_key UNIQUE (email);
ALTER TABLE ONLY teachers
    ADD CONSTRAINT teachers_pkey PRIMARY KEY (id);
ALTER TABLE ONLY template_versions
    ADD CONSTRAINT template_versions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY templates
    ADD CONSTRAINT templates_pkey PRIMARY KEY (id);
ALTER TABLE ONLY ai_profiles
    ADD CONSTRAINT uq_ai_program_type UNIQUE (program_id, submission_type);
ALTER TABLE ONLY templates
    ADD CONSTRAINT uq_program_submission_type UNIQUE (program_id, submission_type);
ALTER TABLE ONLY validation_rules
    ADD CONSTRAINT validation_rules_pkey PRIMARY KEY (id);
ALTER TABLE ONLY admin_users
    ADD CONSTRAINT admin_users_department_id_fkey FOREIGN KEY (department_id) REFERENCES departments(id);
ALTER TABLE ONLY admin_users
    ADD CONSTRAINT admin_users_institute_id_fkey FOREIGN KEY (institute_id) REFERENCES institutes(id);
ALTER TABLE ONLY ai_jobs
    ADD CONSTRAINT ai_jobs_submission_version_id_fkey FOREIGN KEY (submission_version_id) REFERENCES submission_versions(id);
ALTER TABLE ONLY ai_profiles
    ADD CONSTRAINT ai_profiles_program_id_fkey FOREIGN KEY (program_id) REFERENCES programs(id);
ALTER TABLE ONLY departments
    ADD CONSTRAINT departments_faculty_id_fkey FOREIGN KEY (faculty_id) REFERENCES faculties(id);
ALTER TABLE ONLY programs
    ADD CONSTRAINT programs_department_id_fkey FOREIGN KEY (department_id) REFERENCES departments(id);
ALTER TABLE ONLY programs
    ADD CONSTRAINT programs_institute_id_fkey FOREIGN KEY (institute_id) REFERENCES institutes(id);
ALTER TABLE ONLY student_users
    ADD CONSTRAINT student_users_department_id_fkey FOREIGN KEY (department_id) REFERENCES departments(id);
ALTER TABLE ONLY student_users
    ADD CONSTRAINT student_users_institute_id_fkey FOREIGN KEY (institute_id) REFERENCES institutes(id);
ALTER TABLE ONLY submission_versions
    ADD CONSTRAINT submission_versions_submission_id_fkey FOREIGN KEY (submission_id) REFERENCES submissions(id);
ALTER TABLE ONLY submissions
    ADD CONSTRAINT submissions_ai_profile_id_fkey FOREIGN KEY (ai_profile_id) REFERENCES ai_profiles(id);
ALTER TABLE ONLY submissions
    ADD CONSTRAINT submissions_program_id_fkey FOREIGN KEY (program_id) REFERENCES programs(id);
ALTER TABLE ONLY submissions
    ADD CONSTRAINT submissions_student_user_id_fkey FOREIGN KEY (student_user_id) REFERENCES student_users(id);
ALTER TABLE ONLY submissions
    ADD CONSTRAINT submissions_teacher_id_fkey FOREIGN KEY (teacher_id) REFERENCES teachers(id);
ALTER TABLE ONLY submissions
    ADD CONSTRAINT submissions_template_version_id_fkey FOREIGN KEY (template_version_id) REFERENCES template_versions(id);
ALTER TABLE ONLY teachers
    ADD CONSTRAINT teachers_department_id_fkey FOREIGN KEY (department_id) REFERENCES departments(id);
ALTER TABLE ONLY teachers
    ADD CONSTRAINT teachers_institute_id_fkey FOREIGN KEY (institute_id) REFERENCES institutes(id);
ALTER TABLE ONLY template_versions
    ADD CONSTRAINT template_versions_template_id_fkey FOREIGN KEY (template_id) REFERENCES templates(id);
ALTER TABLE ONLY templates
    ADD CONSTRAINT templates_program_id_fkey FOREIGN KEY (program_id) REFERENCES programs(id);
ALTER TABLE ONLY validation_rules
    ADD CONSTRAINT validation_rules_template_version_id_fkey FOREIGN KEY (template_version_id) REFERENCES template_versions(id);
