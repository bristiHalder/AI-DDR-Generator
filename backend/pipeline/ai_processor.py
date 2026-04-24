"""
ai_processor.py
Uses Google Gemini to extract structured data from inspection documents
and merge them into a unified DDR JSON structure.

Key design choices:
- Model fallback: gemini-1.5-flash → gemini-1.5-flash-8b → gemini-1.0-pro
- Smart text trimming to stay well within free-tier token limits
- Exponential backoff on 429 rate-limit errors
- Robust JSON extraction from messy LLM responses
"""

import json
import os
import re
import time
from typing import Any

from google import genai
from google.genai import types

# ── Model priority list (cheapest / fastest first) ────────────────────────────
# gemini-1.5-flash-8b has the HIGHEST free-tier quotas → try it first
# Falls through to flash, then pro if rate limited
MODEL_PRIORITY = [
    "gemini-1.5-flash-8b",
    "gemini-1.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
]

# ── Max chars sent to LLM ─────────────────────────────────────────────────────
# ~4 chars/token → 6000 chars ≈ 1500 tokens. Very safe for free tier.
# The PDFs are large but we extract the key info in the first/last sections.
MAX_TEXT_CHARS = 6_000


def _get_client() -> genai.Client:
    """Return a configured Gemini client."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or api_key == "your_gemini_api_key_here":
        raise RuntimeError(
            "GEMINI_API_KEY is not set. "
            "Add your key to backend/.env or enter it in the UI."
        )
    return genai.Client(api_key=api_key)


def _call_llm(prompt: str, retries: int = 2) -> str:
    """
    Call Gemini with model fallback and exponential back-off on rate limits.
    Returns the raw text response.
    """
    client = _get_client()

    for model in MODEL_PRIORITY:
        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=4096,
                    ),
                )
                print(f"[ai_processor] ✅ Success with model={model} attempt={attempt+1}")
                return response.text

            except Exception as e:
                err_str = str(e)
                is_quota = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                is_daily = "free_tier_requests" in err_str and "limit: 0" in err_str
                is_last_attempt = attempt == retries - 1

                if is_daily:
                    # Daily quota completely exhausted — skip immediately to next model
                    print(f"[ai_processor] ❌ Daily quota exhausted on {model}. Trying next model immediately.")
                    break
                elif is_quota:
                    wait = 20 * (2 ** attempt)   # 20s, 40s
                    print(
                        f"[ai_processor] ⚠️  Rate limit on {model} attempt {attempt+1}. "
                        f"Waiting {wait}s..."
                    )
                    if not is_last_attempt:
                        time.sleep(wait)
                    else:
                        print(f"[ai_processor] ❌ Giving up on {model}, trying next model.")
                        break
                else:
                    wait = 5 * (attempt + 1)
                    print(f"[ai_processor] ⚠️  Error on {model}: {e}. Waiting {wait}s...")
                    if not is_last_attempt:
                        time.sleep(wait)
                    else:
                        print(f"[ai_processor] ❌ Non-quota error, trying next model.")
                        break

    raise RuntimeError(
        "QUOTA_EXHAUSTED: All Gemini models have hit their daily free-tier quota. "
        "Please wait until quota resets (midnight PT) or upgrade your Google AI API key to a paid plan at https://aistudio.google.com/"
    )


def _trim_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    """
    Intelligently trim document text to stay within token limits.
    Keeps the first 60% and last 40% to preserve both header info and body.
    """
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.6)
    tail = max_chars - head
    trimmed = text[:head] + "\n\n[... content trimmed for length ...]\n\n" + text[-tail:]
    print(f"[ai_processor] ℹ️  Text trimmed from {len(text)} to {len(trimmed)} chars")
    return trimmed


def _extract_json(text: str) -> dict:
    """
    Robustly extract a JSON object from an LLM response.
    Handles markdown fences, leading/trailing noise, and partial outputs.
    """
    if not text:
        return {"parse_error": True, "raw": ""}

    # 1. Try to strip ```json ... ``` fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence_match:
        candidate = fence_match.group(1).strip()
    else:
        # 2. Find the first { ... } block
        brace_match = re.search(r"\{[\s\S]+\}", text)
        candidate = brace_match.group(0).strip() if brace_match else text.strip()

    # 3. Strip stray backticks
    candidate = candidate.lstrip("`").rstrip("`").strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # 4. Last resort: try to fix common LLM JSON issues (trailing commas)
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            print(f"[ai_processor] ⚠️  JSON parse failed: {e}")
            return {"parse_error": True, "raw": text[:600]}


# ──────────────────────────────────────────────────────────────────────────────
# PHASE 1a — Inspect Report Extraction
# ──────────────────────────────────────────────────────────────────────────────

INSPECTION_EXTRACTION_PROMPT = """\
You are an expert building inspection analyst. Read the following raw text from a SITE INSPECTION REPORT.

RULES:
1. Do NOT invent or assume any fact not in the text.
2. Use the exact string "Not Available" for any missing field.
3. Return ONLY a valid raw JSON object — no markdown, no explanation.

DOCUMENT TEXT:
{text}

Return exactly this JSON structure:
{{
  "property_info": {{
    "property_type": "...",
    "address": "...",
    "floors": "...",
    "property_age": "...",
    "inspection_date": "...",
    "inspected_by": "...",
    "previous_audit": "...",
    "previous_repairs": "..."
  }},
  "impacted_areas": [
    {{
      "area_id": "1",
      "area_name": "Area name",
      "negative_side_description": "Problem side observations",
      "positive_side_description": "Source side observations",
      "photo_references": ["Photo 1"],
      "checklist_findings": {{
        "leakage_type": "...",
        "concealed_plumbing": "...",
        "tile_issues": "...",
        "other": "..."
      }}
    }}
  ],
  "summary_table": [
    {{
      "point_no": "1",
      "impacted_area_description": "...",
      "exposed_area_description": "..."
    }}
  ],
  "general_checklist": {{
    "rcc_condition": "...",
    "external_wall_condition": "...",
    "plumbing_issues": "...",
    "paint_condition": "..."
  }},
  "missing_info": ["list any incomplete or unclear information"]
}}
"""


# ──────────────────────────────────────────────────────────────────────────────
# PHASE 1b — Thermal Report Extraction
# ──────────────────────────────────────────────────────────────────────────────

THERMAL_EXTRACTION_PROMPT = """\
You are a thermal imaging expert. Read the following raw text from a THERMAL INSPECTION REPORT.

RULES:
1. Do NOT invent any fact.
2. Use "Not Available" for any missing field.
3. Return ONLY a valid raw JSON object — no markdown, no explanation.

DOCUMENT TEXT:
{text}

Return exactly this JSON structure:
{{
  "device_info": {{
    "device": "...",
    "serial_number": "...",
    "emissivity": "...",
    "reflected_temperature": "..."
  }},
  "inspection_date": "...",
  "thermal_scans": [
    {{
      "scan_no": 1,
      "image_filename": "...",
      "hotspot_temp": "28.8 °C",
      "coldspot_temp": "23.4 °C",
      "delta_temp": "5.4 °C",
      "area_reference": "Hall / Room name if mentioned",
      "significance": "High delta indicates active moisture"
    }}
  ],
  "temperature_summary": {{
    "max_hotspot": "...",
    "min_coldspot": "...",
    "average_delta": "...",
    "high_concern_scans": ["scan numbers with delta > 4°C"]
  }},
  "missing_info": ["any unclear information"]
}}
"""


def extract_inspection_data(full_text: str) -> dict[str, Any]:
    """Extract structured data from the inspection report text."""
    trimmed = _trim_text(full_text, MAX_TEXT_CHARS)
    prompt = INSPECTION_EXTRACTION_PROMPT.format(text=trimmed)
    response = _call_llm(prompt)
    return _extract_json(response)


def extract_thermal_data(full_text: str) -> dict[str, Any]:
    """Extract structured data from the thermal report text."""
    trimmed = _trim_text(full_text, MAX_TEXT_CHARS)
    prompt = THERMAL_EXTRACTION_PROMPT.format(text=trimmed)
    response = _call_llm(prompt)
    return _extract_json(response)


# ──────────────────────────────────────────────────────────────────────────────
# PHASE 2 — Merge & Generate DDR
# ──────────────────────────────────────────────────────────────────────────────

DDR_MERGE_PROMPT = """\
You are a senior building diagnostics expert writing a professional Detailed Diagnostic Report (DDR) for a client.

You have two structured data sources below. Merge them into a unified DDR.

RULES:
1. Do NOT invent any facts. Only use information from the provided data.
2. Where information is missing, write exactly: "Not Available"
3. If two sources contradict each other, note the conflict explicitly.
4. Combine duplicate observations — do NOT repeat the same point twice.
5. Use plain, client-friendly English. No excessive jargon.
6. Assign severity: Critical / High / Moderate / Low — based only on evidence.
7. Return ONLY a valid raw JSON object — no markdown, no explanation.

=== INSPECTION REPORT DATA ===
{inspection_json}

=== THERMAL REPORT DATA ===
{thermal_json}

Return exactly this JSON structure:
{{
  "report_metadata": {{
    "property_type": "...",
    "address": "...",
    "inspection_date": "...",
    "inspected_by": "...",
    "report_generated_by": "AI DDR System",
    "floors": "...",
    "property_age": "...",
    "previous_audit": "...",
    "previous_repairs": "..."
  }},
  "property_issue_summary": "3-5 sentence plain-English executive summary for a non-technical client.",
  "area_observations": [
    {{
      "area_name": "Hall",
      "area_id": "1",
      "observations": [
        "Dampness observed at skirting level",
        "Paint peeling along the lower wall section"
      ],
      "thermal_findings": {{
        "scan_nos": ["1", "2"],
        "hotspot_temp": "28.8 °C",
        "coldspot_temp": "23.4 °C",
        "delta_temp": "5.4 °C",
        "interpretation": "Temperature gradient of 5.4°C indicates active moisture migration from adjacent bathroom"
      }},
      "image_refs": ["photo_1", "photo_2"],
      "thermal_image_refs": ["scan_1", "scan_2"],
      "probable_root_cause": "Plain-English explanation of the most likely cause",
      "severity": "High",
      "severity_reasoning": "Reasoning for the severity level assigned",
      "recommended_actions": [
        "Action step 1",
        "Action step 2"
      ]
    }}
  ],
  "overall_severity_assessment": {{
    "overall_level": "High",
    "reasoning": "Overall reasoning for the property-wide severity level",
    "urgent_areas": ["Hall", "Parking Area"]
  }},
  "additional_notes": "Any additional context or observations from the documents",
  "missing_or_unclear_info": [
    "Not Available: Property age not mentioned in inspection report"
  ],
  "conflicts_detected": [
    "No conflicts detected between sources"
  ]
}}
"""


def generate_ddr_json(
    inspection_data: dict[str, Any],
    thermal_data: dict[str, Any],
) -> dict[str, Any]:
    """Merge inspection + thermal data and generate the full DDR JSON."""

    # Serialize structured data — keep compact to save tokens
    insp_json = json.dumps(inspection_data, indent=None, ensure_ascii=False)
    therm_json = json.dumps(thermal_data, indent=None, ensure_ascii=False)

    # Trim if the structured JSONs themselves are very large
    insp_json = _trim_text(insp_json, 5_000)
    therm_json = _trim_text(therm_json, 4_000)

    prompt = DDR_MERGE_PROMPT.format(
        inspection_json=insp_json,
        thermal_json=therm_json,
    )
    response = _call_llm(prompt)
    return _extract_json(response)
