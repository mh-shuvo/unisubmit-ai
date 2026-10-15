# UniSubmit AI v9.9

## Authenticated workspace navigation fix

- Public navigation is hidden after Student, Department/Institute Admin, or Super Admin login.
- Student authenticated top bar now shows Dashboard, New Submission, Profile, and Logout only.
- Admin authenticated top bar now shows Dashboard and Logout only; the existing role-specific sidebar remains the primary admin navigation.
- Student submission pages include an explicit Back to dashboard link.
- Validation report includes both Dashboard and Back to submission navigation.
- Admin logout now clears the session and redirects to the public home page, matching Student logout behavior.
- Public footer is hidden inside authenticated workspaces to avoid mixing public-site navigation with private application areas.
- All v9.8 validation, semantic-rule, report binding, statistics, rule review, RAG, and PDF-report functionality is preserved.
