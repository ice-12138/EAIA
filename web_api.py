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
import threading
import time
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from equipment_db import EquipmentDatabase
from official_hero_data import seed_official_hero_catalog
from screen_capture import CaptureError, choose_target, find_hdc, list_targets


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "equipment.db"
FRONTEND_DIST = ROOT / "frontend" / "dist"
HDC = r"D:\DEVECO~2\sdk\default\OPENHA~1\TOOLCH~1\hdc.exe"
SERIAL = "FMR0223A30001935"

_scan_lock = threading.Lock()
_scan_state = {
    "id": None, "status": "idle", "completed": 0, "total": None,
    "row": None, "column": None, "error": None, "started_at": None,
    "new_count": 0, "duplicate_count": 0, "updated_count": 0,
}


def scan_status() -> dict:
    with _scan_lock:
        state = {key: value for key, value in _scan_state.items() if key != "started_at"}
        started_at = _scan_state["started_at"]
        elapsed = (time.monotonic() - started_at) if started_at else 0.0
        state["elapsed_seconds"] = round(elapsed, 2)
        state["average_seconds"] = round(elapsed / _scan_state["completed"], 2) if _scan_state["completed"] else None
        return state


def _set_scan_state(**values: object) -> None:
    with _scan_lock:
        _scan_state.update(values)

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
    """Remove OCR'd numeric values/units and unlock markers from stat labels."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[+-]?\d+(?:\.\d+)?", "", text)
    text = text.replace("解锁", "").replace("%", "")
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
                elif path == "/api/device/check":
                    hdc = find_hdc(HDC)
                    targets = list_targets(hdc)
                    target = choose_target(hdc, SERIAL)
                    self._send_json({"connected": True, "serial": target, "targets": targets})
                elif path == "/api/scan/status":
                    self._send_json(scan_status())
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
            except (sqlite3.Error, OSError, CaptureError) as error:
                self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/api/scan/start":
            self._send_json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
            resume_row = int(payload.get("resume_row", 1))
            resume_column = int(payload.get("resume_column", 0))
            if resume_row < 1 or resume_column < 0 or resume_column > 8:
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send_json({"error": "续扫位置必须是有效的行号和 0-8 列"}, HTTPStatus.BAD_REQUEST)
            return
        with _scan_lock:
            if _scan_state["status"] in {"starting", "scanning"}:
                self._send_json({"error": "scan already running"}, HTTPStatus.CONFLICT)
                return
            job_id = uuid.uuid4().hex
            _scan_state.update({"id": job_id, "status": "starting", "completed": 0,
                                "total": None, "row": None, "column": None, "error": None,
                                "new_count": 0, "duplicate_count": 0, "updated_count": 0,
                                "started_at": time.monotonic()})
        thread = threading.Thread(target=self._run_scan, args=(job_id, resume_row, resume_column), daemon=True)
        thread.start()
        self._send_json(scan_status(), HTTPStatus.ACCEPTED)

    def _run_scan(self, job_id: str, resume_row: int, resume_column: int) -> None:
        try:
            from run_equipment_scan import _build_fast_scanner, _build_legacy_scanner

            database = EquipmentDatabase(self.database)
            database.initialize()
            try:
                def progress(update: dict) -> None:
                    normalized = dict(update)
                    if "equipment_count" in normalized:
                        normalized["total"] = normalized.pop("equipment_count")
                    _set_scan_state(id=job_id, **normalized)

                def import_result(result: dict) -> None:
                    action = result.get("import_action")
                    key = {"created": "new_count", "duplicate": "duplicate_count", "updated": "updated_count"}.get(action)
                    if key:
                        with _scan_lock:
                            _scan_state[key] += 1

                try:
                    scanner = _build_fast_scanner(database, import_result)
                    scanner.progress_callback = progress
                except Exception:
                    scanner = _build_legacy_scanner(database, import_result)
                    scanner.progress_callback = progress
                _set_scan_state(status="scanning")
                records = scanner.scan_until_bottom(resume_row=resume_row, resume_column=resume_column)
                skipped = (resume_row - 1) * scanner.grid.columns + resume_column
                _set_scan_state(status="completed", completed=len(records),
                                total=max(0, scanner.equipment_count - skipped) if scanner.equipment_count is not None else None,
                                row=None, column=None, error=None)
            finally:
                database.close()
        except Exception as error:
            _set_scan_state(id=job_id, status="failed", error=str(error))

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
