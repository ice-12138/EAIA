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
from collections import OrderedDict
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from data_manager import (
    DataManagerError,
    create_resource,
    delete_equipment,
    delete_resource,
    list_equipment,
    list_resource,
    initialize_equipment_calculability,
    refresh_equipment_calculability,
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
HDC = r"D:\DEVECO~2\sdk\default\OPENHA~1\TOOLCH~1\hdc.exe"
SERIAL = "FMR0223A30001935"

_scan_lock = threading.Lock()
_scan_cancel_event = None
_scan_state = {
    "id": None, "status": "idle", "completed": 0, "total": None,
    "row": None, "column": None, "error": None, "started_at": None,
    "new_count": 0, "duplicate_count": 0, "updated_count": 0,
    "session_completed": 0,
}

_recommend_lock = threading.Lock()
_recommend_jobs: OrderedDict[str, dict] = OrderedDict()
_recommend_queue: list[str] = []
_recommend_worker: threading.Thread | None = None


class ScanCancelled(Exception):
    """Raised at a safe item boundary when the user stops a scan."""


def _scan_review_items(database: Path, *, scan_error: str | None = None, row: object = None, column: object = None) -> list[dict]:
    """Return OCR records that need a human review after a scan."""
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        records = connection.execute(
            """SELECT e.item_id, e.set_id, s.set_name, er.profile,
                      er.set_name_text, er.slot_text, er.primary_text,
                      er.quality_text
                 FROM equipment e
                 LEFT JOIN sets s ON s.set_id=e.set_id
                 LEFT JOIN equipment_recognition er ON er.item_id=e.item_id
                WHERE e.set_id LIKE 'OCR_%'
                   OR (er.item_id IS NOT NULL AND trim(coalesce(er.slot_text, '')) = '')
                   OR (er.item_id IS NOT NULL AND trim(coalesce(er.primary_text, '')) = '')
                ORDER BY e.item_id"""
        ).fetchall()
        result = []
        for record in records:
            issues = []
            if str(record["set_id"] or "").startswith("OCR_"):
                issues.append("unmatched_set")
            if not str(record["slot_text"] or "").strip():
                issues.append("missing_slot")
            if not str(record["primary_text"] or "").strip():
                issues.append("missing_primary")
            result.append({
                "item_id": record["item_id"], "profile": record["profile"],
                "set_name": record["set_name"] or record["set_name_text"] or "未识别",
                "set_name_text": record["set_name_text"],
                "slot_text": record["slot_text"], "primary_text": record["primary_text"],
                "issues": issues,
            })
        if scan_error:
            result.append({
                "item_id": None, "profile": None, "set_name": None,
                "issues": ["scan_error"], "error": scan_error,
                "row": row, "column": column,
            })
        return result
    finally:
        connection.close()


def scan_status(database: Path = DEFAULT_DATABASE) -> dict:
    with _scan_lock:
        state = {key: value for key, value in _scan_state.items() if key != "started_at"}
        started_at = _scan_state["started_at"]
        elapsed = (time.monotonic() - started_at) if started_at else 0.0
        state["elapsed_seconds"] = round(elapsed, 2)
        state["average_seconds"] = round(elapsed / _scan_state["session_completed"], 2) if _scan_state["session_completed"] else None
        if state["status"] in {"completed", "stopped", "failed"}:
            state["review_items"] = _scan_review_items(
                database,
                scan_error=state["error"] if state["status"] == "failed" else None,
                row=state["row"], column=state["column"],
            )
        else:
            state["review_items"] = []
        return state


def _set_scan_state(**values: object) -> None:
    with _scan_lock:
        _scan_state.update(values)


def recommend_status(job_id: str | None = None) -> dict:
    with _recommend_lock:
        jobs = []
        for job in _recommend_jobs.values():
            item = {key: value for key, value in job.items() if key not in {"payload", "result", "started_at"}}
            started_at = job.get("started_at")
            item["elapsed_seconds"] = round(time.monotonic() - started_at, 2) if started_at else 0.0
            # Completed results are part of the queue item as well.  The
            # frontend can therefore render a finished job immediately after
            # polling, without racing a second request for the same job.
            if job.get("status") == "completed":
                item["result"] = job.get("result")
            jobs.append(item)
        return {"jobs": jobs, "active_id": next((x for x in _recommend_queue if _recommend_jobs[x]["status"] in {"starting", "screening", "refining"}), None)}


def _set_recommend_state(job_id: str, **values: object) -> None:
    with _recommend_lock:
        if job_id in _recommend_jobs:
            _recommend_jobs[job_id].update(values)


def _recommend_worker_loop() -> None:
    while True:
        with _recommend_lock:
            job_id = next((x for x in _recommend_queue if _recommend_jobs[x]["status"] == "queued"), None)
            if job_id is None:
                return
            job = _recommend_jobs[job_id]
            job["status"] = "starting"
            job["started_at"] = time.monotonic()
            payload = dict(job["payload"])
        try:
            result = recommend_hero_core(EAIARequestHandler.database, payload,
                                         progress_callback=lambda update: _set_recommend_state(
                                             job_id, status=update.get("phase", "screening"), **update))
            _set_recommend_state(job_id, status="completed", phase=None, error=None, result=result)
        except Exception as error:
            _set_recommend_state(job_id, status="failed", phase=None, error=str(error), result=None)
        finally:
            with _recommend_lock:
                if job_id in _recommend_queue:
                    _recommend_queue.remove(job_id)


def _enqueue_recommendation(payload: dict) -> str:
    global _recommend_worker
    job_id = uuid.uuid4().hex
    with _recommend_lock:
        _recommend_jobs[job_id] = {
            "id": job_id, "hero_name": str(payload.get("hero_name") or payload.get("hero_core_id") or "—"),
            "status": "queued", "phase": "queued", "completed": 0, "total": None,
            "overall_completed": 0, "overall_total": None, "error": None, "result": None,
            "started_at": None, "queued_at": time.monotonic(), "payload": dict(payload),
        }
        _recommend_queue.append(job_id)
        if _recommend_worker is None or not _recommend_worker.is_alive():
            _recommend_worker = threading.Thread(target=_recommend_worker_loop, daemon=True)
            _recommend_worker.start()
    return job_id


def cancel_recommendation(job_id: str) -> dict:
    with _recommend_lock:
        job = _recommend_jobs.get(job_id)
        if job is None:
            raise ValueError("recommendation job not found")
        if job["status"] != "queued":
            raise ValueError("only queued recommendation jobs can be deleted")
        job["status"] = "cancelled"
        job["phase"] = None
        if job_id in _recommend_queue:
            _recommend_queue.remove(job_id)
        return {key: value for key, value in job.items() if key not in {"payload", "result", "started_at", "queued_at"}}

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


def _managed_resource(path: str) -> str | None:
    prefix = "/api/manage/resource/"
    return unquote(path.removeprefix(prefix)) if path.startswith(prefix) else None


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

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
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
                    self._send_json(scan_status(self.database))
                elif path == "/api/hero-core/recommend/status":
                    requested_job_id = parse_qs(urlparse(self.path).query).get("job_id", [None])[0]
                    self._send_json(recommend_status(requested_job_id))
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

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        global _scan_cancel_event
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/scan/stop":
            with _scan_lock:
                if _scan_state["status"] in {"starting", "scanning"}:
                    _scan_state["status"] = "stopping"
                    if _scan_cancel_event is not None:
                        _scan_cancel_event.set()
            self._send_json(scan_status(self.database))
            return
        try:
            if path == "/api/hero-core/simulate":
                self._send_json(simulate_hero_core(self.database, self._read_json()))
                return
            if path == "/api/hero-core/recommend":
                self._send_json(recommend_hero_core(self.database, self._read_json()))
                return
            if path == "/api/hero-core/recommend/start":
                job_id = _enqueue_recommendation(self._read_json())
                self._send_json({"id": job_id, "status": "queued"}, HTTPStatus.ACCEPTED)
                return
            if path == "/api/hero-core/recommend/cancel":
                payload = self._read_json()
                self._send_json(cancel_recommendation(str(payload.get("id") or "")))
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
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
            resume_row = int(payload.get("resume_row", payload.get("start_row", 1)))
            if "resume_column" in payload:
                resume_column = int(payload["resume_column"])
            else:
                resume_column = int(payload.get("start_column", 1)) - 1
            end_row = int(payload.get("end_row", 0))
            end_column = int(payload.get("end_column", 0))
            if resume_row < 1 or resume_column < 0 or resume_column > 8:
                raise ValueError
            if end_row < 1 or end_column < 1 or end_column > 8:
                raise ValueError
            if end_row and (end_row - 1) * 8 + end_column < (resume_row - 1) * 8 + resume_column + 1:
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send_json({"error": "续扫位置必须是有效的行号和 0-8 列"}, HTTPStatus.BAD_REQUEST)
            return
        with _scan_lock:
            if _scan_state["status"] in {"starting", "scanning"}:
                self._send_json({"error": "scan already running"}, HTTPStatus.CONFLICT)
                return
            job_id = uuid.uuid4().hex
            cancel_event = threading.Event()
            _scan_cancel_event = cancel_event
            _scan_state.update({"id": job_id, "status": "starting", "completed": 0,
                                "total": None, "row": None, "column": None, "error": None,
                                "new_count": 0, "duplicate_count": 0, "updated_count": 0,
                                "session_completed": 0,
                                "started_at": time.monotonic()})
        thread = threading.Thread(target=self._run_scan, args=(job_id, resume_row, resume_column, end_row, end_column, cancel_event), daemon=True)
        thread.start()
        self._send_json(scan_status(self.database), HTTPStatus.ACCEPTED)

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

    def _run_scan(self, job_id: str, resume_row: int, resume_column: int, end_row: int, end_column: int, cancel_event: threading.Event) -> None:
        try:
            from run_equipment_scan import _build_fast_scanner, _build_legacy_scanner

            database = EquipmentDatabase(self.database)
            database.initialize()
            try:
                skipped = (resume_row - 1) * 8 + resume_column

                def progress(update: dict) -> None:
                    if cancel_event.is_set():
                        raise ScanCancelled()
                    normalized = dict(update)
                    equipment_count = normalized.pop("equipment_count", None)
                    if equipment_count is not None:
                        normalized["total"] = equipment_count
                    if "completed" in normalized:
                        normalized["session_completed"] = int(normalized["completed"])
                        normalized["completed"] = skipped + normalized["session_completed"]
                    _set_scan_state(id=job_id, **normalized)

                def import_result(result: dict) -> None:
                    refresh_equipment_calculability(self.database, result["item_id"])
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
                records = scanner.scan_until_bottom(resume_row=resume_row, resume_column=resume_column, end_row=end_row, end_column=end_column)
                skipped = (resume_row - 1) * scanner.grid.columns + resume_column
                _set_scan_state(status="completed", completed=skipped + len(records),
                                session_completed=len(records),
                                total=scanner.equipment_count,
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
    initialize_equipment_calculability(database)
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
