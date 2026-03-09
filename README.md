# Rubrica

A privacy-first, locally-run web app that grades handwritten student exams using Claude's vision AI. Student names and SIDs are extracted entirely on-device using a local Ollama vision model -- they never leave your machine.

Built while serving as a Graduate Student Instructor for Microeconomics at UC Berkeley Haas.

---

## How It Works

1. **Upload a rubric** -- PDF or DOCX per exam version; sent natively to Claude to preserve tables and formatting
2. **Enhance the rubric** *(optional)* -- a built-in rubric builder refines your rubric with structured partial credit tiers and common error patterns, improving grading consistency
3. **Upload a roster** *(optional)* -- CSV of student names and SIDs for fuzzy matching during name assignment
4. **Upload exams** -- up to 3 batch PDFs at once; split by page count and merged into one review session
5. **Assign names** -- a local Ollama vision model reads each cover page on-device to pre-fill student name and SID; fuzzy roster matching corrects OCR errors; runs in the background with live progress and abort
6. **Grade** -- Claude Sonnet reads each answer page (2x contrast enhancement) and scores against the rubric using only an anonymous ID; boundary scores are automatically re-graded; vague feedback is refined to cite specific rubric criteria
7. **Review and adjust** -- inline score editing per question, per-student printable reports with exam scans, summary and detailed CSV exports; all grades mapped back to names locally
8. **Analyze** -- grade distribution histogram, letter grade breakdown, per-question difficulty charts with version filtering
9. **Audit** *(optional)* -- re-grade a sample with an independent cross-family model; generate a PDF validation report with ETS benchmark comparisons
10. **Distribute** *(optional)* -- email per-student PDF reports via Gmail OAuth2

---

## Features

### Upload and Review

- **Multi-batch upload** -- up to 3 batch PDFs at once; split by page count and merged into one review session
- **Roster upload** -- CSV import of student names and SIDs for fuzzy matching during name assignment
- **Cover page vision** -- local Ollama vision model auto-extracts names and SIDs in the background; abortable with live progress
- **Cover page consistency check** -- perceptual hash flags exams whose cover page differs from the majority
- **Roster fuzzy matching** -- local name/SID matching with character confusion corrections (O to 0, l to 1, etc.)
- **Auto roster matching** -- after OCR completes, exams are automatically matched against the uploaded roster
- **Scan modal** -- page-by-page exam viewer with keyboard navigation for verifying pages before confirming
- **Persistent review tab** -- nav tab appears whenever an unsaved review session exists; survives page navigation and restarts
- **Launch Ollama button** -- one-click startup from the upload page with GPU-enable reminder

### Grading Accuracy

- **Boundary re-grading** -- scores within +/-1.5% of letter grade thresholds (90/80/70/60) trigger a second independent pass; disagreements are averaged with full audit trail
- **Feedback specificity enforcement** -- vague feedback ("Good work", "Incorrect", <8 words) is refined via a follow-up API call requiring rubric-anchored justification
- **Faint pencil handling** -- 2x contrast enhancement on exam images before grading
- **Deterministic scoring** -- temperature 0.0 on all grading API calls
- **Feedback sanitizer** -- strips AI deliberation language, inline point tallies, scoring verdicts, and em/en dashes from all student-facing feedback
- **MC double-read verification** -- multiple choice questions scored zero are re-read in a focused verification pass to catch letter-reading errors
- **Crossed-out answer handling** -- grading prompt instructs Claude to ignore crossed-out work and only score cleanly marked responses
- **Shared scoring module** -- `scoring.py` is the single source of truth for sub-part consolidation, feedback sanitization, score recalculation, and letter grade assignment; used by both production and audit pipelines
- **No intermediate rounding** -- all per-question math stays full-precision; round only at the final total
- **JSON retry** -- automatic retry on malformed Claude responses with persistent error logging

### Rubric Enhancement

- **Rubric builder** -- refines uploaded rubrics with structured partial credit tiers, expected answers, and common error patterns; learns from grading feedback over time to improve tier accuracy
- **Multi-version support** -- enhanced rubrics can be mapped across exam versions without building separately

### Grading Audit

- **Independent re-grading** -- re-grades stratified samples using a cross-family model as reference scorer
- **ETS benchmark metrics** -- ICC, QWK, exact match, adjacent agreement, MAE, and bias; stratified by rubric version
- **Quality dashboard** -- audit run history with cumulative metrics and health suggestions
- **PDF validation report** -- faculty-ready document with methodology, benchmarks, sample size recommendations, and references
- **Blind validation analysis** -- independent testing agent analyzes audit data with no knowledge of pipeline internals

### Email Distribution

- **Per-student PDF reports** -- individual score reports with question breakdown and feedback
- **Gmail OAuth2** -- send reports directly to students via authenticated Gmail

### Privacy

- **Anonymous grading** -- random 8-character IDs replace student names before any API call
- **Cover page exclusion** -- page 0 is never sent to Claude; OCR runs exclusively through local Ollama
- **Private Mode** -- single toggle hides all names, SIDs, and blurs cover pages across every page; designed for screen-sharing
- **Local-only storage** -- all data (database, PDFs, grades) stays on the instructor's machine
- **Roster matching** -- fuzzy matching runs entirely in Python; no roster data sent to any API

### Results and Export

- **Inline score adjustment** -- edit per-question earned points and feedback after grading; saves via AJAX with live preview
- **Reviewed checkmark** -- per-exam toggle to track review progress; persists across sessions with row highlighting
- **Grade management** -- grade, re-grade, or clear grades per-exam or in bulk; Select Ungraded for batch operations
- **Student reports** -- printable per-student report with score card, exam scans, progress bar, and question breakdown
- **CSV export** -- summary (one row per student) and detailed (one row per question) formats
- **Analytics** -- grade distribution histogram, letter grade donut, per-question difficulty chart with version filtering

### Infrastructure

- **Parallel grading** -- 5 concurrent workers with WAL-mode SQLite for safe concurrent writes
- **Live progress** -- background grading with a progress bar that persists across page navigation
- **API resilience** -- 5-minute request timeout and 10-second connect timeout; prevents worker deadlock
- **Rubric versioning** -- multiple rubric versions (A, B, ...) for different exam variants
- **Batch resume** -- Grade All skips already-graded exams; no duplicate API calls
- **Input validation** -- session IDs validated on all routes; error messages sanitized
- **Upload size limit** -- 50 MB cap per batch PDF with client-side validation and inline hint
- **Dark mode** -- session-persisted toggle; Bootstrap 5 `data-bs-theme`
- **Built-in docs** -- full system documentation at `/docs`, printable as PDF

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Flask 3.x |
| AI grading | `claude-sonnet-4-6` (vision, via Anthropic API) |
| AI name extraction | `llama3.2-vision` via Ollama (local, on-device) |
| PDF handling | pypdf, pdfplumber, pypdfium2 |
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
python grader.py
```

Open `http://localhost:5000` in your browser.

---

## Project Structure

```
Rubrica/
├── grader.py                # Flask app -- all routes and business logic
├── scoring.py               # Shared scoring pipeline (consolidation, sanitization, finalization)
├── requirements.txt         # Python dependencies
├── build_info.json          # Auto-generated build metadata (commit, branch, date)
├── data/                    # Local only -- excluded from version control
│   ├── exam_grader.db       # SQLite database
│   ├── uploads/             # Anonymized exam PDFs
│   ├── rubrics/             # Uploaded rubric files
│   └── audit_results/       # Audit JSON + validation report PDFs
└── templates/               # HTML templates (Bootstrap 5)
```

> Some modules (audit pipeline, rubric builder, insights dashboard, email distribution, report generators) are under active development and not yet included in the open-source distribution. The core grading pipeline is fully functional without them. These modules will be open-sourced once sufficiently tested across different subjects, rubric formats, and class sizes.

---

## Validation and Research

Rubrica's grading pipeline and audit methodology are grounded in established psychometric standards and recent AI grading research. The audit system re-grades stratified random samples using a cross-family model as an independent reference scorer.

### Audit Protocol

The audit re-grades under identical conditions (same rubric, same anonymized pages, same system prompt, temperature 0.0) and computes inter-rater reliability metrics at the individual question level. Both production and audit pipelines share a single scoring module (`scoring.py`) for sub-part consolidation, feedback sanitization, score recalculation, hard cap enforcement, and letter grade assignment; the only variable is the grading model. Production grades are snapshotted as `raw_scores` after the shared pipeline but before production-only safeguards (feedback specificity, contradiction resolution, boundary re-grade), so the audit compares raw model output to raw model output per the Williamson et al. (2012) framework.

Validation across 30 exams (15 per version; 840 scored items):

| Metric | Result | ETS Threshold |
|---|---|---|
| ICC (absolute agreement) | **0.96** (excellent) | >= 0.75 good, >= 0.90 excellent |
| Quadratic Weighted Kappa | **0.91** (excellent) | >= 0.70 |
| Exact score match | **91%** | >= 70% |
| Within-1-point agreement | **96%** | >= 95% |
| Mean absolute error | **0.14 pt** per question | < 1.0 pt |
| Mean bias | **~0.00 pt** | Near 0 |

All metrics are stratified by rubric version. The dual-scoring validation model follows the framework recommended by ETS for automated essay scoring systems (Williamson et al., 2012) and mirrors the independent re-scoring methodology used by Gradescope at UC Berkeley (Singh et al., 2017).

### Research-Backed Safeguards

- **Boundary re-grading** addresses the known concentration of inter-rater disagreements at letter grade thresholds (Williamson et al., 2012). Exams within +/-1.5% of cutoffs are automatically re-graded and averaged on disagreement.
- **Feedback specificity enforcement** responds to research showing AI feedback outperforms human feedback on metacognitive dimensions when it cites specific student work (Nazaretsky et al., 2026, *Journal of Computer Assisted Learning*).
- **2x contrast enhancement** mitigates handwriting quality bias documented in vision LLM grading (arXiv:2601.16724), where faint pencil responses receive systematically lower scores.
- **Deterministic scoring** (temperature 0.0) eliminates random variation between grading runs, a baseline requirement for assessment reliability (Fleiss, 1981).

### Key References

- Williamson, D. M., Xi, X., & Breyer, F. J. (2012). A framework for evaluation and use of automated scoring. *Educational Measurement: Issues and Practice*, 31(1), 2-13.
- Singh, A., Karayev, S., Gutowski, K., & Abbeel, P. (2017). Gradescope: A fast, flexible, and fair system for scalable assessment of handwritten work. *ACM L@S*.
- Fleiss, J. L. (1981). *Statistical Methods for Rates and Proportions* (2nd ed.). Wiley.
- Nazaretsky, T., et al. (2026). AI feedback outperforms human feedback on metacognitive dimensions. *Journal of Computer Assisted Learning*.
- Landis, J. R., & Koch, G. G. (1977). The measurement of observer agreement for categorical data. *Biometrics*, 33(1), 159-174.

---

## Privacy Model

- The `data/` directory is excluded from version control (`.gitignore`)
- Cover page OCR runs locally via Ollama -- student names and SIDs never leave the machine
- Only anonymous IDs, exam page images, and the rubric are transmitted to the Claude API
- Roster fuzzy matching runs entirely locally -- no API call
- **Private Mode** hides all names, SIDs, and blurs cover pages across every page; designed for screen-sharing

---

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

Free to use, modify, and distribute for any purpose, including academic and personal use. If you deploy a modified version as a network service, the AGPL requires you to make your source code available. For commercial licensing inquiries, contact Travis Fraser.
