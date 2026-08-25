"""Official hero/skill reference data from publicly indexed official channels."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS official_hero_catalog (
 hero_key TEXT PRIMARY KEY, hero_name TEXT NOT NULL, title TEXT, faction TEXT, role TEXT,
 completeness TEXT NOT NULL CHECK(completeness IN ('numeric_complete','numeric_partial','mechanic_only','identity_only')),
 mechanic_summary TEXT, source_url TEXT NOT NULL, source_kind TEXT NOT NULL, source_date TEXT,
 official_channel TEXT NOT NULL, data_version TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS official_skill_catalog (
 hero_key TEXT NOT NULL REFERENCES official_hero_catalog(hero_key) ON DELETE CASCADE,
 skill_key TEXT NOT NULL, skill_name TEXT NOT NULL, skill_type TEXT NOT NULL, description TEXT,
 coefficient REAL, target_cap TEXT, duration REAL,
 direct_damage INTEGER CHECK(direct_damage IS NULL OR direct_damage IN (0,1)),
 optimizer_usable INTEGER NOT NULL DEFAULT 0 CHECK(optimizer_usable IN (0,1)),
 source_url TEXT NOT NULL, source_date TEXT, value_json TEXT, notes TEXT,
 PRIMARY KEY(hero_key, skill_key)
);
CREATE INDEX IF NOT EXISTS idx_official_hero_completeness ON official_hero_catalog(completeness);
CREATE INDEX IF NOT EXISTS idx_official_skill_optimizer_usable ON official_skill_catalog(optimizer_usable);
"""


def _read_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _payload() -> dict:
    """Merge the stable catalog with small evidence-backed incremental additions.

    Rows are keyed before insertion so an extra payload may correct source metadata
    without duplicating a hero or skill. Numeric facts are only added when visible
    in publicly indexed official posts; unknown values remain NULL.
    """
    root = Path(__file__).parent
    base = _read_payload(root / "official_hero_seed.json")
    hero_rows = {row[0]: row for row in base.get("heroes", [])}
    skill_rows = {(row[0], row[1]): row for row in base.get("skills", [])}
    versions = [base.get("version", "unknown")]
    for name in ("official_hero_seed_extra.json",):
        path = root / name
        if not path.exists():
            continue
        extra = _read_payload(path)
        versions.append(extra.get("version", name))
        for row in extra.get("heroes", []):
            hero_rows[row[0]] = row
        for row in extra.get("skills", []):
            skill_rows[(row[0], row[1])] = row
    return {
        "version": "+".join(versions),
        "official_channel": base["official_channel"],
        "heroes": list(hero_rows.values()),
        "skills": list(skill_rows.values()),
    }


def ensure_official_hero_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def seed_official_hero_catalog(connection: sqlite3.Connection) -> dict[str, int]:
    """Idempotently write official facts. Unknown numeric fields remain NULL."""
    ensure_official_hero_schema(connection)
    payload = _payload()
    channel = payload["official_channel"]
    version = payload["version"]
    connection.executemany(
        """INSERT INTO official_hero_catalog(
           hero_key,hero_name,title,faction,role,completeness,mechanic_summary,
           source_url,source_kind,source_date,official_channel,data_version
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(hero_key) DO UPDATE SET
          hero_name=excluded.hero_name,title=excluded.title,faction=excluded.faction,
          role=excluded.role,completeness=excluded.completeness,
          mechanic_summary=excluded.mechanic_summary,source_url=excluded.source_url,
          source_kind=excluded.source_kind,source_date=excluded.source_date,
          official_channel=excluded.official_channel,data_version=excluded.data_version,
          updated_at=CURRENT_TIMESTAMP""",
        [tuple(row) + (channel, version) for row in payload["heroes"]],
    )
    values = []
    for row in payload["skills"]:
        row = list(row)
        row[-1] = json.dumps(row[-1], ensure_ascii=False, sort_keys=True) if row[-1] is not None else None
        values.append(tuple(row))
    connection.executemany(
        """INSERT INTO official_skill_catalog(
           hero_key,skill_key,skill_name,skill_type,description,coefficient,target_cap,
           duration,direct_damage,optimizer_usable,source_url,source_date,value_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(hero_key,skill_key) DO UPDATE SET
          skill_name=excluded.skill_name,skill_type=excluded.skill_type,
          description=excluded.description,coefficient=excluded.coefficient,
          target_cap=excluded.target_cap,duration=excluded.duration,
          direct_damage=excluded.direct_damage,optimizer_usable=excluded.optimizer_usable,
          source_url=excluded.source_url,source_date=excluded.source_date,
          value_json=excluded.value_json""",
        values,
    )
    connection.commit()
    return official_catalog_counts(connection)


def official_catalog_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts = {
        "heroes": connection.execute("SELECT COUNT(*) FROM official_hero_catalog").fetchone()[0],
        "skills": connection.execute("SELECT COUNT(*) FROM official_skill_catalog").fetchone()[0],
        "optimizer_usable_skills": connection.execute(
            "SELECT COUNT(*) FROM official_skill_catalog WHERE optimizer_usable=1"
        ).fetchone()[0],
    }
    for completeness, count in connection.execute(
        "SELECT completeness,COUNT(*) FROM official_hero_catalog GROUP BY completeness"
    ):
        counts[str(completeness)] = int(count)
    return counts


def load_optimizer_usable_official_basics(connection: sqlite3.Connection):
    return connection.execute(
        """SELECT h.hero_key,h.hero_name,s.skill_key,s.skill_name,s.coefficient,
                  s.target_cap,s.value_json,s.source_url
           FROM official_hero_catalog h
           JOIN official_skill_catalog s USING(hero_key)
           WHERE s.optimizer_usable=1 AND s.skill_type='basic'
             AND s.coefficient IS NOT NULL AND s.target_cap IS NOT NULL
           ORDER BY h.hero_key"""
    ).fetchall()
