"""
Exam Anonymizer & Grader
========================
Privacy-first local app: student names NEVER sent to Claude.
Only anonymous IDs + exam text + rubric are transmitted.
"""

import base64
import concurrent.futures
import csv
import difflib
import io
import json
import logging
import os
import secrets
import sqlite3
import statistics as _stats
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic
import docx
import ollama
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageEnhance
from pypdf import PdfReader, PdfWriter
from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
RUBRIC_DIR = DATA_DIR / "rubrics"
DB_PATH = DATA_DIR / "exam_grader.db"
ALLOWED_EXT = {".pdf"}

# Local vision model used for cover-page OCR (runs via Ollama — no data leaves machine).
# Swap for any Ollama vision model: "llava:13b", "minicpm-v", etc.
OLLAMA_VISION_MODEL = "llama3.2-vision"

# Claude model used for grading. Update here when upgrading models.
CLAUDE_MODEL = "claude-sonnet-4-6"

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
RUBRIC_DIR.mkdir(exist_ok=True)

# Persistent file logger — survives app restarts
_log = logging.getLogger("rubrica")
_log.setLevel(logging.INFO)
_log_handler = logging.FileHandler(DATA_DIR / "grading.log", encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
_log.addHandler(_log_handler)

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)


@app.context_processor
def inject_private_mode():
    return {"private_mode": session.get("private_mode", False)}


@app.context_processor
def inject_active_review():
    review_files = sorted(DATA_DIR.glob("review_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not review_files:
        return {"active_review_id": None, "active_review_ocr_running": False}
    review_path = review_files[0]
    review_id   = review_path.stem[len("review_"):]
    with _ocr_jobs_lock:
        ocr_running = _ocr_jobs.get(review_id, {}).get("running", False)
    return {"active_review_id": review_id, "active_review_ocr_running": ocr_running}


@app.route("/toggle-private-mode", methods=["POST"])
def toggle_private_mode():
    session["private_mode"] = not session.get("private_mode", False)
    return redirect(request.form.get("next") or request.referrer or "/")

# ---------------------------------------------------------------------------
# Background grading job state
# ---------------------------------------------------------------------------
_grade_job: dict   = {"running": False, "total": 0, "done": 0, "failed": 0, "errors": []}
_grade_lock        = threading.Lock()
GRADE_WORKERS      = 5  # concurrent grading threads

# Background OCR job state (keyed by review_id)
_ocr_jobs: dict    = {}
_ocr_jobs_lock     = threading.Lock()

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS rubrics (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            version    TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS exams (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            anon_id      TEXT UNIQUE NOT NULL,
            student_name TEXT NOT NULL,
            version      TEXT NOT NULL,
            batch        INTEGER NOT NULL,
            file_path    TEXT NOT NULL,
            uploaded_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ocr_text     TEXT,
            ocr_at       TIMESTAMP,
            graded_at    TIMESTAMP,
            grade_data   TEXT
        );

        CREATE TABLE IF NOT EXISTS roster (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name  TEXT NOT NULL,
            sid        TEXT
        );
    """)
    # Migrate existing DB — add columns if not present
    migrations = [
        ("exams",   "ocr_text",         "TEXT"),
        ("exams",   "ocr_at",           "TIMESTAMP"),
        ("rubrics", "rubric_file_path", "TEXT"),
        ("exams",   "student_sid",      "TEXT"),
    ]
    for table, col, typedef in migrations:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass  # column already exists
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def generate_anon_id():
    """8-char URL-safe random ID that won't be guessable or sequential."""
    while True:
        token = secrets.token_urlsafe(6)[:8].upper()
        db = get_db()
        exists = db.execute("SELECT 1 FROM exams WHERE anon_id=?", (token,)).fetchone()
        if not exists:
            return token


def extract_pdf_text(filepath: str) -> str:
    """Extract all text from a PDF. Returns empty string on failure."""
    try:
        with pdfplumber.open(filepath) as pdf:
            return "\n".join(
                page.extract_text() or "" for page in pdf.pages
            ).strip()
    except Exception as e:
        return f"[PDF extraction error: {e}]"


def extract_docx_text(filepath: str) -> str:
    """Extract all text from a .docx file, preserving paragraph breaks."""
    try:
        doc = docx.Document(filepath)
        return "\n".join(para.text for para in doc.paragraphs).strip()
    except Exception as e:
        return f"[DOCX extraction error: {e}]"


def extract_rubric_file(filepath: str, filename: str) -> str:
    """Extract rubric text from a PDF or DOCX file."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return extract_pdf_text(filepath)
    elif ext in (".docx", ".doc"):
        return extract_docx_text(filepath)
    return ""


def get_rubric(version: str):
    db = get_db()
    return db.execute(
        "SELECT * FROM rubrics WHERE version=? ORDER BY id DESC LIMIT 1",
        (version,)
    ).fetchone()


def letter_grade(pct: float) -> str:
    if pct >= 90: return "A"
    if pct >= 80: return "B"
    if pct >= 70: return "C"
    if pct >= 60: return "D"
    return "F"


# ---------------------------------------------------------------------------
# Roster fuzzy matching — 100% local, zero network calls
# ---------------------------------------------------------------------------

def match_against_roster(extracted_name: str, extracted_sid: str, roster_entries: list) -> tuple:
    """
    Pure local fuzzy match against roster entries.
    Normalises common OCR digit/letter confusions in SID (O→0, l→1, I→1, S→5).
    Returns (best_entry_dict, score 0-1) or (None, 0.0).
    PRIVACY: roster data never leaves this function — no API calls made here.
    """
    if not roster_entries:
        return None, 0.0

    def norm_sid(s):
        return s.upper().replace('O', '0').replace('l', '1').replace('I', '1').replace('S', '5')

    SM = difflib.SequenceMatcher
    name_clean = (extracted_name or "").lower().strip()
    sid_clean  = (extracted_sid  or "").strip()
    best_score, best_entry = 0.0, None

    for entry in roster_entries:
        fl = f"{entry['first_name']} {entry['last_name']}".lower()
        lf = f"{entry['last_name']} {entry['first_name']}".lower()
        name_score = max(SM(None, name_clean, fl).ratio(),
                         SM(None, name_clean, lf).ratio())

        sid_score = 0.0
        if sid_clean and entry['sid']:
            a, b = norm_sid(sid_clean), norm_sid(entry['sid'])
            sid_score = 1.0 if a == b else SM(None, a, b).ratio()

        has_both = bool(name_clean) and bool(sid_clean and entry['sid'])
        if has_both:
            score = 0.55 * name_score + 0.45 * sid_score
        elif name_clean:
            score = name_score
        else:
            score = sid_score

        if score > best_score:
            best_score, best_entry = score, entry

    return best_entry, round(best_score, 3)


# ---------------------------------------------------------------------------
# OCR — render PDF pages as images and transcribe with Claude vision
# ---------------------------------------------------------------------------

_pdfium_lock = threading.Lock()

def _render_page_png(pdf_path: str, page_num: int, scale: float = 2.0) -> bytes:
    with _pdfium_lock:
        doc = pdfium.PdfDocument(pdf_path)
        try:
            page    = doc[page_num]
            bitmap  = page.render(scale=scale)
            pil_img = bitmap.to_pil()
            buf     = io.BytesIO()
            pil_img.save(buf, format="PNG")
            result  = buf.getvalue()
            # Explicitly release PDFium sub-objects before closing the document.
            # Python's GC may not free them promptly, which corrupts PDFium's
            # internal state across multiple calls (particularly on Windows).
            del pil_img, bitmap, page
            return result
        finally:
            doc.close()



def _cover_phash(pdf_path: str, hash_size: int = 8) -> int | None:
    """Render cover page at low resolution and return a perceptual hash integer."""
    try:
        img_bytes = _render_page_png(pdf_path, 0, scale=0.3)
        img = Image.open(io.BytesIO(img_bytes)).convert("L").resize(
            (hash_size, hash_size), Image.LANCZOS
        )
        pixels = list(img.getdata())
        avg    = sum(pixels) / len(pixels)
        return sum(1 << i for i, p in enumerate(pixels) if p > avg)
    except Exception:
        return None


def check_cover_consistency(exams: list) -> None:
    """
    Compare cover page perceptual hashes across all exams and flag outliers in-place.
    Adds 'cover_flag': True to any exam whose cover differs significantly from the
    majority. Runs entirely locally — no API calls. Modifies exams in-place.
    """
    if len(exams) < 2:
        for exam in exams:
            exam["cover_flag"] = False
        return

    THRESHOLD = 15  # Hamming distance out of 64 bits
    hashes = [_cover_phash(exam["file_path"]) for exam in exams]
    valid  = [(i, h) for i, h in enumerate(hashes) if h is not None]

    for i, exam in enumerate(exams):
        if hashes[i] is None:
            exam["cover_flag"] = False
            continue
        distances = [bin(hashes[i] ^ h).count("1") for j, h in valid if j != i]
        avg_dist  = sum(distances) / len(distances) if distances else 0
        exam["cover_flag"] = avg_dist > THRESHOLD


def read_name_sid_from_cover(file_path: str) -> dict:
    """
    Extract student name and SID from cover page (page 0) using a local Ollama
    vision model (OLLAMA_VISION_MODEL).  Runs entirely on-device via GPU —
    no data leaves the machine.
    Returns {"name": "...", "sid": "..."}.  Falls back to empty strings on any failure.
    """
    try:
        img_bytes = _render_page_png(file_path, 0, scale=1.5)
        response  = ollama.chat(
            model=OLLAMA_VISION_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "Look at this exam cover page. Extract the student's full name "
                    "and student ID number (labelled SID, ID, Student Number, or similar). "
                    "Respond with ONLY valid JSON, exactly: "
                    '{"name": "Full Name", "sid": "123456789"} '
                    "Use an empty string if you cannot find the value. "
                    "No extra text, no markdown fences — just the JSON object."
                ),
                "images": [img_bytes],
            }],
        )
        text = response["message"]["content"].strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text  = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(text)
        return {"name": str(data.get("name", "")).strip(),
                "sid":  str(data.get("sid",  "")).strip()}
    except Exception:
        return {"name": "", "sid": ""}


def _run_ocr_background(review_id: str, review_path: Path):
    """Run cover-page OCR for every exam in a review session on a background thread."""
    try:
        data  = json.loads(review_path.read_text())
        total = len(data["exams"])
        with _ocr_jobs_lock:
            _ocr_jobs[review_id] = {"total": total, "done": 0, "running": True, "aborted": False}
        for i, exam in enumerate(data["exams"]):
            with _ocr_jobs_lock:
                if _ocr_jobs[review_id].get("aborted"):
                    break
            result       = read_name_sid_from_cover(exam["file_path"])
            exam["name"] = result["name"]
            exam["sid"]  = result["sid"]
            review_path.write_text(json.dumps(data))
            with _ocr_jobs_lock:
                _ocr_jobs[review_id]["done"] = i + 1
    except Exception as e:
        _log.error("OCR failed for review %s: %s", review_id, e)
        with _ocr_jobs_lock:
            if review_id in _ocr_jobs:
                _ocr_jobs[review_id]["error"] = str(e)
    finally:
        with _ocr_jobs_lock:
            if review_id in _ocr_jobs:
                _ocr_jobs[review_id]["running"] = False


# ---------------------------------------------------------------------------
# Batch PDF splitting — fixed page count
# ---------------------------------------------------------------------------

def split_by_page_count_bytes(pdf_bytes: bytes, pages_per_exam: int,
                              output_dir: Path) -> list[dict]:
    """
    Split a PDF (supplied as raw bytes) into chunks of `pages_per_exam` pages.
    Works entirely in memory — no temp file is ever opened, avoiding Windows
    file-locking issues.
    Returns list of dicts: {name, file_path, pages}.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    total  = len(reader.pages)
    exams  = []

    for start in range(0, total, pages_per_exam):
        end = min(start + pages_per_exam - 1, total - 1)

        writer = PdfWriter()
        for p in range(start, end + 1):
            writer.add_page(reader.pages[p])

        buf = io.BytesIO()
        writer.write(buf)

        temp_id  = secrets.token_hex(8)
        out_path = output_dir / f"tmp_{temp_id}.pdf"
        out_path.write_bytes(buf.getvalue())

        exams.append({
            "name":       "",
            "sid":        "",
            "file_path":  str(out_path),
            "pages":      f"{start + 1}–{end + 1}",
            "page_count": end - start + 1,
        })

    return exams



# ---------------------------------------------------------------------------
# Routes: Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    db = get_db()
    total   = db.execute("SELECT COUNT(*) FROM exams").fetchone()[0]
    graded  = db.execute("SELECT COUNT(*) FROM exams WHERE grade_data IS NOT NULL").fetchone()[0]
    rubrics = db.execute("SELECT DISTINCT version FROM rubrics").fetchall()
    versions_with_rubrics = [r["version"] for r in rubrics]
    return render_template("index.html",
                           total=total,
                           graded=graded,
                           ungraded=total - graded,
                           versions_with_rubrics=versions_with_rubrics)


# ---------------------------------------------------------------------------
# Routes: Rubric Setup
# ---------------------------------------------------------------------------

@app.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    if request.method == "POST":
        version = request.form.get("version", "").strip().upper()
        content = request.form.get("content", "").strip()
        file    = request.files.get("rubric_file")

        if not version:
            flash("Exam version is required.", "danger")
            return redirect(url_for("setup"))

        # File upload takes priority over typed content
        rubric_file_path = None
        if file and file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext not in (".pdf", ".docx", ".doc"):
                flash("Only PDF or Word (.docx) files are supported.", "danger")
                return redirect(url_for("setup"))

            # Save permanently so we can send the original file to Claude when grading
            perm_path = RUBRIC_DIR / f"{version}_{secrets.token_hex(6)}{ext}"
            file.save(str(perm_path))
            rubric_file_path = str(perm_path)

            # Also extract text as fallback / for display
            content = extract_rubric_file(str(perm_path), file.filename)
            if not content or content.startswith("["):
                # Text extraction failed but keep the file — Claude will read it visually
                content = f"[See uploaded rubric file — text extraction unavailable for this file]"

        if not content:
            flash("Provide either a rubric file or type the rubric content.", "danger")
            return redirect(url_for("setup"))

        db.execute(
            "INSERT INTO rubrics (version, content, rubric_file_path) VALUES (?, ?, ?)",
            (version, content, rubric_file_path)
        )
        db.commit()
        flash(f"Rubric for Version {version} saved.", "success")
        return redirect(url_for("setup"))

    rubrics = db.execute(
        "SELECT DISTINCT version, MAX(created_at) as updated FROM rubrics GROUP BY version"
    ).fetchall()
    return render_template("setup.html", rubrics=rubrics)


@app.route("/setup/delete/<version>", methods=["POST"])
def delete_rubric(version):
    db = get_db()
    db.execute("DELETE FROM rubrics WHERE version=?", (version.upper(),))
    db.commit()
    flash(f"Rubric for Version {version.upper()} deleted.", "success")
    return redirect(url_for("setup"))


@app.route("/setup/view/<version>")
def view_rubric(version):
    rubric = get_rubric(version.upper())
    if not rubric:
        flash(f"No rubric found for Version {version}.", "warning")
        return redirect(url_for("setup"))
    return render_template("view_rubric.html", rubric=rubric)



# ---------------------------------------------------------------------------
# Routes: View Exams (anonymized — no student names shown by default)
# ---------------------------------------------------------------------------

@app.route("/exams")
def exams():
    db   = get_db()
    rows = db.execute(
        """SELECT anon_id, version, batch, uploaded_at,
                  CASE WHEN grade_data IS NOT NULL THEN 1 ELSE 0 END as graded
           FROM exams ORDER BY version, batch, uploaded_at"""
    ).fetchall()
    rubric_versions = {
        r["version"] for r in db.execute("SELECT DISTINCT version FROM rubrics").fetchall()
    }
    exam_versions = {r["version"] for r in rows}
    missing_rubric_versions = sorted(exam_versions - rubric_versions)
    return render_template("exams.html", exams=rows,
                           rubric_versions=rubric_versions,
                           missing_rubric_versions=missing_rubric_versions)


# ---------------------------------------------------------------------------
# Routes: Delete exam
# ---------------------------------------------------------------------------

@app.route("/exams/delete-all", methods=["POST"])
def delete_all_exams():
    db   = get_db()
    rows = db.execute("SELECT file_path FROM exams").fetchall()
    deleted_files, failed_files = 0, 0
    for row in rows:
        try:
            Path(row["file_path"]).unlink(missing_ok=True)
            deleted_files += 1
        except Exception as e:
            app.logger.warning(f"Could not delete file {row['file_path']}: {e}")
            failed_files += 1
    db.execute("DELETE FROM exams")
    db.commit()
    msg = f"All exams removed ({deleted_files} file{'s' if deleted_files != 1 else ''} deleted"
    if failed_files:
        msg += f", {failed_files} file(s) could not be removed from disk"
    flash(msg + ".", "success" if not failed_files else "warning")
    return redirect(url_for("exams"))


@app.route("/exam/<anon_id>/delete", methods=["POST"])
def delete_exam(anon_id):
    db   = get_db()
    exam = db.execute("SELECT * FROM exams WHERE anon_id=?", (anon_id,)).fetchone()
    if not exam:
        flash("Exam not found.", "danger")
        return redirect(url_for("exams"))

    # Delete the PDF file from disk
    try:
        Path(exam["file_path"]).unlink(missing_ok=True)
    except Exception as e:
        app.logger.warning(f"Could not delete file for {anon_id}: {e}")

    db.execute("DELETE FROM exams WHERE anon_id=?", (anon_id,))
    db.commit()
    flash(f"Exam {anon_id} removed.", "success")
    return redirect(url_for("exams"))


# ---------------------------------------------------------------------------
# Routes: OCR
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Routes: Grading
# ---------------------------------------------------------------------------

def _grade_exam(exam_row, rubric) -> dict:
    """
    Grade an exam by sending page images directly to Claude Sonnet (vision grading).
    Page 0 (name/cover sheet) is skipped — only answer pages are sent.
    If the rubric was uploaded as a PDF, it is sent natively (preserving tables/charts).
    PRIVACY: only anon_id, version, and page images are sent. No student name.
    """
    doc   = pdfium.PdfDocument(exam_row["file_path"])
    total = len(doc)
    if total < 2:
        raise ValueError("Exam PDF must have at least 2 pages (cover + answers).")

    # --- Rubric block: send as native PDF if available, otherwise as text ---
    rubric_file = rubric["rubric_file_path"] if rubric["rubric_file_path"] else None
    rubric_blocks = []
    if rubric_file and Path(rubric_file).exists() and rubric_file.lower().endswith(".pdf"):
        with open(rubric_file, "rb") as f:
            rubric_b64 = base64.standard_b64encode(f.read()).decode()
        rubric_blocks = [
            {"type": "text", "text": "RUBRIC (read the document below carefully before grading):"},
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": rubric_b64},
            },
        ]
    else:
        # Typed rubric or DOCX (text extraction fallback)
        rubric_blocks = [
            {"type": "text", "text": f"RUBRIC:\n{rubric['content']}"},
        ]

    # --- Exam answer page images (skip page 0 — name/cover sheet) ---
    exam_blocks = []
    for page_num in range(1, total):
        img_bytes = _render_page_png(exam_row["file_path"], page_num)
        img = Image.open(io.BytesIO(img_bytes))
        img = ImageEnhance.Contrast(img).enhance(2.0)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()
        b64       = base64.standard_b64encode(img_bytes).decode()
        exam_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
        exam_blocks.append({"type": "text", "text": f"[Page {page_num + 1}]"})

    content = rubric_blocks + [
        {"type": "text", "text": "--- EXAM ANSWER PAGES ---"},
        *exam_blocks,
        {
            "type": "text",
            "text": (
                f"Please grade this exam.\n\n"
                f"EXAM ID: {exam_row['anon_id']}\n"
                f"VERSION: {exam_row['version']}\n\n"
                f"Grade each question strictly according to the rubric above. "
                f"Respond ONLY with the JSON object specified in the system prompt."
            ),
        },
    ]

    system_prompt = f"""You are an impartial exam grader. You will grade handwritten exams anonymously by reading the page images provided.

EXAM VERSION: {exam_row["version"]}
ANONYMOUS EXAM ID: {exam_row["anon_id"]}

Grading instructions:
- The rubric is provided at the start of the user message.
- Read the handwritten answers directly from the exam page images.
- Grade each question strictly according to the rubric.
- Some answers may be written in faint pencil — examine the image carefully before concluding a question is blank or unanswered.
- Be fair and consistent. Do not infer the student's identity.

MULTIPLE CHOICE instructions (critical):
- Each MC question lists options VERTICALLY in order: A (top), B (second), C (third), D (bottom).
- The student circles exactly one letter. That circled letter is their answer.
- READ THE ACTUAL LETTER INSIDE OR NEXT TO THE CIRCLE — do not infer the answer from position alone.
- B and D look similar when handwritten. Look carefully: B has two bumps on the right; D has one large curve.
- If a circle is around the second option, confirm it says "B" not "D" before recording.
- The circled letter IS the student's answer — compare it directly to the correct answer in the rubric table.
- Award full points if the circled letter matches the correct answer. Award 0 otherwise.
- Do NOT award partial credit on multiple choice.
- If no letter is clearly marked, award 0 and note "no answer marked" in feedback.
- If two letters appear marked, use the one with the clearest, most deliberate marking.

QUESTION CONSOLIDATION (critical):
- Each question must appear as a SINGLE entry in the scores array, even if the rubric breaks it into sub-parts (a), (b), (c).
- Sum all sub-part points into one earned_points and one max_points for the parent question.
- Use the question number only (e.g. "Q3", "Q22") — never "Q3a", "Q3b", "Q22a", etc.
- Include feedback for all sub-parts combined in the single feedback string.

FEEDBACK TONE (critical):
- Write feedback as a professor would on a graded exam — definitive, concise, and authoritative.
- State what is correct or incorrect directly. Never hedge, self-correct, or show reasoning process.
- NEVER use phrases like "wait", "actually", "let me reconsider", "on second thought", "hmm", or similar deliberation language.
- If you are uncertain about a reading, make your best judgment and commit to it. Do not narrate your uncertainty.
- Good: "Correct application of the Coase theorem."
- Good: "Incorrect — confused fixed and variable costs in the calculation."
- Bad: "Wait, actually I think the student might have meant... let me look again..."

- Respond ONLY with valid JSON in exactly this format:
{{
  "anon_id": "{exam_row["anon_id"]}",
  "scores": [
    {{"question": "Q1", "max_points": <n>, "earned_points": <n>, "feedback": "<specific feedback>"}}
  ],
  "total_earned": <n>,
  "total_possible": <n>,
  "letter_grade": "<A/B/C/D/F>",
  "overall_feedback": "<2-3 sentence summary>"
}}"""

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    MAX_ATTEMPTS = 2
    last_error = None
    data = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=8192,
            temperature=0.0,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            full_response = stream.get_final_message()

        if full_response.stop_reason == "max_tokens":
            raise ValueError("Response was cut off (max_tokens reached). The exam may be too long.")

        response_text = ""
        for block in full_response.content:
            if block.type == "text":
                response_text = block.text
                break

        start = response_text.find("{")
        end   = response_text.rfind("}") + 1
        if start == -1 or end == 0:
            last_error = ValueError(f"No JSON found. Claude responded with: {response_text[:500]}")
            _log.warning("Attempt %d/%d for %s: no JSON in response", attempt, MAX_ATTEMPTS, exam_row["anon_id"])
            continue

        try:
            data = json.loads(response_text[start:end])
            break  # success
        except json.JSONDecodeError as e:
            last_error = e
            _log.warning("Attempt %d/%d for %s: malformed JSON — %s", attempt, MAX_ATTEMPTS, exam_row["anon_id"], e)
            continue

    if data is None:
        raise last_error or ValueError("Grading failed: no valid response after retries")

    # Consolidate any sub-part rows (e.g. "Q3a", "Q3 (a)", "Q3-a") into their parent
    # question ("Q3") by summing points and concatenating feedback.
    if data.get("scores"):
        import re
        merged = {}   # parent_key -> {max, earned, feedbacks}
        order  = []   # preserve first-seen order
        for s in data["scores"]:
            raw = str(s.get("question", "")).strip()
            # Strip trailing sub-part suffixes: Q3a / Q3(a) / Q3 (a) / Q3-a / Q3_a / Q3.a
            parent = re.sub(r'[\s\-_\.]?\(?[a-zA-Z]\)?$', '', raw).strip()
            if not parent:
                parent = raw
            if parent not in merged:
                merged[parent] = {"max_points": 0, "earned_points": 0, "feedbacks": []}
                order.append(parent)
            merged[parent]["max_points"]    += float(s.get("max_points",    0))
            merged[parent]["earned_points"] += float(s.get("earned_points", 0))
            fb = s.get("feedback", "").strip()
            if fb:
                # Prefix feedback with sub-part label when consolidating
                label = raw if raw != parent else ""
                merged[parent]["feedbacks"].append(f"{label}: {fb}" if label else fb)
        data["scores"] = [
            {
                "question":      k,
                "max_points":    round(v["max_points"],    2),
                "earned_points": round(v["earned_points"], 2),
                "feedback":      " | ".join(v["feedbacks"]),
            }
            for k, v in [(k, merged[k]) for k in order]
        ]

    # Strip deliberation / thinking-out-loud language from feedback.
    # Claude sometimes leaks its reasoning process ("Wait —", "Actually,", "Let me
    # re-read...") into feedback strings despite prompt instructions. This regex-based
    # pass is deterministic: if a sentence matches, it's removed. Scores are unaffected.
    _DELIBERATION = re.compile(
        r'\b(wait|actually|hmm|let me re-?(?:read|check|count|examine)|'
        r'on second thought|re-?reading|I (?:think|miscounted|need to)|'
        r'looking (?:again|more carefully)|hold on|scratch that|'
        r'no,|correction:|upon (?:closer|further))\b',
        re.IGNORECASE,
    )

    def _clean_feedback(text: str) -> str:
        if not text:
            return text
        # Replace em dashes with regular dashes (AI tell)
        text = text.replace("\u2014", "-").replace("\u2013", "-")
        # Split on sentence boundaries, keep only clean sentences
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        clean = [s for s in sentences if not _DELIBERATION.search(s)]
        result = " ".join(clean).strip()
        # If everything was stripped, keep last sentence as fallback (the final answer)
        return result if result else sentences[-1].strip()

    if data.get("scores"):
        for s in data["scores"]:
            if s.get("feedback"):
                s["feedback"] = _clean_feedback(s["feedback"])
    if data.get("overall_feedback"):
        data["overall_feedback"] = _clean_feedback(data["overall_feedback"])

    # Recalculate total_earned from individual scores — Claude's summary total sometimes
    # diverges from its own per-question scores due to rounding (e.g. 18 × 3.33 = 59.94
    # instead of 60).  Keep total_possible as Claude reported it (correctly read from the
    # rubric), but derive earned by subtracting actual missed points from that total.
    if data.get("scores"):
        sum_possible = sum(float(s.get("max_points",    0)) for s in data["scores"])
        sum_earned   = sum(float(s.get("earned_points", 0)) for s in data["scores"])
        missed = sum_possible - sum_earned
        reported_possible = float(data.get("total_possible", sum_possible))
        data["total_earned"] = round(max(reported_possible - missed, 0), 2)

    # Normalize total_possible to nearest integer when rubric point values
    # don't divide evenly (e.g. 18 × 3.33 = 59.94 instead of 60).
    # If the sum is within 0.5 of a whole number, scale both values to that integer.
    raw_possible = data.get("total_possible", 0)
    intended     = round(raw_possible)
    if intended > 0 and abs(raw_possible - intended) < 0.5:
        scale = intended / raw_possible
        data["total_earned"]   = round(data.get("total_earned", 0) * scale, 2)
        data["total_possible"] = intended

    # Recalculate letter grade from the corrected totals
    if data.get("total_possible", 0) > 0:
        pct = data["total_earned"] / data["total_possible"] * 100
        data["letter_grade"] = letter_grade(pct)

    # Hard cap: earned can never exceed possible (guards against rounding overshoot)
    if data.get("total_possible", 0) > 0:
        data["total_earned"] = min(data["total_earned"], data["total_possible"])

    return data


def _grade_one_worker(anon_id: str):
    """Grade a single exam. Called by ThreadPoolExecutor — each call gets its own DB connection."""
    conn = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        exam = conn.execute(
            "SELECT * FROM exams WHERE anon_id=?", (anon_id,)
        ).fetchone()
        if not exam:
            raise ValueError(f"Exam {anon_id} not found")

        # Resume safety: skip if already graded (e.g. by another route mid-batch)
        if exam["grade_data"] is not None:
            _log.info("Skipping %s — already graded", anon_id)
            with _grade_lock:
                _grade_job["done"] += 1
            return

        rubric = conn.execute(
            "SELECT * FROM rubrics WHERE version=? ORDER BY id DESC LIMIT 1",
            (exam["version"],)
        ).fetchone()
        if not rubric:
            raise ValueError(f"No rubric for Version {exam['version']}")

        grade_data = _grade_exam(exam, rubric)
        conn.execute(
            "UPDATE exams SET grade_data=?, graded_at=? WHERE anon_id=?",
            (json.dumps(grade_data), datetime.utcnow(), anon_id)
        )
        conn.commit()
        _log.info("Graded %s — %.1f/%d", anon_id,
                  grade_data.get("total_earned", 0), grade_data.get("total_possible", 0))
        with _grade_lock:
            _grade_job["done"] += 1
    except Exception as e:
        _log.error("Grading failed for %s: %s", anon_id, e)
        with _grade_lock:
            _grade_job["failed"] += 1
            _grade_job["errors"].append(f"{anon_id}: {str(e)[:120]}")
    finally:
        conn.close()


def _run_grade_pool(anon_ids: list):
    """Background thread: grades all exams using a thread pool."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=GRADE_WORKERS) as pool:
            pool.map(_grade_one_worker, anon_ids)
    finally:
        with _grade_lock:
            _grade_job["running"] = False


def _enqueue_and_start(anon_ids: list):
    """Start parallel grading for the given exam IDs."""
    global _grade_job
    with _grade_lock:
        if _grade_job["running"]:
            # Already running — don't start a second pool
            return
        _grade_job = {"running": True, "total": len(anon_ids),
                      "done": 0, "failed": 0, "errors": []}
    threading.Thread(target=_run_grade_pool, args=(anon_ids,), daemon=True).start()


@app.route("/grade/<anon_id>", methods=["POST"])
def grade_one(anon_id):
    db = get_db()
    exam = db.execute("SELECT * FROM exams WHERE anon_id=?", (anon_id,)).fetchone()
    if not exam:
        flash("Exam not found.", "danger")
        return redirect(url_for("exams"))

    rubric = get_rubric(exam["version"])
    if not rubric:
        flash(f"No rubric found for Version {exam['version']}.", "danger")
        return redirect(url_for("exams"))
    try:
        grade_data = _grade_exam(exam, rubric)
        db.execute(
            "UPDATE exams SET grade_data=?, graded_at=? WHERE anon_id=?",
            (json.dumps(grade_data), datetime.utcnow(), anon_id)
        )
        db.commit()
        flash(f"Exam {anon_id} graded successfully.", "success")
    except Exception as e:
        flash(f"Grading failed for {anon_id}: {e}", "danger")

    return redirect(url_for("results"))


@app.route("/grade-all", methods=["POST"])
def grade_all():
    db = get_db()
    version_filter = request.form.get("version", "").strip().upper() or None
    batch_filter   = request.form.get("batch", "").strip() or None

    query  = "SELECT anon_id FROM exams WHERE grade_data IS NULL"
    params = []
    if version_filter:
        query += " AND version=?"
        params.append(version_filter)
    if batch_filter:
        query += " AND batch=?"
        params.append(int(batch_filter))

    rows     = db.execute(query, params).fetchall()
    exam_ids = [r["anon_id"] for r in rows]

    if not exam_ids:
        flash("No ungraded exams found.", "info")
        return redirect(url_for("exams"))

    _enqueue_and_start(exam_ids)
    return redirect(url_for("grade_progress"))


@app.route("/grade-selected", methods=["POST"])
def grade_selected():
    anon_ids = request.form.getlist("anon_ids")
    if not anon_ids:
        return jsonify({"error": "No exams selected."}), 400

    db = get_db()
    placeholders = ",".join("?" * len(anon_ids))
    valid_ids = [
        r["anon_id"] for r in db.execute(
            f"SELECT anon_id FROM exams WHERE anon_id IN ({placeholders})", anon_ids
        ).fetchall()
    ]
    if not valid_ids:
        return jsonify({"error": "None of the selected exams were found."}), 400

    _enqueue_and_start(valid_ids)
    return jsonify({"ok": True, "total": len(valid_ids)})


@app.route("/grade-all/progress")
def grade_progress():
    return render_template("grade_progress.html")


@app.route("/grade-all/status")
def grade_status():
    with _grade_lock:
        return jsonify(dict(_grade_job))


# ---------------------------------------------------------------------------
# Routes: Exam detail / review
# ---------------------------------------------------------------------------

@app.route("/exam/<anon_id>")
def exam_detail(anon_id):
    db   = get_db()
    exam = db.execute("SELECT * FROM exams WHERE anon_id=?", (anon_id,)).fetchone()
    if not exam:
        flash("Exam not found.", "danger")
        return redirect(url_for("exams"))
    doc        = pdfium.PdfDocument(exam["file_path"])
    page_count = len(doc)
    return render_template("exam_detail.html", exam=exam, page_count=page_count)


@app.route("/exam/<anon_id>/clear-grade", methods=["POST"])
def clear_grade(anon_id):
    db = get_db()
    db.execute(
        "UPDATE exams SET grade_data=NULL, graded_at=NULL WHERE anon_id=?", (anon_id,)
    )
    db.commit()
    flash(f"Grade cleared for {anon_id}.", "success")
    return redirect(url_for("exam_detail", anon_id=anon_id))


@app.route("/clear-all-grades", methods=["POST"])
def clear_all_grades():
    version_filter = request.form.get("version", "").strip().upper() or None
    batch_filter   = request.form.get("batch", "").strip() or None
    db = get_db()
    query  = "UPDATE exams SET grade_data=NULL, graded_at=NULL WHERE grade_data IS NOT NULL"
    params = []
    if version_filter:
        query += " AND version=?"
        params.append(version_filter)
    if batch_filter:
        query += " AND batch=?"
        params.append(int(batch_filter))
    db.execute(query, params)
    count = db.execute("SELECT changes()").fetchone()[0]
    db.commit()
    flash(f"Cleared grades for {count} exam(s). Ready to re-grade.", "success")
    return redirect(url_for("exams"))


@app.route("/exam/<anon_id>/update-name", methods=["POST"])
def update_exam_name(anon_id):
    db   = get_db()
    name = request.form.get("student_name", "").strip()
    sid  = request.form.get("student_sid",  "").strip()
    if not name:
        flash("Name cannot be empty.", "danger")
        return redirect(url_for("exam_detail", anon_id=anon_id))
    db.execute(
        "UPDATE exams SET student_name=?, student_sid=? WHERE anon_id=?",
        (name, sid, anon_id)
    )
    db.commit()
    flash("Student info updated.", "success")
    return redirect(url_for("exam_detail", anon_id=anon_id))


@app.route("/exam/<anon_id>/page/<int:page_num>")
def exam_page_image(anon_id, page_num):
    db   = get_db()
    exam = db.execute("SELECT file_path FROM exams WHERE anon_id=?", (anon_id,)).fetchone()
    if not exam:
        return "Not found", 404
    try:
        img_bytes = _render_page_png(exam["file_path"], page_num)
        return send_file(io.BytesIO(img_bytes), mimetype="image/png")
    except Exception as e:
        return f"Error rendering page: {e}", 500


# ---------------------------------------------------------------------------
# Routes: Results
# ---------------------------------------------------------------------------

@app.route("/results")
def results():
    db    = get_db()
    show  = request.args.get("show_names") == "1"
    version_filter = request.args.get("version", "").strip().upper() or None
    batch_filter   = request.args.get("batch", "").strip() or None

    query  = "SELECT * FROM exams WHERE grade_data IS NOT NULL"
    params = []
    if version_filter:
        query += " AND version=?"
        params.append(version_filter)
    if batch_filter:
        query += " AND batch=?"
        params.append(int(batch_filter))
    query += " ORDER BY version, batch, anon_id"

    rows = db.execute(query, params).fetchall()

    graded = []
    for row in rows:
        gd = json.loads(row["grade_data"])
        graded.append({
            "anon_id":      row["anon_id"],
            "student_name": row["student_name"] if show else None,
            "student_sid":  row["student_sid"]  if show else None,
            "version":      row["version"],
            "batch":        row["batch"],
            "total_earned": gd.get("total_earned", "?"),
            "total_possible": gd.get("total_possible", "?"),
            "letter_grade": gd.get("letter_grade", "?"),
            "overall_feedback": gd.get("overall_feedback", ""),
            "scores":       gd.get("scores", []),
        })

    versions = db.execute(
        "SELECT DISTINCT version FROM exams WHERE grade_data IS NOT NULL ORDER BY version"
    ).fetchall()
    batches = db.execute(
        "SELECT DISTINCT batch FROM exams WHERE grade_data IS NOT NULL ORDER BY batch"
    ).fetchall()

    return render_template("results.html",
                           graded=graded,
                           show_names=show,
                           versions=[r["version"] for r in versions],
                           batches=[r["batch"] for r in batches],
                           version_filter=version_filter,
                           batch_filter=batch_filter)


# ---------------------------------------------------------------------------
# Routes: Export CSV
# ---------------------------------------------------------------------------

@app.route("/export")
def export():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM exams WHERE grade_data IS NOT NULL ORDER BY version, batch, anon_id"
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Anon ID", "Student Name", "Student SID", "Version", "Batch",
        "Total Earned", "Total Possible", "Percentage", "Letter Grade",
        "Overall Feedback"
    ])

    for row in rows:
        gd  = json.loads(row["grade_data"])
        earned   = gd.get("total_earned", 0)
        possible = gd.get("total_possible", 0)
        pct = f"{(earned/possible*100):.1f}%" if possible else "N/A"
        writer.writerow([
            row["anon_id"],
            row["student_name"],
            row["student_sid"] or "",
            row["version"],
            row["batch"],
            earned,
            possible,
            pct,
            gd.get("letter_grade", ""),
            gd.get("overall_feedback", ""),
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"grades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )


# ---------------------------------------------------------------------------
# Routes: Batch PDF upload & split
# ---------------------------------------------------------------------------

@app.route("/upload-batch", methods=["GET", "POST"])
def upload_batch():
    if request.method == "POST":
        version        = request.form.get("version", "").strip().upper()
        pages_per_exam = request.form.get("pages_per_exam", "").strip()

        if not version:
            flash("Exam version is required.", "danger")
            return redirect(url_for("upload_batch"))

        try:
            pages_per_exam = int(pages_per_exam)
            if pages_per_exam < 1:
                raise ValueError
        except ValueError:
            flash("Pages per exam must be a whole number of 1 or more.", "danger")
            return redirect(url_for("upload_batch"))

        # Process up to 3 batch slots — each gets its own batch number
        all_exams = []
        for slot in range(1, 4):
            file = request.files.get(f"batch_pdf_{slot}")
            if not file or not file.filename:
                continue
            try:
                batch_num = int(request.form.get(f"batch_{slot}", slot))
            except ValueError:
                batch_num = slot

            pdf_bytes = file.read()
            if not pdf_bytes:
                flash(f"Slot {slot} PDF is empty — skipped.", "warning")
                continue

            try:
                exams = split_by_page_count_bytes(pdf_bytes, pages_per_exam, UPLOAD_DIR)
            except Exception as e:
                flash(f"Failed to split slot {slot}: {e}", "danger")
                continue

            for exam in exams:
                exam["batch"] = batch_num
            all_exams.extend(exams)

        if not all_exams:
            flash("No valid PDFs were uploaded.", "danger")
            return redirect(url_for("upload_batch"))

        # Check cover page consistency across all exams (local, no API)
        check_cover_consistency(all_exams)

        # Discard any existing review sessions before creating a new one
        for stale in DATA_DIR.glob("review_*.json"):
            try:
                stale_data = json.loads(stale.read_text())
                for e in stale_data.get("exams", []):
                    Path(e["file_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            stale.unlink(missing_ok=True)

        # Store split results immediately — OCR runs in background if opted in
        review_id   = secrets.token_hex(8)
        review_data = {"version": version, "exams": all_exams}
        review_path = DATA_DIR / f"review_{review_id}.json"
        review_path.write_text(json.dumps(review_data))

        if request.form.get("run_ocr") == "1":
            t = threading.Thread(
                target=_run_ocr_background,
                args=(review_id, review_path),
                daemon=True,
            )
            t.start()

        return redirect(url_for("batch_review", review_id=review_id))

    return render_template("upload_batch.html")


@app.route("/upload-batch/review/<review_id>")
def batch_review(review_id):
    review_path = DATA_DIR / f"review_{review_id}.json"
    if not review_path.exists():
        flash("Review session not found. Please upload again.", "danger")
        return redirect(url_for("upload_batch"))
    data         = json.loads(review_path.read_text())
    db           = get_db()
    roster_count = db.execute("SELECT COUNT(*) FROM roster").fetchone()[0]
    with _ocr_jobs_lock:
        ocr_running = _ocr_jobs.get(review_id, {}).get("running", False)
    return render_template("batch_review.html",
                           review_id=review_id,
                           version=data["version"],
                           exams=data["exams"],
                           roster_count=roster_count,
                           ocr_running=ocr_running)


@app.route("/upload-batch/ocr-status/<review_id>")
def ocr_status(review_id):
    with _ocr_jobs_lock:
        job = dict(_ocr_jobs.get(review_id, {"total": 0, "done": 0, "running": False}))
    review_path = DATA_DIR / f"review_{review_id}.json"
    exams = []
    if review_path.exists():
        try:
            data  = json.loads(review_path.read_text())
            exams = [{"name": e.get("name", ""), "sid": e.get("sid", "")}
                     for e in data["exams"]]
        except Exception:
            pass
    return jsonify({**job, "exams": exams})


@app.route("/upload-batch/ocr-abort/<review_id>", methods=["POST"])
def ocr_abort(review_id):
    with _ocr_jobs_lock:
        if review_id in _ocr_jobs and _ocr_jobs[review_id].get("running"):
            _ocr_jobs[review_id]["aborted"] = True
            return jsonify({"ok": True, "message": "Abort signal sent — stopping after current exam."})
    return jsonify({"ok": False, "message": "No active OCR job for this session."})


@app.route("/upload-batch/confirm/<review_id>", methods=["POST"])
def batch_confirm(review_id):
    review_path = DATA_DIR / f"review_{review_id}.json"
    if not review_path.exists():
        flash("Review session not found. Please upload again.", "danger")
        return redirect(url_for("upload_batch"))

    data  = json.loads(review_path.read_text())
    db    = get_db()
    saved = 0

    for i, exam in enumerate(data["exams"]):
        student_name = request.form.get(f"name_{i}", "").strip()
        student_sid  = request.form.get(f"sid_{i}",  "").strip()
        anon_id      = generate_anon_id()
        old_path     = Path(exam["file_path"])
        new_path     = UPLOAD_DIR / f"{anon_id}.pdf"
        if old_path.exists():
            # Copy then delete — avoids WinError 32 if a thumbnail request
            # still has the file open in pypdfium2
            new_path.write_bytes(old_path.read_bytes())
            old_path.unlink(missing_ok=True)
        db.execute(
            "INSERT INTO exams (anon_id, student_name, student_sid, version, batch, file_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (anon_id, student_name, student_sid, data["version"], exam["batch"], str(new_path))
        )
        saved += 1

    db.commit()
    review_path.unlink(missing_ok=True)
    flash(
        f"{saved} exams saved → Version {data['version']}.",
        "success"
    )
    return redirect(url_for("exams"))


@app.route("/upload-batch/discard/<review_id>", methods=["POST"])
def batch_discard(review_id):
    review_path = DATA_DIR / f"review_{review_id}.json"
    if review_path.exists():
        try:
            data = json.loads(review_path.read_text())
            for exam in data.get("exams", []):
                Path(exam["file_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        review_path.unlink(missing_ok=True)
    with _ocr_jobs_lock:
        _ocr_jobs.pop(review_id, None)
    return redirect(url_for("upload_batch"))


@app.route("/upload-batch/preview/<review_id>/<int:exam_index>/<int:page_num>")
def batch_preview_page(review_id, exam_index, page_num):
    """Serve a rendered page image from a temp split exam (before it's saved to DB)."""
    review_path = DATA_DIR / f"review_{review_id}.json"
    if not review_path.exists():
        return "Session expired", 404
    data = json.loads(review_path.read_text())
    if exam_index < 0 or exam_index >= len(data["exams"]):
        return "Not found", 404
    file_path = data["exams"][exam_index]["file_path"]
    # Use lower scale for thumbnails to keep them fast
    scale = 0.75 if request.args.get("thumb") else 1.5
    try:
        img_bytes = _render_page_png(file_path, page_num, scale=scale)
        return send_file(io.BytesIO(img_bytes), mimetype="image/png")
    except Exception as e:
        return f"Error rendering page: {e}", 500


# ---------------------------------------------------------------------------
# Routes: Roster management (local only — never sent to Claude)
# ---------------------------------------------------------------------------

@app.route("/roster", methods=["GET", "POST"])
def roster():
    db = get_db()
    if request.method == "POST":
        file = request.files.get("roster_csv")
        if not file or not file.filename:
            flash("Please select a CSV file to upload.", "danger")
            return redirect(url_for("roster"))

        try:
            content  = file.read().decode("utf-8-sig")  # strip BOM if present
            reader   = csv.reader(io.StringIO(content))
            rows     = list(reader)
        except Exception as e:
            flash(f"Could not read CSV: {e}", "danger")
            return redirect(url_for("roster"))

        # Auto-detect and skip header row
        entries = []
        for i, row in enumerate(rows):
            if len(row) < 2:
                continue
            first = row[0].strip()
            last  = row[1].strip()
            sid   = row[2].strip() if len(row) >= 3 else ""
            # Skip header row (first row whose first cell looks like a label)
            if i == 0 and first.lower() in ("first", "first_name", "firstname", "first name"):
                continue
            if not first and not last:
                continue
            entries.append((first, last, sid))

        if not entries:
            flash("No valid rows found in CSV (need at least first_name, last_name columns).", "danger")
            return redirect(url_for("roster"))

        db.execute("DELETE FROM roster")
        db.executemany("INSERT INTO roster (first_name, last_name, sid) VALUES (?, ?, ?)", entries)
        db.commit()
        flash(f"Loaded {len(entries)} students from roster.", "success")
        return redirect(url_for("roster"))

    count      = db.execute("SELECT COUNT(*) FROM roster").fetchone()[0]
    preview    = db.execute("SELECT * FROM roster LIMIT 5").fetchall()
    show_names = request.args.get("show_names") == "1"
    return render_template("roster.html", count=count, preview=preview, show_names=show_names)


@app.route("/roster/clear", methods=["POST"])
def roster_clear():
    db = get_db()
    db.execute("DELETE FROM roster")
    db.commit()
    flash("Roster cleared.", "success")
    return redirect(url_for("roster"))


@app.route("/upload-batch/review/<review_id>/match-roster", methods=["POST"])
def match_roster(review_id):
    review_path = DATA_DIR / f"review_{review_id}.json"
    if not review_path.exists():
        return jsonify({"error": "Review session not found"}), 404

    data    = json.loads(review_path.read_text())
    db      = get_db()
    rows    = db.execute("SELECT * FROM roster").fetchall()
    entries = [dict(r) for r in rows]

    results = []
    for i, exam in enumerate(data["exams"]):
        best, score = match_against_roster(exam.get("name", ""), exam.get("sid", ""), entries)
        if best:
            tier = "high" if score >= 0.85 else ("medium" if score >= 0.60 else "low")
            results.append({
                "exam_index": i,
                "name":  f"{best['first_name']} {best['last_name']}",
                "sid":   best["sid"] or "",
                "score": score,
                "tier":  tier,
            })
        else:
            results.append({
                "exam_index": i,
                "name":  exam.get("name", ""),
                "sid":   exam.get("sid",  ""),
                "score": 0.0,
                "tier":  "low",
            })

    return jsonify({"results": results})


# ---------------------------------------------------------------------------
# Routes: Documentation
# ---------------------------------------------------------------------------

@app.route("/docs")
def docs():
    db = get_db()

    total_exams  = db.execute("SELECT COUNT(*) FROM exams").fetchone()[0]
    graded_exams = db.execute("SELECT COUNT(*) FROM exams WHERE grade_data IS NOT NULL").fetchone()[0]
    exam_versions = [r[0] for r in db.execute(
        "SELECT DISTINCT version FROM exams ORDER BY version"
    ).fetchall()]
    rubric_versions = [r[0] for r in db.execute(
        "SELECT DISTINCT version FROM rubrics ORDER BY version"
    ).fetchall()]
    batches = [r[0] for r in db.execute(
        "SELECT DISTINCT batch FROM exams ORDER BY batch"
    ).fetchall()]

    # Estimate cost: ~$0.12 average per graded exam (midpoint of $0.09–$0.15, 12-page exam)
    est_cost = round(graded_exams * 0.12, 2)

    # Rubric files on disk
    rubric_files = list(RUBRIC_DIR.glob("*"))
    rubric_file_count = len(rubric_files)

    # Upload dir size (MB)
    upload_bytes = sum(f.stat().st_size for f in UPLOAD_DIR.glob("*.pdf") if f.is_file())
    upload_mb    = round(upload_bytes / 1_048_576, 1)

    # Build info written by pre-commit hook
    build_info_path = BASE_DIR / "build_info.json"
    build_info = {}
    if build_info_path.exists():
        try:
            build_info = json.loads(build_info_path.read_text())
        except Exception:
            pass

    return render_template("docs.html",
        total_exams=total_exams,
        graded_exams=graded_exams,
        ungraded_exams=total_exams - graded_exams,
        exam_versions=exam_versions,
        rubric_versions=rubric_versions,
        batches=batches,
        est_cost=est_cost,
        rubric_file_count=rubric_file_count,
        upload_mb=upload_mb,
        claude_model=CLAUDE_MODEL,
        ollama_model=OLLAMA_VISION_MODEL,
        build_info=build_info,
    )


# ---------------------------------------------------------------------------
# Routes: Per-student printable report
# ---------------------------------------------------------------------------

@app.route("/exam/<anon_id>/report/update", methods=["POST"])
def update_report(anon_id):
    db   = get_db()
    exam = db.execute("SELECT * FROM exams WHERE anon_id=?", (anon_id,)).fetchone()
    if not exam or not exam["grade_data"]:
        return jsonify({"error": "No graded data for this exam"}), 404

    gd = json.loads(exam["grade_data"])

    for i, s in enumerate(gd.get("scores", [])):
        raw = request.form.get(f"earned_{i}", "")
        try:
            val = float(raw)
            # Clamp to [0, max_points]
            s["earned_points"] = max(0.0, min(val, float(s["max_points"])))
        except (ValueError, TypeError):
            pass  # leave original if the field was blank or invalid

    # Recalculate totals
    total_earned   = sum(float(s["earned_points"]) for s in gd["scores"])
    total_possible = float(gd["total_possible"])
    total_earned   = min(round(total_earned, 2), total_possible)  # hard cap
    pct            = total_earned / total_possible * 100 if total_possible else 0

    gd["total_earned"]  = total_earned
    gd["letter_grade"]  = letter_grade(pct)

    db.execute("UPDATE exams SET grade_data=? WHERE anon_id=?",
               (json.dumps(gd), anon_id))
    db.commit()
    return jsonify({
        "total_earned":   total_earned,
        "total_possible": gd["total_possible"],
        "pct":            round(pct, 1),
        "letter_grade":   gd["letter_grade"]
    })


@app.route("/exam/<anon_id>/report")
def student_report(anon_id):
    db   = get_db()
    exam = db.execute("SELECT * FROM exams WHERE anon_id=?", (anon_id,)).fetchone()
    if not exam or not exam["grade_data"]:
        flash("No graded data for this exam.", "warning")
        return redirect(url_for("results"))
    gd        = json.loads(exam["grade_data"])
    pct       = gd["total_earned"] / gd["total_possible"] * 100 if gd["total_possible"] else 0
    show_name = request.args.get("show_name") == "1"
    return render_template("student_report.html", exam=exam, gd=gd, pct=pct, show_name=show_name)


# ---------------------------------------------------------------------------
# Routes: Detailed CSV export (per-question columns)
# ---------------------------------------------------------------------------

@app.route("/export-detailed")
def export_detailed():
    db = get_db()
    version_filter = request.args.get("version", "").upper() or None
    batch_filter   = request.args.get("batch", "") or None

    query  = "SELECT * FROM exams WHERE grade_data IS NOT NULL"
    params = []
    if version_filter:
        query += " AND version=?"
        params.append(version_filter)
    if batch_filter:
        query += " AND batch=?"
        params.append(int(batch_filter))
    query += " ORDER BY version, batch, anon_id"

    rows = db.execute(query, params).fetchall()

    # Collect all unique question names in encounter order
    all_questions: dict = {}
    parsed = []
    for row in rows:
        gd = json.loads(row["grade_data"])
        parsed.append((row, gd))
        for s in gd.get("scores", []):
            q = s.get("question", "")
            if q and q not in all_questions:
                all_questions[q] = None

    question_names = list(all_questions.keys())

    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    fixed_cols = [
        "Anon ID", "Name", "SID", "Version", "Batch",
        "Total Earned", "Total Possible", "Pct", "Grade", "Overall Feedback"
    ]
    dynamic_cols = []
    for q in question_names:
        dynamic_cols += [f"{q} Earned", f"{q} Max", f"{q} Feedback"]
    writer.writerow(fixed_cols + dynamic_cols)

    # Data rows
    for row, gd in parsed:
        earned   = gd.get("total_earned", 0)
        possible = gd.get("total_possible", 0)
        pct      = f"{(earned / possible * 100):.1f}%" if possible else "N/A"

        # Build lookup by question name
        scores_by_q = {s["question"]: s for s in gd.get("scores", [])}

        fixed = [
            row["anon_id"],
            row["student_name"],
            row["student_sid"] or "",
            row["version"],
            row["batch"],
            earned,
            possible,
            pct,
            gd.get("letter_grade", ""),
            gd.get("overall_feedback", ""),
        ]
        dynamic = []
        for q in question_names:
            s = scores_by_q.get(q, {})
            dynamic += [
                s.get("earned_points", ""),
                s.get("max_points", ""),
                s.get("feedback", ""),
            ]
        writer.writerow(fixed + dynamic)

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"grades_detailed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )


# ---------------------------------------------------------------------------
# Routes: Analytics
# ---------------------------------------------------------------------------

@app.route("/analytics")
def analytics():
    db = get_db()
    version_filter = request.args.get("version", "").upper() or None
    batch_filter   = request.args.get("batch", "") or None
    q_version      = request.args.get("q_version", "").upper() or None

    # Main query — drives distribution, band, and summary stats
    query  = "SELECT * FROM exams WHERE grade_data IS NOT NULL"
    params = []
    if version_filter:
        query += " AND version=?"
        params.append(version_filter)
    if batch_filter:
        query += " AND batch=?"
        params.append(int(batch_filter))

    rows = db.execute(query, params).fetchall()

    pct_scores = []
    for row in rows:
        gd       = json.loads(row["grade_data"])
        possible = float(gd.get("total_possible", 0))
        earned   = float(gd.get("total_earned",   0))
        if possible > 0:
            pct_scores.append(round(earned / possible * 100, 2))

    # Question stats — separate query filtered by q_version only
    q_query  = "SELECT * FROM exams WHERE grade_data IS NOT NULL"
    q_params = []
    if q_version:
        q_query += " AND version=?"
        q_params.append(q_version)
    elif version_filter:
        q_query += " AND version=?"
        q_params.append(version_filter)

    q_rows = db.execute(q_query, q_params).fetchall()
    question_stats = {}
    for row in q_rows:
        gd = json.loads(row["grade_data"])
        for s in gd.get("scores", []):
            q  = s.get("question", "").strip()
            ep = float(s.get("earned_points", 0))
            mp = float(s.get("max_points",   0))
            if not q:
                continue
            if q not in question_stats:
                question_stats[q] = {"earned": 0.0, "possible": 0.0, "n": 0, "below70": 0}
            question_stats[q]["earned"]   += ep
            question_stats[q]["possible"] += mp
            question_stats[q]["n"]        += 1
            if mp > 0 and ep / mp < 0.70:
                question_stats[q]["below70"] += 1

    # Grade distribution — 10 bins: [0,10), [10,20), …, [90,100]
    dist_bins   = [0] * 10
    dist_labels = ["0–10","10–20","20–30","30–40","40–50",
                   "50–60","60–70","70–80","80–90","90–100"]
    for p in pct_scores:
        dist_bins[min(int(p // 10), 9)] += 1

    # Letter-grade band counts
    grade_bands = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for p in pct_scores:
        grade_bands[letter_grade(p)] += 1

    # Per-question averages and fail rates
    q_labels   = list(question_stats.keys())
    q_avg_pct  = []
    q_fail_rate = []
    struggling  = []   # questions with avg < 60%
    for q in q_labels:
        st  = question_stats[q]
        avg = round(st["earned"] / st["possible"] * 100, 1) if st["possible"] else 0.0
        fr  = round(st["below70"] / st["n"] * 100, 1)       if st["n"]        else 0.0
        q_avg_pct.append(avg)
        q_fail_rate.append(fr)
        if avg < 60:
            struggling.append({"question": q, "avg": avg, "fail_rate": fr})

    # Summary statistics
    n = len(pct_scores)
    if n > 0:
        mean   = round(_stats.mean(pct_scores),   1)
        median = round(_stats.median(pct_scores), 1)
        stdev  = round(_stats.stdev(pct_scores),  1) if n > 1 else 0.0
        lo     = round(min(pct_scores), 1)
        hi     = round(max(pct_scores), 1)
        pass_r = round(sum(1 for p in pct_scores if p >= 70) / n * 100, 1)
    else:
        mean = median = stdev = lo = hi = pass_r = 0.0

    versions = db.execute(
        "SELECT DISTINCT version FROM exams WHERE grade_data IS NOT NULL ORDER BY version"
    ).fetchall()
    batches = db.execute(
        "SELECT DISTINCT batch FROM exams WHERE grade_data IS NOT NULL ORDER BY batch"
    ).fetchall()

    return render_template("analytics.html",
        n=n, mean=mean, median=median, stdev=stdev, lo=lo, hi=hi, pass_r=pass_r,
        dist_bins=dist_bins, dist_labels=dist_labels,
        grade_bands=grade_bands,
        q_labels=q_labels, q_avg_pct=q_avg_pct, q_fail_rate=q_fail_rate,
        struggling=struggling,
        versions=[r["version"] for r in versions],
        batches=[r["batch"] for r in batches],
        version_filter=version_filter,
        batch_filter=batch_filter,
        q_version=q_version,
        now=datetime.utcnow().strftime("%B %d, %Y"),
    )


# ---------------------------------------------------------------------------
# Launch Ollama
# ---------------------------------------------------------------------------

@app.route("/launch-ollama", methods=["POST"])
def launch_ollama():
    import subprocess, shutil, urllib.request
    ollama_path = shutil.which("ollama") or r"C:\Users\tfras\AppData\Local\Programs\Ollama\ollama.exe"

    # Check if already running
    try:
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=2)
        return {"ok": True, "message": "Ollama is already running."}
    except Exception:
        pass

    try:
        subprocess.Popen(
            [ollama_path, "serve"],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "message": "Ollama launched — allow a few seconds to load."}
    except Exception as e:
        return {"ok": False, "message": str(e)}, 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    print("\n Exam Grader running locally.")
    print("   Cover-page OCR uses local Ollama model — no student data leaves this machine.")
    print(f"   OCR model : {OLLAMA_VISION_MODEL}  (change OLLAMA_VISION_MODEL to swap)")
    print("   Grading   : Claude Sonnet via Anthropic API (anonymous IDs only)")
    print("   Open: http://localhost:5000\n")
    app.run(debug=True, port=5000, use_reloader=False)
