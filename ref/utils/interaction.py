"""Read the interaction policy for the current CLI invocation."""

import typer

try:
    # Typer >=0.26 owns a separate context stack after vendoring Click.
    from typer._click.globals import get_current_context
except ImportError:
    from click import get_current_context


def is_non_interactive() -> bool:
    """Return whether the current command explicitly disables interaction."""
    context = get_current_context(silent=True)
    return bool(context and context.find_root().params.get("non_interactive"))


def require_interactive(command: str) -> None:
    """Reject an interactive command before it starts work in unattended mode."""
    if is_non_interactive():
        raise typer.BadParameter(
            f"'{command}' requires interaction; use read for data, "
            "write for reviewed changes, or list/export with explicit arguments."
        )


def require_write_confirmation(command: str, confirmed: bool) -> None:
    """Require explicit user-approval acknowledgement for unattended mutations."""
    if is_non_interactive() and not confirmed:
        raise typer.BadParameter(
            f"{command} requires --yes after explicit user approval in "
            "non-interactive mode; use read/write to prepare reviewable edits."
        )
