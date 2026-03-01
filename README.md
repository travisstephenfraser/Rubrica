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
- **Parallel grading** — 5 concurrent workers grade exams simultaneously with WAL-mode SQLite for safe concurrent writes; ~5x faster batch processing with the same API cost
- **API resilience** — shared Anthropic client with 5-minute request timeout and 10-second connect timeout; prevents worker deadlock on hung connections
- **Faint pencil handling** — 2x contrast enhancement on exam page images + prompt tuning for faint handwriting
- **Feedback sanitizer** — deterministic post-processing strips AI deliberation language ("wait", "actually", "let me re-read") and em/en dashes from all feedback before saving
- **Input validation** — review session IDs validated against expected format on all routes; error messages sanitized to avoid leaking API internals
- **Upload size limit** — 500 MB cap per batch PDF; oversized files are skipped with a warning
- **JSON retry** — automatically retries once if Claude returns malformed JSON, with persistent error logging to `data/grading.log`
- **Batch resume** — re-running Grade All safely skips already-graded exams; no duplicate API calls
- **Live progress** — background grading thread with a live progress bar that persists across page navigation; dismisses cleanly and reappears only when a new grading session starts
- **Select Ungraded** — one-click button to check all ungraded exams at once, surfacing the Grade Selected toolbar for batch grading without manual selection
- **Dark mode** — session-persisted toggle in the navbar; Bootstrap 5 `data-bs-theme` with softened table/card header backgrounds
- **Boundary re-grading** — exams scoring within +/-1.5% of a letter grade threshold (90/80/70/60) are automatically re-graded in a second independent pass; if the two passes disagree on the letter grade, scores are averaged; full audit trail stored per exam
- **Feedback specificity enforcement** — detects vague feedback ("Good work", "Incorrect", <8 words) and re-prompts via a text-only API call for rubric-anchored, student-specific justification
- **Grading audit system** — `audit_grader.py` re-grades a stratified sample using Claude Opus 4.6 as an independent reference scorer; computes exact match, adjacent agreement, MAE, and bias metrics against ETS thresholds; `generate_audit_report.py` produces a PDF validation report with methodology, benchmarks, and references for faculty review
- **Grade management** — grade individual exams, re-grade, or clear grades per-exam or all at once
- **Results navigation** — Expand/Collapse All details, Scan exam link, and Back to Results from any exam detail page
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
| Database | SQLite (WAL mode, local) |
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
OLLAMA_EXE=C:\path\to\ollama.exe   # optional, only if ollama is not in PATH
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
├── grader.py                # Flask app — all routes and business logic
├── audit_grader.py          # Independent re-grading audit (Opus 4.6 reference scorer)
├── generate_audit_report.py # PDF validation report from audit results
├── generate_rubric.py       # Generates Red exam rubric PDF (ReportLab)
├── generate_rubric_green.py # Generates Green exam rubric PDF (ReportLab)
├── requirements.txt         # Python dependencies
├── build_info.json          # Auto-generated build metadata (commit, branch, date)
├── data/                    # Local only — excluded from version control
│   ├── exam_grader.db       # SQLite: name↔ID mappings and grade data
│   ├── uploads/             # Anonymized exam PDFs
│   ├── rubrics/             # Uploaded rubric files
│   └── audit_results/       # Audit JSON + validation report PDFs
└── templates/               # HTML templates (Bootstrap 5)
```

---

## Validation and Research

Rubrica's grading pipeline and audit methodology are grounded in established psychometric standards and recent AI grading research. A dedicated testing agent (`/testing`) uses Claude Opus 4.6 as an independent reference scorer to validate production grades assigned by Claude Sonnet 4.6.

### Audit Protocol

The testing agent re-grades stratified random samples of exams under identical conditions (same rubric, same anonymized pages, temperature 0.0) and computes inter-rater reliability metrics at the individual question level. Preliminary validation across 7 exams (196 scored items) produced:

- **88% exact score match** (ETS threshold: >= 70%)
- **98% within-1-point agreement** (ETS threshold: >= 95%)
- **0.10 pt mean absolute error** per question
- **+2.01 pt mean bias** (production model slightly generous, favoring students)

The dual-scoring validation model follows the framework recommended by ETS for automated essay scoring systems (Williamson et al., 2012) and mirrors the independent re-scoring methodology used by Gradescope at UC Berkeley (Singh et al., 2017).

### Research-Backed Safeguards

The following production features were implemented based on findings from AES and AI grading literature:

- **Boundary re-grading** addresses the known concentration of inter-rater disagreements at letter grade thresholds. Exams within +/-1.5% of cutoffs (90/80/70/60) are automatically re-graded and averaged on disagreement, with a full audit trail.
- **Feedback specificity enforcement** responds to research showing AI feedback outperforms human feedback on metacognitive dimensions when it cites specific student work (Nazaretsky et al., 2026, *Journal of Computer Assisted Learning*). Vague feedback is detected and refined via a targeted follow-up prompt.
- **2x contrast enhancement** mitigates handwriting quality bias documented in vision LLM grading (arXiv:2601.16724), where faint pencil responses receive systematically lower scores.
- **Deterministic scoring** (temperature 0.0) eliminates random variation between grading runs, a baseline requirement for any assessment system claiming reliability (Fleiss, 1981).

### Recommended Sample Sizes

| Threshold | Purpose | Source |
|---|---|---|
| n >= 30 | Minimum for reliable SD, QWK, and ICC estimation | Fleiss (1981); Central Limit Theorem |
| n >= 50 | Stable kappa/ICC with narrow confidence intervals | Gwet (2014); Sim & Wright (2005) |
| n = 100-200 | ETS operational standard for AES system validation | Williamson et al. (2012) |
| 10% of cohort | Ongoing semester-over-semester monitoring | Gradescope / UC Berkeley practice |

### Key References

- Williamson, D. M., Xi, X., & Breyer, F. J. (2012). A framework for evaluation and use of automated scoring. *Educational Measurement: Issues and Practice*, 31(1), 2-13. [doi:10.1111/j.1745-3992.2011.00223.x](https://doi.org/10.1111/j.1745-3992.2011.00223.x)
- Singh, A., Karayev, S., Gutowski, K., & Abbeel, P. (2017). Gradescope: A fast, flexible, and fair system for scalable assessment of handwritten work. *ACM L@S*. [doi:10.1145/3051457.3051466](https://doi.org/10.1145/3051457.3051466)
- Landis, J. R., & Koch, G. G. (1977). The measurement of observer agreement for categorical data. *Biometrics*, 33(1), 159-174. [doi:10.2307/2529310](https://doi.org/10.2307/2529310)
- Fleiss, J. L. (1981). *Statistical Methods for Rates and Proportions* (2nd ed.). Wiley.
- Nazaretsky, T., et al. (2026). AI feedback outperforms human feedback on metacognitive dimensions. *Journal of Computer Assisted Learning*.
- Shermis, M. D., & Burstein, J. (Eds.). (2013). *Handbook of Automated Essay Evaluation*. Routledge.
- Sim, J., & Wright, C. C. (2005). The kappa statistic in reliability studies. *Physical Therapy*, 85(3), 257-268. [doi:10.1093/ptj/85.3.257](https://doi.org/10.1093/ptj/85.3.257)

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
