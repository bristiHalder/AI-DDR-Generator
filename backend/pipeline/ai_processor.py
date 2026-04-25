# -*- coding: utf-8 -*-
"""
ai_processor.py  -- Multi-provider LLM support for DDR generation.

Supported providers (set PROVIDER in .env or pass via UI):
  gemini   -- Google Gemini (gemini-2.0-flash)         [default]
  groq     -- Groq Cloud    (llama-3.3-70b-versatile)  [free, fast, recommended]
  ollama   -- Local Ollama  (llama3 / mistral)          [fully offline, no limits]
  openrouter -- OpenRouter  (free credits on signup)

Single-call design: ONE LLM call per run -> complete DDR JSON.
API key passed explicitly as function arg (thread-safe).
"""

import json
import os
import re
import time
from typing import Any


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Default provider -- can be overridden by PROVIDER env var or UI selection
DEFAULT_PROVIDER = os.getenv("PROVIDER", "gemini").lower()

# Max chars sent per document (~750 tokens each)
MAX_DOC_CHARS = 3_000

# Ollama local endpoint
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "llama3")

# Groq models (in order of preference)
GROQ_MODELS = [
    "llama-3.3-70b-versatile",   # best quality, generous free tier
    "llama-3.1-8b-instant",      # faster, smaller
    "mixtral-8x7b-32768",        # good for long context
]

# Gemini models (2.x only -- 1.5 is retired)
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
]


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, api_key: str) -> str:
    """Call Google Gemini API."""
    from google import genai
    from google.genai import types

    if not api_key or api_key == "your_gemini_api_key_here":
        raise RuntimeError(
            "Gemini API key not set. Enter it in the UI or add GEMINI_API_KEY to .env"
        )

    cl = genai.Client(api_key=api_key)

    for model in GEMINI_MODELS:
        try:
            resp = cl.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=4096,
                ),
            )
            print(f"[llm] OK  gemini/{model}")
            return resp.text

        except Exception as exc:
            s = str(exc)
            if "API_KEY_INVALID" in s or "API key not valid" in s:
                raise RuntimeError(
                    "Invalid Gemini API key. Get one at https://aistudio.google.com/apikey"
                )
            if "404" in s or "NOT_FOUND" in s:
                print(f"[llm] SKIP gemini/{model}: not found")
                continue
            if ("limit: 0" in s or "free_tier" in s) and ("429" in s or "RESOURCE_EXHAUSTED" in s):
                print(f"[llm] SKIP gemini/{model}: daily quota = 0")
                continue
            if "429" in s or "RESOURCE_EXHAUSTED" in s:
                print(f"[llm] WAIT gemini/{model}: rate limited, waiting 15s")
                time.sleep(15)
                continue
            print(f"[llm] ERR  gemini/{model}: {s[:150]}")
            continue

    raise RuntimeError(
        "QUOTA_EXHAUSTED: All Gemini models unavailable. "
        "Switch to Groq (free) or Ollama (local). See README."
    )


def _call_groq(prompt: str, api_key: str) -> str:
    """Call Groq Cloud API (free tier: 14,400 req/day, very fast)."""
    try:
        from groq import Groq
    except ImportError:
        raise RuntimeError(
            "groq package not installed. Run: pip install groq"
        )

    if not api_key or api_key == "your_groq_key_here":
        raise RuntimeError(
            "Groq API key not set. "
            "Get a FREE key at https://console.groq.com (no credit card needed)"
        )

    client = Groq(api_key=api_key)

    for model in GROQ_MODELS:
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=4096,
            )
            result = completion.choices[0].message.content
            print(f"[llm] OK  groq/{model}")
            return result

        except Exception as exc:
            s = str(exc)
            if "invalid_api_key" in s.lower() or "401" in s:
                raise RuntimeError(
                    "Invalid Groq API key. Get one free at https://console.groq.com"
                )
            if "rate_limit" in s.lower() or "429" in s:
                print(f"[llm] WAIT groq/{model}: rate limited, trying next model")
                continue
            if "model_not_found" in s.lower() or "404" in s:
                print(f"[llm] SKIP groq/{model}: model not found")
                continue
            print(f"[llm] ERR  groq/{model}: {s[:150]}")
            continue

    raise RuntimeError("All Groq models failed. Check your API key at https://console.groq.com")


def _call_ollama(prompt: str, model: str = None) -> str:
    """
    Call local Ollama instance (no API key needed, runs fully offline).
    Install: https://ollama.com  then run: ollama pull llama3
    """
    import urllib.request
    import urllib.error

    model = model or OLLAMA_MODEL
    url   = f"{OLLAMA_BASE_URL}/api/generate"
    data  = json.dumps({
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 4096},
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            text = result.get("response", "")
            print(f"[llm] OK  ollama/{model}")
            return text
    except urllib.error.URLError:
        raise RuntimeError(
            "Cannot connect to Ollama. Make sure Ollama is running: "
            "1) Install from https://ollama.com  "
            "2) Run: ollama serve  "
            "3) Pull a model: ollama pull llama3"
        )
    except Exception as exc:
        raise RuntimeError(f"Ollama error: {exc}")


def _call_openrouter(prompt: str, api_key: str) -> str:
    """Call OpenRouter (aggregates many free/cheap models)."""
    try:
        import urllib.request
        url = "https://openrouter.ai/api/v1/chat/completions"
        data = json.dumps({
            "model": "meta-llama/llama-3.1-8b-instruct:free",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 4096,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
            }
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError(f"OpenRouter error: {exc}")


# ---------------------------------------------------------------------------
# Unified call dispatcher
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, api_key: str = "", provider: str = "") -> str:
    """
    Route to the correct provider based on provider name.
    provider can be: gemini, groq, ollama, openrouter
    """
    p = (provider or DEFAULT_PROVIDER).lower().strip()

    print(f"[llm] Using provider={p}")

    if p == "groq":
        return _call_groq(prompt, api_key)
    elif p == "ollama":
        return _call_ollama(prompt)
    elif p == "openrouter":
        return _call_openrouter(prompt, api_key)
    else:  # default: gemini
        return _call_gemini(prompt, api_key)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _trim(text: str, limit: int = MAX_DOC_CHARS) -> str:
    """Keep first 65% + last 35% to stay within token budget."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.65)
    tail = limit - head
    return text[:head] + "\n[...]\n" + text[-tail:]


def _parse_json(raw: str) -> dict:
    """Robustly extract JSON from LLM response."""
    if not raw:
        return {"parse_error": True, "raw": ""}

    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    candidate = m.group(1) if m else raw

    if not candidate.strip().startswith("{"):
        m2 = re.search(r"\{[\s\S]+\}", candidate)
        candidate = m2.group(0) if m2 else candidate

    candidate = candidate.strip().lstrip("`").rstrip("`").strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            print(f"[llm] JSON parse failed: {e} | first 300: {candidate[:300]}")
            return {"parse_error": True, "raw": raw[:600]}


# ---------------------------------------------------------------------------
# DDR prompt
# ---------------------------------------------------------------------------

_DDR_PROMPT = """\
You are a senior building diagnostics expert writing a Detailed Diagnostic Report (DDR).

You are given two raw document texts:
1. SITE INSPECTION REPORT
2. THERMAL IMAGING REPORT

STRICT RULES:
- Do NOT invent any fact. Only use information present in the documents.
- If a field is missing: write exactly "Not Available".
- If sources conflict: note the conflict explicitly.
- No duplicate observations. Merge related points into one.
- Plain English only. No technical jargon. Client-friendly language.
- Severity: Critical / High / Moderate / Low (evidence-based only).
- Output ONLY a single raw JSON object. No markdown. No explanation.

=== SITE INSPECTION REPORT ===
{inspection_text}

=== THERMAL IMAGING REPORT ===
{thermal_text}

Return this JSON structure (fill ALL fields):
{{
  "report_metadata": {{
    "property_type": "",
    "address": "",
    "inspection_date": "",
    "inspected_by": "",
    "report_generated_by": "AI DDR System",
    "floors": "",
    "property_age": "",
    "previous_audit": "",
    "previous_repairs": ""
  }},
  "property_issue_summary": "3-5 plain English sentences summarising ALL findings for a non-technical client.",
  "area_observations": [
    {{
      "area_name": "e.g. Hall / Kitchen / Bathroom",
      "area_id": "1",
      "observations": ["Specific observation 1", "Specific observation 2"],
      "thermal_findings": {{
        "scan_nos": ["1"],
        "hotspot_temp": "e.g. 28.8 degC",
        "coldspot_temp": "e.g. 23.4 degC",
        "delta_temp": "e.g. 5.4 degC",
        "interpretation": "Plain English meaning of the temperature difference"
      }},
      "image_refs": [],
      "thermal_image_refs": [],
      "probable_root_cause": "Most likely cause in plain English",
      "severity": "High",
      "severity_reasoning": "Why this severity level was chosen",
      "recommended_actions": ["Specific action step 1", "Specific action step 2"]
    }}
  ],
  "overall_severity_assessment": {{
    "overall_level": "High",
    "reasoning": "Overall reasoning for property-wide severity",
    "urgent_areas": ["Hall", "Parking"]
  }},
  "additional_notes": "Any extra relevant context from the documents",
  "missing_or_unclear_info": ["Not Available: describe what is missing"],
  "conflicts_detected": ["No conflicts detected"]
}}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_ddr_direct(
    inspection_text: str,
    thermal_text: str,
    api_key: str = "",
    provider: str = "",
) -> dict[str, Any]:
    """
    ONE LLM call -> complete DDR JSON.
    api_key and provider passed explicitly (thread-safe).
    """
    prompt = _DDR_PROMPT.format(
        inspection_text=_trim(inspection_text),
        thermal_text=_trim(thermal_text),
    )
    return _parse_json(_call_llm(prompt, api_key=api_key, provider=provider))


# Backward-compatible shims
def extract_inspection_data(full_text: str) -> dict[str, Any]:
    return {"_raw_text": _trim(full_text)}

def extract_thermal_data(full_text: str) -> dict[str, Any]:
    return {"_raw_text": _trim(full_text)}

def generate_ddr_json(
    inspection_data: dict[str, Any],
    thermal_data: dict[str, Any],
    api_key: str = "",
    provider: str = "",
) -> dict[str, Any]:
    insp_text  = inspection_data.get("_raw_text", json.dumps(inspection_data)[:MAX_DOC_CHARS])
    therm_text = thermal_data.get("_raw_text",    json.dumps(thermal_data)[:MAX_DOC_CHARS])
    return generate_ddr_direct(insp_text, therm_text, api_key=api_key, provider=provider)
