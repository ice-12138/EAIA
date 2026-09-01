"""Rebuild locked-substat learning data from the current equipment inventory.

This command preserves player equipment and only resets derived learning state.
It then repopulates observations from unlocked Mythic sub-stats using the current
Tn-aware estimator (INF is normalized to T3).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from equipment_db import EquipmentDatabase
from sub_stat_estimator import SubStatEstimator


DEFAULT_DATABASE = Path(__file__).resolve().parent / "data" / "equipment.db"
LEARNING_TABLES = (
    "sub_stat_observations",
    "sub_stat_learned_ranges",
    "stat_observation_queue",
)


def _table_exists(connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _table_count(connection, table: str) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def rebuild_substat_learning(
    database_path: str | Path = DEFAULT_DATABASE,
    *,
    min_samples: int = 10,
    percentile: float = 0.60,
) -> dict[str, Any]:
    """Clear derived learning state and relearn from the current equipment table.

    Equipment rows, equipment stats, OCR records, sets and dictionary data are
    never deleted. Only derived sub-stat learning tables are cleared.
    """
    database = EquipmentDatabase(database_path)
    try:
        database.initialize()
        connection = database.connection

        equipment_count = int(connection.execute("SELECT COUNT(*) FROM equipment").fetchone()[0])
        unlocked_substat_count = int(
            connection.execute(
                """SELECT COUNT(*)
                   FROM equipment_stats es
                   JOIN equipment e ON e.item_id=es.item_id
                   WHERE es.stat_source='sub'
                     AND es.is_unlocked=1
                     AND es.stat_value IS NOT NULL
                     AND (COALESCE(e.quality_id,e.tier)='mythic_red'
                          OR COALESCE(e.quality_id,e.tier) IS NULL)"""
            ).fetchone()[0]
        )
        cleared = {table: _table_count(connection, table) for table in LEARNING_TABLES}

        connection.execute("BEGIN")
        try:
            for table in LEARNING_TABLES:
                if _table_exists(connection, table):
                    connection.execute(f'DELETE FROM "{table}"')
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        estimator = SubStatEstimator(
            connection,
            min_samples_for_estimation=min_samples,
            percentile=percentile,
        )
        estimator.ensure_schema()

        rebuilt_observations = _table_count(connection, "sub_stat_observations")
        groups = connection.execute(
            """SELECT UPPER(stat_type) AS stat_type,
                      UPPER(set_tier_id) AS set_tier_id,
                      COUNT(*) AS sample_count
                 FROM sub_stat_observations
                WHERE set_tier_id IS NOT NULL
                GROUP BY UPPER(set_tier_id), UPPER(stat_type)
                ORDER BY UPPER(set_tier_id), UPPER(stat_type)"""
        ).fetchall()
        summaries = [
            estimator.distribution_summary(row["stat_type"], row["set_tier_id"])
            for row in groups
        ]

        return {
            "ok": True,
            "database": str(Path(database_path)),
            "equipment_count": equipment_count,
            "eligible_unlocked_substat_count": unlocked_substat_count,
            "cleared_rows": cleared,
            "rebuilt_observation_count": rebuilt_observations,
            "group_count": len(summaries),
            "min_representative_samples": int(min_samples),
            "percentile": float(percentile),
            "groups": summaries,
        }
    finally:
        database.close()


def _print_report(report: dict[str, Any]) -> None:
    print(f"DATABASE={report['database']}")
    print(f"EQUIPMENT_PRESERVED={report['equipment_count']}")
    print(f"ELIGIBLE_UNLOCKED_SUBSTATS={report['eligible_unlocked_substat_count']}")
    print(
        "CLEARED="
        + ", ".join(f"{table}:{count}" for table, count in report["cleared_rows"].items())
    )
    print(f"REBUILT_OBSERVATIONS={report['rebuilt_observation_count']}")
    print(f"GROUPS={report['group_count']}")
    for group in report["groups"]:
        def fmt(value: object) -> str:
            if value is None:
                return "—"
            return f"{float(value):.6g}"

        print(
            f"{group['set_tier_id']} {group['stat_type']}: "
            f"raw={group['raw_sample_count']} "
            f"representative={group['representative_sample_count']} "
            f"filtered={group['filtered_sample_count']} "
            f"P10={fmt(group['p10'])} P50={fmt(group['p50'])} "
            f"P60={fmt(group['p60'])} P90={fmt(group['p90'])} "
            f"confidence={group['confidence']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="清除旧副词条学习结果，并从当前装备表按 Tn 重新学习。"
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument("--percentile", type=float, default=0.60)
    parser.add_argument("--json", action="store_true", help="以 JSON 输出重建报告")
    args = parser.parse_args()

    report = rebuild_substat_learning(
        args.database,
        min_samples=args.min_samples,
        percentile=args.percentile,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)


if __name__ == "__main__":
    main()
