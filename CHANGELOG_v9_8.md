# UniSubmit AI v9.8

- Added rule-by-rule semantic guideline validation with explicit PASS / FAIL / WARNING / NOT VERIFIED status.
- Semantic results are grounded by Qdrant retrieval and evaluated by Qwen3.5:35b.
- Every active semantic rule is persisted in the same canonical validation_json used by HTML and PDF reports.
- Only Mandatory semantic FAIL blocks the submission.
- Validation statistics now include deterministic, semantic, and additional restriction rules.
- Full HTML report and downloaded PDF both include the same semantic rule table/details.
- Existing report binding by submission + version + AI run remains intact.
