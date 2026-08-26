"""Small read-only HTTP API for the EAIA web application.

The browser receives JSON responses, but every response is assembled from the
SQLite database at request time.  This keeps the frontend independent from
generated JSON exports and makes OCR updates visible after a page refresh.
"""

from __future__ import annotations

import argparse
import json
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
        return (
            {"heroes": heroes, "skills": skills},
            {table: rows(connection, table) for table in CATALOG_TABLES},
            {table: rows(connection, table) for table in EQUIPMENT_TABLES},
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
