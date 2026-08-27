"""Device-side equipment clicking and detail OCR workflow primitives."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol, Sequence

from PIL import Image, ImageEnhance, ImageOps, ImageStat

from screen_capture import _run

if TYPE_CHECKING:
    from equipment_regions import FineEquipmentRecognizer


class WorkflowError(RuntimeError):
    """Raised when a click cannot be verified or OCR cannot run."""


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    right: int
    bottom: int

    def crop(self, image: Image.Image) -> Image.Image:
        return image.crop((self.left, self.top, self.right + 1, self.bottom + 1))


# Calibrated from screen_20260825_122242_383 拷贝.jpg at 2720x1260.
DEFAULT_DETAIL_REGION = Region(2007, 238, 2567, 1133)


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float | None = None


class OcrEngine(Protocol):
    def recognize(self, image_path: Path) -> OcrResult: ...


class HdcController:
    """Injects input on the phone through HDC, never through the Windows cursor."""

    def __init__(self, hdc: str, serial: str):
        self.hdc = hdc
        self.serial = serial

    def _input(self, *args: str) -> None:
        result = _run((self.hdc, "-t", self.serial, "shell", "uitest", "uiInput", *args), timeout=15)
        if result.returncode != 0:
            raise WorkflowError(result.stderr.strip() or result.stdout.strip() or "HDC input failed")

    def click(self, x: int, y: int) -> None:
        self._input("click", str(x), str(y))

    def swipe(self, start: tuple[int, int], end: tuple[int, int], velocity: int = 600) -> None:
        # HDC exposes drag as the explicit press-move-release gesture. The
        # post-drag hold is handled by the scanner before result validation.
        self._input("drag", str(start[0]), str(start[1]), str(end[0]), str(end[1]), str(velocity))


def _small_gray(image: Image.Image, size: tuple[int, int] = (64, 64)) -> list[int]:
    gray = ImageOps.grayscale(image).resize(size)
    return list(gray.get_flattened_data()) if hasattr(gray, "get_flattened_data") else list(gray.getdata())


def mean_difference(first: Image.Image, second: Image.Image) -> float:
    """Return the average grayscale difference on two detail crops."""
    a = _small_gray(first)
    b = _small_gray(second)
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def changed(before: Image.Image, after: Image.Image, threshold: float = 0.1) -> bool:
    return mean_difference(before, after) >= threshold


def stable(first: Image.Image, second: Image.Image, threshold: float = 2.0) -> bool:
    return mean_difference(first, second) <= threshold


class TesseractOcr:
    """OCR adapter for a separately installed tesseract executable."""

    def __init__(self, executable: str | None = None, languages: str = "chi_sim+eng"):
        self.executable = executable or shutil.which("tesseract")
        if not self.executable:
            raise WorkflowError("Tesseract is not installed or not on PATH.")
        self.languages = languages

    def recognize(self, image_path: Path) -> OcrResult:
        result = _run((self.executable, str(image_path), "stdout", "--psm", "6", "-l", self.languages), timeout=30)
        if result.returncode != 0:
            raise WorkflowError(result.stderr.strip() or "Tesseract OCR failed")
        return OcrResult(result.stdout.strip())


class PaddleOcrV5Mobile:
    """PP-OCRv5 Mobile adapter for PaddleOCR 3.x."""

    def __init__(self, cache_dir: Path = Path(".paddle_home"), languages: str = "ch"):
        cache_dir = cache_dir.resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Paddle's legacy dataset cache follows USERPROFILE on Windows.
        os.environ["USERPROFILE"] = str(cache_dir)
        os.environ["HOME"] = str(cache_dir)
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise WorkflowError("PaddleOCR is not installed in the EAIA environment.") from exc
        try:
            self.pipeline = PaddleOCR(
                lang=languages,
                ocr_version="PP-OCRv5",
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device="cpu",
                # The current EAIA Paddle 3.3.1 + PP-OCRv5 combination raises
                # a PIR/oneDNN runtime error when this is enabled.
                enable_mkldnn=False,
            )
        except Exception as exc:
            raise WorkflowError(f"Unable to initialize PP-OCRv5 Mobile: {exc}") from exc

    @staticmethod
    def _result_data(result: object) -> dict:
        if isinstance(result, dict):
            return result
        data = getattr(result, "json", None)
        if callable(data):
            data = data()
        if isinstance(data, str):
            return json.loads(data)
        return data if isinstance(data, dict) else {}

    def recognize(self, image_path: Path) -> OcrResult:
        return self.recognize_many([image_path])[0]

    def recognize_many(self, image_paths: Sequence[Path]) -> list[OcrResult]:
        try:
            predictions = self.pipeline.predict([str(path) for path in image_paths])
            results: list[OcrResult] = []
            for prediction in predictions:
                data = self._result_data(prediction)
                texts = [str(value) for value in data.get("rec_texts", [])]
                scores = [float(value) for value in data.get("rec_scores", [])]
                confidence = sum(scores) / len(scores) if scores else None
                results.append(OcrResult("\n".join(texts), confidence))
            if len(results) != len(image_paths):
                raise WorkflowError(
                    f"PaddleOCR returned {len(results)} results for {len(image_paths)} images"
                )
            return results
        except Exception as exc:
            raise WorkflowError(f"PP-OCRv5 Mobile inference failed: {exc}") from exc


class EquipmentWorkflow:
    def __init__(
        self,
        capture: Callable[[], Path],
        click: Callable[[int, int], None],
        ocr: OcrEngine,
        detail_region: Region,
        output_dir: Path,
        poll_interval: float = 0.12,
        timeout: float = 3.0,
        fine_recognizer: "FineEquipmentRecognizer | None" = None,
        persistence: Callable[[dict, Path], None] | None = None,
        # Preserve the strict low-level behavior for direct callers. The HDC
        # builders opt out because the image-difference gate is sufficient.
        verify_baseline_ocr: bool = True,
        enable_coarse_ocr: bool = True,
    ):
        self.capture = capture
        self.click = click
        self.ocr = ocr
        self.detail_region = detail_region
        self.output_dir = output_dir
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.fine_recognizer = fine_recognizer
        self.persistence = persistence
        self.verify_baseline_ocr = verify_baseline_ocr
        self.enable_coarse_ocr = enable_coarse_ocr

    def capture_item(self, row: int, column: int, x: int, y: int, allow_unchanged: bool = False) -> dict:
        """Switch to one item and save a stable detail screenshot without OCR."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        before_path = self.capture()
        before = Image.open(before_path).convert("RGB")
        baseline_result = None
        # The image-difference gate already verifies that the selected detail
        # panel changed. OCRing the old panel adds a full model invocation per
        # item and does not contribute to the final recognition result.
        if not allow_unchanged and self.verify_baseline_ocr:
            baseline_path = self._save_ocr_crop(before, f"baseline_r{row:03d}_c{column:02d}")
            baseline_result = self.ocr.recognize(baseline_path)
        self.click(x, y)

        deadline = time.monotonic() + self.timeout
        previous = None
        final_path = None
        changed_seen = False
        stable_count = 0
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            current_path = self.capture()
            current = Image.open(current_path).convert("RGB")
            current_detail = self.detail_region.crop(current)
            if not changed_seen:
                changed_seen = changed(self.detail_region.crop(before), current_detail)
            elif previous is not None and stable(previous, current_detail):
                stable_count += 1
                # Two consecutive frames (previous and current) are enough;
                # requiring a third frame can exceed the HDC round-trip time.
                if stable_count >= 1:
                    final_path = current_path
                    break
            else:
                stable_count = 0
            previous = current_detail

        if not changed_seen and allow_unchanged:
            # The tapped item was already selected, so its detail panel is the baseline.
            changed_seen = True
            final_path = before_path
        if not changed_seen:
            raise WorkflowError(f"Detail panel did not change after clicking row={row}, column={column}")
        if final_path is None:
            raise WorkflowError(f"Detail panel did not become stable after clicking row={row}, column={column}")

        stem = f"item_r{row:03d}_c{column:02d}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
        return {
            "row": row,
            "column": column,
            "click": {"x": x, "y": y},
            "final_path": final_path,
            "baseline_result": baseline_result,
            "stem": stem,
        }

    def recognize_captured_item(self, captured: dict) -> dict:
        """Run OCR and persistence for a screenshot captured by the producer."""
        final_path = captured["final_path"]
        final_image = Image.open(final_path).convert("RGB")
        row = captured["row"]
        column = captured["column"]
        stem = captured["stem"]
        baseline_result = captured["baseline_result"]
        crop_path = None
        result = OcrResult("")
        if self.enable_coarse_ocr:
            crop_path = self._save_ocr_crop(final_image, stem)
            result = self.ocr.recognize(crop_path)
        if baseline_result is not None and self._normalize_text(baseline_result.text) == self._normalize_text(result.text):
            raise WorkflowError(f"Detail image changed but OCR text did not change after row={row}, column={column}")
        record = {
            "row": row,
            "column": column,
            "click": captured["click"],
            "detail_region": self.detail_region.__dict__,
            "screenshot": str(final_path.resolve()),
            "ocr_crop": str(crop_path.resolve()) if crop_path is not None else None,
            "ocr_text": result.text,
            "ocr_confidence": result.confidence,
            "baseline_ocr_text": baseline_result.text if baseline_result is not None else None,
            }
        if self.fine_recognizer is not None:
            record["fine_detail"] = self.fine_recognizer.recognize(final_path, stem)
            if self.persistence is not None:
                self.persistence(record["fine_detail"], final_path)
        with (self.output_dir / "ocr_results.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def process_item(self, row: int, column: int, x: int, y: int, allow_unchanged: bool = False) -> dict:
        captured = self.capture_item(row, column, x, y, allow_unchanged)
        return self.recognize_captured_item(captured)


    def _save_ocr_crop(self, image: Image.Image, stem: str) -> Path:
        crop = self.detail_region.crop(image)
        crop = ImageEnhance.Contrast(ImageOps.grayscale(crop)).enhance(1.8)
        crop = crop.resize((crop.width * 2, crop.height * 2))
        path = self.output_dir / f"{stem}.png"
        crop.save(path)
        return path

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join(text.split()).lower()


@dataclass(frozen=True)
class GridConfig:
    """Screen coordinates calibrated from the marked equipment grid."""

    first_x: int = 453
    last_x: int = 1843
    first_y: int = 363
    row_spacing: int = 231
    columns: int = 8
    visible_rows: int = 3
    # The device viewport exposes about 3.5 rows. Advance one row at a time
    # and keep two full rows overlapped so half-rows at either edge are never
    # treated as a new scan page.
    overlap_rows: int = 2
    swipe_start: tuple[int, int] = (1200, 950)
    # Calibrated on the device: one equipment row per drag.
    swipe_end: tuple[int, int] = (1200, 755)
    swipe_velocity: int = 200
    slot_width: int = 164
    slot_height: int = 205

    @property
    def x_centers(self) -> tuple[int, ...]:
        step = (self.last_x - self.first_x) / (self.columns - 1)
        return tuple(round(self.first_x + column * step) for column in range(self.columns))

    @property
    def y_centers(self) -> tuple[int, ...]:
        return tuple(self.first_y + row * self.row_spacing for row in range(self.visible_rows))


class EquipmentScanner:
    """Scan all rows by processing new rows after overlapping device-side swipes."""

    def __init__(self, workflow: EquipmentWorkflow, swipe: Callable[[tuple[int, int], tuple[int, int], int], None], grid: GridConfig = GridConfig()):
        self.workflow = workflow
        self.swipe = swipe
        self.grid = grid

    def _slot_luma(self, image: Image.Image, x: int, y: int) -> float:
        half_width = self.grid.slot_width // 2
        half_height = self.grid.slot_height // 2
        crop = image.crop((x - half_width, y - half_height, x + half_width, y + half_height))
        mean = ImageStat.Stat(crop).mean
        return sum(mean) / 3

    def _grid_snapshot(self, image: Image.Image) -> Image.Image:
        """Build a comparison image from equipment tiles only.

        The surrounding inventory background can animate while the list is
        stationary, so it must not participate in drag-result validation.
        """
        snapshot = Image.new(
            "RGB",
            (
                self.grid.slot_width * self.grid.columns,
                self.grid.slot_height * self.grid.visible_rows,
            ),
        )
        for row, y in enumerate(self.grid.y_centers):
            for column, x in enumerate(self.grid.x_centers):
                half_width = self.grid.slot_width // 2
                half_height = self.grid.slot_height // 2
                crop = image.crop(
                    (x - half_width, y - half_height,
                     x + half_width, y + half_height)
                )
                snapshot.paste(
                    crop,
                    (column * self.grid.slot_width, row * self.grid.slot_height),
                )
        return snapshot

    def _row_occupied_columns(self, image: Image.Image, y: int, calibration: dict) -> list[int]:
        scores = [self._slot_luma(image, x, y) for x in self.grid.x_centers]
        ordered = sorted(scores)
        gaps = [ordered[index + 1] - ordered[index] for index in range(len(ordered) - 1)]
        span = ordered[-1] - ordered[0]
        split_index = max(range(len(gaps)), key=gaps.__getitem__) if gaps else 0
        split_gap = gaps[split_index] if gaps else 0.0

        if "item_level" not in calibration:
            # Use the first row as a reference, but do not assume that every slot
            # is occupied. A large gap separates item tiles from empty placeholders.
            if split_gap >= max(12.0, span * 0.35):
                boundary = (ordered[split_index] + ordered[split_index + 1]) / 2
                occupied = [column for column, score in enumerate(scores, 1) if score > boundary]
                occupied_scores = [score for score in scores if score > boundary]
                calibration["item_level"] = sum(occupied_scores) / len(occupied_scores)
                calibration["item_spread"] = max(occupied_scores) - min(occupied_scores)
                calibration["brightness_gap"] = split_gap
                return occupied

            # With no clear split, retain the old all-occupied behavior for a
            # uniformly filled row. Later rows can still be rejected as empty
            # when their brightness is substantially below this reference.
            calibration["item_level"] = sum(scores) / len(scores)
            calibration["item_spread"] = span
            return list(range(1, len(scores) + 1))

        # Establish a row-local split when the first row was uniformly filled.
        if "brightness_gap" not in calibration and split_gap >= max(12.0, span * 0.35):
            empty_level = ordered[split_index]
            occupied_level = ordered[split_index + 1]
            calibration["empty_level"] = empty_level
            calibration["brightness_gap"] = occupied_level - empty_level
            boundary = (empty_level + occupied_level) / 2
            return [column for column, score in enumerate(scores, 1) if score > boundary]

        if "brightness_gap" in calibration:
            boundary = calibration["item_level"] - calibration["brightness_gap"] / 2
            return [column for column, score in enumerate(scores, 1) if score > boundary]

        # A row with no split and uniformly low brightness is an empty row. This
        # prevents background placeholders from being clicked as equipment.
        empty_threshold = calibration["item_level"] - max(calibration["item_spread"], 12.0) * 1.5
        if max(scores) < empty_threshold:
            return []
        return list(range(1, len(scores) + 1))

    def _slot_selected(self, image: Image.Image, x: int, y: int) -> bool:
        half_width = self.grid.slot_width // 2
        half_height = self.grid.slot_height // 2
        top = y - half_height
        left = x - half_width
        gold_pixels = 0
        for yy in range(top, min(top + 8, image.height)):
            for xx in range(left, min(left + self.grid.slot_width, image.width)):
                red, green, blue = image.getpixel((xx, yy))
                if red > 160 and green > 120 and blue < 130:
                    gold_pixels += 1
        return gold_pixels >= 40

    def _normalize_occupied_rows(
        self, occupied_by_row: dict[int, list[int]]
    ) -> dict[int, list[int]]:
        """Treat every row before the last occupied row as fully populated.

        Equipment is filled left-to-right by row. A dim trailing tile in an
        earlier row must not cause the scanner to skip it when a later row is
        visibly occupied.
        """
        occupied_rows = [row for row, columns in occupied_by_row.items() if columns]
        if not occupied_rows:
            return occupied_by_row
        last_occupied = max(occupied_rows)
        all_columns = list(range(1, len(self.grid.x_centers) + 1))
        for row in occupied_by_row:
            if row <= last_occupied and any(
                occupied_by_row[later_row]
                for later_row in occupied_by_row
                if later_row > row
            ):
                occupied_by_row[row] = all_columns.copy()
        return occupied_by_row

    def _process_rows(self, logical_start: int, screen_rows: Sequence[int]) -> tuple[list[dict], bool]:
        records = []
        reached_empty_row = False
        calibration: dict = {}
        threaded = hasattr(self.workflow, "capture_item") and hasattr(self.workflow, "recognize_captured_item")
        executor = ThreadPoolExecutor(max_workers=1) if threaded else None
        pending: list[Future] = []
        try:
            initial_path = self.workflow.capture()
            initial = Image.open(initial_path).convert("RGB")
            occupied_by_row = self._normalize_occupied_rows({
                screen_row: self._row_occupied_columns(
                    initial, self.grid.y_centers[screen_row], calibration
                )
                for screen_row in screen_rows
            })
            for screen_row in screen_rows:
                logical_row = logical_start + screen_row
                current_path = self.workflow.capture()
                current = Image.open(current_path).convert("RGB")
                occupied_columns = occupied_by_row[screen_row]
                if not occupied_columns:
                    reached_empty_row = True
                    break
                for column, x in enumerate(self.grid.x_centers, 1):
                    if column not in occupied_columns:
                        continue
                    y = self.grid.y_centers[screen_row]
                    allow_unchanged = self._slot_selected(current, x, y)
                    if not threaded:
                        records.append(self.workflow.process_item(
                            logical_row, column, x, y, allow_unchanged=allow_unchanged,
                        ))
                        continue
                    captured = self.workflow.capture_item(
                        logical_row, column, x, y, allow_unchanged=allow_unchanged,
                    )
                    pending.append(executor.submit(self.workflow.recognize_captured_item, captured))
                    # Keep at most two screenshots waiting behind the OCR worker.
                    if len(pending) >= 3:
                        records.append(pending.pop(0).result())
        finally:
            for future in pending:
                records.append(future.result())
            if executor is not None:
                executor.shutdown(wait=True)
        return records, reached_empty_row

    def _list_changed_after_swipe(self, before: Image.Image) -> bool:
        """Wait for the list to settle before deciding whether the swipe reached a new page."""
        before_grid = self._grid_snapshot(before)
        previous = None
        deadline = time.monotonic() + self.workflow.timeout
        while time.monotonic() < deadline:
            current_path = self.workflow.capture()
            current = Image.open(current_path).convert("RGB")
            current_grid = self._grid_snapshot(current)
            if previous is not None and stable(previous, current_grid):
                return changed(before_grid, current_grid)
            previous = current_grid
            time.sleep(self.workflow.poll_interval)
        return changed(before_grid, previous) if previous is not None else False

    def scan_until_bottom(self, max_scrolls: int = 100) -> list[dict]:
        if self.grid.overlap_rows >= self.grid.visible_rows:
            raise ValueError("overlap_rows must be smaller than visible_rows")
        records, reached_empty_row = self._process_rows(1, range(self.grid.visible_rows))
        if reached_empty_row:
            return records
        logical_start = 1
        for _ in range(max_scrolls):
            before_path = self.workflow.capture()
            before = Image.open(before_path).convert("RGB")
            self.swipe(self.grid.swipe_start, self.grid.swipe_end, self.grid.swipe_velocity)
            if not self._list_changed_after_swipe(before):
                break
            logical_start += self.grid.visible_rows - self.grid.overlap_rows
            new_records, reached_empty_row = self._process_rows(logical_start, range(self.grid.overlap_rows, self.grid.visible_rows))
            records.extend(new_records)
            if reached_empty_row:
                break
        return records


def build_hdc_workflow(
    hdc: str,
    serial: str,
    screen_dir: Path,
    ocr: OcrEngine,
    output_dir: Path,
    detail_region: Region = DEFAULT_DETAIL_REGION,
    fine_recognizer: "FineEquipmentRecognizer | None" = None,
    persistence: Callable[[dict, Path], None] | None = None,
    verify_baseline_ocr: bool = False,
    enable_coarse_ocr: bool = False,
) -> EquipmentWorkflow:
    """Build a workflow using HDC screenshots and device-side touch injection."""
    from screen_capture import capture_once

    controller = HdcController(hdc, serial)
    return EquipmentWorkflow(
        capture=lambda: capture_once(hdc, serial, screen_dir),
        click=controller.click,
        ocr=ocr,
        detail_region=detail_region,
        output_dir=output_dir,
        fine_recognizer=fine_recognizer,
        persistence=persistence,
        verify_baseline_ocr=verify_baseline_ocr,
        enable_coarse_ocr=enable_coarse_ocr,
    )


def build_hdc_scanner(
    hdc: str,
    serial: str,
    screen_dir: Path,
    ocr: OcrEngine,
    output_dir: Path,
    detail_region: Region = DEFAULT_DETAIL_REGION,
    grid: GridConfig = GridConfig(),
    fine_recognizer: "FineEquipmentRecognizer | None" = None,
    persistence: Callable[[dict, Path], None] | None = None,
    verify_baseline_ocr: bool = False,
    enable_coarse_ocr: bool = False,
) -> EquipmentScanner:
    """Build the full device-side equipment scanner."""
    workflow = build_hdc_workflow(
        hdc, serial, screen_dir, ocr, output_dir, detail_region, fine_recognizer,
        persistence, verify_baseline_ocr, enable_coarse_ocr,
    )
    controller = HdcController(hdc, serial)
    return EquipmentScanner(workflow, controller.swipe, grid)
