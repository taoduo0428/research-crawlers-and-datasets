#!/usr/bin/env python3
"""Local static/fixture gate; never substitutes for real-platform acceptance.

Exit codes: 0 all selected gates passed, 1 failed, 2 incomplete/skipped.
The unittest phase may start isolated dummy services provided by tests; this
entrypoint never starts project launchers or a real capture job.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PYTHON_FILES = [
    "facebook_capture_api.py", "facebook_android_capture.py", "facebook_http_security.py",
    "facebook_share_contract.py", "facebook_group_inline_route.py", "analyze_facebook_capture.py",
    "facebook_capture_addon_safe.py", "facebook_capture_progress.py", "facebook_api_client.py",
    "resolve_facebook_share_url.py", "tools/maintenance_gate.py",
]
NODE_FILES = ["web/app.js", "web_v2/app.js", "examples/capture_fetch.mjs"]
POWERSHELL_FILES = [
    "facebook_transport.ps1", "open_facebook_post_readonly.ps1",
    "run_facebook_capture.ps1", "facebook_runtime.ps1",
]


def prepare_log_dir(root: Path, requested: Path) -> Path:
    """Create a new external run directory; never overwrite a previous audit."""
    root = root.resolve()
    requested = requested.expanduser().resolve()
    if requested.is_relative_to(root) or root.is_relative_to(requested):
        raise ValueError("log directory must be outside the project and not its ancestor")
    if requested.exists():
        raise ValueError("log directory must be new; existing output is never overwritten")
    requested.mkdir(parents=True, mode=0o700, exist_ok=False)
    return requested


def _log_tail(path: Path) -> tuple[str, bool]:
    # Keep summaries bounded; complete original bytes remain in the log file.
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 16000))
        text = stream.read().decode("utf-8", errors="replace")
    return text[-4000:], size > 16000 or len(text) > 4000


def run(argv: list[str], cwd: Path = ROOT, timeout: int = 180, *, log_dir: Path | None = None) -> dict:
    env = dict(os.environ)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    result = {"command": argv, "returncode": None, "ok": False, "command_started": False}
    if log_dir is not None:
        stem = uuid.uuid4().hex
        stdout_path, stderr_path = log_dir / (stem + ".stdout.log"), log_dir / (stem + ".stderr.log")
        try:
            with ExitStack() as stack:
                # Open both evidence destinations before any command runs.
                stdout = stack.enter_context(stdout_path.open("xb"))
                stderr = stack.enter_context(stderr_path.open("xb"))
                result.update(stdout_file=str(stdout_path), stderr_file=str(stderr_path))
                try:
                    process = subprocess.run(argv, cwd=cwd, stdout=stdout, stderr=stderr,
                                             timeout=timeout, env=env)
                    result.update(returncode=process.returncode, ok=process.returncode == 0, command_started=True)
                except subprocess.TimeoutExpired:
                    result.update(timed_out=True, error="command timed out", command_started=True)
                except OSError as exc:
                    result.update(error=str(exc))
            out_tail, out_truncated = _log_tail(stdout_path)
            err_tail, err_truncated = _log_tail(stderr_path)
            result.update(stdout_tail=out_tail, stderr_tail=err_tail,
                          output_truncated=out_truncated or err_truncated,
                          full_output_retained=result["command_started"])
        except OSError as exc:
            result.update(ok=False, evidence_error=str(exc), full_output_retained=False)
        return result
    try:
        process = subprocess.run(argv, cwd=cwd, text=True, capture_output=True,
                                 encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        def decoded(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
        stdout, stderr = decoded(exc.stdout), decoded(exc.stderr)
        return {**result, "timed_out": True, "command_started": True, "error": "command timed out",
                "stdout_tail": stdout[-4000:], "stderr_tail": stderr[-4000:],
                "output_truncated": len(stdout) > 4000 or len(stderr) > 4000,
                "full_output_retained": False}
    except OSError as exc:
        return {**result, "error": str(exc), "full_output_retained": False}
    return {
        "command": argv, "returncode": process.returncode, "ok": process.returncode == 0, "command_started": True,
        "stdout_tail": process.stdout[-4000:], "stderr_tail": process.stderr[-4000:],
        "output_truncated": len(process.stdout) > 4000 or len(process.stderr) > 4000,
        "full_output_retained": False,
    }


def require_files(root: Path, paths: list[str]) -> dict:
    missing = [p for p in paths if not (root / p).is_file()]
    return {"name": "required-files", "ok": not missing, "missing": missing}


def python_syntax(root: Path, paths: list[str]) -> dict:
    errors = []
    for name in paths:
        try:
            compile((root / name).read_bytes(), name, "exec")
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append({"file": name, "error": str(exc)})
    return {"name": "python-syntax", "ok": not errors, "files": paths, "errors": errors}


def release_paths(root: Path) -> dict:
    """Validate release path hygiene; not a content or Git-history secret scan."""
    errors, seen = [], set()
    try:
        lines = (root / "release-files.txt").read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        return {"name": "release-paths", "ok": False, "errors": [str(exc)]}
    for line in lines:
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        path = PurePosixPath(name)
        if name in seen:
            errors.append("duplicate: " + name)
        seen.add(name)
        if (path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
                or not path.parts or str(path) != name):
            errors.append("invalid path: " + name)
            continue
        if (path.suffix.lower() in {".key", ".pem", ".apk", ".pcap", ".jsonl", ".mitm", ".keystore"}
                or any(p.lower() in {"runs", "api-jobs", "api-batches", "evidence", "backups", "worker-bootstrap"}
                       or p.lower().startswith(".env") or p.lower().endswith(".local.json") for p in path.parts)):
            errors.append("private/runtime path: " + name)
            continue
        current = root
        linked = False
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                linked = True
                break
        if linked or not current.resolve().is_relative_to(root.resolve()):
            errors.append("external/symlink path: " + name)
        elif not current.is_file():
            errors.append("missing: " + name)
    return {"name": "release-paths", "ok": not errors, "files": len(seen), "errors": errors}


def summarize(results: list[dict]) -> dict:
    status = "failed" if any(r.get("ok") is False for r in results) else (
        "passed" if results and all(r.get("ok") is True for r in results) else "partial")
    return {"status": status, "ok": status == "passed", "results": results,
            "scope": "local static and deterministic fixtures; no real-platform acceptance"}


def exit_code(report: dict) -> int:
    return {"passed": 0, "failed": 1, "partial": 2}[report["status"]]



def project_python(root: Path) -> str:
    candidates = ([root / ".venv" / "Scripts" / "python.exe"] if os.name == "nt"
                  else [root / ".venv" / "bin" / "python"])
    return next((str(p) for p in candidates if p.is_file()), sys.executable)



def collect(*, skip_tests: bool = False, log_dir: Path | None = None) -> dict:
    def execute(argv):
        return run(argv, log_dir=log_dir)
    python = project_python(ROOT)
    required = require_files(ROOT, PYTHON_FILES + NODE_FILES + POWERSHELL_FILES + ["tests/__init__.py", "requirements.txt"])
    results = [execute(["git", "status", "--short"]), execute(["git", "diff", "--check"]), required,
               release_paths(ROOT), execute([python, "-B", "-m", "pip", "check"])]
    if required["ok"]:
        results.append(python_syntax(ROOT, PYTHON_FILES))
        node = shutil.which("node")
        if node:
            results.extend(execute([node, "--check", p]) for p in NODE_FILES)
        else:
            results.append({"name": "node-syntax", "ok": None, "skipped": "node unavailable"})
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell:
            names = ",".join("'" + p.replace("'", "''") + "'" for p in POWERSHELL_FILES)
            script = ("$ErrorActionPreference='Stop'; $files=@(" + names + "); "
                      "foreach($f in $files){$t=$null;$e=$null;"
                      "[Management.Automation.Language.Parser]::ParseFile((Join-Path (Get-Location) $f),"
                      "[ref]$t,[ref]$e)|Out-Null;if($e.Count){$e|Format-List;exit 1}}")
            results.append(execute([powershell, "-NoProfile", "-NonInteractive", "-Command", script]))
        else:
            results.append({"name": "powershell-ast", "ok": None, "skipped": "PowerShell unavailable"})
    if skip_tests:
        results.append({"name": "unittest", "ok": None, "skipped": "explicit --skip-tests"})
    elif required["ok"]:
        results.append(execute([python, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."]))
    return {"root": str(ROOT), "python": python, "log_dir": str(log_dir) if log_dir else None, **summarize(results)}



def save_report(log_dir: Path, report: dict) -> None:
    """Publish only a fully written/closed report, refusing an existing target.

    Hard-link creation supplies exclusive atomic publication on supported local
    filesystems. If unavailable, fail; never fall back to partial final-file writes.
    This helper assumes a caller-owned run directory, not untrusted shared scratch.
    """
    temporary = log_dir / (uuid.uuid4().hex + ".report.tmp")
    created = False
    try:
        stream = temporary.open("x", encoding="utf-8")
        created = True
        with stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, log_dir / "report.json")
    finally:
        # Only our uniquely created intermediate file is eligible for removal.
        # Cleanup failure does not invalidate an already fully published report.
        if created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--log-dir", type=Path, help="New external private directory for full stdout/stderr logs")
    args = parser.parse_args()
    log_dir = None
    if args.log_dir is not None:
        try:
            log_dir = prepare_log_dir(ROOT, args.log_dir)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    report = collect(skip_tests=args.skip_tests, log_dir=log_dir)
    if log_dir is not None:
        try:
            save_report(log_dir, report)
        except OSError as exc:
            report = {"root": str(ROOT), "log_dir": str(log_dir), **summarize(
                report["results"] + [{"name": "evidence-report", "ok": False, "error": str(exc)}])}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["status"].upper())
        for item in report["results"]:
            if item.get("ok") is not True:
                print(json.dumps(item, ensure_ascii=False))
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
