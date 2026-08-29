"""Random sub-stat learning and percentile estimation for optimizer projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import sqlite3


_PERCENT_STAT_TYPES = {
    "ATK_PCT", "HP_PCT", "DEF_PCT", "CRIT_RATE", "CRIT_DMG", "RAGE_REGEN"
}


@dataclass(frozen=True)
class SubStatEstimate:
    low: float | None
    expected: float | None
    high: float | None
    source: str
    confidence: str


def _linear_percentile(values: list[float], percentile: float) -> float:
    """Return a deterministic linearly-interpolated empirical percentile."""
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    low_index = int(math.floor(position))
    high_index = int(math.ceil(position))
    if low_index == high_index:
        return ordered[low_index]
    fraction = position - low_index
    return ordered[low_index] + (ordered[high_index] - ordered[low_index]) * fraction


def _range_percentile(low: float, high: float, percentile: float) -> float:
    return float(low) + (float(high) - float(low)) * percentile


def _percentile_source(prefix: str, percentile: float) -> str:
    return f"{prefix}_p{int(round(float(percentile) * 100))}"


def _normalize_optimizer_value(stat_type: str, value: float, data_source: str | None = None) -> float:
    """Normalize legacy OCR observations to optimizer units.

    Equipment persistence stores percentage stats as decimals, while historical
    OCR learning rows may contain the UI percentage-point value. New OCR
    observations are normalized here as well. A percentage observation above
    1.0 from an OCR source is therefore interpreted as percentage points.
    """
    numeric = float(value)
    source = str(data_source or "").lower()
    if str(stat_type).upper() in _PERCENT_STAT_TYPES and "ocr" in source and abs(numeric) > 1.0:
        return numeric / 100.0
    return numeric


class SubStatEstimator:
    """Learn empirical sub-stat ranges and estimate locked values at P60 by default."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        min_samples_for_estimation: int = 3,
        outlier_factor: float = 1.5,
        percentile: float = 0.60,
    ):
        if not 0.0 <= percentile <= 1.0:
            raise ValueError("percentile must be between 0 and 1")
        self.connection = connection
        self.min_samples = max(1, int(min_samples_for_estimation))
        self.outlier_factor = float(outlier_factor)
        self.percentile = float(percentile)

    def ensure_schema(self) -> None:
        """Create learning helpers and backfill samples from unlocked inventory stats."""
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sub_stat_learned_ranges (
              stat_type TEXT NOT NULL,
              roll_grade_id TEXT NOT NULL,
              observed_min REAL,
              observed_max REAL,
              verified_min REAL,
              verified_max REAL,
              sample_count INTEGER NOT NULL DEFAULT 0,
              range_status TEXT NOT NULL DEFAULT 'provisional',
              data_source TEXT,
              updated_at TEXT,
              PRIMARY KEY(stat_type, roll_grade_id)
            );
            CREATE TABLE IF NOT EXISTS stat_observation_queue (
              queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id TEXT,
              stat_type TEXT,
              roll_grade_id TEXT,
              stat_value REAL,
              data_source TEXT NOT NULL,
              reason TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        observation_table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sub_stat_observations'"
        ).fetchone()
        equipment_stats_table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='equipment_stats'"
        ).fetchone()
        if observation_table and equipment_stats_table:
            # equipment_stats already stores optimizer units, so these rows are
            # the cleanest empirical population for percentile estimation. The
            # synthetic ``unknown`` roll grade lets a locked stat without a
            # visible grade use the cross-grade population while exact-grade
            # queries remain grade-specific.
            self.connection.execute(
                """INSERT OR IGNORE INTO sub_stat_observations(
                     item_id,stat_type,roll_grade_id,stat_value,data_source,observed_at
                   )
                   SELECT item_id, UPPER(stat_type),
                          COALESCE(NULLIF(roll_grade_id,''),'unknown'),
                          stat_value, 'equipment_stats', CURRENT_TIMESTAMP
                   FROM equipment_stats
                   WHERE stat_source='sub' AND is_unlocked=1 AND stat_value IS NOT NULL"""
            )
        self.connection.commit()

    def observe(
        self,
        *,
        item_id: str,
        stat_type: str,
        roll_grade_id: str,
        value: float,
        data_source: str = "ocr",
        ocr_confidence: float = 1.0,
        slot: str | None = None,
        allowed: bool = True,
    ) -> str:
        del slot  # reserved for a future slot-conditioned distribution
        self.ensure_schema()
        normalized = _normalize_optimizer_value(stat_type, value, data_source)
        if (
            not item_id
            or normalized < 0
            or ocr_confidence < 0.95
            or not stat_type
            or not roll_grade_id
            or not allowed
        ):
            self._queue(item_id, stat_type, roll_grade_id, normalized, data_source, "validation_failed")
            return "queued"
        duplicate = self.connection.execute(
            "SELECT 1 FROM sub_stat_observations WHERE item_id=? AND UPPER(stat_type)=UPPER(?) AND roll_grade_id=?",
            (item_id, stat_type, roll_grade_id),
        ).fetchone()
        if duplicate:
            return "duplicate"
        row = self.connection.execute(
            "SELECT * FROM sub_stat_learned_ranges WHERE UPPER(stat_type)=UPPER(?) AND roll_grade_id=?",
            (stat_type, roll_grade_id),
        ).fetchone()
        if row and row["observed_max"] is not None:
            previous_max = _normalize_optimizer_value(stat_type, row["observed_max"], row["data_source"])
            if previous_max > 0 and normalized > previous_max * self.outlier_factor:
                self._queue(item_id, stat_type, roll_grade_id, normalized, data_source, "outlier")
                return "queued"
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            values = (str(stat_type).upper(), roll_grade_id, normalized, normalized, None, None, 1, "provisional", data_source, now)
        else:
            previous_min = _normalize_optimizer_value(stat_type, row["observed_min"], row["data_source"])
            previous_max = _normalize_optimizer_value(stat_type, row["observed_max"], row["data_source"])
            values = (
                str(stat_type).upper(),
                roll_grade_id,
                min(previous_min, normalized),
                max(previous_max, normalized),
                row["verified_min"],
                row["verified_max"],
                int(row["sample_count"]) + 1,
                row["range_status"],
                data_source,
                now,
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
        item_exists = self.connection.execute(
            "SELECT 1 FROM equipment WHERE item_id=?", (item_id,)
        ).fetchone()
        if item_exists:
            self.connection.execute(
                "INSERT INTO sub_stat_observations VALUES (?,?,?,?,?,?)",
                (item_id, str(stat_type).upper(), roll_grade_id, normalized, data_source, now),
            )
        self.connection.commit()
        return "observed"

    def _queue(self, item_id, stat_type, grade, value, source, reason) -> None:
        self.ensure_schema()
        self.connection.execute(
            """INSERT INTO stat_observation_queue(
                 item_id,stat_type,roll_grade_id,stat_value,data_source,reason,created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (item_id, stat_type, grade, value, source, reason, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def _observation_values(self, stat_type: str, roll_grade_id: str | None) -> list[float]:
        query = "SELECT stat_value, data_source FROM sub_stat_observations WHERE UPPER(stat_type)=UPPER(?)"
        params: list[object] = [stat_type]
        if roll_grade_id:
            query += " AND roll_grade_id=?"
            params.append(roll_grade_id)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [
            _normalize_optimizer_value(stat_type, row["stat_value"], row["data_source"])
            for row in rows
            if row["stat_value"] is not None
        ]

    def _learned_range(self, stat_type: str, roll_grade_id: str | None) -> SubStatEstimate | None:
        self.ensure_schema()
        query = "SELECT * FROM sub_stat_learned_ranges WHERE UPPER(stat_type)=UPPER(?)"
        params: list[object] = [stat_type]
        if roll_grade_id:
            query += " AND roll_grade_id=?"
            params.append(roll_grade_id)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        verified = [row for row in rows if row["verified_min"] is not None and row["verified_max"] is not None]
        if verified:
            low = min(_normalize_optimizer_value(stat_type, row["verified_min"], row["data_source"]) for row in verified)
            high = max(_normalize_optimizer_value(stat_type, row["verified_max"], row["data_source"]) for row in verified)
            return SubStatEstimate(
                low,
                _range_percentile(low, high, self.percentile),
                high,
                _percentile_source("verified", self.percentile),
                "verified",
            )
        eligible = [row for row in rows if int(row["sample_count"] or 0) >= self.min_samples and row["observed_min"] is not None and row["observed_max"] is not None]
        if eligible:
            low = min(_normalize_optimizer_value(stat_type, row["observed_min"], row["data_source"]) for row in eligible)
            high = max(_normalize_optimizer_value(stat_type, row["observed_max"], row["data_source"]) for row in eligible)
            return SubStatEstimate(
                low,
                _range_percentile(low, high, self.percentile),
                high,
                _percentile_source("learned_range", self.percentile),
                "provisional",
            )
        return None

    def _dictionary_range(self, stat_type: str, roll_grade_id: str | None) -> SubStatEstimate | None:
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(stat_value_ranges)")}
        if not columns:
            return None
        query = "SELECT * FROM stat_value_ranges WHERE UPPER(stat_type)=UPPER(?)"
        params: list[object] = [stat_type]
        if "stat_source" in columns:
            query += " AND (stat_source='sub' OR stat_source IS NULL)"
        if roll_grade_id and "roll_grade_id" in columns:
            query += " AND roll_grade_id=?"
            params.append(roll_grade_id)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        bounds: list[tuple[float, float, str | None]] = []
        for row in rows:
            low = row["min_value"] if "min_value" in columns else None
            high = row["max_value"] if "max_value" in columns else None
            if low is None or high is None:
                low = row["verified_min"] if "verified_min" in columns else None
                high = row["verified_max"] if "verified_max" in columns else None
            if low is None or high is None:
                low = row["observed_min"] if "observed_min" in columns else None
                high = row["observed_max"] if "observed_max" in columns else None
            if low is None or high is None:
                continue
            data_source = row["data_source"] if "data_source" in columns else None
            bounds.append((
                _normalize_optimizer_value(stat_type, low, data_source),
                _normalize_optimizer_value(stat_type, high, data_source),
                data_source,
            ))
        if not bounds:
            return None
        low = min(bound[0] for bound in bounds)
        high = max(bound[1] for bound in bounds)
        return SubStatEstimate(
            low,
            _range_percentile(low, high, self.percentile),
            high,
            _percentile_source("dictionary_range", self.percentile),
            "provisional",
        )

    def estimate(self, stat_type: str, roll_grade_id: str | None = None) -> SubStatEstimate:
        self.ensure_schema()
        observations = self._observation_values(stat_type, roll_grade_id)
        if len(observations) >= self.min_samples:
            return SubStatEstimate(
                min(observations),
                _linear_percentile(observations, self.percentile),
                max(observations),
                _percentile_source("empirical", self.percentile),
                "provisional",
            )
        learned = self._learned_range(stat_type, roll_grade_id)
        if learned is not None:
            return learned
        dictionary = self._dictionary_range(stat_type, roll_grade_id)
        if dictionary is not None:
            return dictionary
        return SubStatEstimate(None, None, None, "insufficient_data", "insufficient_samples")

    def value_for_equipment(
        self,
        *,
        is_unlocked: bool,
        actual_value: float | None,
        stat_type: str,
        roll_grade_id: str | None = None,
    ) -> SubStatEstimate:
        if is_unlocked:
            return SubStatEstimate(actual_value, actual_value, actual_value, "actual", "verified")
        return self.estimate(stat_type, roll_grade_id)
