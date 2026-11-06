import os
import re
import json
import asyncio
import tempfile
from pathlib import Path
from typing import Optional
import time

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic

# Optional document parsing libraries
try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer, util
    import numpy as np
    SBERT_AVAILABLE = True
except ImportError:
    SBERT_AVAILABLE = False

app = FastAPI(title="SRS Standardization Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── ISO 29148 Structure ───────────────────────────────────────────────────────

ISO_SECTIONS = [
    "1. Introduction",
    "1.1 Purpose",
    "1.2 Scope",
    "1.3 Definitions, Acronyms, and Abbreviations",
    "1.4 References",
    "1.5 Overview",
    "2. Overall Description",
    "2.1 Product Perspective",
    "2.2 Product Functions",
    "2.3 User Characteristics",
    "2.4 Constraints",
    "2.5 Assumptions and Dependencies",
    "3. Specific Requirements",
    "3.1 External Interface Requirements",
    "3.2 System Features",
    "3.3 Performance Requirements",
    "3.4 Design Constraints",
    "3.5 Software System Attributes",
    "3.5.1 Reliability",
    "3.5.2 Availability",
    "3.5.3 Security",
    "3.5.4 Maintainability",
    "3.5.5 Portability",
]

ISO_SECTION_DESCRIPTIONS = {
    "1. Introduction": "High-level overview of the document itself.",
    "1.1 Purpose": "What this SRS is for and who it is intended for.",
    "1.2 Scope": "Name of the software, what it does, and its benefits.",
    "1.3 Definitions, Acronyms, and Abbreviations": "All terms, acronyms, and abbreviations used in this document.",
    "1.4 References": "List of all referenced documents and standards.",
    "1.5 Overview": "How the rest of the SRS is organized.",
    "2. Overall Description": "General factors affecting the product and its requirements.",
    "2.1 Product Perspective": "Context and origin of the product, including system interfaces.",
    "2.2 Product Functions": "Summary of major functions the software will perform.",
    "2.3 User Characteristics": "Characteristics of the intended users.",
    "2.4 Constraints": "Anything that limits the developer's options.",
    "2.5 Assumptions and Dependencies": "Factors that affect the requirements.",
    "3. Specific Requirements": "All detailed software requirements.",
    "3.1 External Interface Requirements": "Interfaces with users, hardware, software, and communications.",
    "3.2 System Features": "Functional requirements grouped by feature.",
    "3.3 Performance Requirements": "Static and dynamic numerical requirements.",
    "3.4 Design Constraints": "Constraints imposed by other standards or hardware.",
    "3.5 Software System Attributes": "Non-functional requirements.",
    "3.5.1 Reliability": "Reliability requirements.",
    "3.5.2 Availability": "Availability and uptime requirements.",
    "3.5.3 Security": "Security and access control requirements.",
    "3.5.4 Maintainability": "Maintenance and documentation requirements.",
    "3.5.5 Portability": "Portability and deployment requirements.",
}

# ─── Text Extraction ───────────────────────────────────────────────────────────

def extract_text_from_txt(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")

def extract_text_from_pdf(content: bytes) -> str:
    if not PDF_AVAILABLE:
        raise HTTPException(status_code=400, detail="PDF support not installed. Run: pip install pdfplumber")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(content)
        tmp_path = f.name
    try:
        text_parts = []
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)
    finally:
        os.unlink(tmp_path)

def extract_text_from_docx(content: bytes) -> str:
    if not DOCX_AVAILABLE:
        raise HTTPException(status_code=400, detail="DOCX support not installed. Run: pip install python-docx")
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(content)
        tmp_path = f.name
    try:
        doc = DocxDocument(tmp_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    finally:
        os.unlink(tmp_path)

def preprocess_text(raw: str) -> str:
    lines = raw.split("\n")
    cleaned = []
    skip_patterns = [
        r"^\s*table of contents\s*$",
        r"^\s*\d+\s*$",           # lone page numbers
        r"^\s*page \d+",
        r"^\s*confidential\s*$",
        r"^\s*draft\s*$",
    ]
    toc_mode = False
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if lower in ("table of contents", "contents"):
            toc_mode = True
            continue
        if toc_mode:
            # TOC lines typically have dots/tabs leading to page numbers
            if re.search(r"\.{3,}|\t\d+\s*$", stripped) or re.match(r"^\d+$", stripped):
                continue
            else:
                toc_mode = False
        skip = any(re.match(p, stripped, re.IGNORECASE) for p in skip_patterns)
        if skip:
            continue
        # Normalize bullet items - add period if missing
        if re.match(r"^[-•*]\s+", stripped):
            if not stripped[-1] in ".,:;":
                stripped = stripped + "."
        cleaned.append(stripped)
    text = "\n".join(cleaned)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def split_into_sentences(text: str) -> list[str]:
    # Simple sentence splitter
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]

# ─── LLM Prompts ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert requirements engineer specializing in ISO/IEC/IEEE 29148 standards.
Your task is to restructure Software Requirements Specifications (SRS) into the standard ISO 29148 format.

CRITICAL RULES:
1. Only use content from the provided source SRS. Do NOT fabricate or hallucinate new requirements.
2. Use [N/A] for required standard items that have no corresponding content in the source.
3. Use [NULL] only as a last resort if a section is truly impossible to populate.
4. Preserve the original wording as much as possible while reorganizing into the correct sections.
5. Output clean, well-structured markdown with proper heading levels."""

def build_pall_prompt(source_text: str) -> str:
    sections_list = "\n".join(f"- {s}: {ISO_SECTION_DESCRIPTIONS[s]}" for s in ISO_SECTIONS)
    return f"""Restructure the following SRS document into ISO/IEC/IEEE 29148 standard format.

TARGET SECTIONS:
{sections_list}

SOURCE SRS:
{source_text}

OUTPUT: A complete restructured SRS in markdown format covering all sections above.
For missing content use [N/A]. Do not add new requirements not present in the source."""

def build_peach_prompt(source_text: str, section: str, description: str) -> str:
    return f"""Restructure the following SRS document to fill ONLY this ISO 29148 section:

SECTION: {section}
DESCRIPTION: {description}

SOURCE SRS:
{source_text}

OUTPUT: Content for the "{section}" section only, in markdown.
Use [N/A] if the source contains no relevant content for this section.
Do NOT add requirements not present in the source. Keep it concise."""

# ─── LLM Calls ────────────────────────────────────────────────────────────────

async def call_claude(prompt: str, system: str = SYSTEM_PROMPT, max_tokens: int = 4096) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in environment")
    client = anthropic.Anthropic(api_key=api_key)
    # Run in thread to avoid blocking
    loop = asyncio.get_event_loop()
    def _call():
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    return await loop.run_in_executor(None, _call)

async def run_pall(source_text: str) -> str:
    prompt = build_pall_prompt(source_text)
    return await call_claude(prompt, max_tokens=8000)

async def run_peach(source_text: str) -> str:
    section_outputs = []
    for section in ISO_SECTIONS:
        desc = ISO_SECTION_DESCRIPTIONS[section]
        prompt = build_peach_prompt(source_text, section, desc)
        content = await call_claude(prompt, max_tokens=1000)
        # Clean up - remove any re-stated section header if model repeated it
        content = re.sub(rf"^#+\s*{re.escape(section)}\s*\n", "", content, flags=re.IGNORECASE).strip()
        section_outputs.append(f"## {section}\n\n{content}")
    return "\n\n---\n\n".join(section_outputs)

# ─── Evaluation ───────────────────────────────────────────────────────────────

def compute_scores_sbert(source_text: str, output_text: str) -> dict:
    if not SBERT_AVAILABLE:
        return compute_scores_simple(source_text, output_text)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    src_sents = split_into_sentences(source_text)
    out_sents = split_into_sentences(output_text)
    if not src_sents or not out_sents:
        return {"necessity": 0.0, "sufficiency": 0.0, "method": "sbert"}
    src_embs = model.encode(src_sents, convert_to_tensor=True)
    out_embs = model.encode(out_sents, convert_to_tensor=True)
    # Necessity: for each source sentence, find max similarity to any output sentence
    sim_matrix = util.cos_sim(src_embs, out_embs).numpy()
    necessity = float(np.mean(np.max(sim_matrix, axis=1)))
    # Sufficiency: for each output sentence, find max similarity to any source sentence
    sufficiency = float(np.mean(np.max(sim_matrix, axis=0)))
    return {"necessity": round(necessity, 4), "sufficiency": round(sufficiency, 4), "method": "sbert"}

def compute_scores_simple(source_text: str, output_text: str) -> dict:
    """Fallback: token overlap-based scoring when SBERT not available."""
    def tokens(t):
        return set(re.findall(r"\b\w+\b", t.lower()))
    src_tokens = tokens(source_text)
    out_tokens = tokens(output_text)
    if not src_tokens or not out_tokens:
        return {"necessity": 0.0, "sufficiency": 0.0, "method": "token_overlap"}
    necessity = len(src_tokens & out_tokens) / len(src_tokens)
    sufficiency = len(src_tokens & out_tokens) / len(out_tokens)
    return {"necessity": round(necessity, 4), "sufficiency": round(sufficiency, 4), "method": "token_overlap"}

def compute_checklist(output_text: str) -> dict:
    results = {}
    for section in ISO_SECTIONS:
        # Check if section heading appears in output
        pattern = re.escape(section.split(" ", 1)[-1])  # strip number
        found = bool(re.search(pattern, output_text, re.IGNORECASE))
        # Also check for [N/A] or [NULL] — present but empty
        results[section] = {
            "present": found,
            "na": bool(re.search(r"\[N/A\]", output_text)) if found else False,
        }
    total = len(ISO_SECTIONS)
    present = sum(1 for v in results.values() if v["present"])
    coverage = round(present / total * 100, 1)
    return {"sections": results, "coverage_pct": coverage, "present": present, "total": total}

def insert_nulls(output_text: str) -> str:
    """Insert [NULL] for any ISO sections completely missing from output."""
    for section in ISO_SECTIONS:
        heading_variants = [
            f"## {section}",
            f"# {section}",
            section,
        ]
        found = any(v.lower() in output_text.lower() for v in heading_variants)
        if not found:
            output_text += f"\n\n## {section}\n\n[NULL]\n"
    return output_text

# ─── Main Pipeline Endpoint ───────────────────────────────────────────────────

@app.post("/api/process")
async def process_srs(
    file: UploadFile = File(...),
    strategy: str = Form("peach"),
    num_runs: int = Form(3),
):
    content = await file.read()
    filename = file.filename.lower()

    # Extract text
    if filename.endswith(".txt"):
        raw_text = extract_text_from_txt(content)
    elif filename.endswith(".pdf"):
        raw_text = extract_text_from_pdf(content)
    elif filename.endswith(".docx"):
        raw_text = extract_text_from_docx(content)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type. Use PDF, DOCX, or TXT.")

    source_text = preprocess_text(raw_text)

    # Word count check
    word_count = len(source_text.split())
    if word_count > 12000:
        strategy = "peach"  # Force peach for large docs

    num_runs = max(1, min(num_runs, 5))

    # Run generations
    runs = []
    for i in range(num_runs):
        if strategy == "pall":
            output = await run_pall(source_text)
        else:
            output = await run_peach(source_text)
        output = insert_nulls(output)
        scores = compute_scores_sbert(source_text, output)
        runs.append({"output": output, "scores": scores, "run": i + 1})

    # Select best by sufficiency
    best = max(runs, key=lambda r: r["scores"]["sufficiency"])

    # Checklist
    checklist = compute_checklist(best["output"])

    # Warning
    warning = None
    if best["scores"]["sufficiency"] < 0.70:
        warning = f"Sufficiency score ({best['scores']['sufficiency']:.2f}) is below 0.70 — human review recommended."

    return JSONResponse({
        "standardized_srs": best["output"],
        "scores": best["scores"],
        "checklist": checklist,
        "warning": warning,
        "strategy_used": strategy,
        "runs_completed": len(runs),
        "best_run": best["run"],
        "word_count": word_count,
        "all_run_scores": [r["scores"] for r in runs],
    })

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "pdf_support": PDF_AVAILABLE,
        "docx_support": DOCX_AVAILABLE,
        "sbert_support": SBERT_AVAILABLE,
        "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }

# ─── Serve Frontend ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text())
