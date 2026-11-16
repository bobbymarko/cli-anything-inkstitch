"""`regress` command group: the planner regression suite."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click


@click.group("regress")
def regress():
    """Planner regression suite: run the cases, compare two runs.

    Judges a planner change on the stitches the engine will sew (engine_gate), across fonts
    and shapes, against a baseline. Run before and after a change and attach the compare.
    """


@regress.command("run")
@click.option("--results", "results_path", type=click.Path(dir_okay=False),
              default="regress-results.json", show_default=True,
              help="Results JSON to write; contact sheets go to <stem>_sheets/ beside it.")
@click.option("--cases", "extra", type=click.Path(exists=True, dir_okay=False), multiple=True,
              help="Extra case file (repeatable), e.g. private client cases kept outside the repo.")
@click.option("--quick", is_flag=True, help="Only the quick subset (about six cases, a few minutes).")
@click.option("--only", default=None, help="Comma-separated case ids.")
@click.option("--versus", is_flag=True,
              help="Run only the planner-versus-contender cases (ours and Bold Bobbin's, same words, same fonts).")
@click.option("--no-public", is_flag=True, help="Skip the repo's public cases (run only --cases files).")
@click.option("--no-sheets", is_flag=True, help="Do not draw contact sheets.")
@click.option("--keep-work", is_flag=True, help="Keep each case's project and SVG.")
@click.option("--list", "list_only", is_flag=True, help="List the selected cases and exit.")
@click.pass_context
def run(ctx, results_path, extra, quick, only, versus, no_public, no_sheets, keep_work, list_only):
    """Run the suite sequentially and write the results JSON."""
    from cli_anything_inkstitch.regress import cases as C
    from cli_anything_inkstitch.regress.runner import run_suite
    try:
        cases = C.select(C.load_cases(list(extra), public=not no_public), quick=quick,
                         only=only.split(",") if only else None, versus=versus)
    except C.CaseError as e:
        raise click.ClickException(str(e))
    if list_only:
        for c in cases:
            click.echo(f"{c.id}\t{c.kind}\t{c.family}\t{'quick' if c.quick else ''}"
                       f"\t{'SKIP: ' + c.skip_reason if c.skip_reason else ''}")
        return
    if not cases:
        raise click.ClickException("no cases selected")
    files = [str(C.PUBLIC_CASES.name)] if not no_public else []
    files += [Path(p).name for p in extra]        # names only: a results file may be shared
    log = lambda s: click.echo(s, err=True)
    doc = run_suite(cases, Path(results_path), case_files=files, quick=quick,
                    sheets=not no_sheets, keep_work=keep_work,
                    binary=(ctx.obj or {}).get("binary_override"), log=log)
    s = doc["summary"]
    log(f"{s['ok']}/{s['cases']} ok, {s['errors']} errors, {s['skipped']} skipped, "
        f"{s['multi_pass_columns']} multi-pass of {s['satin_columns']} satin columns, "
        f"{doc['meta']['total_runtime_s']}s -> {results_path}")
    if (ctx.obj or {}).get("json"):
        click.echo(json.dumps({"results": str(results_path), "summary": s}))
    sys.exit(1 if s["errors"] else 0)


@regress.command("compare")
@click.argument("base", type=click.Path(exists=True, dir_okay=False))
@click.argument("new", type=click.Path(exists=True, dir_okay=False))
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default="regress-compare",
              show_default=True, help="Directory for the side-by-side image of each changed case.")
@click.option("--no-images", is_flag=True)
@click.option("--accepted", "accepted_files", type=click.Path(exists=True, dir_okay=False), multiple=True,
              help="More accepted changes (private cases keep theirs beside their case files); the repo's "
                   "regress/accepted.json is always read.")
@click.option("--json", "as_json", is_flag=True, help="Print the comparison as JSON.")
@click.pass_context
def compare_cmd(ctx, base, new, out_dir, no_images, accepted_files, as_json):
    """Compare two results files; exit 1 on a regression.

    Regressions: any multi-pass column increase; a new untrimmed move over 3 mm; longest
    stitch over 8 mm (when worse than the baseline); trims up by more than max(2, 10%); a
    case that now fails. A stitch count moving more than 5% is a warning. Thresholds are
    constants at the top of regress/compare.py. A regression listed as an accepted change
    (regress/accepted.json, plus any --accepted file: {case, metric, reason, max?}) is reported
    as accepted and does not fail the compare.
    """
    from cli_anything_inkstitch.regress import compare as K
    try:
        accepted = K.load_accepted(accepted_files)
    except ValueError as e:
        raise click.ClickException(str(e))
    res = K.compare(K.load(base), K.load(new), out_dir, images=not no_images, accepted=accepted)
    if as_json or (ctx.obj or {}).get("json"):
        click.echo(json.dumps(res, indent=1))
    else:
        click.echo(K.format_table(res))
    sys.exit(0 if res["ok"] else 1)


@regress.command("versus")
@click.argument("results", type=click.Path(exists=True, dir_okay=False))
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default="regress-versus",
              show_default=True, help="Directory for the planner | contender image of each case.")
@click.option("--no-images", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def versus_cmd(ctx, results, out_dir, no_images, as_json):
    """Our planner next to a contender (Bold Bobbin), per case, from a `run --versus` results file.

    Stitches, trims, untrimmed moves over 3 mm, longest stitch, time and ink size for both, and a
    side-by-side contact sheet per case. Multi-pass is n/a for the contender. Never exits nonzero:
    it reports, `compare` judges.
    """
    from cli_anything_inkstitch.regress import versus as V
    doc = json.loads(Path(results).read_text())
    res = V.versus(doc, Path(results).resolve().parent, out_dir, images=not no_images)
    click.echo(json.dumps(res, indent=1) if as_json or (ctx.obj or {}).get("json") else V.format_table(res))
