"""Fast supervised equipment scanning for stable game UI frames.

This module keeps the existing HDC capture and PP-OCRv5 pipeline available as
fallbacks, but optimizes the normal path for the EAIA equipment screen:

* reuse the scanner's current screenshot instead of capturing a fresh baseline
  for every item;
* in supervised mode, accept one post-click frame when the detail panel changed,
  with one recovery frame if the first frame is too early;
* run PP-OCRv5 Mobile recognition directly on fixed fine-grained ROIs in memory,
  avoiding text detection and PNG round-trips on the common path;
* lazily fall back to the original detector+recognizer for low-confidence or
  domain-invalid fields;
* keep OCR on the worker thread while deferring SQLite persistence to the scanner
  thread, preserving SQLite's default same-thread safety contract.
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image

from equipment_regions import SUB_FIELDS, FineEquipmentRecognizer, FineRegion
from equipment_workflow import (
    DEFAULT_EQUIPMENT_COUNT_REGION,
    DEFAULT_DETAIL_REGION,
    EquipmentScanner,
    EquipmentWorkflow,
    GridConfig,
    HdcController,
    OcrEngine,
    OcrResult,
    Region,
    WorkflowError,
    changed,
)


class PaddleTextRecognitionV5Mobile:
    """Recognition-only PP-OCRv5 Mobile adapter for already localized text ROIs."""

    def __init__(
        self,
        cache_dir: Path = Path(".paddle_home"),
        *,
        model_name: str = "PP-OCRv5_mobile_rec",
        device: str = "cpu",
        batch_size: int = 10,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        cache_dir = cache_dir.resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["USERPROFILE"] = str(cache_dir)
        os.environ["HOME"] = str(cache_dir)
        try:
            from paddleocr import TextRecognition
        except ImportError as exc:
            raise WorkflowError("PaddleOCR is not installed in the EAIA environment.") from exc
        try:
            self.model = TextRecognition(model_name=model_name, device=device)
        except Exception as exc:
            raise WorkflowError(f"Unable to initialize {model_name}: {exc}") from exc
        self.batch_size = batch_size
        self.model_name = model_name

    @staticmethod
    def _result_data(result: object) -> dict:
        if isinstance(result, dict):
            data = result
        else:
            data = getattr(result, "json", None)
            if callable(data):
                data = data()
            if isinstance(data, str):
                data = json.loads(data)
        if not isinstance(data, dict):
            return {}
        nested = data.get("res")
        return nested if isinstance(nested, dict) else data

    def _predict(self, inputs: Sequence[object]) -> list[OcrResult]:
        if not inputs:
            return []
        try:
            predictions = list(
                self.model.predict(
                    input=list(inputs),
                    batch_size=min(self.batch_size, len(inputs)),
                )
            )
        except Exception as exc:
            raise WorkflowError(f"{self.model_name} inference failed: {exc}") from exc
        results = []
        for prediction in predictions:
            data = self._result_data(prediction)
            text = str(data.get("rec_text") or "")
            score = data.get("rec_score")
            confidence = float(score) if score is not None else None
            results.append(OcrResult(text, confidence))
        if len(results) != len(inputs):
            raise WorkflowError(
                f"{self.model_name} returned {len(results)} results for {len(inputs)} inputs"
            )
        return results

    @staticmethod
    def _to_ndarray(image: Image.Image):
        try:
            import numpy as np
        except ImportError as exc:
            raise WorkflowError("numpy is required for in-memory OCR.") from exc
        # PaddleOCR examples commonly consume OpenCV-style BGR arrays.
        rgb = np.asarray(image.convert("RGB"))
        return rgb[:, :, ::-1].copy()

    def recognize(self, image_path: Path) -> OcrResult:
        return self.recognize_many([image_path])[0]

    def recognize_many(self, image_paths: Sequence[Path]) -> list[OcrResult]:
        return self._predict([str(path) for path in image_paths])

    def recognize_image(self, image: Image.Image) -> OcrResult:
        return self.recognize_images([image])[0]

    def recognize_images(self, images: Sequence[Image.Image]) -> list[OcrResult]:
        return self._predict([self._to_ndarray(image) for image in images])


class FastFineEquipmentRecognizer(FineEquipmentRecognizer):
    """Use recognition-only OCR on fixed ROIs with lazy full-OCR fallback."""

    STAT_TERMS = (
        "攻击",
        "生命",
        "防御",
        "暴击",
        "攻速",
        "攻击速度",
        "怒气",
        "能量回复",
        "治疗",
    )
    SLOT_TERMS = ("武器", "护甲", "铠甲", "防具", "手镯", "手环", "项链", "戒指")

    def __init__(
        self,
        ocr: PaddleTextRecognitionV5Mobile,
        regions: dict[str, dict[str, FineRegion]],
        output_dir: Path,
        *,
        fallback_factory: Callable[[], OcrEngine] | None = None,
        min_confidence: float = 0.55,
        save_debug_crops: bool = False,
    ):
        super().__init__(ocr=ocr, regions=regions, output_dir=output_dir)
        self.fallback_factory = fallback_factory
        self.min_confidence = min_confidence
        self.save_debug_crops = save_debug_crops
        self._fallback_ocr: OcrEngine | None = None

    def _fallback(self) -> OcrEngine | None:
        if self.fallback_factory is None:
            return None
        if self._fallback_ocr is None:
            self._fallback_ocr = self.fallback_factory()
        return self._fallback_ocr

    @staticmethod
    def _has_number(text: str) -> bool:
        return bool(re.search(r"\d", text))

    def _field_valid(self, name: str, result: OcrResult) -> bool:
        text = result.text.strip()
        if result.confidence is not None and result.confidence < self.min_confidence:
            return False
        if name == "强化等级":
            return self._has_number(text)
        if name == "装备品质":
            return bool(text)
        if name == "装备部位":
            return any(term in text for term in self.SLOT_TERMS)
        if name == "主词条与数值":
            return self._has_number(text) and any(term in text for term in self.STAT_TERMS)
        if name == "套装名称":
            return bool(text)
        if name in SUB_FIELDS:
            if not text:
                return True
            return (
                "解锁" in text
                or self._has_number(text)
                or any(term in text for term in self.STAT_TERMS)
            )
        return bool(text)

    def _save_crop(self, image: Image.Image, region: FineRegion, stem: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{stem}_{region.name}.png"
        region.crop(image).save(path)
        return path

    def _fallback_field(
        self,
        image: Image.Image,
        region: FineRegion,
        stem: str,
        current: OcrResult,
    ) -> OcrResult:
        fallback = self._fallback()
        if fallback is None:
            if self.save_debug_crops:
                self._save_crop(image, region, stem)
            return current
        path = self._save_crop(image, region, f"{stem}_fallback")
        result = fallback.recognize(path)
        if result.text.strip():
            return result
        return current

    def _recognize_memory(self, image: Image.Image, regions: Sequence[FineRegion]) -> list[OcrResult]:
        crops = [region.crop(image) for region in regions]
        results = self.ocr.recognize_images(crops)
        if self.save_debug_crops:
            for region, crop in zip(regions, crops):
                self.output_dir.mkdir(parents=True, exist_ok=True)
                crop.save(self.output_dir / f"debug_{region.name}.png")
        return results

    def recognize(self, screenshot: Path, item_id: str) -> dict:
        image = Image.open(screenshot).convert("RGB")
        probe_region = self.regions["exclusive"]["专属标识"]
        probe = self.ocr.recognize_image(probe_region.crop(image))
        if probe.confidence is not None and probe.confidence < self.min_confidence:
            probe = self._fallback_field(image, probe_region, f"{item_id}_probe", probe)
        is_exclusive = "专属" in probe.text
        profile = "exclusive" if is_exclusive else "general"
        selected = self.regions[profile]

        field_regions = [
            selected["强化等级"],
            selected["装备品质"],
            selected["装备部位"],
            selected["主词条与数值"],
            selected["套装名称"],
            *[selected[field_name] for field_name in SUB_FIELDS],
        ]
        field_results = self._recognize_memory(image, field_regions)
        corrected_results = []
        for region, ocr_result in zip(field_regions, field_results):
            if self._field_valid(region.name, ocr_result):
                corrected_results.append(ocr_result)
            else:
                corrected_results.append(
                    self._fallback_field(image, region, item_id, ocr_result)
                )

        field_values = {
            region.name: ocr_result
            for region, ocr_result in zip(field_regions, corrected_results)
        }
        result = {
            "item_id": item_id,
            "profile": profile,
            "is_exclusive": is_exclusive,
            "exclusive_probe_text": probe.text,
            "enhancement_level": self._field_value_from_result(
                selected["强化等级"], field_values["强化等级"]
            ),
            "quality": self._field_value_from_result(
                selected["装备品质"], field_values["装备品质"]
            ),
            "slot": self._field_value_from_result(
                selected["装备部位"], field_values["装备部位"]
            ),
            "primary": self._field_value_from_result(
                selected["主词条与数值"], field_values["主词条与数值"]
            ),
            "set_name": self._field_value_from_result(
                selected["套装名称"], field_values["套装名称"]
            ),
            "sub_attributes": [],
            "fully_unlocked": True,
            "ocr_mode": "recognition_only_with_lazy_fallback",
        }
        for index, field_name in enumerate(SUB_FIELDS, 1):
            field = self._field_value_from_result(selected[field_name], field_values[field_name])
            if field["raw_text"].strip() == "":
                field["value"] = None
            if field["value"] == -1:
                field["locked"] = True
                result["fully_unlocked"] = False
            field["index"] = index
            result["sub_attributes"].append(field)
        return result


class FastEquipmentWorkflow(EquipmentWorkflow):
    """Optimistic supervised capture path with main-thread persistence."""

    def __init__(
        self,
        *args,
        settle_delay: float = 0.20,
        recovery_delay: float = 0.12,
        persistence: Callable[[dict, Path], None] | None = None,
        **kwargs,
    ):
        # The parent OCR routine may run in the worker thread. Never let it call
        # the SQLite-backed persistence callback there; keep that callback here
        # and invoke it only after the Future is collected by the scanner thread.
        super().__init__(*args, persistence=None, **kwargs)
        if settle_delay < 0 or recovery_delay < 0:
            raise ValueError("settle delays must be non-negative")
        self.settle_delay = settle_delay
        self.recovery_delay = recovery_delay
        self.main_thread_persistence = persistence

    def persist_record(self, record: dict) -> None:
        """Persist one completed OCR record on the caller/scanner thread."""
        if self.main_thread_persistence is None:
            return
        fine_detail = record.get("fine_detail")
        screenshot = record.get("screenshot")
        if fine_detail is None or not screenshot:
            return
        self.main_thread_persistence(fine_detail, Path(screenshot))

    def capture_item(
        self,
        row: int,
        column: int,
        x: int,
        y: int,
        allow_unchanged: bool = False,
        *,
        before_path: Path | None = None,
        before_image: Image.Image | None = None,
    ) -> dict:
        """Capture one item using a reused baseline and normally one post-click frame."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if before_path is None:
            before_path = self.capture()
        if before_image is None:
            before_image = Image.open(before_path).convert("RGB")
        before = before_image
        stem = f"item_r{row:03d}_c{column:02d}_{time.time_ns()}"

        # The scanner has already verified the gold selection marker. Re-clicking the
        # currently selected tile cannot improve the detail panel and only forces an
        # unnecessary timeout/round-trip sequence.
        if allow_unchanged:
            return {
                "row": row,
                "column": column,
                "click": {"x": x, "y": y},
                "final_path": before_path,
                "baseline_result": None,
                "stem": stem,
                "fast_path": "reuse_selected",
            }

        self.click(x, y)
        if self.settle_delay:
            time.sleep(self.settle_delay)
        final_path = self.capture()
        current = Image.open(final_path).convert("RGB")
        if not changed(self.detail_region.crop(before), self.detail_region.crop(current)):
            if self.recovery_delay:
                time.sleep(self.recovery_delay)
            recovery_path = self.capture()
            recovery = Image.open(recovery_path).convert("RGB")
            if not changed(self.detail_region.crop(before), self.detail_region.crop(recovery)):
                raise WorkflowError(
                    f"Detail panel did not change after clicking row={row}, column={column}"
                )
            final_path = recovery_path

        return {
            "row": row,
            "column": column,
            "click": {"x": x, "y": y},
            "final_path": final_path,
            "baseline_result": None,
            "stem": stem,
            "fast_path": "single_post_click",
        }


class FastEquipmentScanner(EquipmentScanner):
    """Scanner that carries the latest frame forward across items and rows."""

    def __init__(
        self,
        workflow: FastEquipmentWorkflow,
        swipe: Callable[[tuple[int, int], tuple[int, int], int], None],
        grid: GridConfig = GridConfig(),
        *,
        scroll_settle_delay: float = 0.25,
        count_ocr: OcrEngine | None = None,
        count_region: Region = DEFAULT_EQUIPMENT_COUNT_REGION,
        progress_callback: Callable[[dict], None] | None = None,
    ):
        super().__init__(workflow, swipe, grid, count_ocr=count_ocr, count_region=count_region, progress_callback=progress_callback)
        self.scroll_settle_delay = scroll_settle_delay

    def _collect_future(self, future: Future) -> dict:
        """Collect OCR result and persist it on the scanner thread."""
        record = future.result()
        self.workflow.persist_record(record)
        return record

    def _process_rows(self, logical_start: int, screen_rows: Sequence[int], initial_column: int = 1) -> tuple[list[dict], bool]:
        records = []
        reached_empty_row = False
        calibration: dict = {}
        executor = ThreadPoolExecutor(max_workers=1)
        pending: list[Future] = []
        current_path = self.workflow.capture()
        current = Image.open(current_path).convert("RGB")
        try:
            occupied_by_row = self._normalize_occupied_rows({
                screen_row: self._row_occupied_columns(
                    current, self.grid.y_centers[screen_row], calibration
                )
                for screen_row in screen_rows
            })
            for screen_row in screen_rows:
                logical_row = logical_start + screen_row
                expected = None
                if self.scan_limit is not None:
                    expected = min(
                        self.grid.columns,
                        max(0, self.scan_limit - (logical_row - 1) * self.grid.columns),
                    )
                    if screen_row == screen_rows[0]:
                        expected = max(0, expected - initial_column + 1)
                attempt = 0
                while True:
                    attempt += 1
                    occupied_columns = occupied_by_row[screen_row]
                    if expected is not None:
                        first_column = initial_column if screen_row == screen_rows[0] else 1
                        occupied_columns = list(range(first_column, first_column + expected))
                    if not occupied_columns:
                        reached_empty_row = True
                        break
                    row_futures = []
                    first_column = initial_column if screen_row == screen_rows[0] else 1
                    for column, x in enumerate(self.grid.x_centers, 1):
                        if column < first_column:
                            continue
                        if column not in occupied_columns:
                            continue
                        y = self.grid.y_centers[screen_row]
                        allow_unchanged = self._slot_selected(current, x, y)
                        captured = self.workflow.capture_item(
                            logical_row, column, x, y,
                            allow_unchanged=allow_unchanged,
                            before_path=current_path,
                            before_image=current,
                        )
                        current_path = captured["final_path"]
                        current = Image.open(current_path).convert("RGB")
                        row_futures.append(executor.submit(
                            self.workflow.recognize_captured_item, captured
                        ))
                    recognized = [future.result() for future in row_futures]
                    required = expected if expected is not None else len(occupied_columns)
                    distinct = {
                        self._equipment_signature(record) for record in recognized
                    }
                    if len(distinct) >= required:
                        for record in recognized:
                            self.workflow.persist_record(record)
                            self._report_record(record)
                        records.extend(recognized)
                        break
                    # The row was not fully and distinctly recognized. Repeat
                    # it from column 1 before allowing the scanner to advance.
                    if attempt % 5 == 0:
                        time.sleep(0.2)
        finally:
            for future in pending:
                records.append(self._collect_future(future))
            executor.shutdown(wait=True)
        return records, reached_empty_row

    def _list_changed_after_swipe(self, before: Image.Image) -> bool:
        before_grid = self._grid_snapshot(before)
        if self.scroll_settle_delay:
            time.sleep(self.scroll_settle_delay)
        current_path = self.workflow.capture()
        current = Image.open(current_path).convert("RGB")
        current_grid = self._grid_snapshot(current)
        if changed(before_grid, current_grid):
            return True
        # One recovery sample avoids declaring bottom-of-list just because the first
        # post-swipe frame arrived too early.
        if self.workflow.poll_interval:
            time.sleep(self.workflow.poll_interval)
        retry_path = self.workflow.capture()
        retry = Image.open(retry_path).convert("RGB")
        return changed(before_grid, self._grid_snapshot(retry))


def build_fast_hdc_scanner(
    hdc: str,
    serial: str,
    screen_dir: Path,
    ocr: OcrEngine,
    output_dir: Path,
    detail_region: Region = DEFAULT_DETAIL_REGION,
    grid: GridConfig = GridConfig(),
    fine_recognizer: FineEquipmentRecognizer | None = None,
    persistence: Callable[[dict, Path], None] | None = None,
    *,
    settle_delay: float = 0.20,
    recovery_delay: float = 0.12,
    scroll_settle_delay: float = 0.25,
    count_ocr: OcrEngine | None = None,
    count_region: Region = DEFAULT_EQUIPMENT_COUNT_REGION,
    progress_callback: Callable[[dict], None] | None = None,
) -> FastEquipmentScanner:
    """Build the supervised fast scanner while retaining the HDC capture backend."""
    from screen_capture import capture_once

    controller = HdcController(hdc, serial)
    workflow = FastEquipmentWorkflow(
        capture=lambda: capture_once(hdc, serial, screen_dir),
        click=controller.click,
        ocr=ocr,
        detail_region=detail_region,
        output_dir=output_dir,
        fine_recognizer=fine_recognizer,
        persistence=persistence,
        verify_baseline_ocr=False,
        enable_coarse_ocr=False,
        settle_delay=settle_delay,
        recovery_delay=recovery_delay,
    )
    return FastEquipmentScanner(
        workflow,
        controller.swipe,
        grid,
        scroll_settle_delay=scroll_settle_delay,
        count_ocr=count_ocr,
        count_region=count_region,
        progress_callback=progress_callback,
    )
