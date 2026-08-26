"""Small read-only HTTP API for the EAIA web application.

The browser receives JSON responses, but every response is assembled from the
SQLite database at request time.  This keeps the frontend independent from
generated JSON exports and makes OCR updates visible after a page refresh.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from equipment_db import EquipmentDatabase
from official_hero_data import seed_official_hero_catalog


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "equipment.db"
FRONTEND_DIST = ROOT / "frontend" / "dist"

CATALOG_TABLES = (
    "equipment_categories", "equipment_slots", "set_tiers", "gear_qualities",
    "stat_roll_grades", "stat_definitions", "stat_category_map",
    "stat_slot_rules", "stat_value_ranges", "main_stat_max_values",
    "sets", "set_effects", "ocr_aliases", "set_evolutions",
    "special_effect_definitions", "v_set_catalog",
)
EQUIPMENT_TABLES = ("v_equipment_full", "equipment", "equipment_stats", "equipment_recognition")

_PERCENT_STAT_NAMES = {"攻击加成", "生命加成", "防御加成", "暴击率", "暴击伤害", "怒气回复", "治疗效果", "治疗加成"}


def _display_stat_name(value: object) -> object:
    """Remove OCR'd numeric values/units from equipment stat labels."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[+-]?\d+(?:\.\d+)?", "", text)
    text = text.replace("%", "")
    text = "".join(text.split())
    return text or None


def _display_stat_value(name: object, value: object) -> object:
    """Format overview values with the unit implied by the stat label."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    label = str(name or "").strip()
    if label in _PERCENT_STAT_NAMES:
        if number.is_integer():
            return f"{int(number)}%"
        return f"{number:g}%"
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _format_equipment_overview(rows: list[dict]) -> list[dict]:
    """Prepare the UI-facing overview without changing database value semantics."""
    result = []
    for row in rows:
        row = dict(row)
        for name_key, value_key in (
            ("main_stat_name", "main_stat_value"),
            ("sub_stat_1_name", "sub_stat_1_value"),
            ("sub_stat_2_name", "sub_stat_2_value"),
            ("sub_stat_3_name", "sub_stat_3_value"),
            ("sub_stat_4_name", "sub_stat_4_value"),
        ):
            row[name_key] = _display_stat_name(row.get(name_key))
            row[value_key] = _display_stat_value(row.get(name_key), row.get(value_key))
        result.append(row)
    return result


def rows(connection: sqlite3.Connection, source: str) -> list[dict]:
    """Read an allowlisted table or view as JSON-compatible dictionaries."""
    result = connection.execute(f'SELECT * FROM "{source}"')
    return [dict(row) for row in result.fetchall()]


def database_payload(database: Path) -> tuple[dict, dict, dict]:
    """Return hero, dictionary, and equipment payloads from one DB snapshot."""
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        heroes = rows(connection, "official_hero_catalog")
        skills = rows(connection, "official_skill_catalog")
        # Keep value_json structured for API consumers while preserving NULL.
        for skill in skills:
            if skill["value_json"]:
                try:
                    skill["value_json"] = json.loads(skill["value_json"])
                except json.JSONDecodeError:
                    pass
        equipment = {table: rows(connection, table) for table in EQUIPMENT_TABLES}
        equipment["v_equipment_full"] = _format_equipment_overview(equipment["v_equipment_full"])
        return (
            {"heroes": heroes, "skills": skills},
            {table: rows(connection, table) for table in CATALOG_TABLES},
            equipment,
        )
    finally:
        connection.close()


class EAIARequestHandler(SimpleHTTPRequestHandler):
    """Serve API responses and the built frontend from the same origin."""

    database = DEFAULT_DATABASE
    frontend_root = FRONTEND_DIST

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.frontend_root), **kwargs)

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path.startswith("/api/"):
            try:
                if path == "/api/health":
                    self._send_json({"status": "ok", "database": str(self.database)})
                else:
                    hero_data, catalog_data, equipment_data = database_payload(self.database)
                    payloads = {
                        "/api/heroes": hero_data,
                        "/api/catalog": catalog_data,
                        "/api/equipment": equipment_data,
                    }
                    if path in payloads:
                        self._send_json(payloads[path])
                    else:
                        self._send_json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
            except (sqlite3.Error, OSError) as error:
                self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def create_server(host: str = "127.0.0.1", port: int = 8000, database: Path = DEFAULT_DATABASE) -> ThreadingHTTPServer:
    database = Path(database).resolve()
    initializer = EquipmentDatabase(database)
    try:
        initializer.initialize()
        seed_official_hero_catalog(initializer.connection)
    finally:
        initializer.close()
    EAIARequestHandler.database = database
    EAIARequestHandler.frontend_root = FRONTEND_DIST
    return ThreadingHTTPServer((host, port), EAIARequestHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the EAIA frontend and SQLite-backed API")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.database)
    print(f"EAIA web server: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
