"""Minimal REPL — line-oriented, dispatches each line as a subcommand.

v0.1: no readline integration, no tab completion. State lives only in the
project JSON, which is reloaded on every command (matching one-shot mode).
This keeps it correct, just slower. A future revision can hold the SVG in
memory.
"""

from __future__ import annotations

import shlex

import click

from cli_anything_inkstitch.errors import CLIError


def run_repl(ctx: click.Context) -> None:
    project_path = ctx.obj.get("project_path")
    click.echo(f"cli-anything-inkstitch REPL — project: {project_path}")
    click.echo("type :help for meta-commands, :exit to quit")
    while True:
        try:
            line = input("inkstitch> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo()
            break
        if not line:
            continue
        if line.startswith(":"):
            meta = line[1:].split()
            if not meta or meta[0] in ("exit", "quit", "q"):
                break
            if meta[0] == "help":
                click.echo("meta: :help :exit  /  any subcommand: document, element, params, ...")
                continue
            click.echo(f"unknown meta: {meta[0]}", err=True)
            continue
        try:
            argv = shlex.split(line)
        except ValueError as e:
            click.echo(f"parse error: {e}", err=True)
            continue
        if not argv:
            continue
        # always inject --project so the user doesn't have to repeat it
        if "--project" not in argv and "-p" not in argv:
            argv = [argv[0]] + ["--project", project_path] + argv[1:]
        try:
            from cli_anything_inkstitch.cli import root
            # invoke subcommand without exiting the REPL
            root.main(args=argv, standalone_mode=False, prog_name="cli-anything-inkstitch")
        except SystemExit:
            pass
        except CLIError as e:
            click.echo(f"error: {e}", err=True)
        except click.ClickException as e:
            e.show()
        except Exception as e:  # noqa: BLE001
            click.echo(f"error: {e}", err=True)
