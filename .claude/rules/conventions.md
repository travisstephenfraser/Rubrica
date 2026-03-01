# Conventions

- **Model:** `claude-sonnet-4-6` (constant `CLAUDE_MODEL`)
- **OCR model:** `llama3.2-vision` via local Ollama (constant `OLLAMA_VISION_MODEL`)
- **Workers:** 5 parallel grading threads (constant `GRADE_WORKERS`)
- **Rubric generators:** `generate_rubric.py` (RED V3), `generate_rubric_green.py` (GREEN V1) -- require `reportlab`
- **Pre-commit hook:** writes `build_info.json` with commit/branch/date for the About page
- **No em/en dashes in output** -- the feedback sanitizer replaces them with regular dashes (AI tell)
- **`use_reloader=False`** so Ctrl+C kills the server cleanly
- **Run:** `C:/Python314/python.exe grader.py` -- serves at `http://localhost:5000`
