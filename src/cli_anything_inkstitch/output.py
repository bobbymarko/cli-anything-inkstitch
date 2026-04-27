"""Stdout rendering: --json vs human-readable."""

from __future__ import annotations

import json
import sys

import click
from rich.console import Console
from rich.table import Table

_console = Console()


def emit(ctx: click.Context, payload: dict, *, human=None) -> None:
    """Print payload in the format chosen by the global --json flag.

    `human` may be a callable taking the rich Console (for richer output) or
    a string. If omitted, json mode just dumps `payload` and human mode prints
    it via json indent=2.
    """
    as_json = ctx.obj.get("json", False) if ctx.obj else False
    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return
    if callable(human):
        human(_console)
    elif isinstance(human, str):
        _console.print(human)
    else:
        _console.print(json.dumps(payload, indent=2, default=str))


def print_error(ctx: click.Context, error_type: str, message: str) -> None:
    """Print error to stderr; if --json, also emit a JSON error body to stdout."""
    as_json = ctx.obj.get("json", False) if ctx.obj else False
    click.echo(f"error: {message}", err=True)
    if as_json:
        click.echo(json.dumps({"error": {"type": error_type, "message": message}}))


def table(rows: list[dict], columns: list[str]) -> Table:
    t = Table(show_lines=False)
    for c in columns:
        t.add_column(c)
    for r in rows:
        t.add_row(*[_fmt(r.get(c)) for c in columns])
    return t


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v)


def print_table(rows: list[dict], columns: list[str]) -> None:
    _console.print(table(rows, columns))
