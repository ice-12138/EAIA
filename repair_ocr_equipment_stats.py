"""Repair normalized optimizer stats from persisted fine-OCR raw records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from equipment_db import EquipmentDatabase
from equipment_persistence import build_database_rows


def repair_recognized_stats(database: EquipmentDatabase, *, dry_run: bool = False) -> dict[str, int]:
    rows = database.connection.execute(
        "SELECT item_id, raw_result FROM equipment_recognition ORDER BY recognized_at"
    ).fetchall()
    repaired = 0
    skipped = 0
    written_stats = 0

    for row in rows:
        try:
            record = json.loads(row["raw_result"])
            record["item_id"] = row["item_id"]
            _, stats, _ = build_database_rows(record)
        except (TypeError, ValueError, json.JSONDecodeError):
            skipped += 1
            continue

        database.connection.execute("DELETE FROM equipment_stats WHERE item_id=?", (row["item_id"],))
        database.connection.executemany(
            "INSERT INTO equipment_stats(item_id,stat_index,stat_source,stat_type,stat_value) VALUES (?,?,?,?,?)",
            stats,
        )
        repaired += 1
        written_stats += len(stats)

    if dry_run:
        database.connection.rollback()
    else:
        database.connection.commit()
    return {"recognized": len(rows), "repaired": repaired, "skipped": skipped, "written_stats": written_stats}


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair OCR-derived equipment stats")
    parser.add_argument("--database", default="data/equipment.db", help="SQLite database path")
    parser.add_argument("--dry-run", action="store_true", help="Calculate changes and roll them back")
    args = parser.parse_args()

    database = EquipmentDatabase(Path(args.database))
    try:
        database.initialize()
        result = repair_recognized_stats(database, dry_run=args.dry_run)
    finally:
        database.close()

    mode = "DRY_RUN" if args.dry_run else "REPAIRED"
    print(
        f"{mode} recognized={result['recognized']} repaired={result['repaired']} "
        f"skipped={result['skipped']} written_stats={result['written_stats']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
