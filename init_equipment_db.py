"""Create an empty local equipment database for manual or CSV imports."""

from __future__ import annotations

import argparse
from pathlib import Path

from equipment_db import EquipmentDatabase


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the EAIA equipment optimizer SQLite database")
    parser.add_argument("--database", type=Path, default=Path("data/equipment.db"))
    args = parser.parse_args()
    database = EquipmentDatabase(args.database)
    try:
        database.initialize()
    finally:
        database.close()
    print(f"DATABASE_INITIALIZED path={args.database.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
