"""Click root + global flags + error handling shim."""

from __future__ import annotations

import sys

import click

from cli_anything_inkstitch import __version__
from cli_anything_inkstitch.errors import CLIError
from cli_anything_inkstitch.output import print_error


class GroupedCLI(click.Group):
    """Group that catches CLIError and translates to the documented exit codes."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except CLIError as e:
            print_error(ctx, e.error_type, str(e))
            sys.exit(e.exit_code)


@click.group(cls=GroupedCLI, invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--project", "-p", "project_path", type=click.Path(), default=None,
              help="Absolute path to .inkstitch-cli.json project file.")
@click.option("--inkstitch-binary", "binary_override", type=click.Path(), default=None,
              help="Path to the Ink/Stitch binary (overrides discovery).")
@click.option("--verbose", "-v", is_flag=True)
@click.option("--debug", is_flag=True)
@click.version_option(__version__)
@click.pass_context
def root(ctx, as_json, project_path, binary_override, verbose, debug):
    ctx.ensure_object(dict)
    ctx.obj["json"] = as_json
    ctx.obj["project_path"] = project_path
    ctx.obj["binary_override"] = binary_override
    ctx.obj["verbose"] = verbose
    ctx.obj["debug"] = debug

    if ctx.invoked_subcommand is None:
        if project_path is None:
            click.echo(ctx.get_help())
            ctx.exit(0)
        # REPL stub for v0.1
        from cli_anything_inkstitch.repl import run_repl
        run_repl(ctx)


# ---- subcommand registration ----

from cli_anything_inkstitch.commands import (  # noqa: E402
    document as _document,
    element as _element,
    params as _params,
    commands_group as _commands_group,
    tools as _tools,
    validate as _validate,
    preview as _preview,
    export as _export,
    schema_group as _schema_group,
    session as _session,
)

root.add_command(_document.document, "document")
root.add_command(_element.element, "element")
root.add_command(_params.params, "params")
root.add_command(_commands_group.commands_group, "commands")
root.add_command(_tools.tools, "tools")
root.add_command(_validate.validate, "validate")
root.add_command(_preview.preview, "preview")
root.add_command(_export.export, "export")
root.add_command(_schema_group.schema_group, "schema")
root.add_command(_session.session, "session")
