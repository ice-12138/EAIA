"""Red-box annotation mapping for fine-grained equipment OCR."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from equipment_workflow import DEFAULT_DETAIL_REGION, OcrEngine, OcrResult


FIELD_NAMES = (
    "专属标识",
    "强化等级",
    "装备品质",
    "装备部位",
    "主词条与数值",
    "套装名称",
    "副词条1与数值",
    "副词条2与数值",
    "副词条3与数值",
    "副词条4与数值",
)
SHARED_FIELDS = {"强化等级", "装备品质", "装备部位"}
SUB_FIELDS = tuple(f"副词条{index}与数值" for index in range(1, 5))


@dataclass(frozen=True)
class FineRegion:
    name: str
    left: int
    top: int
    right: int
    bottom: int

    def crop(self, image: Image.Image) -> Image.Image:
        return image.crop((self.left, self.top, self.right + 1, self.bottom + 1))


def find_red_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    """Find the solid red annotation rectangle in a marked image."""
    pixels = image.convert("RGB").load()
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue = pixels[x, y]
            if red > 230 and green < 40 and blue < 40:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise ValueError("No red annotation rectangle found")
    return min(xs), min(ys), max(xs), max(ys)


def load_fine_regions(annotation_root: Path, detail_region=DEFAULT_DETAIL_REGION) -> dict[str, dict[str, FineRegion]]:
    """Load exclusive/general red boxes and map them into full-screen coordinates."""
    result: dict[str, dict[str, FineRegion]] = {"exclusive": {}, "general": {}}
    for profile in result:
        folder = annotation_root / profile
        for image_path in sorted(folder.glob("*.png")):
            field_name = image_path.stem
            if field_name not in FIELD_NAMES:
                continue
            image = Image.open(image_path)
            left, top, right, bottom = find_red_bbox(image)
            scale_x = image.width / (detail_region.right - detail_region.left + 1)
            scale_y = image.height / (detail_region.bottom - detail_region.top + 1)
            result[profile][field_name] = FineRegion(
                field_name,
                detail_region.left + round(left / scale_x),
                detail_region.top + round(top / scale_y),
                detail_region.left + round((right + 1) / scale_x) - 1,
                detail_region.top + round((bottom + 1) / scale_y) - 1,
            )
    missing = {
        "exclusive": set(FIELD_NAMES) - set(result["exclusive"]),
        "general": (set(FIELD_NAMES) - {"专属标识"} - SHARED_FIELDS) - set(result["general"]),
    }
    if missing["exclusive"] or missing["general"]:
        raise ValueError(f"Missing fine OCR regions: {missing}")
    for profile in result:
        for field in SHARED_FIELDS:
            if field not in result[profile]:
                result[profile][field] = result["exclusive"][field]
    return result


def extract_numeric_value(text: str) -> float | None:
    if "解锁" in text:
        return -1
    matches = re.findall(r"[+-]?\d+(?:\.\d+)?", text.replace(",", ""))
    if not matches:
        return None
    value = float(matches[-1])
    return int(value) if value.is_integer() else value


class FineEquipmentRecognizer:
    def __init__(self, ocr: OcrEngine, regions: dict[str, dict[str, FineRegion]], output_dir: Path):
        self.ocr = ocr
        self.regions = regions
        self.output_dir = output_dir

    def _recognize_field(self, image: Image.Image, region: FineRegion, stem: str) -> OcrResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{stem}_{region.name}.png"
        region.crop(image).save(path)
        return self.ocr.recognize(path)

    def _recognize_fields(
        self, image: Image.Image, regions: list[FineRegion], stem: str
    ) -> list[OcrResult]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for region in regions:
            path = self.output_dir / f"{stem}_{region.name}.png"
            region.crop(image).save(path)
            paths.append(path)
        recognize_many = getattr(self.ocr, "recognize_many", None)
        if callable(recognize_many):
            return recognize_many(paths)
        return [self.ocr.recognize(path) for path in paths]

    def recognize(self, screenshot: Path, item_id: str) -> dict:
        image = Image.open(screenshot).convert("RGB")
        probe = self._recognize_field(image, self.regions["exclusive"]["专属标识"], f"{item_id}_probe")
        is_exclusive = "专属" in probe.text
        profile = "exclusive" if is_exclusive else "general"
        selected = self.regions[profile]

        field_regions = [
            selected["强化等级"], selected["装备品质"], selected["装备部位"], selected["主词条与数值"],
            selected["套装名称"], *[selected[field_name] for field_name in SUB_FIELDS],
        ]
        field_results = self._recognize_fields(image, field_regions, item_id)
        field_values = {
            region.name: ocr_result
            for region, ocr_result in zip(field_regions, field_results)
        }
        result = {
            "item_id": item_id,
            "profile": profile,
            "is_exclusive": is_exclusive,
            "exclusive_probe_text": probe.text,
            "enhancement_level": self._field_value_from_result(
                selected["强化等级"], field_values["强化等级"]
            ),
            "quality": self._field_value_from_result(selected["装备品质"], field_values["装备品质"]),
            "slot": self._field_value_from_result(selected["装备部位"], field_values["装备部位"]),
            "primary": self._field_value_from_result(selected["主词条与数值"], field_values["主词条与数值"]),
            "set_name": self._field_value_from_result(selected["套装名称"], field_values["套装名称"]),
            "sub_attributes": [],
            "fully_unlocked": True,
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

    def _field_value(self, image: Image.Image, region: FineRegion, item_id: str) -> dict:
        ocr_result = self._recognize_field(image, region, item_id)
        return self._field_value_from_result(region, ocr_result)

    @staticmethod
    def _field_value_from_result(region: FineRegion, ocr_result: OcrResult) -> dict:
        return {
            "region": region.name,
            "raw_text": ocr_result.text,
            "confidence": ocr_result.confidence,
            "value": extract_numeric_value(ocr_result.text),
            "locked": "解锁" in ocr_result.text,
        }
