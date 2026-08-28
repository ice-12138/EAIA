"""Run the equipment click-and-OCR scan with the supervised fast path."""

from pathlib import Path
from time import perf_counter

from equipment_db import EquipmentDatabase
from equipment_fast_scan import (
    FastFineEquipmentRecognizer,
    PaddleTextRecognitionV5Mobile,
    build_fast_hdc_scanner,
)
from equipment_regions import FineEquipmentRecognizer, load_fine_regions
from equipment_workflow import PaddleOcrV5Mobile, WorkflowError, build_hdc_scanner


HDC = r"D:\DEVECO~2\sdk\default\OPENHA~1\TOOLCH~1\hdc.exe"
SERIAL = "FMR0223A30001935"
CACHE_DIR = Path(".paddle_home")


def _persistence(database: EquipmentDatabase, result_callback=None):
    def persist(record, screenshot):
        result = database.upsert_recognized_equipment(record, source_screenshot=screenshot)
        if result_callback is not None:
            result_callback(result)
        return result
    return persist


def _build_fast_scanner(database: EquipmentDatabase, result_callback=None):
    regions = load_fine_regions(Path("captures"))
    recognition_ocr = PaddleTextRecognitionV5Mobile(cache_dir=CACHE_DIR)
    fine = FastFineEquipmentRecognizer(
        ocr=recognition_ocr,
        regions=regions,
        output_dir=Path("fine_ocr_results"),
        # Only construct the original detector+recognizer if a field is low
        # confidence or violates simple game-domain constraints.
        fallback_factory=lambda: PaddleOcrV5Mobile(cache_dir=CACHE_DIR),
        min_confidence=0.55,
        save_debug_crops=False,
    )
    return build_fast_hdc_scanner(
        hdc=HDC,
        serial=SERIAL,
        screen_dir=Path("captures/scan_fast"),
        ocr=recognition_ocr,
        output_dir=Path("ocr_results_fast"),
        fine_recognizer=fine,
        persistence=_persistence(database, result_callback),
        settle_delay=0.20,
        recovery_delay=0.12,
        # The device calibration is stable without an extra post-drag pause.
        # Validation still captures the list immediately after the drag.
        scroll_settle_delay=0.0,
        count_ocr=recognition_ocr,
    )


def _build_legacy_scanner(database: EquipmentDatabase, result_callback=None):
    ocr = PaddleOcrV5Mobile(cache_dir=CACHE_DIR)
    fine = FineEquipmentRecognizer(
        ocr=ocr,
        regions=load_fine_regions(Path("captures")),
        output_dir=Path("fine_ocr_results"),
    )
    return build_hdc_scanner(
        hdc=HDC,
        serial=SERIAL,
        screen_dir=Path("captures/scan_run5"),
        ocr=ocr,
        output_dir=Path("ocr_results_run5"),
        fine_recognizer=fine,
        persistence=_persistence(database, result_callback),
        enable_coarse_ocr=False,
        count_ocr=ocr,
    )


def main() -> int:
    database = EquipmentDatabase(Path("data/equipment.db"))
    database.initialize()
    try:
        try:
            scanner = _build_fast_scanner(database)
            mode = "fast"
        except WorkflowError as exc:
            # Keep the existing implementation available on older local
            # PaddleOCR installations. Programming/data errors remain visible.
            print(f"FAST_SCAN_UNAVAILABLE reason={exc}", flush=True)
            scanner = _build_legacy_scanner(database)
            mode = "legacy"

        print(f"SCAN_MODE={mode}", flush=True)
        started = perf_counter()
        records = scanner.scan_until_bottom()
        elapsed = perf_counter() - started
        rate = len(records) / elapsed if elapsed > 0 else 0.0
        average_item_time = elapsed / len(records) if records else 0.0
        print(
            f"SCAN_COMPLETE mode={mode} records={len(records)} "
            f"elapsed_s={elapsed:.3f} items_per_s={rate:.3f} "
            f"average_item_s={average_item_time:.3f}",
            flush=True,
        )
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
