# Privacy

Student data protection is non-negotiable. Every change must preserve these invariants:

- **Names and SIDs never leave the machine.** The grading prompt receives only the `anon_id`, answer page images (pages 1+), and rubric. Never add student_name, student_sid, or any other PII to API calls.
- **Cover pages (page 0) are never sent to Claude.** Cover OCR runs exclusively through local Ollama. Do not change this boundary.
- **Roster matching is local-only.** Fuzzy matching runs in pure Python. No roster data goes to any API.
- **Private Mode hides names in the UI** via CSS opacity and template conditionals. If adding new views that display student info, respect `session["private_mode"]`.
- **anon_id generation uses `secrets.token_urlsafe`** with uniqueness verification. Do not downgrade to predictable IDs.
- When in doubt about whether something constitutes PII leakage, treat it as a violation and don't do it.
- **Never push personal information or API keys to any remote repository.** This includes student data, `.env` files, API keys, credentials, and any other secrets. Verify staged files before committing.
