"""Tier-aware random sub-stat learning and percentile estimation."""

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


def normalize_set_tier(set_tier_id: str | None) -> str | None:
    """Normalize the optimizer's statistical tier grouping.

    INF equipment follows the T3 sub-stat distribution and is therefore folded
    into T3 for estimation. Unknown tiers remain unknown rather than borrowing
    samples from another tier.
    """
    if set_tier_id is None:
        return None
    value = str(set_tier_id).strip().upper()
    if not value:
        return None
    if value.startswith("INF"):
        return "T3"
    return value


def _robust_iqr_filter(values: list[float], factor: float = 1.5) -> list[float]:
    """Remove statistically non-representative tails without invalidating data.

    Removed values remain valid observations in storage. This filtering step is
    only used to build the representative distribution for locked-stat
    estimation; unusually strong task/reward gear is not treated as OCR or
    system failure.
    """
    ordered = sorted(float(value) for value in values)
    if len(ordered) < 4:
        return ordered
    q1 = _linear_percentile(ordered, 0.25)
    q3 = _linear_percentile(ordered, 0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return ordered
    low = q1 - float(factor) * iqr
    high = q3 + float(factor) * iqr
    filtered = [value for value in ordered if low <= value <= high]
    return filtered or ordered


def _confidence_for_count(sample_count: int) -> str:
    if sample_count >= 100:
        return "high"
    if sample_count >= 30:
        return "stable"
    if sample_count >= 10:
        return "provisional"
    return "insufficient_samples"


class SubStatEstimator:
    """Estimate locked values from robust same-tier empirical distributions.

    The primary distribution is conditioned on ``set_tier_id + stat_type``.
    Mythic quality is assumed by the optimizer inventory. INF is normalized to
    T3. Raw observations are preserved, while an IQR filter removes statistically
    non-representative tails only from the estimation population. P10/P60/P90
    describe the resulting representative range and expected value.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        min_samples_for_estimation: int = 10,
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
        """Create learning helpers and refresh observations from current inventory."""
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
        equipment_table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='equipment'"
        ).fetchone()
        sets_table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sets'"
        ).fetchone()
        if observation_table:
            columns = {row[1] for row in self.connection.execute("PRAGMA table_info(sub_stat_observations)")}
            if "set_tier_id" not in columns:
                self.connection.execute("ALTER TABLE sub_stat_observations ADD COLUMN set_tier_id TEXT")
        if observation_table and equipment_stats_table and equipment_table and sets_table:
            set_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(sets)")}
            equipment_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(equipment)")}
            stats_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(equipment_stats)")}
            if "set_tier_id" in set_columns:
                quality_expr = (
                    "COALESCE(e.quality_id,e.tier)" if "quality_id" in equipment_columns else "e.tier"
                )
                confidence_expr = (
                    "COALESCE(es.value_confidence,1.0)" if "value_confidence" in stats_columns else "1.0"
                )
                self.connection.execute(
                    f"""INSERT INTO sub_stat_observations(
                         item_id,stat_type,roll_grade_id,stat_value,data_source,observed_at,set_tier_id
                       )
                       SELECT es.item_id, UPPER(es.stat_type),
                              COALESCE(NULLIF(es.roll_grade_id,''),'unknown'),
                              es.stat_value, 'equipment_stats', CURRENT_TIMESTAMP,
                              CASE
                                WHEN UPPER(COALESCE(s.set_tier_id,'')) LIKE 'INF%' THEN 'T3'
                                ELSE UPPER(NULLIF(s.set_tier_id,''))
                              END
                       FROM equipment_stats es
                       JOIN equipment e ON e.item_id=es.item_id
                       LEFT JOIN sets s ON s.set_id=e.set_id
                       WHERE es.stat_source='sub'
                         AND es.is_unlocked=1
                         AND es.stat_value IS NOT NULL
                         AND {confidence_expr} >= 0.95
                         AND ({quality_expr}='mythic_red' OR {quality_expr} IS NULL)
                       ON CONFLICT(item_id,stat_type,roll_grade_id) DO UPDATE SET
                         stat_value=excluded.stat_value,
                         data_source=excluded.data_source,
                         observed_at=excluded.observed_at,
                         set_tier_id=excluded.set_tier_id"""
                )
        self.connection.commit()

    def _set_tier_for_item(self, item_id: str) -> str | None:
        if not item_id:
            return None
        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('equipment','sets')"
            )
        }
        if tables != {"equipment", "sets"}:
            return None
        set_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(sets)")}
        if "set_tier_id" not in set_columns:
            return None
        row = self.connection.execute(
            """SELECT s.set_tier_id
               FROM equipment e LEFT JOIN sets s ON s.set_id=e.set_id
               WHERE e.item_id=?""",
            (item_id,),
        ).fetchone()
        return normalize_set_tier(None if row is None else row[0])

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
        set_tier_id: str | None = None,
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

        tier = normalize_set_tier(set_tier_id) or self._set_tier_for_item(item_id)
        now = datetime.now(timezone.utc).isoformat()
        item_exists = self.connection.execute(
            "SELECT 1 FROM equipment WHERE item_id=?", (item_id,)
        ).fetchone()
        if not item_exists:
            return "ignored"

        # Unusually high/low real rolls are valid observations. Keep them in raw
        # storage and let the robust distribution filter decide whether they are
        # representative for estimation.
        self.connection.execute(
            """INSERT INTO sub_stat_observations(
                 item_id,stat_type,roll_grade_id,stat_value,data_source,observed_at,set_tier_id
               ) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(item_id,stat_type,roll_grade_id) DO UPDATE SET
                 stat_value=excluded.stat_value,
                 data_source=excluded.data_source,
                 observed_at=excluded.observed_at,
                 set_tier_id=excluded.set_tier_id""",
            (item_id, str(stat_type).upper(), roll_grade_id, normalized, data_source, now, tier),
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

    def _observation_values(self, stat_type: str, set_tier_id: str | None) -> list[float]:
        query = (
            "SELECT stat_value, data_source FROM sub_stat_observations "
            "WHERE UPPER(stat_type)=UPPER(?)"
        )
        params: list[object] = [stat_type]
        tier = normalize_set_tier(set_tier_id)
        if tier:
            query += " AND UPPER(COALESCE(set_tier_id,''))=?"
            params.append(tier)
        else:
            query += " AND set_tier_id IS NULL"
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [
            _normalize_optimizer_value(stat_type, row["stat_value"], row["data_source"])
            for row in rows
            if row["stat_value"] is not None
        ]

    def _learned_range(self, stat_type: str, roll_grade_id: str | None) -> SubStatEstimate | None:
        """Legacy non-tier fallback retained only for callers without tier data."""
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
        eligible = [
            row for row in rows
            if int(row["sample_count"] or 0) >= self.min_samples
            and row["observed_min"] is not None
            and row["observed_max"] is not None
        ]
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

    def _dictionary_range(
        self,
        stat_type: str,
        roll_grade_id: str | None,
        set_tier_id: str | None,
    ) -> SubStatEstimate | None:
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(stat_value_ranges)")}
        if not columns:
            return None
        query = "SELECT * FROM stat_value_ranges WHERE UPPER(stat_type)=UPPER(?)"
        params: list[object] = [stat_type]
        if "stat_source" in columns:
            query += " AND (stat_source='sub' OR stat_source IS NULL)"
        tier = normalize_set_tier(set_tier_id)
        if tier and "set_tier_id" in columns:
            query += " AND UPPER(COALESCE(set_tier_id,''))=?"
            params.append(tier)
        elif tier:
            return None
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

    def estimate(
        self,
        stat_type: str,
        roll_grade_id: str | None = None,
        *,
        set_tier_id: str | None = None,
        item_id: str | None = None,
    ) -> SubStatEstimate:
        self.ensure_schema()
        tier = normalize_set_tier(set_tier_id) or self._set_tier_for_item(item_id or "")
        observations = self._observation_values(stat_type, tier)
        representative = _robust_iqr_filter(observations, self.outlier_factor)
        if len(representative) >= self.min_samples:
            return SubStatEstimate(
                _linear_percentile(representative, 0.10),
                _linear_percentile(representative, self.percentile),
                _linear_percentile(representative, 0.90),
                _percentile_source(f"empirical_{tier or 'unknown_tier'}_iqr", self.percentile),
                _confidence_for_count(len(representative)),
            )

        # A known Tn must never silently borrow another tier's distribution.
        dictionary = self._dictionary_range(stat_type, roll_grade_id, tier)
        if dictionary is not None:
            return dictionary
        if tier is None:
            learned = self._learned_range(stat_type, roll_grade_id)
            if learned is not None:
                return learned
        return SubStatEstimate(None, None, None, "insufficient_data", "insufficient_samples")

    def distribution_summary(self, stat_type: str, set_tier_id: str) -> dict[str, object]:
        """Expose raw/representative counts and P10/P50/P60/P90 for diagnostics."""
        self.ensure_schema()
        tier = normalize_set_tier(set_tier_id)
        raw = self._observation_values(stat_type, tier)
        representative = _robust_iqr_filter(raw, self.outlier_factor)
        if not representative:
            return {
                "stat_type": str(stat_type).upper(),
                "set_tier_id": tier,
                "raw_sample_count": len(raw),
                "representative_sample_count": 0,
                "filtered_sample_count": len(raw),
                "p10": None,
                "p50": None,
                "p60": None,
                "p90": None,
                "confidence": "insufficient_samples",
            }
        return {
            "stat_type": str(stat_type).upper(),
            "set_tier_id": tier,
            "raw_sample_count": len(raw),
            "representative_sample_count": len(representative),
            "filtered_sample_count": len(raw) - len(representative),
            "p10": _linear_percentile(representative, 0.10),
            "p50": _linear_percentile(representative, 0.50),
            "p60": _linear_percentile(representative, 0.60),
            "p90": _linear_percentile(representative, 0.90),
            "confidence": _confidence_for_count(len(representative)),
        }

    def value_for_equipment(
        self,
        *,
        is_unlocked: bool,
        actual_value: float | None,
        stat_type: str,
        roll_grade_id: str | None = None,
        set_tier_id: str | None = None,
        item_id: str | None = None,
    ) -> SubStatEstimate:
        if is_unlocked:
            return SubStatEstimate(actual_value, actual_value, actual_value, "actual", "verified")
        return self.estimate(
            stat_type,
            roll_grade_id,
            set_tier_id=set_tier_id,
            item_id=item_id,
        )
