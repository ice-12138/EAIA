"""Export markdown dictionary tables to the packaged V2.2 seed JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path


SOURCE = Path(r"C:\Users\lenovo\Desktop\潮汐守望者_装备字典初始化数据_2026-08_V2.2.md")
TARGET = Path(__file__).with_name("equipment_v22_seed.json")


def cells(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def parse_table(lines: list[str], start: int) -> tuple[list[dict[str, str]], int]:
    headers = cells(lines[start])
    index = start + 2
    rows = []
    while index < len(lines) and lines[index].lstrip().startswith("|"):
        values = cells(lines[index])
        if len(values) == len(headers):
            rows.append(dict(zip(headers, values)))
        index += 1
    return rows, index


def normalize(row: dict[str, str]) -> dict[str, object]:
    output = {}
    for key, value in row.items():
        key = key.strip().lower().replace(" ", "_")
        if "+16" in key and "标准值" in key:
            key = "max_value_at_level_cap"
        value = value.strip()
        if value in {"", "NULL", "null"}:
            output[key] = None
        elif value.lower() in {"true", "false"}:
            output[key] = 1 if value.lower() == "true" else 0
        else:
            try:
                output[key] = float(value) if "." in value else int(value)
            except ValueError:
                output[key] = value
    return output


def main() -> None:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    wanted = {
        "equipment_categories": "equipment_categories", "equipment_slots": "equipment_slots", "set_tiers": "set_tiers",
        "gear_qualities": "gear_qualities", "stat_roll_grades": "stat_roll_grades", "stat_definitions": "stat_definitions",
        "stat_category_map": "stat_category_map", "stat_slot_rules": "stat_slot_rules", "stat_value_ranges": "stat_value_ranges",
        "main_stat_max_values": "main_stat_max_values", "sets": "sets", "set_effects": "set_effects", "ocr_aliases": "ocr_aliases",
        "set_evolutions": "set_evolutions", "special_effect_definitions": "special_effect_definitions",
    }
    result = {name: [] for name in wanted.values()}
    current = None
    i = 0
    while i < len(lines):
        if lines[i].startswith("## "):
            match = re.match(r"^##\s+[^`]*`([^`]+)`", lines[i])
            current = wanted.get(match.group(1).split("：")[0]) if match else None
        if current and lines[i].lstrip().startswith("|") and i + 1 < len(lines) and lines[i + 1].lstrip().startswith("|"):
            rows, i = parse_table(lines, i)
            result[current].extend(normalize(row) for row in rows)
            continue
        i += 1
    # The evolution and special-effect sections use prose headings instead of backtick table names.
    for key, heading in (("set_evolutions", "首批数据"), ("special_effect_definitions", "异化效果字典")):
        start = next((n for n, line in enumerate(lines) if heading in line), None)
        if start is None:
            continue
        for n in range(start, len(lines) - 1):
            if lines[n].lstrip().startswith("|") and lines[n + 1].lstrip().startswith("|"):
                rows, _ = parse_table(lines, n)
                result[key] = [normalize(row) for row in rows]
                break
    result["set_evolutions"] = [
        {key: row.get(key) for key in ("from_set_id", "to_set_id", "material_type", "notes") if key in row}
        for row in result["set_evolutions"]
    ]
    result["main_stat_max_values"] = [
        {key: value for key, value in row.items() if key in {
            "quality_id", "slot_scope", "stat_type", "max_enhancement_level", "max_value_at_level_cap", "observed_max",
            "confirmation_count", "value_status", "data_source", "game_version", "confidence", "notes"
        }}
        for row in result["main_stat_max_values"]
    ]
    special_rows = []
    for row in result["special_effect_definitions"]:
        converted = {key: value for key, value in row.items() if key != "effect_summary"}
        if not converted.get("notes"):
            converted["notes"] = row.get("effect_summary")
        converted.setdefault("special_type", "variant")
        special_rows.append(converted)
    result["special_effect_definitions"] = special_rows
    TARGET.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {TARGET} rows=" + ", ".join(f"{k}:{len(v)}" for k, v in result.items()))


if __name__ == "__main__":
    main()
