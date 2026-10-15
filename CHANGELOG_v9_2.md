# v9.2 — Qwen3.5 35B Default

- Changed the default Ollama LLM from Qwen3 14B to `qwen3.5:35b`.
- Updated Super Admin AI profile defaults and seeded AI profiles to `qwen3.5:35b`.
- Updated Docker environment default to `qwen3.5:35b`.
- Added AI-service `.env.example` and automatic `.env` loading with `python-dotenv`.
- Preserved guideline-first parsing, deterministic formatting validation, RAG semantic validation, page-wise issue evidence, and same-type issue consolidation from v9.1.
- Existing database AI profiles created by an older version may still contain `qwen3:14b`; edit those profiles in AI Management to `qwen3.5:35b` or recreate them.
