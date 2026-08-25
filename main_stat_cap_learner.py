"""Deterministic Mythic +16 main-stat value learning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3


@dataclass(frozen=True)
class MainStatLearningResult:
    status: str
    confirmation_count: int
    max_value_at_level_cap: float | None
    conflict_value: float | None = None


class MainStatCapLearner:
    """Learn fixed cap values without inferring them from lower enhancement levels."""

    def __init__(self, connection: sqlite3.Connection, *, tolerance: float = 0.0,
                 confirmations_required: int = 2):
        self.connection = connection
        self.tolerance = tolerance
        self.confirmations_required = confirmations_required

    @staticmethod
    def _scope(slot: str) -> str:
        slot = str(slot).lower()
        if slot == "weapon":
            return "weapon"
        if slot == "armor":
            return "armor"
        if slot in {"bracelet", "necklace", "ring", "right"}:
            return "right"
        raise ValueError(f"unsupported slot scope: {slot}")

    def learn(self, *, item_id: str, quality_id: str, slot: str, stat_type: str,
              enhancement_level: int, value: float, stat_source: str = "main",
              data_source: str = "ocr") -> MainStatLearningResult | None:
        if quality_id != "mythic_red" or enhancement_level != 16 or stat_source != "main":
            return None
        if not item_id or value < 0:
            raise ValueError("item_id and a non-negative value are required")
        scope = self._scope(slot)
        now = datetime.now(timezone.utc).isoformat()
        row = self.connection.execute(
            "SELECT * FROM main_stat_max_values WHERE quality_id=? AND slot_scope=? AND stat_type=?",
            (quality_id, scope, stat_type),
        ).fetchone()
        if row is None:
            result = (quality_id, scope, stat_type, enhancement_level, None, value, 1, "provisional", None, data_source, now)
        elif row["value_status"] in {"verified", "conflict"} and row["max_value_at_level_cap"] is not None \
                and abs(value - row["max_value_at_level_cap"]) > self.tolerance:
            result = (
                quality_id, scope, stat_type, row["max_enhancement_level"] or enhancement_level,
                row["max_value_at_level_cap"], row["observed_value"], row["confirmation_count"],
                "conflict", value, row["data_source"], now,
            )
        else:
            same = row["observed_value"] is not None and abs(value - row["observed_value"]) <= self.tolerance
            count = row["confirmation_count"] + 1
            observed = row["observed_value"] if same else value
            verified = row["max_value_at_level_cap"]
            status = row["value_status"]
            if verified is None and same and count >= self.confirmations_required:
                verified, status = value, "verified"
            elif not same and status == "provisional":
                status = "conflict"
            result = (
                quality_id, scope, stat_type, row["max_enhancement_level"] or enhancement_level,
                verified, observed, count, status, None, data_source, now,
            )
        self.connection.execute(
            """INSERT INTO main_stat_max_values
               (quality_id,slot_scope,stat_type,max_enhancement_level,max_value_at_level_cap,
                observed_value,confirmation_count,value_status,conflict_value,data_source,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(quality_id,slot_scope,stat_type) DO UPDATE SET
                 max_enhancement_level=excluded.max_enhancement_level,
                 max_value_at_level_cap=excluded.max_value_at_level_cap,
                 observed_value=excluded.observed_value, confirmation_count=excluded.confirmation_count,
                 value_status=excluded.value_status, conflict_value=excluded.conflict_value,
                 data_source=excluded.data_source, updated_at=excluded.updated_at""",
            result,
        )
        self.connection.commit()
        return MainStatLearningResult(result[7], result[6], result[4], result[8])

    def get_cap(self, *, slot: str, stat_type: str, quality_id: str = "mythic_red") -> float | None:
        row = self.connection.execute(
            "SELECT max_value_at_level_cap FROM main_stat_max_values WHERE quality_id=? AND slot_scope=? AND stat_type=?",
            (quality_id, self._scope(slot), stat_type),
        ).fetchone()
        return None if row is None else row[0]
