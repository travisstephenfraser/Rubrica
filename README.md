# ✒ Rubrica

A privacy-first, locally-run web app that grades handwritten student exams using Claude's vision AI. Student names and SIDs are extracted entirely on-device using a local Ollama vision model — they never leave your machine at any point.

Built while serving as a Graduate Student Instructor for Microeconomics at UC Berkeley Haas.

---

## How It Works

1. **Upload a rubric** — PDF or DOCX; sent natively to Claude to preserve tables and formatting
2. **Upload exams** — up to 3 batch PDFs at once; the app splits all of them by page count and merges into one review session
3. **Assign names** — a local Ollama vision model reads each cover page on-device to pre-fill student name and SID; fuzzy roster matching corrects OCR errors; extraction runs in the background with live progress and an abort button
4. **Grade** — Claude Sonnet reads each answer page as an image and scores it against the rubric, using only an anonymous ID — never a name
5. **Review & export** — grades mapped back to names locally; export to CSV or print per-student reports
6. **Analyze** — grade distribution, band breakdown, and per-question difficulty charts

---

## Features

- **Anonymous grading** — random 8-character IDs replace student names before any API call
- **Multi-batch upload** — upload up to 3 batch PDFs at once, each with its own batch number; all split and merged into one review session in a single pass
- **Cover page vision** — local Ollama vision model (default: `llama3.2-vision`) auto-extracts names and SIDs in the background with live progress; opt-in checkbox to skip on CPU-only machines; abortable mid-run
- **Cover page consistency check** — perceptual hash comparison (8×8 phash) flags any exam whose cover page differs structurally from the majority; runs locally with no API call; flagged exams show a yellow border and ⚠ badge
- **Launch Ollama button** — one-click Ollama startup from the upload page with a GPU-enable reminder tooltip
- **Review tab** — persistent nav tab appears whenever an unsaved review session exists, survives page navigation and app restarts
- **Roster fuzzy matching** — local name/SID matching with character confusion corrections (O→0, l→1, etc.)
- **Private Mode** — single toggle hides all student names, SIDs, and blurs cover pages across every page simultaneously; designed for presentations and screen-sharing
- **Rubric versioning** — upload and manage multiple rubric versions (A, B, …) for different exam variants
- **Score adjustment** — edit per-question earned points inline after grading; updates save via AJAX
- **Analytics** — grade distribution histogram, letter grade donut chart, per-question average score bar chart with per-version filter, automatic low-performance alerts
- **Student reports** — printable per-student report with score card, progress bar, and question breakdown
- **Export to CSV** — summary (one row per student) and detailed (one column per question) formats
- **Parallel grading** — 5 concurrent workers grade exams simultaneously; ~5x faster batch processing with the same API cost
- **Faint pencil handling** — 2x contrast enhancement on exam page images + prompt tuning for faint handwriting
- **Feedback sanitizer** — deterministic post-processing strips AI deliberation language ("wait", "actually", "let me re-read") and em/en dashes from all feedback before saving
- **JSON retry** — automatically retries once if Claude returns malformed JSON, with persistent error logging to `data/grading.log`
- **Batch resume** — re-running Grade All safely skips already-graded exams; no duplicate API calls
- **Live progress** — background grading thread with a live progress bar that persists across page navigation; dismisses cleanly and reappears only when a new grading session starts
- **Select Ungraded** — one-click button to check all ungraded exams at once, surfacing the Grade Selected toolbar for batch grading without manual selection
- **Grade management** — grade individual exams, re-grade, or clear grades per-exam or all at once
- **Results navigation** — Expand/Collapse All details, Review exam link, and Back to Results from any exam detail page
- **Built-in docs** — full system documentation at `/docs`, printable as PDF

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Flask 3.x |
| AI grading | `claude-sonnet-4-6` (vision, via Anthropic API) |
| AI name extraction | `llama3.2-vision` via Ollama (local, on-device) |
| PDF handling | pypdf, pdfplumber |
| Document parsing | python-docx |
| Environment | python-dotenv |
| Database | SQLite (local) |
| Frontend | Bootstrap 5 + Chart.js 4 |

---

## Setup

**Requirements:** Python 3.10+, an [Anthropic API key](https://console.anthropic.com/), [Ollama](https://ollama.com) (for local cover page OCR)

```bash
git clone https://github.com/travisstephenfraser/Rubrica.git
cd Rubrica
pip install -r requirements.txt
```

Install the local vision model (one-time, ~6 GB):

```bash
ollama pull llama3.2-vision
```

Create a `.env` file in the project root with your API key:

```
ANTHROPIC_API_KEY=your_key_here
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
├── grader.py              # Flask app — all routes and business logic
├── requirements.txt       # Python dependencies
├── build_info.json        # Auto-generated build metadata (commit, branch, date)
├── generate_rubric.py     # Generates Red exam rubric PDF (ReportLab)
├── generate_rubric_green.py # Generates Green exam rubric PDF (ReportLab)
├── data/                  # Local only — excluded from version control
│   ├── exam_grader.db     # SQLite: name↔ID mappings and grade data
│   ├── uploads/           # Anonymized exam PDFs
│   └── rubrics/           # Uploaded rubric files
└── templates/             # HTML templates (Bootstrap 5)
```

---

## Privacy Model

- The `data/` directory is excluded from version control (`.gitignore`)
- Cover page OCR runs locally via Ollama — student names and SIDs never leave the machine
- Only anonymous IDs, exam page images, and the rubric are transmitted to the Claude API
- Roster fuzzy matching runs entirely locally — no API call
- **Private Mode** provides an additional presentation layer: names become invisible in the UI while remaining intact in the database and form submissions

---

## License

MIT
