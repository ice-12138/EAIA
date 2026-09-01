"""Optimizer-only projection of inventory equipment to its final enhancement state.

The inventory database keeps equipment exactly as observed in game. The
recommendation engine compares gear at its cultivation ceiling whenever that
ceiling is known, estimates locked sub-stats at P60 by default, and exposes
normalized set effects to HeroCore. If a max-level main stat value is still
unknown, the observed current-level main stat is retained as a conservative
fallback instead of excluding the item. Stored inventory rows are never
mutated.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from equipment_db import EquipmentDatabase, STAT_TYPES
from equipment_models import EquipmentItem, EquipmentStat, Slot, StatType
from equipment_set_variants import load_hero_core_set_effects
from sub_stat_estimator import SubStatEstimator, normalize_set_tier


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
    """Read-only optimizer view using max enhancement and tier-aware P60 sub-stats.

    Main stats use the known value at the quality enhancement cap when one is
    available. Unlocked sub-stats keep their observed value. Locked sub-stats
    use an explicit override when present, otherwise a robust empirical P60
    estimated from the same set tier and stat type. INF is grouped into T3.

    When the max-level main-stat value is not yet known, an item with a known
    current enhancement level and observed main-stat value remains usable. The
    current observed main stat is used as a conservative fallback and the
    projection report explicitly marks that item as partial. This lets, for
    example, a known +8 main stat participate in recommendation until its +16
    cap is measured, without pretending that the +8 value is the +16 value.

    HeroCore also reads the full normalized V2.2 set-effect catalog through this
    database. Historical ``enabled_in_optimizer`` flags only described limits of
    the old simulator and must not suppress effects that HeroCore can now model.
    Temporary T1 -> T2 set overrides are calculation-only and are never
    persisted to SQLite.

    A Mythic item still needs all four sub-stat identities. Missing stat
    identities or missing locked-substat estimates remain exclusion reasons.
    """

    def __init__(
        self,
        path: str | Path = "data/equipment.db",
        *,
        percentile: float = 0.60,
        min_samples_for_percentile: int = 10,
    ):
        if not 0.0 <= percentile <= 1.0:
            raise ValueError("percentile must be between 0 and 1")
        super().__init__(path)
        self.percentile = float(percentile)
        self.min_samples_for_percentile = int(min_samples_for_percentile)
        self.projection_reports: dict[str, dict[str, Any]] = {}
        self.projection_exclusions: dict[str, str] = {}
        self._set_variant_overrides: dict[str, str] = {}

    def initialize(self) -> None:
        super().initialize()
        SubStatEstimator(
            self.connection,
            min_samples_for_estimation=self.min_samples_for_percentile,
            percentile=self.percentile,
        ).ensure_schema()

    def load_set_effects(self):
        """Return all semantically normalizable set effects for HeroCore."""
        return load_hero_core_set_effects(self)

    def set_variant_overrides(self, overrides: dict[str, str] | None = None) -> None:
        """Apply calculation-only set identities for one ascension variant."""
        self._set_variant_overrides = {
            str(item_id): str(set_id)
            for item_id, set_id in (overrides or {}).items()
            if item_id and set_id
        }

    def clear_variant_overrides(self) -> None:
        self._set_variant_overrides.clear()

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

    def _set_tier(self, row) -> str | None:
        set_id = self._row_value(row, "set_id")
        if not set_id:
            return None
        tier = self.connection.execute(
            "SELECT set_tier_id FROM sets WHERE set_id=?",
            (set_id,),
        ).fetchone()
        return normalize_set_tier(None if tier is None else tier[0])

    def _project_item(self, row) -> EquipmentItem:
        item_id = str(row["item_id"])
        slot_value = self._row_value(row, "slot_id") or row["slot"]
        slot = Slot(slot_value)
        quality_id = self._row_value(row, "quality_id") or self._row_value(row, "tier")
        set_tier_id = self._set_tier(row)
        current_level = self._current_level(row)
        target_level = self._target_level(row)
        stat_rows = self.connection.execute(
            "SELECT * FROM equipment_stats WHERE item_id=? ORDER BY stat_index",
            (item_id,),
        ).fetchall()

        if quality_id == "mythic_red":
            indices = {int(stat["stat_index"]) for stat in stat_rows}
            if indices != {0, 1, 2, 3, 4}:
                missing = sorted({0, 1, 2, 3, 4} - indices)
                raise EquipmentProjectionError(
                    f"{item_id}: Mythic projection requires stat identities 0-4; missing {missing}"
                )

        estimator = SubStatEstimator(
            self.connection,
            min_samples_for_estimation=self.min_samples_for_percentile,
            percentile=self.percentile,
        )
        projected_stats: list[EquipmentStat] = []
        report_stats: list[dict[str, Any]] = []
        report_warnings: list[str] = []
        uses_current_main_fallback = False
        main_stat_level_used: int | None = None

        for stat in stat_rows:
            stat_type_raw = str(stat["stat_type"]).upper()
            if stat_type_raw not in STAT_TYPES:
                continue
            actual = None if stat["stat_value"] is None else float(stat["stat_value"])
            source = str(stat["stat_source"])
            projected: float | None
            projection_source: str
            stat_level_used: int | None = None
            normalized_unlocked = source == "main" or actual is not None

            if source == "main":
                cap = self._main_cap(
                    quality_id=quality_id,
                    slot=slot.value,
                    stat_type=stat_type_raw,
                )
                if cap is not None:
                    projected = cap
                    projection_source = "main_stat_cap"
                    stat_level_used = target_level
                elif (
                    target_level is not None
                    and current_level is not None
                    and current_level >= target_level
                    and actual is not None
                ):
                    projected = actual
                    projection_source = "actual_at_level_cap"
                    stat_level_used = current_level
                elif actual is not None and current_level is not None:
                    projected = actual
                    projection_source = "current_level_main_fallback"
                    stat_level_used = current_level
                    uses_current_main_fallback = True
                    target_text = f"+{target_level}" if target_level is not None else "max level"
                    warning = (
                        f"{item_id}: {stat_type_raw} {target_text} main-stat cap is unknown; "
                        f"using observed +{current_level} value {actual:g} for recommendation"
                    )
                    report_warnings.append(warning)
                elif actual is not None and target_level is None:
                    projected = actual
                    projection_source = "actual_unknown_quality_cap"
                    stat_level_used = current_level
                else:
                    raise EquipmentProjectionError(
                        f"{item_id}: no usable main-stat value for {stat_type_raw} "
                        f"({quality_id or 'unknown quality'}, {slot.value})"
                    )
                main_stat_level_used = stat_level_used
            else:
                # Some historical/manual rows contain an inverted or stale
                # is_unlocked flag. A numeric observed value is definitive:
                # use it as actual. NULL means the roll is still unknown and
                # should use override/P60. Do not let metadata overwrite data.
                unlocked = actual is not None
                override = stat["estimate_override"]
                if unlocked:
                    projected = actual
                    projection_source = "actual"
                    stat_level_used = current_level
                elif override is not None:
                    projected = float(override)
                    projection_source = "override"
                    stat_level_used = target_level
                else:
                    estimate = estimator.estimate(
                        stat_type_raw,
                        stat["roll_grade_id"],
                        set_tier_id=set_tier_id,
                        item_id=item_id,
                    )
                    projected = estimate.expected
                    projection_source = estimate.source
                    stat_level_used = target_level
                    if projected is None:
                        tier_text = set_tier_id or "unknown-tier"
                        raise EquipmentProjectionError(
                            f"{item_id}: no P{int(round(self.percentile * 100))} estimate for locked "
                            f"{stat_type_raw} ({tier_text}; representative samples "
                            f"<{self.min_samples_for_percentile})"
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
                "value_level": stat_level_used,
                "is_unlocked": normalized_unlocked,
                "roll_grade_id": stat["roll_grade_id"],
                "set_tier_id": set_tier_id,
            })

        projected_level = target_level if target_level is not None else current_level
        self.projection_reports[item_id] = {
            "item_id": item_id,
            "quality_id": quality_id,
            "set_tier_id": set_tier_id,
            "slot": slot.value,
            "current_level": current_level,
            "projected_level": projected_level,
            "main_stat_level_used": main_stat_level_used,
            "uses_current_main_fallback": uses_current_main_fallback,
            "projection_complete": not uses_current_main_fallback,
            "warnings": report_warnings,
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
                item = self._project_item(row)
                override = self._set_variant_overrides.get(item_id)
                if override:
                    item = replace(item, set_id=override)
                result.append(item)
                self.projection_exclusions.pop(item_id, None)
            except EquipmentProjectionError as error:
                self.projection_exclusions[item_id] = str(error)
                if direct:
                    raise
        return result

    def projection_summary(self, item_ids: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        selected = set(item_ids or self.projection_reports)
        reports = [
            self.projection_reports[item_id]
            for item_id in sorted(selected)
            if item_id in self.projection_reports
        ]
        fallback_items = [report for report in reports if report.get("uses_current_main_fallback")]
        active_overrides = {
            item_id: set_id
            for item_id, set_id in sorted(self._set_variant_overrides.items())
            if item_id in selected
        }
        percentile_label = int(round(self.percentile * 100))
        return {
            "mode": f"max_enhancement_p{percentile_label}",
            "locked_substat_percentile": self.percentile,
            "locked_substat_grouping": "set_tier_id+stat_type",
            "locked_substat_robust_filter": "IQR_1.5",
            "locked_substat_min_representative_samples": self.min_samples_for_percentile,
            "inf_tier_mapping": "T3",
            "main_stat_fallback_policy": "use_current_observed_value_when_max_cap_unknown",
            "current_main_fallback_item_count": len(fallback_items),
            "items": reports,
            "set_variant_overrides": active_overrides,
            "excluded_item_count": len(self.projection_exclusions),
            "excluded_items": dict(sorted(self.projection_exclusions.items())),
        }
