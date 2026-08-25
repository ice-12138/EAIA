#!/usr/bin/env python3
"""Convert the game's Markdown terminology tables into frontend JSON.

The source Markdown remains the editorial source of truth. This script keeps
the parsed rows and also builds an ID-indexed lookup for the web UI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


NULL_MARKERS = {"NULL", "NULL（待确认）", "NULL (待确认)", "待确认"}
# These are intentional compatibility aliases, not replacements for canonical IDs.
COMMON_ID_ALIASES = {
    "level": "level_lvl",
}
HEADER_ALIASES = {
    "中文": "zh",
    "中文标准名": "zh",
    "英文": "en",
    "English": "en",
    "English Set Name": "en",
    "English Variant Name": "en",
    "English Full Form": "en_full",
    "缩写/写法": "alias",
    "标准中文": "canonical_zh",
    "标准英文": "canonical_en",
    "数据库ID": "id",
    "数据库 ID": "id",
    "备注": "notes",
    "Tier": "tier",
    "中文状态": "status",
    "术语": "term",
    "可能实体": "entities",
    "判别方式": "disambiguation",
}


def clean(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^`|`$", "", value).strip()
    return value


def split_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [clean(part) for part in value.split("|")]


def is_separator(line: str) -> bool:
    cells = split_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def heading_level(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    return (len(match.group(1)), match.group(2)) if match else None


def slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return value or "term"


def lookup_key(value: str) -> str:
    return re.sub(r"[^\w]+", "_", value.strip().lower(), flags=re.UNICODE).strip("_")


def is_null(value: str | None) -> bool:
    return not value or value in NULL_MARKERS


def unique_id(candidate: str, used: set[str]) -> str:
    base = slug(candidate)
    result = base
    index = 2
    while result in used:
        result = f"{base}_{index}"
        index += 1
    used.add(result)
    return result


def parse_markdown(text: str, source: str) -> dict[str, Any]:
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    current_heading: str | None = None
    index = 0

    while index < len(lines):
        heading = heading_level(lines[index])
        if heading:
            level, title = heading
            if level == 2:
                current_section = {"title": title, "entries": []}
                sections.append(current_section)
            current_heading = title
            index += 1
            continue

        if current_section and lines[index].lstrip().startswith("|") and index + 1 < len(lines) and is_separator(lines[index + 1]):
            headers = split_row(lines[index])
            normalized = [HEADER_ALIASES.get(header, slug(header)) for header in headers]
            index += 2
            rows: list[dict[str, str]] = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                cells = split_row(lines[index])
                if len(cells) == len(normalized):
                    rows.append({key: cells[pos] for pos, key in enumerate(normalized)})
                index += 1
            for row in rows:
                row["section"] = current_section["title"]
                if current_heading and current_heading != current_section["title"]:
                    row["subsection"] = current_heading
                current_section["entries"].append(row)
            continue

        index += 1

    used: set[str] = set()
    terms: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    lookup: dict[str, str] = {}
    context_rules: list[dict[str, str]] = []
    entry_count = 0

    for section in sections:
        for row in section["entries"]:
            entry_count += 1
            explicit_id = row.get("id", "")
            display_zh = row.get("zh") or row.get("canonical_zh")
            display_en = row.get("en") or row.get("canonical_en")
            candidate = explicit_id if not is_null(explicit_id) else display_en or display_zh or row.get("alias", "term")
            term_id = unique_id(candidate, used)
            record: dict[str, Any] = {
                "id": term_id,
                "zh-CN": None if is_null(display_zh) else display_zh,
                "en-US": None if is_null(display_en) else display_en,
                "section": row.pop("section"),
            }
            for key in ("subsection", "notes", "tier", "status", "en_full", "entities", "disambiguation"):
                if row.get(key):
                    record[key] = row[key]
            for key, value in row.items():
                if key not in {"id", "zh", "en", "canonical_zh", "canonical_en", "section", "subsection", "notes", "tier", "status", "en_full", "entities", "disambiguation", "alias"} and value:
                    record[key] = value
            terms[term_id] = record

            lookup[lookup_key(term_id)] = term_id
            for value in (display_zh, display_en):
                if value and not is_null(value):
                    lookup.setdefault(lookup_key(value), term_id)

            alias = row.get("alias")
            if alias and display_zh and not is_null(display_zh):
                aliases[alias] = term_id
                lookup[lookup_key(alias)] = term_id

            if row.get("term") and row.get("entities"):
                context_rules.append({
                    "term": row["term"],
                    "entities": row["entities"],
                    "disambiguation": row.get("disambiguation", ""),
                })

    return {
        "version": "2026-08",
        "source": Path(source).name,
        "entryCount": entry_count,
        "terms": terms,
        "aliases": aliases,
        "lookup": {**{lookup_key(key): value for key, value in COMMON_ID_ALIASES.items()}, **lookup},
        "contextRules": context_rules,
        "sections": sections,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="source Markdown terminology file")
    parser.add_argument("--output", required=True, type=Path, help="generated JSON file")
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 2

    document = args.input.read_text(encoding="utf-8")
    result = parse_markdown(document, str(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Converted {result['entryCount']} rows into {len(result['terms'])} terms: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
