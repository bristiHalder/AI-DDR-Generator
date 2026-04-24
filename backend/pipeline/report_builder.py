"""
report_builder.py
Assembles the final DDR by combining:
- DDR JSON from the LLM
- Actual extracted images from both PDFs
Maps image references to real extracted images and returns a complete report object.
"""

from typing import Any


def assign_images_to_areas(
    ddr_json: dict[str, Any],
    inspection_images: list[dict],
    thermal_images: list[dict],
) -> dict[str, Any]:
    """
    Assign real extracted images to each area in the DDR.

    Strategy:
    - Inspection photos are distributed sequentially across areas
      (based on how many areas reference photos).
    - Thermal images map to thermal readings in page order.

    Returns the ddr_json with 'assigned_images' and 'assigned_thermal_images'
    added to each area.
    """
    areas = ddr_json.get("area_observations", [])
    if not areas:
        return ddr_json

    # Filter meaningful images (not tiny logos)
    sig_inspection = [img for img in inspection_images if img.get("width", 0) >= 120 and img.get("height", 0) >= 120]
    sig_thermal = [img for img in thermal_images if img.get("width", 0) >= 120 and img.get("height", 0) >= 120]

    # Distribute inspection images across areas roughly equally
    total_areas = len(areas)
    insp_per_area = max(1, len(sig_inspection) // total_areas) if sig_inspection else 0

    for i, area in enumerate(areas):
        # Assign inspection photos
        start = i * insp_per_area
        end = start + insp_per_area if i < total_areas - 1 else len(sig_inspection)
        area["assigned_images"] = sig_inspection[start:end]

        # Assign thermal images — map by area index proportionally
        t_start = i * (len(sig_thermal) // total_areas) if sig_thermal else 0
        t_end = t_start + (len(sig_thermal) // total_areas) if sig_thermal else 0
        area["assigned_thermal_images"] = sig_thermal[t_start:t_end]

    ddr_json["area_observations"] = areas
    return ddr_json


def build_final_report(
    ddr_json: dict[str, Any],
    inspection_pdf_data: dict[str, Any],
    thermal_pdf_data: dict[str, Any],
) -> dict[str, Any]:
    """
    Combine DDR JSON with extracted images to create the final report object.
    """
    from .pdf_extractor import get_significant_images

    inspection_images = get_significant_images(inspection_pdf_data)
    thermal_images = get_significant_images(thermal_pdf_data)

    # Assign images to areas
    ddr_json = assign_images_to_areas(ddr_json, inspection_images, thermal_images)

    # Attach source metadata
    ddr_json["_meta"] = {
        "inspection_source": inspection_pdf_data.get("source", "Inspection Report"),
        "thermal_source": thermal_pdf_data.get("source", "Thermal Report"),
        "total_inspection_pages": inspection_pdf_data.get("total_pages", 0),
        "total_thermal_pages": thermal_pdf_data.get("total_pages", 0),
        "total_inspection_images": len(inspection_images),
        "total_thermal_images": len(thermal_images),
    }

    return ddr_json
