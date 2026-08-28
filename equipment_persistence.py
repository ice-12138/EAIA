"""Translate fine OCR records into normalized equipment database rows."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol


class EquipmentFieldDictionary(Protocol):
    """Dictionary hook for canonicalizing and validating OCR field names."""

    def validate(self, record: dict) -> None: ...

    def canonicalize(self, field: str, raw_text: str) -> str: ...


def normalize_attribute(raw_text: str) -> str:
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


def is_upgrade_of(previous: dict, current: dict, *, compare_set: bool = True) -> bool:
    if previous.get("profile") != current.get("profile"):
        return False
    keys = ("slot", "set_name") if compare_set else ("slot",)
    for key in keys:
        previous_value = normalize_set_name(previous.get(key)) if key == "set_name" else normalize_attribute(_text(previous.get(key)))
        current_value = normalize_set_name(current.get(key)) if key == "set_name" else normalize_attribute(_text(current.get(key)))
        if previous_value != current_value:
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


def normalize_set_name(raw_text: object) -> str:
    """Remove OCR-only decoration while preserving the recognized name."""
    text = _text(raw_text)
    text = re.sub(r"^[\[\]（）(){}<>【】]+|[\[\]（）(){}<>【】]+$", "", text)
    text = "".join(text.split())
    return re.sub(r"套装$", "", text).strip()


def _attribute_name(field: object) -> str | None:
    """Return only the stat label, excluding OCR'd numeric values and units."""
    text = _text(field)
    if not text:
        return None
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    if not lines:
        return None
    # Some OCR engines return label and value on the same line, e.g.
    # ``攻击加成66%`` or ``暴击率22%``.  The name column must never contain
    # the numeric value, regardless of whether OCR inserted a line break.
    return normalize_attribute(lines[0]) or None


def _attribute_value(field: object) -> float | None:
    value = _field_value(field)
    return value if value is not None and value >= 0 else None


def _recognition_attributes(raw_result: str) -> tuple:
    try:
        record = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError):
        record = {}
    primary = record.get("primary") or {}
    values: list[object] = [_attribute_name(primary), _attribute_value(primary)]
    sub_attributes = record.get("sub_attributes") or []
    by_index = {int(field.get("index")): field for field in sub_attributes if isinstance(field, dict) and str(field.get("index", "")).isdigit()}
    for index in range(1, 5):
        field = by_index.get(index)
        values.extend((_attribute_name(field), _attribute_value(field)))
    return tuple(values)


def _slot(raw: str) -> str:
    terms = (("武器", "weapon"), ("护甲", "armor"), ("铠甲", "armor"), ("防具", "armor"), ("胸甲", "armor"), ("手镯", "bracelet"), ("手环", "bracelet"), ("项链", "necklace"), ("戒指", "ring"))
    for name, value in terms:
        if name in raw:
            return value
    raise ValueError(f"Unable to map OCR slot to database slot: {raw!r}")


def _set_id(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    return f"OCR_{digest}"


def _stat_type(raw: str) -> str | None:
    if "暴击率" in raw:
        return "CRIT_RATE"
    if "暴击伤害" in raw:
        return "CRIT_DMG"
    if "攻速" in raw or "攻击速度" in raw:
        return "ATK_SPEED"
    if "怒气" in raw or "能量回复" in raw:
        return "RAGE_REGEN"
    if "治疗效果" in raw or "治疗加成" in raw:
        return "HEALING_EFFECT"
    if "生命加成" in raw:
        return "HP_PCT"
    if "生命值" in raw or raw.strip().startswith("生命"):
        return "HP_FLAT"
    if "防御加成" in raw:
        return "DEF_PCT"
    if "防御" in raw:
        return "DEF_FLAT"
    if "攻击" in raw and ("%" in raw or "加成" in raw):
        return "ATK_PCT"
    if "攻击" in raw:
        return "ATK_FLAT"
    return None


_PERCENT_STAT_TYPES = {"ATK_PCT", "HP_PCT", "DEF_PCT", "CRIT_RATE", "CRIT_DMG", "RAGE_REGEN"}


def _normalized_stat_value(raw: str, stat_type: str, value: float | None) -> float | None:
    """Convert fine-OCR display units into optimizer storage units.

    Fine OCR extracts the number shown by the game UI. Percentage stats are
    therefore percentage points even when OCR misses the '%' glyph; once the
    stat label identifies a percentage type, 16 means 16% and is stored as
    0.16. This function is intentionally specific to the OCR persistence path.
    """
    if value is None or value < 0:
        return None
    numeric = float(value)
    if stat_type in _PERCENT_STAT_TYPES:
        return numeric / 100.0
    return numeric


def _unlock_level(raw: str, *, locked: bool) -> int:
    if not locked:
        return 0
    match = re.search(r"\+?\s*(\d+)\s*解锁", raw)
    return int(match.group(1)) if match else 0


def build_database_rows(record: dict, *, source_screenshot: str | Path | None = None) -> tuple[tuple, list[tuple], tuple]:
    """Build normalized equipment rows from one OCR record.

    Stat rows are ``(item_id, stat_index, stat_source, stat_type, stat_value,
    unlock_level, is_unlocked)``. Locked recognized substats are retained with
    ``stat_value=NULL`` so the effective-stat view may estimate them later.
    """
    item_id = str(record["item_id"])
    slot_text = _text(record.get("slot"))
    set_name = normalize_set_name(record.get("set_name")) or "未识别"
    primary = record.get("primary") or {}
    quality = _text(record.get("quality"))
    enhancement = record.get("enhancement_level") or {}
    enhancement_value = _attribute_value(enhancement)
    item = (item_id, _slot(slot_text), _set_id(set_name), quality or None, int(enhancement_value) if enhancement_value is not None else None)
    stats: list[tuple] = []
    candidates: list[tuple[int, str, object]] = [(0, "main", primary)]
    for fallback_index, field in enumerate(record.get("sub_attributes", []), 1):
        index = int(field.get("index", fallback_index)) if isinstance(field, dict) else fallback_index
        candidates.append((index, "sub", field))
    seen_indices: set[int] = set()
    for stat_index, source, field in candidates:
        if stat_index in seen_indices or not 0 <= stat_index <= 4:
            continue
        raw = _text(field)
        stat_type = _stat_type(raw)
        if stat_type is None:
            continue
        value = _field_value(field)
        locked = bool(isinstance(field, dict) and (field.get("locked") is True or value == -1))
        if value is None and not locked:
            continue
        seen_indices.add(stat_index)
        stats.append((item_id, stat_index, source, stat_type, _normalized_stat_value(raw, stat_type, value), _unlock_level(raw, locked=locked), 0 if locked else 1))
    raw_result = json.dumps(record, ensure_ascii=False, sort_keys=True)
    parsed_attributes = _recognition_attributes(raw_result)
    recognition = (item_id, str(record.get("profile", "general")), int(record.get("fully_unlocked") is not False), quality, slot_text, _text(primary), *parsed_attributes, set_name, raw_result, str(Path(source_screenshot).resolve()) if source_screenshot else None)
    return item, stats, recognition
