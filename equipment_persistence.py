"""Translate fine OCR records into the existing equipment SQLite schema."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol


class EquipmentFieldDictionary(Protocol):
    """Future dictionary hook for canonicalizing and validating OCR field names."""

    def validate(self, record: dict) -> None: ...

    def canonicalize(self, field: str, raw_text: str) -> str: ...


def normalize_attribute(raw_text: str) -> str:
    """Keep the OCR attribute name while removing its numeric value and markers."""
    text = re.sub(r"[+-]?\d+(?:\.\d+)?", "", raw_text or "")
    text = text.replace("解锁", "")
    return "".join(text.split()).replace("%", "")


def _field_value(field: object) -> float | None:
    if isinstance(field, (int, float)):
        return float(field)
    if not isinstance(field, dict):
        return None
    value = field.get("value")
    return float(value) if value is not None else None


def is_upgrade_of(previous: dict, current: dict) -> bool:
    """Return whether current is the same equipment with upgraded values."""
    for key in ("profile",):
        if previous.get(key) != current.get(key):
            return False
    for key in ("slot", "set_name"):
        if normalize_attribute(_text(previous.get(key))) != normalize_attribute(_text(current.get(key))):
            return False
    previous_primary = previous.get("primary") or {}
    current_primary = current.get("primary") or {}
    if normalize_attribute(_text(previous_primary)) != normalize_attribute(_text(current_primary)):
        return False
    old_value = _field_value(previous_primary)
    new_value = _field_value(current_primary)
    if old_value is None or new_value is None or new_value < old_value:
        return False

    old_subs = previous.get("sub_attributes", [])
    new_subs = current.get("sub_attributes", [])
    if len(old_subs) != len(new_subs):
        return False
    for old, new in zip(old_subs, new_subs):
        if normalize_attribute(_text(old)) != normalize_attribute(_text(new)):
            return False
        old_value = _field_value(old)
        new_value = _field_value(new)
        if old_value is None or new_value is None:
            return False
        if new_value != old_value and not (old_value == -1 and new_value != -1):
            return False
    return True


def _text(field: object) -> str:
    if isinstance(field, dict):
        return str(field.get("raw_text") or "").strip()
    return str(field or "").strip()


def _attribute_name(field: object) -> str | None:
    """Extract the stat name from an OCR field's raw text."""
    text = _text(field)
    if not text:
        return None
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    return lines[0] if lines else None


def _attribute_value(field: object) -> float | None:
    """Return a known numeric OCR value; negative values represent locked fields."""
    value = _field_value(field)
    return value if value is not None and value >= 0 else None


def _recognition_attributes(raw_result: str) -> tuple:
    """Parse the denormalized recognition columns from the persisted raw JSON."""
    try:
        record = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError):
        record = {}
    primary = record.get("primary") or {}
    values: list[object] = [_attribute_name(primary), _attribute_value(primary)]
    sub_attributes = record.get("sub_attributes") or []
    by_index = {
        int(field.get("index")): field
        for field in sub_attributes
        if isinstance(field, dict) and str(field.get("index", "")).isdigit()
    }
    for index in range(1, 5):
        field = by_index.get(index)
        values.extend((_attribute_name(field), _attribute_value(field)))
    return tuple(values)


def _slot(raw: str) -> str:
    terms = (("武器", "weapon"), ("护甲", "armor"), ("铠甲", "armor"),
             ("手镯", "bracelet"), ("手环", "bracelet"), ("项链", "necklace"),
             ("戒指", "ring"))
    for name, value in terms:
        if name in raw:
            return value
    raise ValueError(f"Unable to map OCR slot to database slot: {raw!r}")


def _set_id(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    return f"OCR_{digest}"


def _stat_type(raw: str) -> str | None:
    """Map an OCR stat label to the optimizer stat vocabulary.

    More specific labels must be checked before the generic ``攻击`` branch;
    otherwise ``攻击速度`` is incorrectly stored as flat attack.
    """
    if "暴击率" in raw:
        return "CRIT_RATE"
    if "暴击伤害" in raw:
        return "CRIT_DMG"
    if "攻速" in raw or "攻击速度" in raw:
        return "ATK_SPEED"
    if "怒气" in raw or "能量回复" in raw:
        return "RAGE_REGEN"
    if "攻击" in raw and ("%" in raw or "加成" in raw):
        return "ATK_PCT"
    if "攻击" in raw:
        return "ATK_FLAT"
    return None


_PERCENT_STAT_TYPES = {"ATK_PCT", "CRIT_RATE", "CRIT_DMG", "RAGE_REGEN"}


def _normalized_stat_value(raw: str, stat_type: str, value: float) -> float:
    """Convert OCR percentage displays to the decimal representation used by rules.

    The raw OCR record remains untouched for display/audit. Only the normalized
    ``equipment_stats.stat_value`` uses decimal percentages, e.g. ``66%`` ->
    ``0.66``. Flat attack and attack speed remain in their displayed units.
    """
    numeric = float(value)
    if stat_type in _PERCENT_STAT_TYPES and "%" in raw:
        return numeric / 100.0
    return numeric


def build_database_rows(record: dict, *, source_screenshot: str | Path | None = None) -> tuple[tuple, list[tuple], tuple]:
    item_id = str(record["item_id"])
    slot_text = _text(record.get("slot"))
    set_name = _text(record.get("set_name")) or "未识别套装"
    primary = record.get("primary") or {}
    quality = _text(record.get("quality"))
    enhancement = record.get("enhancement_level") or {}
    enhancement_value = _attribute_value(enhancement)
    item = (item_id, _slot(slot_text), _set_id(set_name), quality or None,
            int(enhancement_value) if enhancement_value is not None else None)
    stats: list[tuple] = []
    candidates = [("main", primary)] + [("sub", value) for value in record.get("sub_attributes", [])]
    seen: set[tuple[str, str]] = set()
    for source, field in candidates:
        raw = _text(field)
        value = field.get("value") if isinstance(field, dict) else _field_value(field)
        stat_type = _stat_type(raw)
        if stat_type is None or value is None or float(value) < 0:
            continue
        key = (source, stat_type)
        if key in seen:  # Existing schema keys stats by source and type.
            continue
        seen.add(key)
        stats.append((item_id, source, stat_type, _normalized_stat_value(raw, stat_type, float(value))))
    raw_result = json.dumps(record, ensure_ascii=False, sort_keys=True)
    parsed_attributes = _recognition_attributes(raw_result)
    recognition = (item_id, str(record.get("profile", "general")), int(not bool(record.get("fully_unlocked") is False)),
                   quality, slot_text, _text(primary), *parsed_attributes, set_name, raw_result,
                   str(Path(source_screenshot).resolve()) if source_screenshot else None)
    return item, stats, recognition
