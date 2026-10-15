# UniSubmit AI v6 highlights

- Rebuilt role boundaries for Super Admin vs Department/Institute Admin
- Student account is Department/Institute scoped, not Program scoped
- Student sees eligible Programs only when creating a submission
- Supervisor Directory is Department/Institute scoped, not Program scoped
- Program and Submission Type determine guideline/template configuration
- Official Guideline PDF is the primary AI validation source
- Optional Additional Rules PDF is supported
- Optional structured deterministic restrictions are supported
- Guideline and additional-rules documents can be indexed into the same RAG collection
- Persistent sequential AI queue worker with concurrency 1 and retry logic
- Student private dashboard with tabular submission tracking
- Mobile-responsive tables/cards and colorful modern UI
- Protected Admin login with role-scoped navigation
- Isolated AI service retained: Qwen + BGE-M3 + Qdrant + RAG
