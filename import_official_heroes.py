"""Seed the official hero/skill catalog into an EAIA SQLite database."""

from __future__ import annotations

import argparse
from pathlib import Path

from equipment_db import EquipmentDatabase
from official_hero_data import seed_official_hero_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description="Import official hero/skill catalog")
    parser.add_argument("--database", type=Path, default=Path("data/equipment.db"))
    args = parser.parse_args()
    database = EquipmentDatabase(args.database)
    try:
        database.initialize()
        counts = seed_official_hero_catalog(database.connection)
    finally:
        database.close()
    print("OFFICIAL_HERO_IMPORT " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
