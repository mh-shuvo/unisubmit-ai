"""
Standalone test: run process_job() against the real DB job #1
(submission_1_v2_*.pdf) and print a full pass/fail breakdown.

Run from the unisubmit_ai/ root:
    python test_job.py
"""
import asyncio
import json
import sys
import traceback
from pathlib import Path

# Make the app package importable from project root.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal
from app.models import AIJob

# Job to run: defaults to #2 (submission_1_v2_*.pdf).
JOB_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 2


async def main():
    db = SessionLocal()
    try:
        job = db.get(AIJob, JOB_ID)
        if not job:
            print(f"❌  AIJob #{JOB_ID} not found in the database.")
            return

        sv = job.submission_version
        sub = sv.submission
        tv = sub.template_version

        print("=" * 60)
        print(f"Job #{JOB_ID}  —  status: {job.status}  attempts: {job.attempt_count}")
        print(f"Submission #{sub.id}: {sub.title}")
        print(f"PDF path:  {sv.pdf_path}")
        print(f"Template version:  v{tv.version}  ({tv.status})")
        print(f"Profile status:    {tv.format_profile_status}")
        print(f"RAG status:        {tv.rag_status}")
        print(f"AI profile linked: {'Yes → ' + sub.ai_profile.service_url if sub.ai_profile else 'None'}")
        print("=" * 60)

        # Reset job so the worker can pick it up fresh — or run inline.
        print("\n▶  Running process_job() inline …\n")

        from app.main import process_job  # import after sys.path set

        try:
            # Reset status so we can re-run without touching the worker.
            job.status = "PROCESSING"
            job.attempt_count = (job.attempt_count or 0) + 1
            db.commit()

            await process_job(db, job)

            job.status = "COMPLETED"
            db.commit()
            print("\n✅  process_job() completed without exception.\n")

        except Exception as exc:
            job.error_message = str(exc)
            job.status = "FAILED"
            db.commit()
            print(f"\n❌  process_job() raised an exception:\n")
            traceback.print_exc()
            print(f"\nStored error_message: {exc}\n")
            return

        # ── Print results summary ──────────────────────────────────────
        db.refresh(sv)
        print(f"Final status : {sv.status}")
        print(f"Report path  : {sv.report_path or '(none)'}\n")

        try:
            results = json.loads(sv.validation_json or "[]")
        except Exception:
            results = []

        categories: dict = {}
        for r in results:
            if r.get("category") == "Validation Statistics":
                continue
            cat = r.get("category", "Unknown")
            categories.setdefault(cat, []).append(r)

        for cat, rows in categories.items():
            print(f"── {cat} ({len(rows)} rules) ──")
            for r in rows:
                icon = {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️ ", "NOT VERIFIED": "🔲"}.get(r.get("status", ""), "❔")
                print(f"  {icon} [{r.get('status','?'):12s}] {r.get('name','?')}")
                if r.get("status") in ("FAIL", "WARNING"):
                    print(f"            Expected : {r.get('expected','')}")
                    print(f"            Detected : {r.get('detected','')}")
            print()

        ai_text = sv.ai_suggestions or ""
        if ai_text and ai_text != "AI validation unavailable.":
            print("── Qwen AI narrative (first 600 chars) ──")
            print(ai_text[:600])
            print("…" if len(ai_text) > 600 else "")
        else:
            print(f"── Qwen AI narrative: {ai_text or '(empty)'}")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
