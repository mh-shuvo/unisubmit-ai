# UniSubmit AI v9.5

## Rule review and validation statistics
- Parsed guideline rules now keep both AI-suggested severity and admin-selected final severity.
- Final severity options: Mandatory, Warning, Recommendation, Ignore.
- Department/Institute admins can review each deterministic and semantic rule and apply bulk severity changes.
- Any severity edit returns the profile to `Parsed - Review Required`; it must be re-activated before affecting new submissions.
- Review changes are stored in the guideline profile audit history with admin, timestamp, previous severity and new severity.
- Ignored parsed rules are excluded from validation and semantic RAG review.
- Warning/Recommendation mismatches are non-blocking.
- Qwen semantic prompt now explicitly honors final severity; only Mandatory requirements may drive an overall FAIL.

## Statistics
- Each validation run stores a statistics snapshot in `validation_json`.
- HTML and PDF reports show total checked, passed, failed, warnings, semantic review count, compliance rate, and category breakdown.
- Consolidated occurrences do not inflate failed-rule counts.
- Semantic requirements are shown separately and excluded from the numeric rate until per-requirement structured semantic statuses are available.
