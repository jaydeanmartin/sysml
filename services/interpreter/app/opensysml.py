"""Thin, process-per-request adapter around the OpenSysML ``sysml`` CLI.

Every request writes the model files into a fresh temporary workspace and runs
the binary with a list of arguments (never through a shell). The CLI's
``-json`` report is the contract: ``status``, ``exit``, ``checks``,
``diagnostics`` and ``output``.

The adapter is deliberately small so it can be swapped for the OpenSysML gRPC
service (``sysml-grpc``) later without touching the HTTP API.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SYSML_BIN = os.environ.get("OPENSYSML_BIN", "sysml")
DEFAULT_TIMEOUT = float(os.environ.get("OPENSYSML_RUN_TIMEOUT", "120"))
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass
class ModelFile:
    name: str
    text: str


@dataclass
class RunResult:
    returncode: int
    report: dict[str, Any] | None
    stdout: str
    stderr: str
    elapsed_ms: int


class OpenSysMLError(RuntimeError):
    pass


def binary_path() -> str | None:
    return shutil.which(SYSML_BIN) or (SYSML_BIN if Path(SYSML_BIN).is_file() else None)


def version() -> str:
    out = subprocess.run([SYSML_BIN, "-version"], capture_output=True, text=True, timeout=10)
    return out.stdout.strip().splitlines()[0] if out.stdout else "unknown"


def _safe_file_name(name: str, index: int) -> str:
    base = _SAFE_NAME.sub("_", Path(name).name) or f"model{index}"
    if not base.endswith((".sysml", ".kerml")):
        base += ".sysml"
    return base


@contextmanager
def workspace(files: list[ModelFile]) -> Iterator[tuple[Path, dict[str, str]]]:
    """Write files to a temp dir. Yields (dir, {written name -> original name})."""
    tmp = Path(tempfile.mkdtemp(prefix="sysml-ws-"))
    names: dict[str, str] = {}
    try:
        for i, f in enumerate(files):
            fname = _safe_file_name(f.name, i)
            while fname in names:
                fname = f"{i}_{fname}"
            (tmp / fname).write_text(f.text, encoding="utf-8")
            names[fname] = f.name
        yield tmp, names
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    expect_json: bool = True,
) -> RunResult:
    full_env = {**os.environ, **(env or {})}
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [SYSML_BIN, *args],
            cwd=cwd,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout or DEFAULT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpenSysMLError(f"OpenSysML timed out after {exc.timeout}s") from exc
    except FileNotFoundError as exc:
        raise OpenSysMLError(f"OpenSysML binary not found: {SYSML_BIN}") from exc
    elapsed = int((time.monotonic() - start) * 1000)
    report = None
    if expect_json and proc.stdout.strip().startswith("{"):
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            report = None
    return RunResult(proc.returncode, report, proc.stdout, proc.stderr, elapsed)


def map_diagnostics(diags: list[dict[str, Any]] | None, names: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for d in diags or []:
        f = Path(d.get("file") or "").name
        out.append(
            {
                "severity": d.get("severity", "error"),
                "message": d.get("message", ""),
                "file": names.get(f, d.get("file")),
                "line": d.get("line") or 1,
                "column": d.get("column") or 1,
                "code": d.get("code"),
                "pass": d.get("pass"),
            }
        )
    return out


def stderr_diagnostics(stderr: str) -> list[dict[str, Any]]:
    """Errors the CLI reports on stderr (e.g. a refused load) as diagnostics."""
    out = []
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith("sysml:"):
            out.append({"severity": "error", "message": line[6:].strip(), "file": None, "line": 1, "column": 1, "code": "cli"})
    return out
