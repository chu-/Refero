from __future__ import annotations

import os
import stat
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import shlex
import yaml

from .console import echo
from .picker.bindings import (
    DEFAULT_PICKER_BINDINGS,
    PickerBinding,
    binding_specs_by_raw,
    parse_binding_list,
)

DEFAULT_STORAGE_DIR = "~/Zotero/storage"

__all__ = ["RefConfig", "ZoteroConfig", "PickerBinding", "load"]


@dataclass(frozen=True)
class ZoteroConfig:
    mode: str
    endpoint: Optional[str]
    local_api: str
    library_id: int
    library_type: str
    api_key: Optional[str]
    storage_dir: Path

    @property
    def is_local(self) -> bool:
        return self.mode == "local"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ZoteroConfig":
        mode = str(data.get("mode") or "local").strip().lower()
        endpoint = _clean_optional_string(data.get("endpoint"))
        local_api = str(data.get("local_api") or "http://localhost:23119/api").strip()
        library_type = str(data.get("library_type") or "user").strip().lower() or "user"
        api_key = _clean_optional_string(data.get("api_key"))
        library_id = _coerce_int(data.get("library_id"), 0)
        storage_dir_raw = data.get("storage_dir") or DEFAULT_STORAGE_DIR
        storage_dir = Path(str(storage_dir_raw)).expanduser()
        return cls(
            mode=mode or "local",
            endpoint=endpoint,
            local_api=local_api or "http://localhost:23119/api",
            library_id=library_id,
            library_type=library_type or "user",
            api_key=api_key,
            storage_dir=storage_dir,
        )


@dataclass(frozen=True)
class RefConfig:
    picker_limit: int
    fzf_style: str
    fzf_binary: str
    fzf_extra_flags: List[str]
    fzf_extra_bindings: List[str]
    fzf_header_format: str
    match_format: str
    diff_style: str
    color_scheme: str
    color_schemes: Dict[str, Dict[str, str]]
    note_editor: str
    zotero: ZoteroConfig
    fzf_preview_window: Optional[str]
    picker_bindings: List[PickerBinding]
    zotero_unknown_keys: List[str]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RefConfig":
        specs_map = binding_specs_by_raw(DEFAULT_PICKER_BINDINGS)
        binding_values = _coerce_bindings(data.get("fzf-extra-bindings"))
        bindings = parse_binding_list(binding_values, specs=specs_map)
        picker_limit = _coerce_int(data.get("picker_limit"), 1000)
        fzf_style = str(data.get("fzf-style") or "title-preview").strip()
        fzf_binary = str(data.get("fzf-binary") or "fzf").strip()
        fzf_header_format = str(data.get("fzf-header-format") or "")
        match_format = str(data.get("match-format") or "")
        diff_style = str(data.get("diff_style") or "inline").strip().lower() or "inline"
        note_editor = str(data.get("note_editor") or "markdown").strip().lower() or "markdown"
        color_scheme = str(data.get("color_scheme") or "calm").strip()
        color_schemes = _normalize_color_schemes(data.get("color_schemes"))
        fzf_preview_window = _clean_optional_string(data.get("fzf-preview-window"))
        fzf_flags = list(_coerce_flags(data.get("fzf-extra-flags")))
        raw_zotero = data.get("zotero")
        if isinstance(raw_zotero, Mapping):
            allowed_keys = {
                "mode",
                "endpoint",
                "local_api",
                "library_id",
                "library_type",
                "api_key",
                "storage_dir",
            }
            unknown_zotero = [str(key) for key in raw_zotero.keys() if key not in allowed_keys]
            zotero_data: Mapping[str, Any] = raw_zotero
        else:
            unknown_zotero = []
            zotero_data = {}
        zotero_cfg = ZoteroConfig.from_mapping(zotero_data)
        return cls(
            picker_limit=picker_limit,
            fzf_style=fzf_style or "title-preview",
            fzf_binary=fzf_binary or "fzf",
            fzf_extra_flags=fzf_flags,
            fzf_extra_bindings=[binding.raw for binding in bindings],
            fzf_header_format=fzf_header_format,
            match_format=match_format,
            diff_style=diff_style,
            color_scheme=color_scheme or "calm",
            color_schemes=color_schemes,
            note_editor=note_editor,
            zotero=zotero_cfg,
            fzf_preview_window=fzf_preview_window,
            picker_bindings=bindings,
            zotero_unknown_keys=unknown_zotero,
        )

    def picker_help_lines(self) -> List[str]:
        return [
            line
            for line in (binding.help_line() for binding in self.picker_bindings)
            if line
        ]


DEFAULTS: Dict[str, Any] = {
    "picker_limit": 1000,
    "fzf-style": "title-preview",
    "fzf-binary": "fzf",
    "fzf-extra-flags": ["--ansi", "--multi", "-i", "--delimiter= :: ", "--cycle"],
    "fzf-extra-bindings": [spec.raw() for spec in DEFAULT_PICKER_BINDINGS],
    "fzf-header-format": "{doc[title]:<70.70} :: {doc[author]} :: «{doc[year]}» :: :{doc[tags]} :: {doc[key]} :: {doc[citekey]}",
    "match-format": "{doc[title]} :: {doc[author]} :: {doc[year]} :: :{doc[tags]}",
    "diff_style": "inline",
    "color_scheme": "calm",
    "color_schemes": {
        "bright": {
            "title": "bold bright_cyan",
            "author": "magenta dim",
            "venue": "yellow",
        },
        "calm": {
            "title": "bold blue",
            "author": "dark_magenta",
            "venue": "dark_orange3",
        },
        "minimal": {
            "title": "bold",
            "author": "",
            "venue": "",
        },
        "contrast": {
            "title": "bold bright_white",
            "author": "bright_black",
            "venue": "bright_yellow",
        },
    },
    "note_editor": "markdown",
    "zotero": {
        "mode": "web",
        "endpoint": None,
        "local_api": "http://localhost:23119/api",
        "library_id": 0,
        "library_type": "user",
        "api_key": None,
        "storage_dir": DEFAULT_STORAGE_DIR,
    },
}


def _dedupe_flags(flags: Sequence[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            result.append(flag)
    return result


def _coerce_bindings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return [str(value)]


def _coerce_flags(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except Exception:
            return [value]
    return [str(value)]


def _apply_style(cfg: Dict[str, Any]) -> None:
    style = (cfg.get("fzf-style") or "").strip().lower()
    if not style:
        return

    aligned_header = (
        "{doc[title]:<70.70} :: {doc[author]} :: «{doc[year]}» :: :{doc[tags]} :: {doc[key]} :: {doc[citekey]}"
    )
    compact_header = (
        "{doc[title]:.80} :: {doc[author]:.40} :: {doc[year]} :: :{doc[tags]} :: {doc[key]} :: {doc[citekey]}"
    )
    colored_header = (
        "\x1b[1;36m{doc[title]:.80}\x1b[0m :: "
        "\x1b[33m{doc[author]:.40}\x1b[0m :: "
        "\x1b[90m{doc[year]}\x1b[0m :: :{doc[tags]} :: {doc[key]} :: {doc[citekey]}"
    )
    default_match = "{doc[title]} :: {doc[author]} :: {doc[year]} :: :{doc[tags]}"

    flags: List[str] = list(cfg.get("fzf-extra-flags", []))

    def ensure_flag(flag: str) -> None:
        if flag not in flags:
            flags.append(flag)

    def ensure_flag_pair(flag: str, value: str) -> None:
        try:
            idx = flags.index(flag)
            if idx + 1 < len(flags):
                flags[idx + 1] = value
            else:
                flags.extend([flag, value])
        except ValueError:
            flags.extend([flag, value])

    ensure_flag_pair("--delimiter", " :: ")

    if style == "aligned":
        cfg["fzf-header-format"] = aligned_header
        cfg["match-format"] = default_match
        ensure_flag("--ansi")
    elif style == "compact":
        cfg["fzf-header-format"] = compact_header
        cfg["match-format"] = default_match
        ensure_flag("--ansi")
    elif style == "colored":
        cfg["fzf-header-format"] = colored_header
        cfg["match-format"] = default_match
        ensure_flag("--ansi")
    elif style == "title-preview":
        cfg["fzf-header-format"] = (
            "\x1b[1;36m{doc[title]:.120}\x1b[0m :: {doc[author]} :: «{doc[year]}» :: :{doc[tags]} :: {doc[key]} :: {doc[citekey]}"
        )
        cfg["match-format"] = default_match
        ensure_flag("--ansi")
        ensure_flag_pair("--with-nth", "1")
        # Use the preview client (daemon-backed) but render compact text
        # to match the original title-preview single-line look.
        ensure_flag_pair("--preview", "ref preview-client --text {5}")
        ensure_flag_pair("--preview-window", "up,1,wrap")
    elif style == "preview":
        cfg["fzf-header-format"] = aligned_header
        cfg["match-format"] = default_match
        ensure_flag("--ansi")
        # Use the preview client; it talks to an optional background daemon via FDs.
        ensure_flag_pair("--preview", "ref preview-client {5}")
        ensure_flag_pair("--preview-window", "right,60,border")
    else:
        echo(
            f"Warning: unknown fzf-style '{style}'. Using defaults. Supported: compact, aligned, colored, preview, title-preview",
            err=True,
        )
        return

    custom_preview_window = cfg.get("fzf-preview-window")
    if isinstance(custom_preview_window, str) and custom_preview_window:
        ensure_flag_pair("--preview-window", custom_preview_window)

    cfg["fzf-extra-flags"] = _dedupe_flags(flags)


def _clean_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return result if result >= 0 else default


def _normalize_color_schemes(value: Any) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    if not isinstance(value, Mapping):
        return result
    for name, scheme in value.items():
        if not isinstance(scheme, Mapping):
            continue
        result[str(name)] = {str(k): str(v) for k, v in scheme.items()}
    return result


def _normalize_config(cfg: Dict[str, Any]) -> None:
    cfg["fzf-extra-bindings"] = _coerce_bindings(cfg.get("fzf-extra-bindings"))
    cfg["fzf-extra-flags"] = _coerce_flags(cfg.get("fzf-extra-flags"))
    cfg["diff_style"] = str(cfg.get("diff_style", "inline")).strip().lower()
    cfg["note_editor"] = str(cfg.get("note_editor", "markdown")).strip().lower()
    cfg["color_schemes"] = _normalize_color_schemes(cfg.get("color_schemes"))
    try:
        _apply_style(cfg)
    except Exception:
        pass


def _ensure_secure_permissions(path: Path) -> None:
    """Ensure the config file has owner-only permissions (0600).

    If the file is group/other readable or writable, tighten it to 0o600
    and emit a warning once. This protects sensitive fields like
    `zotero.api_key` from other local users.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return

    # Detect any group/other bits and strip them.
    bad_bits = stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH
    if st.st_mode & bad_bits:
        try:
            os.chmod(path, 0o600)
            echo(f"Hardened permissions on {path} to 0600 for privacy.", err=True)
        except Exception:
            # Best-effort; if chmod fails, at least warn.
            echo(
                f"Warning: {path} has permissive permissions. Consider 'chmod 600' to protect your api_key.",
                err=True,
            )


def _load_raw_config(*, read_only: bool = False) -> Dict[str, Any]:
    cfg = deepcopy(DEFAULTS)

    path = _config_path()
    if not path.exists() and not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(cfg, handle, sort_keys=False, allow_unicode=True)
        # Lock down permissions on first write.
        try:
            os.chmod(path, 0o600)
        except Exception:
            echo(
                f"Warning: could not set secure permissions on {path}. Run 'chmod 600' manually.",
                err=True,
            )
    if path.exists():
        # Ensure permissions are secure before reading.
        if not read_only:
            _ensure_secure_permissions(path)
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
            if read_only and not isinstance(loaded, dict):
                raise ValueError("Config must contain a YAML mapping")
            if isinstance(loaded, dict):
                for key, value in loaded.items():
                    if read_only and key == "zotero" and not isinstance(value, dict):
                        raise ValueError("zotero must contain a YAML mapping")
                    if key == "zotero" and isinstance(value, dict):
                        cfg["zotero"].update(value)  # type: ignore[index]
                    else:
                        cfg[key] = value

    _normalize_config(cfg)
    return cfg


def _config_path() -> Path:
    return Path(os.environ.get("REF_CONFIG", "~/.config/ref/config.yaml")).expanduser()


def load(*, read_only: bool = False) -> RefConfig:
    """Load configuration, optionally without creating files or changing modes."""
    raw_cfg = _load_raw_config(read_only=read_only)
    return RefConfig.from_mapping(raw_cfg)
