"""
main.py — FastAPI Backend for DDR Generation System
Handles file uploads, runs the AI pipeline, and serves generated reports.
"""

import json
import os
import sys
import uuid
import asyncio
import concurrent.futures
from pathlib import Path

# Force UTF-8 output on Windows (prevents charmap encode errors from Unicode in logs/docstrings)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
from pipeline.ai_processor import generate_ddr_direct
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

# Shared thread pool — reused across all jobs for efficiency
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=6)


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

def run_pipeline(job_id: str, inspection_path: str, thermal_path: str, api_key: str, provider: str = "gemini"):
    """
    Full DDR generation pipeline.

    Timeline (typical with paid key):
      ~5s   PDF extraction  (parallel)
      ~15s  AI extraction   (parallel - both docs at once)
      ~10s  AI merge
      ~2s   HTML render
      -----------------
      ~32s  total
    """
    import time as _time
    t0 = _time.time()

    def _tick(label: str) -> None:
        print(f"[pipeline] {label} | {_time.time()-t0:.1f}s elapsed")

    def _set(status: str, pct: int, msg: str) -> None:
        jobs[job_id].update({"status": status, "progress": pct, "message": msg})

    try:
        # ── STEP 1: Extract both PDFs in parallel (no API cost) ───────────
        _set("extracting_pdfs", 15, "Reading and extracting both PDFs...")
        _tick("PDF extraction start")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fi = pool.submit(extract_pdf, inspection_path)
            ft = pool.submit(extract_pdf, thermal_path)
            inspection_data = fi.result()
            thermal_data    = ft.result()

        _tick(f"PDF done: insp={inspection_data['total_pages']}p/{len(inspection_data['all_images'])}imgs  therm={thermal_data['total_pages']}p/{len(thermal_data['all_images'])}imgs")

        # ── STEP 2: ONE LLM call generates the complete DDR ───────────────
        # (Previously 3 calls — now 1, saving 67% of API quota)
        _set("ai_extraction", 40, "AI reading both documents and generating DDR...")
        _tick("Single LLM call start")

        ddr_json = generate_ddr_direct(
            inspection_data["full_text"],
            thermal_data["full_text"],
            api_key=api_key,
            provider=provider,
        )

        _tick("LLM call done")

        # ── STEP 3: Assign images + render HTML ───────────────────────────
        _set("assembling_report", 82, "Embedding images and rendering report...")

        final_report = build_final_report(ddr_json, inspection_data, thermal_data)

        html_content = render_html_report(final_report)
        html_path    = OUTPUTS_DIR / f"{job_id}.html"
        html_path.write_text(html_content, encoding="utf-8")

        # Lean JSON without large base64 blobs (those live in the HTML)
        json_safe = {k: v for k, v in final_report.items() if k != "area_observations"}
        json_safe["area_observations"] = [
            {k2: v2 for k2, v2 in area.items()
             if k2 not in ("assigned_images", "assigned_thermal_images")}
            for area in final_report.get("area_observations", [])
        ]
        (OUTPUTS_DIR / f"{job_id}.json").write_text(
            json.dumps(json_safe, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        jobs[job_id].update({
            "status":    "completed",
            "progress":  100,
            "message":   "DDR Report generated successfully!",
            "report_id": job_id,
        })
        _tick("DONE")

    except Exception as e:
        err = str(e)
        if "Invalid Gemini API key" in err or "API_KEY_INVALID" in err:
            user_msg = (
                "Invalid API key. Please check the key you entered and try again. "
                "Get a valid key at https://aistudio.google.com/apikey"
            )
        elif "QUOTA_EXHAUSTED" in err or "RESOURCE_EXHAUSTED" in err or "429" in err:
            user_msg = (
                "Gemini API quota hit. The free tier has daily limits. "
                "Try: (1) wait 60s and retry, (2) try tomorrow when quota resets, "
                "or (3) use a paid key at https://aistudio.google.com/"
            )
        else:
            user_msg = f"Pipeline failed: {err}"

        jobs[job_id].update({"status": "failed", "progress": 0, "message": user_msg})
        print(f"[ERROR] Job {job_id} provider={provider}: {err}")




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
    x_gemini_key:   Optional[str] = Header(None, alias="X-Gemini-Key"),
    x_api_key:      Optional[str] = Header(None, alias="X-Api-Key"),
    x_ai_provider:  Optional[str] = Header(None, alias="X-AI-Provider"),
):
    """
    Upload two PDFs and kick off the DDR generation pipeline.
    Supports multiple AI providers via X-AI-Provider header:
      gemini     -- Google Gemini (needs X-Gemini-Key or X-Api-Key)
      groq       -- Groq Cloud   (needs X-Api-Key, free at console.groq.com)
      ollama     -- Local Ollama (no key needed)
      openrouter -- OpenRouter   (needs X-Api-Key)
    Returns a job_id to poll for status.
    """
    provider = (x_ai_provider or os.getenv("PROVIDER", "gemini")).lower().strip()

    # Resolve API key based on provider
    if provider == "ollama":
        api_key = ""  # Ollama is local, no key needed
    else:
        api_key = x_api_key or x_gemini_key or os.getenv("GEMINI_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail=f"API key required for provider '{provider}'. "
                       "Pass it via the X-Api-Key header or set it in backend/.env"
            )

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

    # Run pipeline in shared thread pool (non-blocking)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        _EXECUTOR,
        run_pipeline,
        job_id,
        str(inspection_path),
        str(thermal_path),
        api_key,    # passed explicitly -- thread-safe
        provider,   # gemini / groq / ollama / openrouter
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

    # Convert HTML → PDF using Playwright (pixel-perfect modern CSS rendering)
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # Load the local HTML file into the headless browser
            file_url = f"file:///{html_path.resolve().as_posix()}"
            await page.goto(file_url, wait_until="networkidle")
            
            # Wait for base64 images to be fully decoded and painted by the browser
            await page.evaluate('''
                async () => {
                    const images = Array.from(document.images);
                    await Promise.all(images.map(img => {
                        if (img.complete) return Promise.resolve();
                        return new Promise(resolve => { img.onload = resolve; img.onerror = resolve; });
                    }));
                }
            ''')
            await page.wait_for_timeout(2000) # Give Chromium 2 seconds to paint the layout
            
            # Generate a gorgeous, styled PDF
            await page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"}
            )
            await browser.close()
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
        "gemini_configured": bool(api_key and api_key != "your_gemini_api_key_here"),
        "active_jobs": len(jobs),
    }
