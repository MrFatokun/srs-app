# SRS Standardizer

Automatically transforms non-standard Software Requirements Specifications into **ISO/IEC/IEEE 29148** format using Claude AI.

## Features

- 📄 Upload SRS in **PDF, DOCX, or TXT**
- 🧠 Two restructuring strategies: **PEACH** (section-by-section) and **PALL** (whole document)
- 🔁 Multiple generation runs with automatic **best-output selection** by sufficiency score
- 📊 **Necessity & Sufficiency** evaluation via SBERT sentence embeddings
- ☑️ **ISO 29148 checklist** coverage report
- ⚠️ Human review warning when sufficiency < 0.70
- 🚫 Zero hallucination — uses `[N/A]` and `[NULL]` markers only

---

## Quick Start

### 1. Set your API key
```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 2. Run the app
```bash
chmod +x run.sh
./run.sh
```

### 3. Open your browser
```
http://localhost:8000
```

---

## Manual Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

---

## Configuration

| Setting | Default | Description |
|---|---|---|
| Strategy | PEACH | PEACH = section-by-section (better coverage); PALL = whole-doc (faster) |
| Runs | 3 | Number of generation passes; best by sufficiency score is kept |
| Max input | ~8,500 words | Larger docs auto-switch to PEACH strategy |

---

## Project Structure

```
srs-app/
├── main.py          # FastAPI backend — pipeline, evaluation, API
├── index.html       # Single-file frontend (served at /)
├── requirements.txt # Python dependencies
├── run.sh           # Setup & start script
└── README.md
```

---

## API Reference

### `POST /api/process`
Multipart form:
- `file` — SRS document (PDF/DOCX/TXT)
- `strategy` — `peach` or `pall`
- `num_runs` — integer 1–5

Returns JSON with `standardized_srs`, `scores`, `checklist`, `warning`, and run metadata.

### `GET /api/health`
Returns status of dependencies and API key.

---

## Dependencies

- **fastapi + uvicorn** — web server
- **anthropic** — Claude API client
- **pdfplumber** — PDF text extraction
- **python-docx** — DOCX parsing
- **sentence-transformers** — SBERT embeddings for evaluation
- **numpy** — cosine similarity computation

If `sentence-transformers` is unavailable, the system falls back to token-overlap scoring.
