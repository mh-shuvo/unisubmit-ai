"""Time one validation, stage by stage, without touching the queue.

    python tools/measure_validation.py --template-version 1 --pdf path/to/thesis.pdf

Reads the template version from DATABASE_URL (read only), runs the
deterministic checks in this process and the semantic check through the AI
service at AI_SERVICE_URL, exactly as the worker would, and prints the time of
each stage as JSON. It writes nothing to the database, the queue or storage,
so it can run beside a live stack. Ollama is shared, though: do not run it
while the worker is validating (LIM-003).

Restructuring plan task P0.11: the measured time sets RAG_VALIDATE_TIMEOUT.
"""

import argparse
import asyncio
import json
import platform
import sys
import time
from pathlib import Path

from unisubmit.ai_service_client import health, rag_validate
from unisubmit.config import settings
from unisubmit.db import SessionLocal
from unisubmit.format_validation import validate_all_profile_rules
from unisubmit.main import _normalize_profile_severities, semantic_validation_payload
from unisubmit.models import TemplateVersion
from unisubmit.pdf_utils import inspect_pdf
from unisubmit.validators import run_rules


def timed(fn, *args):
    started = time.perf_counter()
    result = fn(*args)
    return result, round(time.perf_counter() - started, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template-version", type=int, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--model", help="override the AI profile's base model")
    args = parser.parse_args()

    with SessionLocal() as db:
        tv = db.get(TemplateVersion, args.template_version)
        if tv is None:
            print(f"Template version {args.template_version} not found.", file=sys.stderr)
            return 1
        profile = next(
            (
                p
                for p in tv.template.program.ai_profiles
                if p.submission_type == tv.template.submission_type
            ),
            None,
        )
        model = args.model or (profile.base_model if profile else settings.ollama_model)
        fmt_profile = _normalize_profile_severities(json.loads(tv.format_profile_json or "{}"))

        info, inspect_s = timed(inspect_pdf, str(args.pdf))
        started = time.perf_counter()
        run_rules(tv.rules, info)
        format_results = (
            validate_all_profile_rules(fmt_profile, info)
            if tv.format_profile_status == "Active"
            else []
        )
        deterministic_s = round(time.perf_counter() - started, 2)
        payload = semantic_validation_payload(
            tv.template.program, tv, fmt_profile, info["text"], model
        )

    service = health(settings.ai_service_url)
    started = time.perf_counter()
    ai = asyncio.run(rag_validate(settings.ai_service_url, payload))
    semantic_s = round(time.perf_counter() - started, 2)

    print(
        json.dumps(
            {
                "machine": f"{platform.system()} {platform.machine()}",
                "model": model,
                "embedding_device": ((service.get("data") or {}).get("embedding") or {}).get(
                    "device"
                ),
                "pdf": args.pdf.name,
                "pages": info.get("page_count"),
                "deterministic_rules": len(format_results) + len(tv.rules),
                "semantic_requirements": len(payload["explicit_requirements"]),
                "semantic_batches": ai.get("semantic_batches"),
                "semantic_batches_failed": ai.get("semantic_batches_failed"),
                "seconds": {
                    "inspect_pdf": inspect_s,
                    "deterministic": deterministic_s,
                    "semantic_ai": semantic_s,
                    "total": round(inspect_s + deterministic_s + semantic_s, 2),
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
