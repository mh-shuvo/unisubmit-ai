# UniSubmit AI v9.7

- Bound PDF downloads to exact submission ID, submission-version ID, and AI validation-run ID.
- Added no-cache headers and run-specific PDF filenames to prevent stale/cross-run report downloads.
- Every active deterministic guideline rule now appears in the report with PASS, FAIL, WARNING, or NOT VERIFIED.
- PASS rows retain expected value, detected summary, guideline source, and optional evidence.
- Unsupported deterministic rules are NOT VERIFIED rather than falsely passed.
- Statistics now count explicit rule results directly and exclude NOT VERIFIED from the compliance-rate denominator.
- HTML and PDF both use the same stored validation_json and AI response for the selected version/run.
