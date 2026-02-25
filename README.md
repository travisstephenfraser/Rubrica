# ✒ Rubrica

A privacy-first, locally-run web app that grades handwritten student exams using Claude's vision AI. Student identities are fully anonymized before any API call — names never leave your machine.

Built while serving as a Graduate Student Instructor for Microeconomics at UC Berkeley Haas.

---

## How It Works

1. **Upload a rubric** — PDF or DOCX; sent natively to Claude to preserve tables and formatting
2. **Upload exams** — one combined batch PDF; the app splits it automatically by page count
3. **Assign names** — Claude Haiku reads each cover page to pre-fill student name and SID; fuzzy roster matching corrects OCR errors
4. **Grade** — Claude Sonnet reads each answer page as an image and scores it against the rubric, using only an anonymous ID — never a name
5. **Review & export** — grades mapped back to names locally; export to CSV or print per-student reports
6. **Analyze** — grade distribution, band breakdown, and per-question difficulty charts

---

## Features

- **Anonymous grading** — random 8-character IDs replace student names before any API call
- **Batch PDF splitting** — upload one file for the whole class; split happens entirely in memory
- **Cover page vision** — Claude Haiku auto-extracts names and SIDs from cover pages at upload time
- **Roster fuzzy matching** — local name/SID matching with character confusion corrections (O→0, l→1, etc.)
- **Private Mode** — single toggle hides all student names, SIDs, and blurs cover pages across every page simultaneously; designed for presentations and screen-sharing
- **Rubric versioning** — upload and manage multiple rubric versions (A, B, …) for different exam variants
- **Score adjustment** — edit per-question earned points inline after grading; updates save via AJAX
- **Analytics** — grade distribution histogram, letter grade donut chart, per-question average score bar chart, automatic low-performance alerts
- **Student reports** — printable per-student report with score card, progress bar, and question breakdown
- **Export to CSV** — summary (one row per student) and detailed (one column per question) formats
- **Live progress** — background grading thread with a live progress bar; browser stays usable
- **Built-in docs** — full system documentation at `/docs`, printable as PDF

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Flask 3.x |
| AI grading | `claude-sonnet-4-6` (vision) |
| AI name extraction | `claude-haiku-4-5` (cover page vision) |
| PDF handling | pypdf, pypdfium2, pdfplumber |
| Document parsing | python-docx |
| Database | SQLite (local) |
| Frontend | Bootstrap 5 + Chart.js 4 |

---

## Setup

**Requirements:** Python 3.10+, an [Anthropic API key](https://console.anthropic.com/)

```bash
git clone https://github.com/travisstephenfraser/Rubrica.git
cd exam_grader
pip install -r requirements.txt
```

Set your API key:

```bash
# Mac/Linux
export ANTHROPIC_API_KEY=your_key_here

# Windows
set ANTHROPIC_API_KEY=your_key_here
```

Run the app:

```bash
# Windows (required for emoji output)
set PYTHONIOENCODING=utf-8
python grader.py
```

Open `http://localhost:5000` in your browser.

---

## Project Structure

```
Rubrica/
├── grader.py            # Flask app — all routes and business logic
├── requirements.txt     # Dependencies
├── data/
│   ├── exam_grader.db   # SQLite: name↔ID mappings and grade data (local only)
│   ├── uploads/         # Anonymized exam PDFs
│   └── rubrics/         # Uploaded rubric files
└── templates/           # HTML templates (Bootstrap 5)
```

---

## Privacy Model

- The `data/` directory is excluded from version control (`.gitignore`)
- Only anonymous IDs, exam page images, and the rubric are transmitted to the Claude API
- Student names and SIDs never touch the network at any point
- Roster fuzzy matching runs entirely locally — no API call
- **Private Mode** provides an additional presentation layer: names become invisible in the UI while remaining intact in the database and form submissions

---

## License

MIT
