"""Shared command registration helpers for the CLI."""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Iterable, Iterator, Optional, Tuple

import typer

from ..utils.interaction import require_interactive

CommandCallback = Callable[..., None]


@dataclass(frozen=True)
class CommandBinding:
    """Describe how a Typer command should be registered."""

    callback: CommandCallback
    name: Optional[str] = None
    aliases: Tuple[str, ...] = ()
    interactive: bool = False

    def primary_name(self) -> Optional[str]:
        return self.name

    def iter_aliases(self) -> Iterator[str]:
        yield from self.aliases


def register_bindings(app: typer.Typer, bindings: Iterable[CommandBinding]) -> None:
    """Register one or more command bindings with the Typer app."""

    for binding in bindings:
        primary = binding.primary_name()
        callback = binding.callback
        if binding.interactive:
            callback = _guard_interactive(callback, primary or callback.__name__)
        decorator = app.command(name=primary) if primary else app.command()
        decorator(callback)
        for alias in binding.iter_aliases():
            app.command(name=alias)(callback)


def _guard_interactive(callback: CommandCallback, name: str) -> CommandCallback:
    """Preserve command options while enforcing the opt-in interaction policy."""
    @wraps(callback)
    def guarded(*args: Any, **kwargs: Any) -> None:
        require_interactive(name)
        return callback(*args, **kwargs)

    return guarded

