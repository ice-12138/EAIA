"""HTTP API and static frontend server for EAIA."""

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
from urllib.parse import unquote, urlparse

from data_manager import (
    DataManagerError,
    create_resource,
    delete_equipment,
    delete_resource,
    list_equipment,
    list_resource,
    resource_catalog,
    save_equipment,
    update_resource,
)
from equipment_db import EquipmentDatabase
from hero_core_engine import HeroCoreError
from hero_core_service import (
    hero_core_catalog,
    hero_core_codex_payload,
    hero_core_detail,
    import_hero_core,
    recommend_hero_core,
    simulate_hero_core,
)
from official_hero_data import seed_official_hero_catalog
from screen_capture import CaptureError, choose_target, find_hdc, list_targets


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "equipment.db"
FRONTEND_DIST = ROOT / "frontend" / "dist"
HDC = r"D:\\DEVECO~2\\sdk\\default\\OPENHA~1\\TOOLCH~1\\hdc.exe"
SERIAL = "FMR0223A30001935"

_scan_lock = threading.Lock()
_scan_cancel_event = None
_scan_state = {
    "id": None, "status": "idle", "completed": 0, "total": None,
    "row": None, "column": None, "error": None, "started_at": None,
    "new_count": 0, "duplicate_count": 0, "updated_count": 0,
    "session_completed": 0,
}


class ScanCancelled(Exception):
    """Raised at a safe item boundary when the user stops a scan."""


def scan_status() -> dict:
    with _scan_lock:
        state = {key: value for key, value in _scan_state.items() if key != "started_at"}
        started_at = _scan_state["started_at"]
        elapsed = (time.monotonic() - started_at) if started_at else 0.0
        state["elapsed_seconds"] = round(elapsed, 2)
        state["average_seconds"] = round(elapsed / _scan_state["session_completed"], 2) if _scan_state["session_completed"] else None
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
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if str(name or "").strip() in _PERCENT_STAT_NAMES:
        return f"{int(number)}%" if number.is_integer() else f"{number:g}%"
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _format_equipment_overview(source_rows: list[dict]) -> list[dict]:
    result = []
    for source in source_rows:
        row = dict(source)
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
    result = connection.execute(f'SELECT * FROM "{source}"')
    return [dict(row) for row in result.fetchall()]


def _merge_hero_core_codex(heroes: list[dict], skills: list[dict]) -> tuple[list[dict], list[dict]]:
    core_payload = hero_core_codex_payload()
    hero_map = {str(row["hero_key"]): dict(row) for row in heroes}
    for row in core_payload["heroes"]:
        key = str(row["hero_key"])
        hero_map[key] = {**hero_map.get(key, {}), **row}
    skill_map = {(str(row["hero_key"]), str(row["skill_key"])): dict(row) for row in skills}
    for row in core_payload["skills"]:
        key = (str(row["hero_key"]), str(row["skill_key"]))
        skill_map[key] = {**skill_map.get(key, {}), **row}
    return (
        sorted(hero_map.values(), key=lambda row: str(row.get("hero_name") or row.get("hero_key"))),
        sorted(skill_map.values(), key=lambda row: (str(row.get("hero_key")), str(row.get("skill_key")))),
    )


def database_payload(database: Path) -> tuple[dict, dict, dict]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        heroes = rows(connection, "official_hero_catalog")
        skills = rows(connection, "official_skill_catalog")
        for skill in skills:
            if skill.get("value_json"):
                try:
                    skill["value_json"] = json.loads(skill["value_json"])
                except json.JSONDecodeError:
                    pass
        heroes, skills = _merge_hero_core_codex(heroes, skills)
        equipment = {table: rows(connection, table) for table in EQUIPMENT_TABLES}
        equipment["v_equipment_full"] = _format_equipment_overview(equipment["v_equipment_full"])
        return (
            {"heroes": heroes, "skills": skills},
            {table: rows(connection, table) for table in CATALOG_TABLES},
            equipment,
        )
    finally:
        connection.close()


def _managed_resource(path: str) -> str | None:
    prefix = "/api/manage/resource/"
    return unquote(path.removeprefix(prefix)) if path.startswith(prefix) else None


class EAIARequestHandler(SimpleHTTPRequestHandler):
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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def _send_user_error(self, error: Exception) -> None:
        self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path.startswith("/api/"):
            try:
                if path == "/api/health":
                    self._send_json({"status": "ok", "database": str(self.database), "hero_core": True, "data_management": True})
                elif path == "/api/device/check":
                    hdc = find_hdc(HDC)
                    targets = list_targets(hdc)
                    target = choose_target(hdc, SERIAL)
                    self._send_json({"connected": True, "serial": target, "targets": targets})
                elif path == "/api/scan/status":
                    self._send_json(scan_status())
                elif path == "/api/hero-cores":
                    self._send_json(hero_core_catalog())
                elif path.startswith("/api/hero-cores/"):
                    self._send_json(hero_core_detail(unquote(path.removeprefix("/api/hero-cores/"))))
                elif path == "/api/manage/resources":
                    self._send_json(resource_catalog())
                elif path == "/api/manage/equipment":
                    self._send_json(list_equipment(self.database))
                elif _managed_resource(path) is not None:
                    self._send_json(list_resource(self.database, _managed_resource(path)))
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
            except HeroCoreError as error:
                self._send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
            except DataManagerError as error:
                self._send_user_error(error)
            except (sqlite3.Error, OSError, CaptureError) as error:
                self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        global _scan_cancel_event
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/scan/stop":
            with _scan_lock:
                if _scan_state["status"] in {"starting", "scanning"}:
                    _scan_state["status"] = "stopping"
                    if _scan_cancel_event is not None:
                        _scan_cancel_event.set()
            self._send_json(scan_status())
            return
        try:
            if path == "/api/hero-core/simulate":
                self._send_json(simulate_hero_core(self.database, self._read_json()))
                return
            if path == "/api/hero-core/recommend":
                self._send_json(recommend_hero_core(self.database, self._read_json()))
                return
            if path in {"/api/hero-core/import", "/api/hero-cores/import"}:
                self._send_json(import_hero_core(self._read_json()), HTTPStatus.CREATED)
                return
            if path == "/api/manage/equipment":
                payload = self._read_json()
                self._send_json(save_equipment(self.database, payload.get("values") or payload), HTTPStatus.CREATED)
                return
            resource = _managed_resource(path)
            if resource is not None:
                payload = self._read_json()
                self._send_json(create_resource(self.database, resource, payload.get("values") or payload), HTTPStatus.CREATED)
                return
        except (HeroCoreError, DataManagerError, TypeError, ValueError, json.JSONDecodeError, sqlite3.IntegrityError) as error:
            self._send_user_error(error)
            return
        except (sqlite3.Error, OSError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path != "/api/scan/start":
            self._send_json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
            start_row = int(payload.get("start_row", 1))
            start_column = int(payload.get("start_column", 1))
            end_row = int(payload.get("end_row", 0))
            end_column = int(payload.get("end_column", 0))
            if start_row < 1 or start_column < 1 or start_column > 8:
                raise ValueError
            if (end_row == 0) != (end_column == 0) or (end_row < 0 or end_column < 0) or (end_row and (end_column < 1 or end_column > 8)):
                raise ValueError
            if end_row and (end_row - 1) * 8 + end_column < (start_row - 1) * 8 + start_column:
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send_json({"error": "开始位置必须为有效的行号和 1-8 列；终止位置为 0/0 或有效的行号和 1-8 列"}, HTTPStatus.BAD_REQUEST)
            return
        with _scan_lock:
            if _scan_state["status"] in {"starting", "scanning"}:
                self._send_json({"error": "scan already running"}, HTTPStatus.CONFLICT)
                return
            job_id = uuid.uuid4().hex
            cancel_event = threading.Event()
            _scan_cancel_event = cancel_event
            _scan_state.update({
                "id": job_id, "status": "starting", "completed": 0, "total": None,
                "row": None, "column": None, "error": None,
                "new_count": 0, "duplicate_count": 0, "updated_count": 0,
                "session_completed": 0,
                "started_at": time.monotonic(),
            })
        threading.Thread(target=self._run_scan, args=(job_id, start_row, start_column, end_row or None, end_column or None, cancel_event), daemon=True).start()
        self._send_json(scan_status(), HTTPStatus.ACCEPTED)

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            payload = self._read_json()
            if path == "/api/manage/equipment":
                original = str(payload.get("original_item_id") or "")
                if not original:
                    raise DataManagerError("缺少原装备ID")
                self._send_json(save_equipment(self.database, payload.get("values") or {}, original_item_id=original))
                return
            resource = _managed_resource(path)
            if resource is not None:
                self._send_json(update_resource(self.database, resource, payload.get("key") or {}, payload.get("values") or {}))
                return
            self._send_json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except (DataManagerError, TypeError, ValueError, json.JSONDecodeError, sqlite3.IntegrityError) as error:
            self._send_user_error(error)
        except (sqlite3.Error, OSError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            payload = self._read_json()
            if path == "/api/manage/equipment":
                self._send_json(delete_equipment(self.database, str(payload.get("item_id") or "")))
                return
            resource = _managed_resource(path)
            if resource is not None:
                self._send_json(delete_resource(self.database, resource, payload.get("key") or {}))
                return
            self._send_json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except (DataManagerError, TypeError, ValueError, json.JSONDecodeError, sqlite3.IntegrityError) as error:
            self._send_user_error(error)
        except (sqlite3.Error, OSError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _run_scan(self, job_id: str, start_row: int, start_column: int, end_row: int | None, end_column: int | None, cancel_event: threading.Event) -> None:
        try:
            from run_equipment_scan import _build_fast_scanner, _build_legacy_scanner

            database = EquipmentDatabase(self.database)
            database.initialize()
            try:
                skipped = (start_row - 1) * 8 + start_column - 1

                def progress(update: dict) -> None:
                    if cancel_event.is_set():
                        raise ScanCancelled()
                    normalized = dict(update)
                    equipment_count = normalized.pop("equipment_count", None)
                    scan_limit = normalized.pop("scan_limit", None)
                    if scan_limit is not None:
                        normalized["total"] = scan_limit
                    elif equipment_count is not None:
                        normalized["total"] = equipment_count
                    if "completed" in normalized:
                        normalized["session_completed"] = int(normalized["completed"])
                        normalized["completed"] = skipped + normalized["session_completed"]
                    _set_scan_state(id=job_id, **normalized)

                def import_result(result: dict) -> None:
                    key = {"created": "new_count", "duplicate": "duplicate_count", "updated": "updated_count"}.get(result.get("import_action"))
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
                records = scanner.scan_until_bottom(
                    start_row=start_row, start_column=start_column,
                    end_row=end_row, end_column=end_column,
                )
                skipped = (start_row - 1) * scanner.grid.columns + start_column - 1
                _set_scan_state(status="completed", completed=skipped + len(records),
                                session_completed=len(records),
                                total=scanner.scan_limit,
                                row=None, column=None, error=None)
            finally:
                database.close()
        except ScanCancelled:
            _set_scan_state(id=job_id, status="stopped", error=None)
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
