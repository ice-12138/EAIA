"""Initialize the local equipment database and seed official reference data."""

from __future__ import annotations

import argparse
from pathlib import Path

from equipment_db import EquipmentDatabase
from official_hero_data import seed_official_hero_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the EAIA equipment optimizer SQLite database")
    parser.add_argument("--database", type=Path, default=Path("data/equipment.db"))
    args = parser.parse_args()
    database = EquipmentDatabase(args.database)
    try:
        database.initialize()
        official_counts = seed_official_hero_catalog(database.connection)
    finally:
        database.close()
    summary = " ".join(f"{key}={value}" for key, value in sorted(official_counts.items()))
    print(f"DATABASE_INITIALIZED path={args.database.resolve()} {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
