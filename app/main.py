"""
Newspaper Antisemitism Risk Ranking — FastAPI backend.
Uses background job processing so long analyses don't time out the browser.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime, date
from typing import List, Dict, Any, Optional
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, HTMLResponse
from sqlalchemy.orm import Session

from app.database import (
    create_tables, get_db, SessionLocal, upsert_analysis,
    get_all_analyses, get_historical_data,
)
from app.pdf_extractor import extract_text_from_pdf, build_analysis_context, parse_filename
from app.analyzer import analyze_newspaper, CATEGORIES
from app.report_gen import (
    generate_newspaper_json, generate_daily_excel,
    generate_ranking_excel, generate_ranking_csv,
    generate_evidence_excel, generate_evidence_csv, generate_evidence_json,
    generate_evidence_phrases_docx, generate_comprehensive_risk_docx,
    score_to_risk_level,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Newspaper Antisemitism Risk Analyzer", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ── In-memory job store ────────────────────────────────────────────────────────
# { job_id: { status, progress, message, results, errors, total, done } }
JOBS: Dict[str, Dict[str, Any]] = {}


@app.on_event("startup")
def on_startup():
    create_tables()
    logger.info("Database ready.")


# ── Frontend ───────────────────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent.parent / "static"

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    p = STATIC_DIR / "index.html"
    return HTMLResponse(p.read_text(encoding="utf-8") if p.exists()
                        else "<h1>Frontend not found</h1>", status_code=200 if p.exists() else 404)


# ── Background processing ──────────────────────────────────────────────────────

def _process_file(job_id: str, filename: str, file_bytes: bytes, idx: int, total: int):
    """Process a single PDF in a background thread. Updates JOBS[job_id] in place."""
    job = JOBS[job_id]

    def progress(pct: int, msg: str):
        # Overall progress = each file gets an equal slice
        slice_size = 90 / total
        base = (idx / total) * 90
        job["progress"] = int(base + slice_size * pct / 100)
        job["message"] = f"[{idx+1}/{total}] {filename}: {msg}"
        logger.info(job["message"])

    progress(0, "Parsing filename…")
    try:
        iso_date, newspaper = parse_filename(filename)
        analysis_date = date.fromisoformat(iso_date)
    except ValueError as e:
        job["errors"].append({"filename": filename, "error": str(e)})
        return

    progress(10, "Extracting text…")
    context_text = ""
    used_ocr = False
    pages_data = []

    try:
        pages_data, used_ocr = extract_text_from_pdf(file_bytes, filename)
        context_text = build_analysis_context(pages_data)
    except RuntimeError as e:
        job["errors"].append({"filename": filename, "newspaper": newspaper, "error": str(e)})
        return
    except Exception as e:
        job["errors"].append({"filename": filename, "newspaper": newspaper,
                               "error": f"Extraction failed: {e}"})
        return

    if not context_text.strip():
        job["errors"].append({"filename": filename, "newspaper": newspaper,
                               "error": "No text extracted — PDF may be corrupted."})
        return

    progress(30, "Sending to Claude for analysis…")
    try:
        analysis = analyze_newspaper(
            text=context_text,
            newspaper=newspaper,
            analysis_date=iso_date,
            filename=filename,
        )
    except Exception as e:
        job["errors"].append({"filename": filename, "newspaper": newspaper,
                               "error": f"Analysis failed: {e}"})
        return

    progress(85, "Saving to database…")
    try:
        db: Session = SessionLocal()
        record = upsert_analysis(
            db=db,
            newspaper=newspaper,
            analysis_date=analysis_date,
            category_scores=analysis["category_scores"],
            overall_rating=analysis["overall_rating"],
            evidence=analysis.get("evidence_items", []),
            source_filename=filename,
        )
        db.close()
        db_id = record.id
    except Exception as e:
        logger.error(f"DB upsert failed for {newspaper}: {e}")
        db_id = None

    result = {
        "newspaper": newspaper,
        "date": iso_date,
        "filename": filename,
        "overall_rating": analysis["overall_rating"],
        "risk_level": analysis.get("risk_level", score_to_risk_level(analysis["overall_rating"])),
        "confidence_overall": analysis.get("confidence_overall", ""),
        "main_drivers": analysis.get("main_drivers", ""),
        "mitigating_factors": analysis.get("mitigating_factors", ""),
        "category_scores": analysis["category_scores"],
        "evidence_items": analysis.get("evidence_items", []),
        "used_ocr": used_ocr,
        "extraction_mode": "ocr" if used_ocr else "text",
        "relevant_pages": sum(1 for p in pages_data if p.get("is_relevant")),
        "total_pages": len(pages_data),
        "db_id": db_id,
        "methodology_version": "1.0",
        "processing_timestamp": datetime.utcnow().isoformat(),
    }
    job["results"].append(result)
    progress(100, "Done ✓")


def _run_job(job_id: str, files_data: List[Dict]):
    """Main background thread: process all files sequentially."""
    job = JOBS[job_id]
    total = len(files_data)
    try:
        for idx, fd in enumerate(files_data):
            _process_file(job_id, fd["filename"], fd["bytes"], idx, total)
    except Exception as e:
        job["errors"].append({"error": f"Unexpected job error: {e}"})
        logger.exception(f"Job {job_id} crashed")
    finally:
        job["status"] = "done"
        job["progress"] = 100
        job["message"] = (f"Complete — {len(job['results'])} analyzed, "
                          f"{len(job['errors'])} failed")
        logger.info(f"Job {job_id} finished: {job['message']}")


# ── API: Submit job ────────────────────────────────────────────────────────────

@app.post("/api/analyze")
async def analyze_pdfs(files: List[UploadFile] = File(...)):
    """
    Accept PDF uploads, start background processing, return job_id immediately.
    Frontend polls /api/job/{job_id} for progress and results.
    """
    # Read all file bytes before spawning thread (UploadFile is not thread-safe)
    files_data = []
    errors = []
    for upload in files:
        try:
            iso_date, newspaper = parse_filename(upload.filename)
        except ValueError as e:
            errors.append({"filename": upload.filename, "error": str(e)})
            continue
        file_bytes = await upload.read()
        files_data.append({"filename": upload.filename, "bytes": file_bytes})

    if not files_data:
        return {"job_id": None, "errors": errors,
                "message": "No valid files to process"}

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "running",
        "progress": 0,
        "message": f"Queued {len(files_data)} file(s)…",
        "results": [],
        "errors": errors,
        "total": len(files_data),
    }

    t = threading.Thread(target=_run_job, args=(job_id, files_data), daemon=True)
    t.start()

    return {"job_id": job_id, "total": len(files_data), "errors": errors}


# ── API: Poll job status ───────────────────────────────────────────────────────

@app.get("/api/job/{job_id}")
async def get_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, f"Job {job_id} not found")
    job = JOBS[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "results": job["results"],
        "errors": job["errors"],
        "total": job.get("total", 0),
        "done_count": len(job["results"]),
    }


# ── API: Historical data & results ─────────────────────────────────────────────

@app.get("/api/history")
async def get_history(db: Session = Depends(get_db)):
    data = get_historical_data(db)
    return {"data": data, "count": len(data)}


@app.get("/api/results")
async def get_results(db: Session = Depends(get_db)):
    records = get_all_analyses(db)
    out = []
    for r in records:
        out.append({
            "id": r.id,
            "newspaper": r.newspaper,
            "date": r.date.isoformat(),
            "overall_rating": r.overall_rating,
            "risk_level": score_to_risk_level(r.overall_rating),
            "category_scores": json.loads(r.category_scores),
            "evidence": json.loads(r.evidence) if r.evidence else [],
            "source_filename": r.source_filename,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })
    return {"results": out, "count": len(out)}


# ── API: Exports ───────────────────────────────────────────────────────────────

@app.post("/api/export/json/{newspaper}")
async def export_newspaper_json(newspaper: str, payload: dict):
    data = generate_newspaper_json(
        newspaper=payload["newspaper"], analysis_date=payload["date"],
        analysis=payload, filename=payload.get("filename", ""),
    )
    safe = newspaper.replace(" ", "_")
    return Response(content=data, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{safe}_{payload["date"]}.json"'})


@app.post("/api/export/daily-excel")
async def export_daily_excel(payload: dict):
    data = generate_daily_excel(payload.get("results", []), payload.get("date", ""))
    fname = f"Daily_Antisemitism_Risk_Dataset_{payload.get('date','')}.xlsx"
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/export/ranking-excel")
async def export_ranking_excel(payload: dict):
    data = generate_ranking_excel(payload.get("results", []))
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="Spanish_Newspapers_Antisemitic_Framing_Risk_Ranking.xlsx"'})


@app.post("/api/export/ranking-csv")
async def export_ranking_csv(payload: dict):
    data = generate_ranking_csv(payload.get("results", []))
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="Spanish_Newspapers_Antisemitic_Framing_Risk_Ranking.csv"'})


@app.post("/api/export/evidence-excel")
async def export_evidence_excel(payload: dict):
    data = generate_evidence_excel(payload.get("results", []))
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="Expanded_Evidence_Table_With_Pages_and_Articles.xlsx"'})


@app.post("/api/export/evidence-csv")
async def export_evidence_csv(payload: dict):
    data = generate_evidence_csv(payload.get("results", []))
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="Expanded_Evidence_Table_With_Pages_and_Articles.csv"'})


@app.post("/api/export/evidence-json")
async def export_evidence_json(payload: dict):
    data = generate_evidence_json(payload.get("results", []))
    return Response(content=data, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="Expanded_Evidence_Table_With_Pages_and_Articles.json"'})


# ── API: DOCX reports by date ──────────────────────────────────────────────────

def _results_for_date(analysis_date: str) -> list:
    """Fetch all analyses for a given date from DB and format as result dicts."""
    db: Session = SessionLocal()
    try:
        records = get_all_analyses(db)
        out = []
        for r in records:
            if r.date.isoformat() != analysis_date:
                continue
            cat_scores = json.loads(r.category_scores) if r.category_scores else {}
            evidence   = json.loads(r.evidence) if r.evidence else []
            out.append({
                "newspaper":        r.newspaper,
                "date":             r.date.isoformat(),
                "overall_rating":   r.overall_rating,
                "risk_level":       score_to_risk_level(r.overall_rating),
                "category_scores":  cat_scores,
                "evidence_items":   evidence,
                "main_drivers":     "",
                "mitigating_factors": "",
                "confidence_overall": "Moderate",
                "total_pages":      0,
                "extraction_mode":  "text",
            })
        return out
    finally:
        db.close()


@app.get("/api/export/evidence-phrases-docx")
async def export_evidence_phrases_docx(date: str):
    """Generate Evidence Phrases & FRS Formula Walk-Through .docx for a date."""
    results = _results_for_date(date)
    if not results:
        raise HTTPException(404, f"No analyses found for date {date}")
    try:
        data = generate_evidence_phrases_docx(results, date)
    except Exception as e:
        logger.exception(f"evidence-phrases-docx generation failed for {date}")
        raise HTTPException(500, f"Report generation error: {e}")
    fname = f"Evidence_Phrases_and_FRS_Formula_Report_{date}.docx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/export/comprehensive-risk-docx")
async def export_comprehensive_risk_docx(date: str):
    """Generate Comprehensive Antisemitic Framing Risk Review .docx for a date."""
    results = _results_for_date(date)
    if not results:
        raise HTTPException(404, f"No analyses found for date {date}")
    try:
        data = generate_comprehensive_risk_docx(results, date)
    except Exception as e:
        logger.exception(f"comprehensive-risk-docx generation failed for {date}")
        raise HTTPException(500, f"Report generation error: {e}")
    fname = f"Comprehensive_Antisemitic_Framing_Risk_Report_{date}.docx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── API: Health & metadata ─────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/categories")
async def get_categories():
    return {"categories": [
        {"key": k, "label": v["label"], "weight": v["weight"]}
        for k, v in CATEGORIES.items()
    ]}
