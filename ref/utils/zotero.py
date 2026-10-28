from __future__ import annotations

import json
import os
import socket
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

import requests
import typer

from ..backends.zotero_client import ZoteroClient
from ..config import _config_path, load as load_config
from .interaction import is_non_interactive
T = TypeVar("T")

__all__ = [
    "mask_api_key",
    "echo_missing_creds",
    "echo_auth_error",
    "echo_network_error",
    "is_network_error",
    "run_with_network_retry",
    "echo_user_not_found",
    "ensure_no_proxy_for_local",
    "is_local_write_blocked",
    "retry_or_escalate",
    "zot_from_config",
    "load_latest_item_data_and_version",
]


def mask_api_key(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.strip().lower() in {"", "none", "null"}:
        return "None"
    return ("****" + text[-4:]) if len(text) >= 4 else ("****" + text)


def echo_missing_creds(cfg_file: str, library_id: Any, api_key: Any) -> None:
    typer.echo(
        "Web mode:\nNo installation of Zotero client needed. If wanna faster performance, change to Local mode in config.yaml.\n\n"
        "Web mode requires both your user ID and an API key.\n"
        f"- Current library_id: {library_id}\n"
        f"- Current api_key: {mask_api_key(api_key)}\n\n"
        "1. Find your user ID(library_id): https://www.zotero.org/settings/security#applications\n"
        "2. Create a new API key: https://www.zotero.org/settings/keys/new\n\n"
        f"Then, update your library_id and api_key in config.yaml at: {cfg_file}\n",
        err=True,
    )


def echo_auth_error(status: Optional[int] = None) -> None:
    typer.echo(
        (
            f"Zotero Web API authorization failed{f' (HTTP {status})' if status else ''}.\n"
            "- Check your library_id and api_key.\n"
            "- Ensure the key has access to the target library.\n"
            "- Recreate the key if needed: https://www.zotero.org/settings/keys/new\n"
            "- Verify user ID: https://www.zotero.org/settings/security#applications\n"
            "- Fix it in config.yaml"
        ),
        err=True,
    )


def echo_network_error(error: Exception) -> None:
    typer.echo(
        "Network error when connecting to Zotero Web API.\n"
        "- Check your internet connection and firewall/proxy settings.\n"
        f"Details: {error}",
        err=True,
    )


def is_network_error(error: Exception) -> bool:
    try:
        req_err = isinstance(error, requests.exceptions.RequestException)
    except Exception:
        req_err = False
    sys_err = isinstance(
        error,
        (
            ConnectionError,
            TimeoutError,
            OSError,
            socket.timeout,
            socket.gaierror,
            socket.error,
        ),
    )
    if req_err or sys_err:
        return True
    message = str(error).lower()
    indicators = (
        "connection reset",
        "timed out",
        "timeout",
        "name or service not known",
        "temporary failure in name resolution",
        "failed to establish a new connection",
        "max retries exceeded",
        "connection refused",
        "network is unreachable",
        "ssl: certificate verify failed",
        "ssl: unexpected_eof",
        "unexpected eof",
        "ssl certificate",
    )
    return any(indicator in message for indicator in indicators)


def run_with_network_retry(op: Callable[[], T], *, prompt: str = "Network/SSL error during operation. Retry now?") -> T:
    while True:
        try:
            return op()
        except Exception as exc:
            if is_network_error(exc) and not is_non_interactive():
                if typer.confirm(prompt, default=True):
                    continue
            raise


def echo_user_not_found(cfg_file: str, library_id: int) -> None:
    typer.echo(
        (
            "Zotero user ID (library_id) not found (HTTP 404).\n"
            f"- Provided library_id: {library_id}\n"
            "- Verify your user ID here: https://www.zotero.org/settings/security#applications\n"
            f"- Fix it in config.yaml at: {cfg_file}"
        ),
        err=True,
    )


def ensure_no_proxy_for_local() -> None:
    def _append(var: str, host: str) -> None:
        current = os.environ.get(var, "")
        parts = [part.strip() for part in current.split(",") if part.strip()]
        if host not in parts:
            parts.append(host)
        os.environ[var] = ",".join(parts) if parts else host

    for var in ("NO_PROXY", "no_proxy"):
        for host in ("localhost", "127.0.0.1", "::1"):
            _append(var, host)


def is_local_write_blocked(zot: ZoteroClient, error: Exception) -> bool:
    message = str(error).lower()
    return zot.is_local() and (
        "request not allowed" in message
        or "method not allowed" in message
        or "not implemented" in message
        or "endpoint does not support method" in message
        or "404" in message
        or "405" in message
        or "write" in message
    )


def retry_or_escalate(
    zot: ZoteroClient,
    action_label: str,
    operation: Callable[[ZoteroClient], T],
) -> T:
    """Run a Zotero write against the preferred client, falling back to Web API.

    The helper first attempts the operation against the provided client. If the
    local API blocks writes, we escalate to the Web API using credentials from
    config.yaml, prompting for network retries as needed.
    """

    try:
        return operation(zot)
    except Exception as exc:
        if not is_local_write_blocked(zot, exc):
            raise

    cfg = load_config()
    zotero_cfg = cfg.zotero
    web_library_id = zotero_cfg.library_id
    web_api_key = zotero_cfg.api_key

    if web_library_id and web_api_key:
        typer.echo(
            "- Local Zotero API blocked the write;\n"
            "- Using Web API for this command instead.\n"
            "- No write support in server_localAPI.js until now, see https://groups.google.com/g/zotero-dev/c/ElvHhIFAXrY/m/fA7SKKwsAgAJ\n",
            err=True,
        )
        web_client = ZoteroClient(
            library_id=web_library_id,
            library_type=zotero_cfg.library_type,
            api_key=web_api_key,
            endpoint=None,
            local=False,
        )

        prompt = (
            f"Network/SSL error while using Zotero Web API to {action_label}. Retry now?"
        )
        try:
            return run_with_network_retry(lambda: operation(web_client), prompt=prompt)
        except Exception as exc:
            typer.echo(
                f"Local {action_label} failed; Web API fallback also failed: {exc}",
                err=True,
            )
            raise

    typer.echo(
        "Local Zotero API blocked the write, and Web API credentials are not configured.\n"
        "- Set zotero.mode to 'web' or provide 'library_id' and 'api_key' in config.yaml,\n"
        "  then retry.",
        err=True,
    )
    raise typer.Exit(code=1)


def zot_from_config() -> ZoteroClient:
    cfg = load_config()
    zotero_cfg = cfg.zotero
    cfg_file = str(_config_path())

    if cfg.zotero_unknown_keys:
        typer.echo(
            (
                "Unknown keys in zotero config: " + ", ".join(sorted(cfg.zotero_unknown_keys)) + "\n"
                "- Valid keys: mode, endpoint, local_api, library_id, library_type, api_key, storage_dir\n"
                f"- Fix it in config.yaml at: {cfg_file}"
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    mode = zotero_cfg.mode
    if mode not in {"local", "web"}:
        typer.echo(
            (
                f"Invalid zotero.mode: '{mode}'.\n"
                "- Valid values: local | web\n"
                f"- Fix it in config.yaml at: {cfg_file}"
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    local_mode = mode == "local"
    if local_mode:
        ensure_no_proxy_for_local()

    library_id_int = 0
    if not local_mode:
        library_id_int = zotero_cfg.library_id
        if not library_id_int:
            echo_missing_creds(cfg_file, zotero_cfg.library_id, zotero_cfg.api_key)
            raise typer.Exit(code=1)

    endpoint = zotero_cfg.local_api if local_mode else zotero_cfg.endpoint
    client = ZoteroClient(
        library_id=library_id_int,
        library_type=zotero_cfg.library_type,
        api_key=zotero_cfg.api_key,
        endpoint=endpoint,
        local=local_mode,
    )

    if local_mode and not client.ping():
        typer.echo(
            "Local mode: need Zotero client installed\n"
            "Couldn't connect your Zotero,\n"
            "1. If Zotero is not running, please open your Zotero.\n"
            "2. If Zotero is in background, still this problem. Please check your local_api in Zotero Settings -> Advanced -> Miscellaneous. Then fix the correct local_api in your config.yaml at: "
            f"{cfg_file}",
            err=True,
        )
        raise typer.Exit(code=1)

    if not local_mode:
        api_key = zotero_cfg.api_key
        if not api_key:
            echo_missing_creds(cfg_file, zotero_cfg.library_id, api_key)
            raise typer.Exit(code=1)

        try:
            if is_non_interactive():
                client.list_items(limit=1)
            else:
                client.create_items([])
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response else None
            if status == 404:
                echo_user_not_found(cfg_file, library_id_int)
                raise typer.Exit(code=1)
            if status in (401, 403):
                echo_auth_error(status)
                raise typer.Exit(code=1)
            if status and 500 <= status < 600:
                echo_network_error(exc)
                raise typer.Exit(code=1)
            raise
        except Exception as exc:
            if is_network_error(exc):
                echo_network_error(exc)
                raise typer.Exit(code=1)
            raise

    return client


def load_latest_item_data_and_version(zot: ZoteroClient, key: str) -> Tuple[Dict[str, Any], int]:
    item = zot.get_item(key)
    return (item.get("data", {}) or {}, int(item.get("version") or 0))
