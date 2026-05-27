from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from . import diff as _diff
from .config import load_config
from .fixes import apply_fixes, count_fixable
from .llm import probe_transports, select_transport
from .reporters import render_github, render_human, render_json, render_markdown
from .rules.registry import SpecLintError
from .runner import exit_code_for, run


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, help="Show speclint version and exit.")
@click.pass_context
def main(ctx: click.Context, version: bool) -> None:
    """speclint — semantic linter for spec folders."""
    if version:
        click.echo(f"speclint {__version__}")
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(0)


@main.command()
@click.argument("repo_root", default=".", type=click.Path(file_okay=False, exists=True))
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["human", "json", "markdown", "github"]),
    default="human",
    help=(
        "Output format. `markdown` is PR-comment friendly; `github` emits "
        "workflow commands for inline annotations."
    ),
)
@click.option(
    "--fail-on",
    type=click.Choice(["error", "warn", "never"]),
    default=None,
    help="Override config fail_on threshold.",
)
@click.option(
    "--base",
    default=None,
    help=(
        "Git ref to diff against for Path B coupling rules "
        "(e.g., origin/main). Runs `git diff --name-only <base>...HEAD`."
    ),
)
@click.option(
    "--changed-files-from",
    type=click.Path(dir_okay=False),
    default=None,
    help=(
        "Path to a file containing newline-separated changed paths. "
        "Mutually exclusive with --base."
    ),
)
@click.option(
    "--fix",
    "apply_fix",
    is_flag=True,
    default=False,
    help=(
        "Apply auto-fix patches emitted by rules. Rewrites files in "
        "place; review the diff before committing."
    ),
)
def check(repo_root: str, output_format: str, fail_on: str | None,
          base: str | None, changed_files_from: str | None,
          apply_fix: bool) -> None:
    """Lint spec folders under REPO_ROOT (default: current directory)."""
    if base and changed_files_from:
        click.echo("speclint: --base and --changed-files-from are mutually exclusive", err=True)
        sys.exit(2)

    root = Path(repo_root).resolve()
    try:
        cfg = load_config(root)
        if fail_on:
            cfg.fail_on = fail_on
        changed_paths: tuple[str, ...] | None = None
        if base:
            changed_paths = _diff.from_git(root, base)
        elif changed_files_from:
            changed_paths = _diff.from_file(Path(changed_files_from))
        result = run(root, cfg, changed_paths=changed_paths)
    except SpecLintError as e:
        click.echo(f"speclint: {e}", err=True)
        sys.exit(2)

    if apply_fix:
        fix_result = apply_fixes(root, result.findings)
        click.echo(
            f"[fix] applied {len(fix_result.applied)}, "
            f"skipped {len(fix_result.skipped)}",
            err=True,
        )
        for path, rule_id in fix_result.applied:
            click.echo(f"[fix]   ✓ {path}  ({rule_id})", err=True)
        for path, rule_id, reason in fix_result.skipped:
            click.echo(f"[fix]   ✗ {path}  ({rule_id}): {reason}", err=True)

    renderers = {
        "human": render_human,
        "json": render_json,
        "markdown": render_markdown,
        "github": render_github,
    }
    click.echo(renderers[output_format](result), nl=False)

    fixable = count_fixable(result.findings)
    if fixable and not apply_fix and output_format == "human":
        click.echo(f"\n{fixable} finding(s) auto-fixable — rerun with --fix to apply.")

    sys.exit(exit_code_for(result, cfg.fail_on))


@main.group()
def llm() -> None:
    """LLM transport utilities."""


@llm.command("doctor")
def llm_doctor() -> None:
    """Show which LLM transports are available and what would be auto-picked."""
    probes = probe_transports()
    click.echo("[transport probe]")
    for p in probes:
        mark = "✓" if p.available else "✗"
        click.echo(f"  {p.name:<7} {mark} {p.reason}")

    click.echo("\n[auto resolution]")
    try:
        chosen = select_transport("auto")
    except SpecLintError as e:
        click.echo(f"  error: {e}")
        return
    if chosen is None:
        click.echo("  chosen: none (all transports unavailable)")
        click.echo(
            "  hint: set ANTHROPIC_API_KEY/OPENAI_API_KEY or install claude/codex CLI"
        )
    else:
        click.echo(f"  chosen: {chosen}")
        from .llm import DEFAULT_MODELS

        click.echo(f"  default model: {DEFAULT_MODELS[chosen]}")


if __name__ == "__main__":
    main()
