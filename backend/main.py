"""
main.py — FastAPI Backend for DDR Generation System
Handles file uploads, runs the AI pipeline, and serves generated reports.
"""

import json
import os
import uuid
import asyncio
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from dotenv import load_dotenv
import aiofiles
from typing import Optional

# Load environment variables
load_dotenv()

# Import pipeline modules
from pipeline.pdf_extractor import extract_pdf
from pipeline.ai_processor import extract_inspection_data, extract_thermal_data, generate_ddr_json
from pipeline.report_builder import build_final_report

# ── App Setup ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="DDR Report Generation API",
    description="AI-powered Detailed Diagnostic Report generator",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories
BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
TEMPLATES_DIR = BASE_DIR / "templates"

UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# Mount frontend directory for static files (CSS, JS)
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if FRONTEND_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# In-memory job status store
jobs: dict[str, dict] = {}


# ── Utility ────────────────────────────────────────────────────────────────────

def render_html_report(ddr_data: dict) -> str:
    """Render the DDR data into an HTML report using Jinja2."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("ddr_report.html")
    return template.render(ddr=ddr_data)


async def save_upload(upload_file: UploadFile, dest: Path) -> None:
    """Save an uploaded file to disk asynchronously."""
    async with aiofiles.open(dest, "wb") as f:
        content = await upload_file.read()
        await f.write(content)


# ── Background Pipeline ────────────────────────────────────────────────────────

def run_pipeline(job_id: str, inspection_path: str, thermal_path: str):
    """
    Full DDR generation pipeline (runs in background thread).
    Updates jobs[job_id] with progress and results.
    """
    try:
        jobs[job_id]["status"] = "extracting_pdfs"
        jobs[job_id]["progress"] = 10
        jobs[job_id]["message"] = "Extracting text and images from PDFs..."

        inspection_data = extract_pdf(inspection_path)
        thermal_data = extract_pdf(thermal_path)

        jobs[job_id]["status"] = "ai_extraction"
        jobs[job_id]["progress"] = 35
        jobs[job_id]["message"] = "AI is analyzing the inspection report..."

        inspection_structured = extract_inspection_data(inspection_data["full_text"])

        jobs[job_id]["progress"] = 55
        jobs[job_id]["message"] = "AI is analyzing the thermal report..."

        thermal_structured = extract_thermal_data(thermal_data["full_text"])

        jobs[job_id]["status"] = "generating_ddr"
        jobs[job_id]["progress"] = 70
        jobs[job_id]["message"] = "Merging data and generating DDR..."

        ddr_json = generate_ddr_json(inspection_structured, thermal_structured)

        jobs[job_id]["status"] = "assembling_report"
        jobs[job_id]["progress"] = 85
        jobs[job_id]["message"] = "Assembling final report with images..."

        final_report = build_final_report(ddr_json, inspection_data, thermal_data)

        # Save JSON output
        json_path = OUTPUTS_DIR / f"{job_id}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(final_report, f, indent=2, ensure_ascii=False)

        # Render HTML
        html_content = render_html_report(final_report)
        html_path = OUTPUTS_DIR / f"{job_id}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["message"] = "DDR Report generated successfully!"
        jobs[job_id]["report_id"] = job_id

    except Exception as e:
        err_str = str(e)
        # Provide a clear, actionable message for quota errors
        if "QUOTA_EXHAUSTED" in err_str or "RESOURCE_EXHAUSTED" in err_str or "429" in err_str:
            user_msg = (
                "⚠️ Gemini API quota exhausted. "
                "The free tier allows ~1,500 tokens/minute and limited daily requests. "
                "Solutions: (1) Wait ~60 seconds and retry, (2) Try again tomorrow when quota resets, "
                "or (3) Upgrade to a paid Gemini API key at https://aistudio.google.com/"
            )
        else:
            user_msg = f"Pipeline failed: {err_str}"
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["progress"] = 0
        jobs[job_id]["message"] = user_msg
        print(f"[ERROR] Job {job_id} failed: {e}")


# ── API Endpoints ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the frontend."""
    frontend_path = BASE_DIR.parent / "frontend" / "index.html"
    if frontend_path.exists():
        return HTMLResponse(content=frontend_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>DDR API Running. Use POST /generate to create reports.</h1>")


@app.post("/generate")
async def generate_report(
    background_tasks: BackgroundTasks,
    inspection_report: UploadFile = File(..., description="Site Inspection Report PDF"),
    thermal_report: UploadFile = File(..., description="Thermal Images Report PDF"),
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key"),
):
    """
    Upload two PDFs and kick off the DDR generation pipeline.
    Returns a job_id to poll for status.
    """
    # Configure Gemini with key from header (fallback to env)
    api_key = x_gemini_key or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is required. Set GEMINI_API_KEY in .env or pass X-Gemini-Key header.")
    os.environ["GEMINI_API_KEY"] = api_key  # propagate to pipeline modules (google-genai picks up env var)

    # Validate file types
    for f in [inspection_report, thermal_report]:
        if not f.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"File must be a PDF: {f.filename}")

    job_id = str(uuid.uuid4())
    inspection_path = UPLOADS_DIR / f"{job_id}_inspection.pdf"
    thermal_path = UPLOADS_DIR / f"{job_id}_thermal.pdf"

    # Save uploaded files
    await save_upload(inspection_report, inspection_path)
    await save_upload(thermal_report, thermal_path)

    # Initialize job
    jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "message": "Job queued. Starting pipeline...",
        "report_id": None,
    }

    # Run pipeline in background thread (avoids blocking the async event loop)
    import concurrent.futures
    loop = asyncio.get_event_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    loop.run_in_executor(
        executor,
        run_pipeline,
        job_id,
        str(inspection_path),
        str(thermal_path),
    )

    return JSONResponse({"job_id": job_id, "message": "Pipeline started"})


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    """Poll the status of a DDR generation job."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(jobs[job_id])


@app.get("/report/{job_id}", response_class=HTMLResponse)
async def get_report_html(job_id: str):
    """Get the generated DDR as an HTML page."""
    html_path = OUTPUTS_DIR / f"{job_id}.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Report not found or not yet generated")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/download/{job_id}/html")
async def download_html(job_id: str):
    """Download the generated DDR as an HTML file."""
    html_path = OUTPUTS_DIR / f"{job_id}.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Report not generated yet")
    return FileResponse(
        path=str(html_path),
        media_type="text/html",
        filename=f"DDR_Report_{job_id[:8]}.html",
    )


@app.get("/download/{job_id}/pdf")
async def download_pdf(job_id: str):
    """Download the generated DDR as a PDF file."""
    html_path = OUTPUTS_DIR / f"{job_id}.html"
    pdf_path = OUTPUTS_DIR / f"{job_id}.pdf"

    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Report not generated yet")

    # Convert HTML → PDF using xhtml2pdf (works on Windows without GTK)
    try:
        from xhtml2pdf import pisa
        html_content = html_path.read_text(encoding="utf-8")
        with open(pdf_path, "wb") as pdf_file:
            pisa_status = pisa.CreatePDF(html_content, dest=pdf_file)
        if pisa_status.err:
            raise RuntimeError("xhtml2pdf conversion error")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF conversion failed: {e}")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"DDR_Report_{job_id[:8]}.pdf",
    )


@app.get("/health")
async def health_check():
    """Simple health check."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    return {
        "status": "healthy",
        "gemini_configured": bool(api_key),
        "active_jobs": len(jobs),
    }
