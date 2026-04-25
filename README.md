# DDR AI — Detailed Diagnostic Report Generator

> **AI-powered system** that reads raw site inspection PDFs and thermal imaging reports, then generates a structured, client-ready **Detailed Diagnostic Report (DDR)** — with images, severity assessments, and actionable recommendations.

Built for the **Applied AI Builder** assignment evaluation.

---

## 🚀 Live Demo

> **[➜ Click to open the live app](http://localhost:8000)**  
> *(Replace with your deployed URL)*

---

## 🎬 Demo Video

> **[➜ Watch the Loom walkthrough](#)**  
> *(3–5 min: what it does, how it works, limitations, improvements)*

---

## 🧠 How It Works

```
User uploads 2 PDFs
        │
        ▼
PDF Extraction (PyMuPDF — < 1 second)
• Extract full text from both PDFs
• Extract up to 30 inspection/thermal images per PDF
• Clean text (remove headers, page numbers, boilerplate)
        │
        ▼
AI Extraction & Merging (Groq / Gemini / Ollama / OpenRouter)
• Analyze Inspection Report → structured JSON
• Analyze Thermal Report → structured JSON
• Combine both JSONs into unified DDR
• Deduplicate overlapping observations, flag conflicts, and assign severity
• Generate plain-English recommendations
        │
        ▼
Report Assembly & PDF Generation
• Assign inspection photos & thermal scans → relevant area sections
• Render professional HTML report (Jinja2 template)
• Generate Pixel-Perfect PDF via headless browser (Playwright)
• Available as: live preview | HTML download | PDF download
```

---

## 📋 DDR Output Sections

| # | Section | Content |
|---|---|---|
| 1 | **Property Issue Summary** | 3–5 sentence executive overview for non-technical clients |
| 2 | **Area-wise Observations** | Per-area findings + site photos + thermal readings |
| 3 | **Probable Root Cause** | AI-reasoned explanation per area |
| 4 | **Severity Assessment** | Critical/High/Moderate/Low with reasoning table |
| 5 | **Recommended Actions** | Numbered action steps per area |
| 6 | **Additional Notes** | Extra context from source documents |
| 7 | **Missing / Unclear Info** | Explicit "Not Available" flags + conflict notes |

---

## ⚡ Quick Start

### 1. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
playwright install chromium
```

### 2. Run the Server

```bash
cd backend
python -m uvicorn main:app --reload --port 8096
```

### 3. Open the App & Configure AI

```
http://localhost:8096
```

1. Select your preferred **AI Provider** from the dropdown (`Groq`, `Gemini`, `Ollama`, or `OpenRouter`).
2. Paste your free API key into the UI (No `.env` configuration required!).
3. Upload your **Inspection Report PDF** + **Thermal Report PDF**.
4. Click **Generate DDR Report** → Download your pixel-perfect PDF!

---

## 📁 Project Structure

```
assignmn/
├── backend/
│   ├── main.py                  # FastAPI app + API endpoints
│   ├── pipeline/
│   │   ├── pdf_extractor.py     # PDF text + image extraction (PyMuPDF)
│   │   ├── ai_processor.py      # Gemini LLM extraction + DDR merge
│   │   └── report_builder.py    # Image assignment + report assembly
│   ├── templates/
│   │   └── ddr_report.html      # Professional Jinja2 DDR template
│   ├── uploads/                 # Temporary PDF storage (auto-cleaned)
│   ├── outputs/                 # Generated reports (HTML + PDF)
│   └── requirements.txt
├── frontend/
│   ├── index.html               # Dark-mode drag-and-drop UI
│   ├── style.css                # Premium glassmorphism design
│   └── app.js                   # Upload, polling, progress, preview logic
├── Dockerfile                   # For Railway / Render deployment
├── .env.example                 # API key template
└── README.md
```

---

## 🌐 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/generate` | Upload 2 PDFs, start the pipeline |
| `GET` | `/status/{job_id}` | Poll job progress (0–100%) |
| `GET` | `/report/{job_id}` | View HTML report in browser |
| `GET` | `/download/{job_id}/html` | Download report as HTML |
| `GET` | `/download/{job_id}/pdf` | Download report as PDF |
| `GET` | `/health` | Health check + API key status |

---

## 🛡 AI Guardrails

Every LLM prompt enforces:

| Rule | Implementation |
|---|---|
| No hallucination | `"Do NOT invent any fact not present in the text"` |
| Missing data | Returns exactly `"Not Available"` |
| Conflict detection | `"If sources contradict, note the conflict explicitly"` |
| Client language | `"Use plain English, avoid technical jargon"` |
| Retry on failure | Exponential backoff + model fallback chain |

**Model fallback chain** (quota-aware):  
`gemini-1.5-flash-8b` → `gemini-1.5-flash` → `gemini-2.0-flash-lite` → `gemini-1.5-pro`

---

## 🖼 Image Handling

- Extracted directly from source PDFs using **PyMuPDF**
- Capped at **30 images per PDF** to prevent memory issues
- Downscaled to max **800px** for performance
- Inspection photos placed under corresponding area sections
- Thermal scans placed alongside thermal readings
- Missing images → `"Image Not Available"` displayed

---

## 🚢 Deploy to Railway (Free)

```bash
# 1. Push to GitHub
git push origin main

# 2. Go to railway.app → New Project → Deploy from GitHub repo
# 3. Add environment variable: GEMINI_API_KEY = your_key
# 4. Railway auto-builds using the Dockerfile
# 5. Your live URL is ready in ~2 minutes
```

---

## ⚠️ Limitations

1. **Scanned PDFs** (image-only, no text layer) — text extraction returns empty; OCR not yet implemented
2. **Free-tier API quota** — ~1,500 tokens/min; large PDFs may hit daily limits (resets midnight PT)
3. **Image ordering** — thermal-to-area mapping is sequential, not label-matched
4. **Context window** — very long PDFs are trimmed to 6,000 chars before LLM processing

---

## 🔮 Future Improvements

- [ ] OCR for scanned PDFs (Tesseract / Google Document AI)
- [ ] Semantic image-to-area matching via CLIP embeddings
- [ ] LangChain + vector store for multi-document retrieval
- [ ] Automatic report comparison across inspection dates
- [ ] Multi-language DDR output
- [ ] Webhook-based async job notifications

---

## 🔧 Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn |
| PDF Parsing | PyMuPDF (fitz) |
| AI / LLM Pipeline | Groq (Llama 3), Google Gemini, Ollama, OpenRouter |
| Templating | Jinja2 |
| PDF Export | Playwright (Headless Chromium) |
| Frontend | HTML5, Vanilla CSS, Vanilla JavaScript |
| Deployment | Docker → Railway / Render |

---

## 📝 Assignment Notes

Built as part of the **Applied AI Builder (DDR Report Generation)** evaluation.  
The system generalizes to any inspection + thermal report pair — no hardcoded field names or regex rules.

