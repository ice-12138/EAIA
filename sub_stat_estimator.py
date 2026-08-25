"""Random sub-stat range learning and midpoint estimation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3


@dataclass(frozen=True)
class SubStatEstimate:
    low: float | None
    expected: float | None
    high: float | None
    source: str
    confidence: str


class SubStatEstimator:
    """Learn empirical sub-stat ranges without mutating the canonical V2.2 dictionary."""

    def __init__(self, connection: sqlite3.Connection, *, min_samples_for_estimation: int = 3,
                 outlier_factor: float = 1.5):
        self.connection = connection
        self.min_samples = min_samples_for_estimation
        self.outlier_factor = outlier_factor

    def observe(self, *, item_id: str, stat_type: str, roll_grade_id: str, value: float,
                data_source: str = "ocr", ocr_confidence: float = 1.0,
                slot: str | None = None, allowed: bool = True) -> str:
        if not item_id or value < 0 or ocr_confidence < 0.95 or not stat_type or not roll_grade_id or not allowed:
            self._queue(item_id, stat_type, roll_grade_id, value, data_source, "validation_failed")
            return "queued"
        duplicate = self.connection.execute(
            "SELECT 1 FROM sub_stat_observations WHERE item_id=? AND stat_type=? AND roll_grade_id=?",
            (item_id, stat_type, roll_grade_id),
        ).fetchone()
        if duplicate:
            return "duplicate"
        row = self.connection.execute(
            "SELECT * FROM sub_stat_learned_ranges WHERE stat_type=? AND roll_grade_id=?",
            (stat_type, roll_grade_id),
        ).fetchone()
        if row and row["observed_max"] is not None and value > row["observed_max"] * self.outlier_factor:
            self._queue(item_id, stat_type, roll_grade_id, value, data_source, "outlier")
            return "queued"
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            values = (stat_type, roll_grade_id, value, value, None, None, 1, "provisional", data_source, now)
        else:
            values = (
                stat_type, roll_grade_id,
                min(row["observed_min"], value), max(row["observed_max"], value),
                row["verified_min"], row["verified_max"], row["sample_count"] + 1,
                row["range_status"], data_source, now,
            )
        self.connection.execute(
            """INSERT INTO sub_stat_learned_ranges
               (stat_type,roll_grade_id,observed_min,observed_max,verified_min,verified_max,
                sample_count,range_status,data_source,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(stat_type,roll_grade_id) DO UPDATE SET
                observed_min=excluded.observed_min, observed_max=excluded.observed_max,
                sample_count=excluded.sample_count, data_source=excluded.data_source,
                updated_at=excluded.updated_at""",
            values,
        )
        item_exists = self.connection.execute("SELECT 1 FROM equipment WHERE item_id=?", (item_id,)).fetchone()
        if item_exists:
            self.connection.execute(
                "INSERT INTO sub_stat_observations VALUES (?,?,?,?,?,?)",
                (item_id, stat_type, roll_grade_id, value, data_source, now),
            )
        self.connection.commit()
        return "observed"

    def _queue(self, item_id, stat_type, grade, value, source, reason):
        self.connection.execute(
            """INSERT INTO stat_observation_queue(
                 item_id,stat_type,roll_grade_id,stat_value,data_source,reason,created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (item_id, stat_type, grade, value, source, reason, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def estimate(self, stat_type: str, roll_grade_id: str | None = None) -> SubStatEstimate:
        if roll_grade_id:
            rows = self.connection.execute(
                "SELECT * FROM sub_stat_learned_ranges WHERE stat_type=? AND roll_grade_id=?",
                (stat_type, roll_grade_id),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM sub_stat_learned_ranges WHERE stat_type=?", (stat_type,)
            ).fetchall()
        verified = [r for r in rows if r["verified_min"] is not None and r["verified_max"] is not None]
        if verified:
            low = min(r["verified_min"] for r in verified)
            high = max(r["verified_max"] for r in verified)
            source = "verified"
        elif rows and all(r["sample_count"] >= self.min_samples for r in rows):
            low = min(r["observed_min"] for r in rows)
            high = max(r["observed_max"] for r in rows)
            source = "observed"
        else:
            return SubStatEstimate(None, None, None, "insufficient_data", "insufficient_samples")
        return SubStatEstimate(low, (low + high) / 2, high, source, "verified" if source == "verified" else "provisional")

    def value_for_equipment(self, *, is_unlocked: bool, actual_value: float | None,
                            stat_type: str, roll_grade_id: str | None = None) -> SubStatEstimate:
        if is_unlocked:
            return SubStatEstimate(actual_value, actual_value, actual_value, "actual", "verified")
        return self.estimate(stat_type, roll_grade_id)
