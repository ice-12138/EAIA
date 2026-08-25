"""Capture Huawei device screens through the HDC connection used by HOScrcpy."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Sequence


DEFAULT_HDC_CANDIDATES = (
    Path(r"D:\DEVECO\sdk\default\openharmony\toolchains\hdc.exe"),
    Path(r"D:\DEVECO~2\sdk\default\OPENHA~1\TOOLCH~1\hdc.exe"),
)


class CaptureError(RuntimeError):
    """Raised when HDC cannot produce a screenshot."""


def find_hdc(explicit: str | None = None) -> str:
    """Return an HDC executable path, preferring the configured path."""
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise CaptureError(f"HDC executable not found: {path}")
        return str(path)

    discovered = shutil.which("hdc")
    if discovered:
        return discovered
    for candidate in DEFAULT_HDC_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    raise CaptureError("Cannot find hdc.exe. Pass --hdc with its full path.")


def _run(command: Sequence[str], *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )


def list_targets(hdc: str) -> list[str]:
    result = _run((hdc, "list", "targets"))
    if result.returncode != 0:
        raise CaptureError(result.stderr.strip() or "HDC failed to list targets")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def choose_target(hdc: str, serial: str | None = None) -> str:
    targets = list_targets(hdc)
    if serial:
        if serial not in targets:
            raise CaptureError(f"Target {serial!r} is not connected. Available: {', '.join(targets) or 'none'}")
        return serial
    if not targets:
        raise CaptureError("No HOScrcpy device is connected.")
    return targets[0]


def capture_once(hdc: str, serial: str, output_dir: Path) -> Path:
    """Capture one device frame without changing desktop focus or input state."""
    output_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    remote = f"/data/local/tmp/eaia_{token}.jpeg"
    output = output_dir / f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.jpeg"
    try:
        snapshot = _run((hdc, "-t", serial, "shell", "snapshot_display", "-f", remote))
        if snapshot.returncode != 0:
            raise CaptureError(snapshot.stderr.strip() or snapshot.stdout.strip() or "HDC screenshot failed")
        receive = _run((hdc, "-t", serial, "file", "recv", remote, str(output)))
        if receive.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            raise CaptureError(receive.stderr.strip() or receive.stdout.strip() or "HDC screenshot transfer failed")
        return output
    finally:
        _run((hdc, "-t", serial, "shell", "rm", remote), timeout=10)


def capture_loop(hdc: str, serial: str, output_dir: Path, interval: float, count: int | None) -> list[Path]:
    if interval < 0:
        raise ValueError("interval must be non-negative")
    saved: list[Path] = []
    index = 0
    while count is None or index < count:
        saved.append(capture_once(hdc, serial, output_dir))
        index += 1
        if count is None or index < count:
            time.sleep(interval)
    return saved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture the HOScrcpy device screen without touching the desktop.")
    parser.add_argument("--hdc", help="Full path to hdc.exe")
    parser.add_argument("--serial", help="HDC target serial/IP; defaults to the first connected target")
    parser.add_argument("--output-dir", type=Path, default=Path("captures"))
    parser.add_argument("--interval", type=float, default=0.0, help="Seconds between captures; 0 captures once")
    parser.add_argument("--count", type=int, help="Number of captures; omitted with interval keeps running")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.count is not None and args.count < 1:
            raise CaptureError("--count must be at least 1")
        hdc = find_hdc(args.hdc)
        serial = choose_target(hdc, args.serial)
        saved = capture_loop(hdc, serial, args.output_dir, args.interval, args.count or (1 if args.interval == 0 else None))
        for path in saved:
            print(path.resolve())
        return 0
    except (CaptureError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Error: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
