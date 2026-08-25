"""Run the approved full equipment click-and-OCR scan."""

from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_regions import FineEquipmentRecognizer, load_fine_regions
from equipment_workflow import PaddleOcrV5Mobile, build_hdc_scanner


def main() -> int:
    database = EquipmentDatabase(Path("data/equipment.db"))
    database.initialize()
    ocr = PaddleOcrV5Mobile(cache_dir=Path(".paddle_home"))
    fine = FineEquipmentRecognizer(
        ocr=ocr,
        regions=load_fine_regions(Path("captures")),
        output_dir=Path("fine_ocr_results"),
    )
    try:
        scanner = build_hdc_scanner(
            hdc=r"D:\DEVECO~2\sdk\default\OPENHA~1\TOOLCH~1\hdc.exe",
            serial="FMR0223A30001935",
            screen_dir=Path("captures/scan_run5"),
            ocr=ocr,
            output_dir=Path("ocr_results_run5"),
            fine_recognizer=fine,
            persistence=lambda record, screenshot: database.upsert_recognized_equipment(
                record, source_screenshot=screenshot
            ),
            enable_coarse_ocr=False,
        )
        records = scanner.scan_until_bottom()
        print(f"SCAN_COMPLETE records={len(records)}", flush=True)
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
