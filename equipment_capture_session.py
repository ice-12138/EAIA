"""Capture-first equipment scanning with fully offline OCR recognition.

The device-facing phase only clicks, validates image changes, swipes, and saves
stable screenshots. OCR is initialized only after capture has completed, so the
phone can be released immediately while recognition and SQLite persistence
continue on the PC.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image

from equipment_fast_scan import FastEquipmentScanner, FastEquipmentWorkflow
from equipment_workflow import (
    DEFAULT_DETAIL_REGION,
    GridConfig,
    HdcController,
    OcrResult,
    Region,
)


MANIFEST_VERSION = 1
DEFAULT_SESSION_ROOT = Path("captures/equipment_sessions")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DisabledOcr:
    """Sentinel OCR engine used by the capture-only workflow.

    Any call indicates that OCR accidentally leaked back into the device phase.
    """

    def recognize(self, image_path: Path) -> OcrResult:
        raise RuntimeError(f"OCR is disabled during capture-only scanning: {image_path}")


class CaptureSession:
    """Crash-resilient manifest plus the stable screenshots selected for OCR."""

    def __init__(self, path: Path, manifest: dict):
        self.path = Path(path)
        self.manifest_path = self.path / "manifest.json"
        self.frames_dir = self.path / "frames"
        self.working_dir = self.path / "working"
        self.manifest = manifest

    @classmethod
    def create(cls, root: Path = DEFAULT_SESSION_ROOT) -> "CaptureSession":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
        path = root / session_id
        path.mkdir(parents=True, exist_ok=False)
        session = cls(
            path,
            {
                "version": MANIFEST_VERSION,
                "session_id": session_id,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "status": "capturing",
                "device_released": False,
                "capture_count": 0,
                "recognized_count": 0,
                "items": [],
            },
        )
        session.frames_dir.mkdir(parents=True, exist_ok=True)
        session.working_dir.mkdir(parents=True, exist_ok=True)
        session._write()
        return session

    @classmethod
    def load(cls, path: Path) -> "CaptureSession":
        path = Path(path)
        manifest_path = path / "manifest.json" if path.is_dir() else path
        with manifest_path.open("r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        if int(manifest.get("version", 0)) != MANIFEST_VERSION:
            raise ValueError(
                f"Unsupported capture manifest version: {manifest.get('version')!r}"
            )
        return cls(manifest_path.parent, manifest)

    @property
    def session_id(self) -> str:
        return str(self.manifest["session_id"])

    @property
    def items(self) -> list[dict]:
        return list(self.manifest.get("items") or [])

    def _write(self) -> None:
        self.manifest["updated_at"] = _utc_now()
        self.path.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(self.manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(self.manifest_path)

    def set_status(self, status: str, **values: object) -> None:
        self.manifest["status"] = status
        self.manifest.update(values)
        self._write()

    def append_capture(self, captured: dict) -> dict:
        source = Path(captured["final_path"])
        sequence = len(self.manifest["items"]) + 1
        suffix = source.suffix.lower() if source.suffix else ".png"
        filename = (
            f"item_{sequence:06d}_r{int(captured['row']):03d}_"
            f"c{int(captured['column']):02d}{suffix}"
        )
        destination = self.frames_dir / filename
        shutil.copy2(source, destination)
        entry = {
            "sequence": sequence,
            "row": int(captured["row"]),
            "column": int(captured["column"]),
            "click": dict(captured.get("click") or {}),
            "image": destination.relative_to(self.path).as_posix(),
            "stem": str(captured.get("stem") or destination.stem),
            "fast_path": captured.get("fast_path"),
            "captured_at": _utc_now(),
            "recognized": False,
        }
        self.manifest["items"].append(entry)
        self.manifest["capture_count"] = len(self.manifest["items"])
        self._write()
        return self.as_captured(entry)

    def as_captured(self, entry: dict) -> dict:
        return {
            "row": int(entry["row"]),
            "column": int(entry["column"]),
            "click": dict(entry.get("click") or {}),
            "final_path": self.path / str(entry["image"]),
            "baseline_result": None,
            "stem": str(entry.get("stem") or Path(str(entry["image"])).stem),
            "fast_path": entry.get("fast_path") or "offline_session",
            "capture_sequence": int(entry["sequence"]),
        }

    def mark_capture_complete(self, total_count: int | None = None) -> None:
        count = int(total_count if total_count is not None else len(self.manifest["items"]))
        self.set_status(
            "captured",
            capture_count=count,
            captured_at=_utc_now(),
            device_released=True,
        )
        # The stable frames have already been copied to frames/. HDC may have
        # produced many transient validation screenshots in working/; they are
        # no longer needed once device interaction has ended.
        shutil.rmtree(self.working_dir, ignore_errors=True)

    def mark_capture_interrupted(self, error: str | None = None) -> None:
        self.set_status(
            "capture_interrupted",
            device_released=True,
            capture_error=error,
        )

    def mark_recognized(self, sequence: int) -> None:
        recognized_count = 0
        for entry in self.manifest["items"]:
            if int(entry["sequence"]) == int(sequence):
                entry["recognized"] = True
                entry["recognized_at"] = _utc_now()
            if entry.get("recognized"):
                recognized_count += 1
        self.manifest["recognized_count"] = recognized_count
        self._write()

    def mark_recognition_complete(self) -> None:
        self.set_status(
            "recognized",
            recognized_count=sum(
                1 for item in self.manifest["items"] if item.get("recognized")
            ),
            recognized_at=_utc_now(),
            device_released=True,
        )


class CaptureOnlyEquipmentScanner(FastEquipmentScanner):
    """Fast scanner variant whose hot path contains no OCR call at all."""

    def __init__(self, *args, session: CaptureSession, **kwargs):
        kwargs["count_ocr"] = None
        super().__init__(*args, **kwargs)
        self.session = session

    def _process_rows(
        self,
        logical_start: int,
        screen_rows: Sequence[int],
        initial_column: int = 1,
    ) -> tuple[list[dict], bool]:
        records: list[dict] = []
        reached_empty_row = False
        calibration: dict = {}
        current_path = self.workflow.capture()
        current = Image.open(current_path).convert("RGB")
        occupied_by_row = self._normalize_occupied_rows(
            {
                screen_row: self._row_occupied_columns(
                    current, self.grid.y_centers[screen_row], calibration
                )
                for screen_row in screen_rows
            }
        )

        for screen_row in screen_rows:
            logical_row = logical_start + screen_row
            first_column = initial_column if screen_row == screen_rows[0] else 1
            occupied_columns = occupied_by_row[screen_row]
            if not occupied_columns:
                reached_empty_row = True
                break

            for column, x in enumerate(self.grid.x_centers, 1):
                if column < first_column or column not in occupied_columns:
                    continue
                y = self.grid.y_centers[screen_row]
                self._report_progress(
                    status="scanning",
                    phase="capturing",
                    row=logical_row,
                    column=column,
                )
                allow_unchanged = self._slot_selected(current, x, y)
                captured = self.workflow.capture_item(
                    logical_row,
                    column,
                    x,
                    y,
                    allow_unchanged=allow_unchanged,
                    before_path=current_path,
                    before_image=current,
                )
                # Carry the newest stable frame forward for the next visual
                # change check, but save a separate immutable frame for OCR.
                current_path = captured["final_path"]
                current = Image.open(current_path).convert("RGB")
                session_record = self.session.append_capture(captured)
                records.append(session_record)
                self._report_record(session_record)

        return records, reached_empty_row


class OfflineEquipmentRecognizer:
    """Consume one completed capture session without touching the phone."""

    def __init__(
        self,
        workflow: FastEquipmentWorkflow,
        session: CaptureSession,
        *,
        progress_callback: Callable[[dict], None] | None = None,
    ):
        self.workflow = workflow
        self.session = session
        self.progress_callback = progress_callback

    def recognize(self, *, retry_recognized: bool = False) -> list[dict]:
        entries = self.session.items
        pending = [
            item for item in entries if retry_recognized or not item.get("recognized")
        ]
        total = len(entries)
        records: list[dict] = []
        self.session.set_status("recognizing", device_released=True)
        try:
            for entry in pending:
                captured = self.session.as_captured(entry)
                record = self.workflow.recognize_captured_item(captured)
                self.workflow.persist_record(record)
                self.session.mark_recognized(int(entry["sequence"]))
                records.append(record)
                if self.progress_callback is not None:
                    self.progress_callback(
                        {
                            "status": "scanning",
                            "phase": "recognizing",
                            "device_released": True,
                            "completed": int(entry["sequence"]),
                            "equipment_count": total,
                            "row": entry.get("row"),
                            "column": entry.get("column"),
                            "session_id": self.session.session_id,
                        }
                    )
        except Exception as error:
            self.session.set_status(
                "recognition_interrupted",
                recognition_error=str(error),
                device_released=True,
            )
            raise
        self.session.mark_recognition_complete()
        return records


class SeparatedEquipmentScanner:
    """Compatibility adapter: capture everything first, then initialize OCR."""

    def __init__(
        self,
        capture_scanner: CaptureOnlyEquipmentScanner,
        recognizer_factory: Callable[[CaptureSession], OfflineEquipmentRecognizer],
    ):
        self.capture_scanner = capture_scanner
        self.recognizer_factory = recognizer_factory
        self.session = capture_scanner.session
        self.grid = capture_scanner.grid
        self.equipment_count: int | None = None
        self.progress_callback: Callable[[dict], None] | None = None

    def _progress(self, update: dict) -> None:
        if self.progress_callback is not None:
            self.progress_callback(update)

    def scan_until_bottom(
        self,
        max_scrolls: int = 100,
        resume_row: int = 1,
        resume_column: int = 0,
    ) -> list[dict]:
        self.capture_scanner.progress_callback = self._progress
        try:
            captures = self.capture_scanner.scan_until_bottom(
                max_scrolls=max_scrolls,
                resume_row=resume_row,
                resume_column=resume_column,
            )
        except Exception as error:
            self.session.mark_capture_interrupted(str(error))
            raise

        skipped = (resume_row - 1) * self.grid.columns + resume_column
        self.equipment_count = skipped + len(captures)
        self.session.mark_capture_complete(len(captures))
        self._progress(
            {
                "status": "scanning",
                "phase": "recognizing",
                "device_released": True,
                "completed": 0,
                "equipment_count": self.equipment_count,
                "capture_completed": len(captures),
                "row": None,
                "column": None,
                "session_id": self.session.session_id,
            }
        )

        # This is deliberately lazy: PaddleOCR and its models are not touched
        # until every device screenshot has already been persisted.
        recognizer = self.recognizer_factory(self.session)

        def recognition_progress(update: dict) -> None:
            normalized = dict(update)
            normalized["equipment_count"] = self.equipment_count
            self._progress(normalized)

        recognizer.progress_callback = recognition_progress
        return recognizer.recognize()


def build_capture_only_hdc_scanner(
    hdc: str,
    serial: str,
    *,
    session_root: Path = DEFAULT_SESSION_ROOT,
    detail_region: Region = DEFAULT_DETAIL_REGION,
    grid: GridConfig = GridConfig(),
    settle_delay: float = 0.20,
    recovery_delay: float = 0.12,
    scroll_settle_delay: float = 0.0,
    progress_callback: Callable[[dict], None] | None = None,
) -> CaptureOnlyEquipmentScanner:
    """Build a zero-OCR HDC scanner and a durable capture session."""
    from screen_capture import capture_once

    session = CaptureSession.create(session_root)
    controller = HdcController(hdc, serial)
    workflow = FastEquipmentWorkflow(
        capture=lambda: capture_once(hdc, serial, session.working_dir),
        click=controller.click,
        ocr=DisabledOcr(),
        detail_region=detail_region,
        output_dir=session.path / "capture_meta",
        fine_recognizer=None,
        persistence=None,
        verify_baseline_ocr=False,
        enable_coarse_ocr=False,
        settle_delay=settle_delay,
        recovery_delay=recovery_delay,
    )
    return CaptureOnlyEquipmentScanner(
        workflow,
        controller.swipe,
        grid,
        session=session,
        scroll_settle_delay=scroll_settle_delay,
        progress_callback=progress_callback,
    )
