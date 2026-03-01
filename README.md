# ✒ Rubrica

A privacy-first, locally-run web app that grades handwritten student exams using Claude's vision AI. Student names and SIDs are extracted entirely on-device using a local Ollama vision model — they never leave your machine at any point.

Built while serving as a Graduate Student Instructor for Microeconomics at UC Berkeley Haas.

---

## How It Works

1. **Upload a rubric** — PDF or DOCX per exam version; sent natively to Claude to preserve tables and formatting
2. **Upload a roster** *(optional)* — CSV of student names and SIDs for fuzzy matching during name assignment
3. **Upload exams** — up to 3 batch PDFs at once; the app splits all of them by page count and merges into one review session
4. **Assign names** — a local Ollama vision model reads each cover page on-device to pre-fill student name and SID; fuzzy roster matching corrects OCR errors; extraction runs in the background with live progress and an abort button
5. **Grade** — Claude Sonnet reads each answer page (with 2x contrast enhancement) and scores against the rubric using only an anonymous ID; boundary scores are automatically re-graded in a second pass; vague feedback is refined to cite specific rubric criteria
6. **Review & adjust** — inline score editing per question, per-student printable reports, summary and detailed CSV exports; all grades mapped back to names locally
7. **Analyze** — grade distribution histogram, letter grade breakdown, per-question difficulty charts with version filtering
8. **Audit** *(optional)* — re-grade a sample with Claude Opus 4.6 as an independent reference scorer; generate a PDF validation report with ETS benchmark comparisons

---

## Features

### Upload and Review

- **Multi-batch upload** — up to 3 batch PDFs at once; split by page count and merged into one review session
- **Roster upload** — CSV import of student names and SIDs for fuzzy matching during name assignment
- **Cover page vision** — local Ollama vision model auto-extracts names and SIDs in the background; abortable with live progress
- **Cover page consistency check** — perceptual hash flags exams whose cover page differs from the majority
- **Roster fuzzy matching** — local name/SID matching with character confusion corrections (O→0, l→1, etc.)
- **Scan modal** — page-by-page exam viewer for verifying all pages are attached before confirming
- **Persistent review tab** — nav tab appears whenever an unsaved review session exists; survives page navigation and restarts
- **Launch Ollama button** — one-click startup from the upload page with GPU-enable reminder

### Grading Accuracy

- **Boundary re-grading** — scores within +/-1.5% of letter grade thresholds (90/80/70/60) trigger a second independent pass; disagreements are averaged with full audit trail
- **Feedback specificity enforcement** — vague feedback ("Good work", "Incorrect", <8 words) is refined via a follow-up API call requiring rubric-anchored justification
- **Faint pencil handling** — 2x contrast enhancement on exam images before grading
- **Deterministic scoring** — temperature 0.0 on all API calls
- **Feedback sanitizer** — strips AI deliberation language and em/en dashes from all student-facing feedback
- **Score recalculation** — sub-part consolidation and normalization run in Python after API response
- **JSON retry** — automatic retry on malformed Claude responses with persistent error logging

### Grading Audit

- **Independent re-grading** — `audit_grader.py` re-grades stratified samples using Claude Opus 4.6 as a reference scorer
- **ETS benchmark metrics** — exact match, adjacent agreement, MAE, bias, and letter grade agreement
- **PDF validation report** — `generate_audit_report.py` produces a faculty-ready report with methodology, benchmarks, sample size recommendations, and references

### Privacy

- **Anonymous grading** — random 8-character IDs replace student names before any API call
- **Cover page exclusion** — page 0 is never sent to Claude; OCR runs exclusively through local Ollama
- **Private Mode** — single toggle hides all names, SIDs, and blurs cover pages across every page; designed for screen-sharing
- **Local-only storage** — all data (database, PDFs, grades) stays on the instructor's machine

### Results and Export

- **Inline score adjustment** — edit per-question earned points after grading; saves via AJAX with live preview
- **Grade management** — grade, re-grade, or clear grades per-exam or in bulk; Select Ungraded for batch operations
- **Student reports** — printable per-student report with score card, progress bar, and question breakdown
- **CSV export** — summary (one row per student) and detailed (one row per question) formats
- **Analytics** — grade distribution histogram, letter grade donut, per-question difficulty chart with version filtering

### Infrastructure

- **Parallel grading** — 5 concurrent workers with WAL-mode SQLite for safe concurrent writes
- **Live progress** — background grading with a progress bar that persists across page navigation
- **API resilience** — 5-minute request timeout and 10-second connect timeout; prevents worker deadlock
- **Rubric versioning** — multiple rubric versions (A, B, ...) for different exam variants
- **Batch resume** — Grade All skips already-graded exams; no duplicate API calls
- **Input validation** — session IDs validated on all routes; error messages sanitized
- **Upload size limit** — 500 MB cap per batch PDF
- **Dark mode** — session-persisted toggle; Bootstrap 5 `data-bs-theme`
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
