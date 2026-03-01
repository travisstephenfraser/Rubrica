# Architecture

- **Single file:** `grader.py` contains all routes, business logic, and DB access
- **Database:** `data/exam_grader.db` (SQLite) with tables: `rubrics`, `exams`, `roster`
- **Templates:** `templates/` (Jinja2 + Bootstrap 5, dark mode via `data-bs-theme`)
- **Storage:** `data/uploads/` (exam PDFs), `data/rubrics/` (rubric files)
- **Logging:** `data/grading.log` (logger name: "rubrica")
- **Config:** `.env` for `ANTHROPIC_API_KEY`, loaded via `python-dotenv`

## Data Flow

1. Rubric upload -> `data/rubrics/`, text extracted to `rubrics` table
2. Batch PDF upload -> split into per-exam PDFs -> `review_*.json` (transient)
3. Optional cover OCR -> local Ollama only -> results in review JSON
4. User confirms names -> files renamed to `anon_id.pdf` -> inserted into `exams` table -> review JSON deleted
5. Grading -> 5 parallel workers (`ThreadPoolExecutor`) -> anon pages + rubric sent to Claude -> grade_data stored as JSON in `exams.grade_data`
