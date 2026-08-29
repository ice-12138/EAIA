"""Run capture-first equipment scanning with fully offline OCR."""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

from equipment_capture_session import (
    CaptureSession,
    OfflineEquipmentRecognizer,
    SeparatedEquipmentScanner,
    build_capture_only_hdc_scanner,
)
from equipment_db import EquipmentDatabase
from equipment_fast_scan import (
    FastEquipmentWorkflow,
    FastFineEquipmentRecognizer,
    PaddleTextRecognitionV5Mobile,
)
from equipment_regions import FineEquipmentRecognizer, load_fine_regions
from equipment_workflow import DEFAULT_DETAIL_REGION, PaddleOcrV5Mobile, build_hdc_scanner


HDC = r"D:\DEVECO~2\sdk\default\OPENHA~1\TOOLCH~1\hdc.exe"
SERIAL = "FMR0223A30001935"
CACHE_DIR = Path(".paddle_home")
SESSION_ROOT = Path("captures/equipment_sessions")


def _persistence(database: EquipmentDatabase, result_callback=None):
    def persist(record, screenshot):
        result = database.upsert_recognized_equipment(record, source_screenshot=screenshot)
        if result_callback is not None:
            result_callback(result)
        return result

    return persist


def _build_offline_recognizer(
    database: EquipmentDatabase,
    session: CaptureSession,
    result_callback=None,
) -> OfflineEquipmentRecognizer:
    """Initialize PaddleOCR only after the device capture session has ended."""
    regions = load_fine_regions(Path("captures"))
    recognition_ocr = PaddleTextRecognitionV5Mobile(cache_dir=CACHE_DIR)
    fine_output = session.path / "fine_ocr"
    ocr_output = session.path / "ocr_results"
    ocr_output.mkdir(parents=True, exist_ok=True)
    fine = FastFineEquipmentRecognizer(
        ocr=recognition_ocr,
        regions=regions,
        output_dir=fine_output,
        # Full detector+recognizer remains a lazy field-level fallback.
        fallback_factory=lambda: PaddleOcrV5Mobile(cache_dir=CACHE_DIR),
        min_confidence=0.55,
        save_debug_crops=False,
    )
    workflow = FastEquipmentWorkflow(
        capture=lambda: (_ for _ in ()).throw(
            RuntimeError("offline OCR must not request a device screenshot")
        ),
        click=lambda *_: (_ for _ in ()).throw(
            RuntimeError("offline OCR must not send device input")
        ),
        ocr=recognition_ocr,
        detail_region=DEFAULT_DETAIL_REGION,
        output_dir=ocr_output,
        fine_recognizer=fine,
        persistence=_persistence(database, result_callback),
        verify_baseline_ocr=False,
        enable_coarse_ocr=False,
        settle_delay=0,
        recovery_delay=0,
    )
    return OfflineEquipmentRecognizer(workflow, session)


def _build_fast_scanner(
    database: EquipmentDatabase,
    result_callback=None,
) -> SeparatedEquipmentScanner:
    """Build the default pipeline without constructing any OCR model yet."""
    capture_scanner = build_capture_only_hdc_scanner(
        hdc=HDC,
        serial=SERIAL,
        session_root=SESSION_ROOT,
        settle_delay=0.20,
        recovery_delay=0.12,
        scroll_settle_delay=0.0,
    )
    return SeparatedEquipmentScanner(
        capture_scanner,
        recognizer_factory=lambda session: _build_offline_recognizer(
            database, session, result_callback
        ),
    )


def _build_legacy_scanner(database: EquipmentDatabase, result_callback=None):
    """Keep the original coupled scanner available for explicit compatibility use."""
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


def _run_capture_only(resume_row: int, resume_column: int) -> int:
    scanner = build_capture_only_hdc_scanner(
        hdc=HDC,
        serial=SERIAL,
        session_root=SESSION_ROOT,
        settle_delay=0.20,
        recovery_delay=0.12,
        scroll_settle_delay=0.0,
    )
    started = perf_counter()
    try:
        captures = scanner.scan_until_bottom(
            resume_row=resume_row,
            resume_column=resume_column,
        )
    except Exception as error:
        scanner.session.mark_capture_interrupted(str(error))
        raise
    scanner.session.mark_capture_complete(len(captures))
    elapsed = perf_counter() - started
    print(
        f"CAPTURE_COMPLETE session={scanner.session.path} frames={len(captures)} "
        f"elapsed_s={elapsed:.3f} phone_released=true",
        flush=True,
    )
    return 0


def _run_offline_session(
    database: EquipmentDatabase,
    session_path: Path,
) -> int:
    session = CaptureSession.load(session_path)
    recognizer = _build_offline_recognizer(database, session)
    started = perf_counter()
    records = recognizer.recognize()
    elapsed = perf_counter() - started
    print(
        f"OCR_COMPLETE session={session.path} records={len(records)} "
        f"elapsed_s={elapsed:.3f} device_access=false",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture equipment screenshots first, then recognize them offline."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--capture-only",
        action="store_true",
        help="Only capture a durable session; do not initialize OCR.",
    )
    mode.add_argument(
        "--recognize-session",
        type=Path,
        help="Recognize an existing capture session without accessing the phone.",
    )
    parser.add_argument("--resume-row", type=int, default=1)
    parser.add_argument("--resume-column", type=int, default=0)
    args = parser.parse_args()

    if args.resume_row < 1 or args.resume_column < 0 or args.resume_column > 8:
        parser.error("resume position must be a valid 1-based row and 0-8 column")

    if args.capture_only:
        return _run_capture_only(args.resume_row, args.resume_column)

    database = EquipmentDatabase(Path("data/equipment.db"))
    database.initialize()
    try:
        if args.recognize_session is not None:
            return _run_offline_session(database, args.recognize_session)

        scanner = _build_fast_scanner(database)
        released_printed = False

        def progress(update: dict) -> None:
            nonlocal released_printed
            if update.get("device_released") and not released_printed:
                released_printed = True
                print(
                    f"CAPTURE_COMPLETE session={scanner.session.path} "
                    f"frames={update.get('capture_completed', 0)} phone_released=true",
                    flush=True,
                )

        scanner.progress_callback = progress
        print("SCAN_MODE=capture_then_offline_ocr", flush=True)
        started = perf_counter()
        records = scanner.scan_until_bottom(
            resume_row=args.resume_row,
            resume_column=args.resume_column,
        )
        elapsed = perf_counter() - started
        rate = len(records) / elapsed if elapsed > 0 else 0.0
        average_item_time = elapsed / len(records) if records else 0.0
        print(
            f"SCAN_COMPLETE mode=capture_then_offline_ocr records={len(records)} "
            f"elapsed_s={elapsed:.3f} items_per_s={rate:.3f} "
            f"average_item_s={average_item_time:.3f} session={scanner.session.path}",
            flush=True,
        )
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
