"""Optimizer-only projection of inventory equipment to its final enhancement state.

The inventory database keeps the equipment exactly as observed in game.  The
recommendation engine, however, is a cultivation-value optimizer: Mythic gear
must be compared at +16, and locked sub-stats must be assigned an explicit
estimate instead of being silently omitted.  This module provides that view
without mutating the stored equipment rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from equipment_db import EquipmentDatabase, STAT_TYPES
from equipment_models import EquipmentItem, EquipmentStat, Slot, StatType
from sub_stat_estimator import SubStatEstimator


class EquipmentProjectionError(ValueError):
    """Raised when an item cannot be projected without inventing game data."""


def _slot_scope(slot: str) -> str:
    value = str(slot).lower()
    if value == "weapon":
        return "weapon"
    if value == "armor":
        return "armor"
    if value in {"bracelet", "necklace", "ring", "right"}:
        return "right"
    raise EquipmentProjectionError(f"unsupported equipment slot: {slot}")


class OptimizerEquipmentDatabase(EquipmentDatabase):
    """Read-only optimizer view using max enhancement and P90 locked sub-stats.

    Stored inventory rows are never updated.  Main stats are replaced by the
    known value at the quality enhancement cap.  Unlocked sub-stats keep their
    observed value.  Locked sub-stats use an explicit override when present,
    otherwise the empirical/canonical P90 estimate from :class:`SubStatEstimator`.

    If an under-levelled item has no known main-stat cap, or a Mythic item does
    not expose all four sub-stat identities, the item is excluded from broad
    recommendation searches.  A direct request for that item raises an error.
    This is intentionally strict: using the current partial value would make a
    supposed +16 recommendation mathematically false.
    """

    def __init__(
        self,
        path: str | Path = "data/equipment.db",
        *,
        percentile: float = 0.90,
        min_samples_for_percentile: int = 3,
    ):
        if not 0.0 <= percentile <= 1.0:
            raise ValueError("percentile must be between 0 and 1")
        super().__init__(path)
        self.percentile = float(percentile)
        self.min_samples_for_percentile = int(min_samples_for_percentile)
        self.projection_reports: dict[str, dict[str, Any]] = {}
        self.projection_exclusions: dict[str, str] = {}

    def initialize(self) -> None:
        super().initialize()
        SubStatEstimator(
            self.connection,
            min_samples_for_estimation=self.min_samples_for_percentile,
            percentile=self.percentile,
        ).ensure_schema()

    @staticmethod
    def _row_value(row, key: str, fallback: Any = None) -> Any:
        return row[key] if key in row.keys() else fallback

    def _target_level(self, row) -> int | None:
        quality_id = self._row_value(row, "quality_id") or self._row_value(row, "tier")
        if quality_id:
            quality = self.connection.execute(
                "SELECT max_enhancement_level FROM gear_qualities WHERE quality_id=?",
                (quality_id,),
            ).fetchone()
            if quality is not None and quality[0] is not None:
                return int(quality[0])
        current = self._row_value(row, "enhancement_level")
        if current is None:
            current = self._row_value(row, "level")
        return None if current is None else int(current)

    def _current_level(self, row) -> int | None:
        value = self._row_value(row, "enhancement_level")
        if value is None:
            value = self._row_value(row, "level")
        return None if value is None else int(value)

    def _main_cap(self, *, quality_id: str | None, slot: str, stat_type: str) -> float | None:
        if not quality_id:
            return None
        row = self.connection.execute(
            """SELECT max_value_at_level_cap
               FROM main_stat_max_values
               WHERE quality_id=? AND slot_scope=? AND UPPER(stat_type)=UPPER(?)
               LIMIT 1""",
            (quality_id, _slot_scope(slot), stat_type),
        ).fetchone()
        return None if row is None or row[0] is None else float(row[0])

    def _project_item(self, row) -> EquipmentItem:
        item_id = str(row["item_id"])
        slot_value = self._row_value(row, "slot_id") or row["slot"]
        slot = Slot(slot_value)
        quality_id = self._row_value(row, "quality_id") or self._row_value(row, "tier")
        current_level = self._current_level(row)
        target_level = self._target_level(row)
        stat_rows = self.connection.execute(
            "SELECT * FROM equipment_stats WHERE item_id=? ORDER BY stat_index",
            (item_id,),
        ).fetchall()

        # Mythic recommendation is explicitly a +16 / all-four-substats view.
        if quality_id == "mythic_red":
            indices = {int(stat["stat_index"]) for stat in stat_rows}
            if indices != {0, 1, 2, 3, 4}:
                missing = sorted({0, 1, 2, 3, 4} - indices)
                raise EquipmentProjectionError(
                    f"{item_id}: Mythic +16 projection requires stat identities 0-4; missing {missing}"
                )

        estimator = SubStatEstimator(
            self.connection,
            min_samples_for_estimation=self.min_samples_for_percentile,
            percentile=self.percentile,
        )
        projected_stats: list[EquipmentStat] = []
        report_stats: list[dict[str, Any]] = []

        for stat in stat_rows:
            stat_type_raw = str(stat["stat_type"]).upper()
            if stat_type_raw not in STAT_TYPES:
                continue
            actual = None if stat["stat_value"] is None else float(stat["stat_value"])
            source = str(stat["stat_source"])
            projected: float | None
            projection_source: str

            if source == "main":
                cap = self._main_cap(
                    quality_id=quality_id,
                    slot=slot.value,
                    stat_type=stat_type_raw,
                )
                if cap is not None:
                    projected = cap
                    projection_source = "main_stat_cap"
                elif target_level is not None and current_level is not None and current_level >= target_level and actual is not None:
                    projected = actual
                    projection_source = "actual_at_level_cap"
                elif actual is not None and target_level is None:
                    projected = actual
                    projection_source = "actual_unknown_quality_cap"
                else:
                    raise EquipmentProjectionError(
                        f"{item_id}: no verified max-level main-stat value for {stat_type_raw} "
                        f"({quality_id or 'unknown quality'}, {slot.value})"
                    )
            else:
                unlocked = bool(stat["is_unlocked"])
                override = stat["estimate_override"]
                if unlocked:
                    if actual is None:
                        raise EquipmentProjectionError(
                            f"{item_id}: unlocked sub-stat {stat_type_raw} has no value"
                        )
                    projected = actual
                    projection_source = "actual"
                elif override is not None:
                    projected = float(override)
                    projection_source = "override"
                else:
                    estimate = estimator.estimate(stat_type_raw, stat["roll_grade_id"])
                    projected = estimate.expected
                    projection_source = estimate.source
                    if projected is None:
                        grade = stat["roll_grade_id"] or "all-grades"
                        raise EquipmentProjectionError(
                            f"{item_id}: no P{int(round(self.percentile * 100))} estimate for locked "
                            f"{stat_type_raw} ({grade})"
                        )

            projected_stats.append(
                EquipmentStat(
                    item_id=item_id,
                    stat_source=source,
                    stat_type=StatType(stat_type_raw),
                    stat_value=float(projected),
                    stat_index=int(stat["stat_index"]),
                )
            )
            report_stats.append({
                "stat_index": int(stat["stat_index"]),
                "stat_source": source,
                "stat_type": stat_type_raw,
                "actual_value": actual,
                "projected_value": float(projected),
                "projection_source": projection_source,
                "is_unlocked": bool(stat["is_unlocked"]),
                "roll_grade_id": stat["roll_grade_id"],
            })

        projected_level = target_level if target_level is not None else current_level
        self.projection_reports[item_id] = {
            "item_id": item_id,
            "quality_id": quality_id,
            "slot": slot.value,
            "current_level": current_level,
            "projected_level": projected_level,
            "percentile": self.percentile,
            "stats": report_stats,
        }
        return EquipmentItem(
            item_id=item_id,
            slot=slot,
            set_id=row["set_id"],
            tier=quality_id,
            level=projected_level,
            locked=bool(row["locked"]),
            available=bool(row["available"]),
            stats=tuple(projected_stats),
        )

    def load_equipment(self, item_ids: list[str] | None = None) -> list[EquipmentItem]:
        query = "SELECT * FROM equipment WHERE available=1 AND locked=0"
        params: tuple[Any, ...] = ()
        if item_ids:
            marks = ",".join("?" for _ in item_ids)
            query += f" AND item_id IN ({marks})"
            params = tuple(item_ids)
        rows = self.connection.execute(query, params).fetchall()
        result: list[EquipmentItem] = []
        direct = bool(item_ids)
        for row in rows:
            item_id = str(row["item_id"])
            try:
                result.append(self._project_item(row))
                self.projection_exclusions.pop(item_id, None)
            except EquipmentProjectionError as error:
                self.projection_exclusions[item_id] = str(error)
                if direct:
                    raise
        return result

    def projection_summary(self, item_ids: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        selected = set(item_ids or self.projection_reports)
        return {
            "mode": "max_enhancement_p90",
            "locked_substat_percentile": self.percentile,
            "items": [
                self.projection_reports[item_id]
                for item_id in sorted(selected)
                if item_id in self.projection_reports
            ],
            "excluded_item_count": len(self.projection_exclusions),
            "excluded_items": dict(sorted(self.projection_exclusions.items())),
        }
