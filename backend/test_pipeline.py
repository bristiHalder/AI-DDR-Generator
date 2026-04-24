"""
test_pipeline.py — End-to-end test for the DDR generation pipeline.
Tests PDF extraction on the provided sample files without needing the server.
Run: python test_pipeline.py
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# Load env
load_dotenv(".env")

# Paths to sample documents
BASE = Path(__file__).parent.parent
INSPECTION_PDF = BASE / "Sample Report.pdf"
THERMAL_PDF = BASE / "Thermal Images.pdf"

def test_pdf_extraction():
    """Test 1: Verify PDF extraction works on both sample files."""
    print("=" * 60)
    print("TEST 1: PDF Extraction")
    print("=" * 60)
    
    from pipeline.pdf_extractor import extract_pdf, get_significant_images
    
    print(f"\n📄 Extracting: {INSPECTION_PDF.name}")
    insp = extract_pdf(str(INSPECTION_PDF))
    print(f"  ✅ Pages: {insp['total_pages']}")
    print(f"  ✅ Total images: {len(insp['all_images'])}")
    sig_insp = get_significant_images(insp)
    print(f"  ✅ Significant images (>=120px): {len(sig_insp)}")
    print(f"  📝 Text preview (first 200 chars):\n     {insp['full_text'][:200].strip()}")
    
    print(f"\n🌡 Extracting: {THERMAL_PDF.name}")
    therm = extract_pdf(str(THERMAL_PDF))
    print(f"  ✅ Pages: {therm['total_pages']}")
    print(f"  ✅ Total images: {len(therm['all_images'])}")
    sig_therm = get_significant_images(therm)
    print(f"  ✅ Significant images (>=120px): {len(sig_therm)}")
    print(f"  📝 Text preview (first 200 chars):\n     {therm['full_text'][:200].strip()}")
    
    return insp, therm


def test_ai_extraction(insp_data, therm_data):
    """Test 2: Verify AI extraction works with Gemini."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or api_key == "your_gemini_api_key_here":
        print("\n⚠️  SKIPPING AI tests — GEMINI_API_KEY not set in .env")
        print("   Add your key to backend/.env to run full pipeline test")
        return None, None
    
    print("\n" + "=" * 60)
    print("TEST 2: AI Extraction (Gemini)")
    print("=" * 60)
    
    from pipeline.ai_processor import extract_inspection_data, extract_thermal_data, generate_ddr_json
    
    print("\n🤖 Extracting inspection data...")
    inspection_structured = extract_inspection_data(insp_data["full_text"])
    
    if inspection_structured.get("parse_error"):
        print("  ❌ Inspection extraction failed to parse JSON")
        print(f"  Raw: {inspection_structured.get('raw', '')[:300]}")
    else:
        areas = inspection_structured.get("impacted_areas", [])
        print(f"  ✅ Property: {inspection_structured.get('property_info', {}).get('property_type', 'N/A')}")
        print(f"  ✅ Impacted areas found: {len(areas)}")
        for a in areas[:3]:
            print(f"     - {a.get('area_name', 'Unknown')}")
    
    print("\n🌡 Extracting thermal data...")
    thermal_structured = extract_thermal_data(therm_data["full_text"])
    
    if thermal_structured.get("parse_error"):
        print("  ❌ Thermal extraction failed to parse JSON")
    else:
        scans = thermal_structured.get("thermal_scans", [])
        print(f"  ✅ Thermal scans found: {len(scans)}")
        device = thermal_structured.get("device_info", {})
        print(f"  ✅ Device: {device.get('device', 'N/A')}")
    
    return inspection_structured, thermal_structured


def test_ddr_generation(insp_structured, therm_structured, insp_data, therm_data):
    """Test 3: Generate DDR and save output."""
    if insp_structured is None:
        return
    
    print("\n" + "=" * 60)
    print("TEST 3: DDR Generation & Report Building")
    print("=" * 60)
    
    from pipeline.ai_processor import generate_ddr_json
    from pipeline.report_builder import build_final_report
    
    print("\n🔗 Generating merged DDR JSON...")
    ddr_json = generate_ddr_json(insp_structured, therm_structured)
    
    if ddr_json.get("parse_error"):
        print("  ❌ DDR generation failed to parse JSON")
        return
    
    print(f"  ✅ Areas in DDR: {len(ddr_json.get('area_observations', []))}")
    print(f"  ✅ Overall severity: {ddr_json.get('overall_severity_assessment', {}).get('overall_level', 'N/A')}")
    print(f"  ✅ Missing info items: {len(ddr_json.get('missing_or_unclear_info', []))}")
    
    print("\n🏗 Building final report with images...")
    final_report = build_final_report(ddr_json, insp_data, therm_data)
    
    # Save test output
    out_path = Path("outputs/test_report.json")
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False, default=str)
    
    # Don't save base64 images to the JSON preview, too large
    print(f"  ✅ Report JSON saved to: {out_path}")
    
    # Render HTML
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("ddr_report.html")
    html = template.render(ddr=final_report)
    
    html_path = Path("outputs/test_report.html")
    html_path.write_text(html, encoding="utf-8")
    print(f"  ✅ HTML report saved to: {html_path}")
    print(f"     Open: file:///{html_path.resolve()}")


if __name__ == "__main__":
    print("\n🚀 DDR Pipeline — End-to-End Test\n")
    
    # Check files exist
    if not INSPECTION_PDF.exists():
        print(f"❌ Inspection PDF not found: {INSPECTION_PDF}")
        sys.exit(1)
    if not THERMAL_PDF.exists():
        print(f"❌ Thermal PDF not found: {THERMAL_PDF}")
        sys.exit(1)
    
    try:
        insp_data, therm_data = test_pdf_extraction()
        insp_structured, therm_structured = test_ai_extraction(insp_data, therm_data)
        test_ddr_generation(insp_structured, therm_structured, insp_data, therm_data)
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
