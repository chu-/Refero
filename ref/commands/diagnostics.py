"""Read-only installation and Zotero connectivity checks."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import replace
from typing import Any, Optional

import typer
import yaml

from .. import __version__
from ..backends.zotero_client import ZoteroClient
from ..config import RefConfig, _config_path, load as load_config
from ..utils.zotero import ensure_no_proxy_for_local
from ._registry import CommandBinding


def _check_config(*, web: bool = False) -> tuple[Optional[RefConfig], dict[str, str]]:
    """Read and validate configuration without creating or modifying files."""
    try:
        if not _config_path().is_file():
            return None, {
                "name": "config", "status": "error",
                "message": (
                    "Configuration file is missing; "
                    "create it using the installation guide."
                ),
            }
        cfg = load_config(read_only=True)
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return None, {
            "name": "config", "status": "error",
            "message": "Cannot read configuration; check file access and YAML mapping syntax.",
        }

    zotero = cfg.zotero
    if cfg.zotero_unknown_keys:
        message = "Unknown Zotero configuration keys; check the installation guide."
    elif zotero.mode not in {"local", "web"}:
        message = "zotero.mode must be local or web."
    elif zotero.library_type not in {"user", "group"}:
        message = "zotero.library_type must be user or group."
    elif (web or zotero.mode == "web") and not (zotero.library_id and zotero.api_key):
        message = "Web mode requires a positive library_id and an api_key."
    else:
        if web:
            cfg = replace(cfg, zotero=replace(zotero, mode="web"))
        return cfg, {
            "name": "config", "status": "ok",
            "message": f"Configuration is valid for {cfg.zotero.mode} mode.",
        }
    return cfg, {"name": "config", "status": "error", "message": message}


def _connection_failure(
    *, local: bool, detail: str, status: Optional[int], web: bool = False,
) -> dict[str, Any]:
    """Describe a failed read without inferring desktop process state."""
    if local:
        code = "LOCAL_API_ACCESS_DENIED" if status == 403 else "LOCAL_API_UNAVAILABLE"
        steps = [
            "Start Zotero Desktop on the machine running Refero.",
            "In Zotero Settings > Advanced, enable 'Allow other applications "
            "on this computer to communicate with Zotero'.",
            "Check zotero.local_api, the port, and local proxy/firewall settings.",
            "Run the same ref executable with doctor --json again.",
        ]
        message = (
            f"Local API read failed ({detail}); Zotero may be closed, "
            "Local API access may be disabled, or the endpoint may be unreachable."
        )
    else:
        code = "WEB_READ_FAILED"
        retry = "doctor --web --json" if web else "doctor --json"
        steps = [
            "Check the configured Web endpoint, proxy, and network settings.",
            f"Run the same ref executable with {retry} again.",
        ]
        if status in {401, 403}:
            code = "WEB_ACCESS_DENIED"
            steps.insert(0, "Check the API key and read access to the intended library.")
        elif status == 404:
            code = "WEB_LIBRARY_NOT_FOUND"
            steps.insert(0, "Check the intended library ID and library type.")
        message = f"Web API read failed ({detail}); check credentials and connectivity."
    return {
        "name": "connection", "status": "error", "code": code,
        "message": message, "next_steps": steps,
    }


def _check_connection(cfg: RefConfig, *, web: bool = False) -> dict[str, Any]:
    """Probe the configured library with a read request, without write checks."""
    zotero = cfg.zotero
    try:
        if zotero.is_local:
            ensure_no_proxy_for_local()
        client = ZoteroClient(
            library_id=0 if zotero.is_local else zotero.library_id,
            library_type=zotero.library_type,
            api_key=zotero.api_key,
            endpoint=zotero.local_api if zotero.is_local else zotero.endpoint,
            local=zotero.is_local,
        )
        client.list_items(limit=1)
    except Exception as exc:
        # Exception text and request URLs can contain credentials. Report only
        # the category and HTTP status, leaving configuration values private.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        return _connection_failure(
            local=zotero.is_local, detail=detail, status=status, web=web,
        )
    return {
        "name": "connection", "status": "ok",
        "message": "Library is readable; write permissions were not tested.",
    }


def collect_checks(*, connect: bool = True, web: bool = False) -> dict[str, Any]:
    """Return versioned diagnostics; optionally probe the library with one read.

    Args:
        connect: Whether to check Zotero connectivity after config validation.
        web: Explicitly select the configured Web library for this diagnosis.

    Returns:
        A JSON-compatible report with overall readiness and individual checks.
    """
    cfg, check_config = _check_config(web=web)
    checks = [
        {
            "name": "python",
            "status": "ok" if sys.version_info >= (3, 9) else "error",
            "message": f"Python {sys.version.split()[0]} (requires 3.9 or newer).",
        },
        check_config,
    ]
    if cfg:
        picker_available = bool(shutil.which(cfg.fzf_binary))
        checks.append({
            "name": "picker", "status": "ok" if picker_available else "warning",
            "message": "fzf is available." if picker_available else
            "fzf was not found; only interactive picker flows require it.",
        })
    if connect and cfg and check_config["status"] == "ok":
        checks.append(_check_connection(cfg, web=web))
    else:
        checks.append({
            "name": "connection", "status": "skipped",
            "message": "Disabled by --no-connect or configuration is not valid.",
        })
    return {
        "schema_version": 1,
        "version": __version__,
        "config_path": str(_config_path()),
        "backend": "web" if web else (
            cfg.zotero.mode if cfg and cfg.zotero.mode in {"local", "web"} else None
        ),
        "ok": not any(check["status"] == "error" for check in checks),
        "checks": checks,
    }


def doctor(
    json_output: bool = typer.Option(False, "--json", help="Print a versioned JSON report"),
    connect: bool = typer.Option(True, "--connect/--no-connect", help="Check library read access"),
    web: bool = typer.Option(False, "--web", help="Diagnose the configured Web library explicitly"),
) -> None:
    """Check installation and config without changing files or library data."""
    report = collect_checks(connect=connect, web=web)
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False))
    else:
        typer.echo(f"Refero {report['version']} — {report['config_path']}")
        for check in report["checks"]:
            typer.echo(f"{check['status'].upper()}: {check['name']}: {check['message']}")
            for step in check.get("next_steps", []):
                typer.echo(f"  - {step}")
    if not report["ok"]:
        raise typer.Exit(code=1)


COMMANDS = (CommandBinding(callback=doctor, name="doctor"),)
