# speclint

ESLint-shaped linter for markdown spec folders. Catches the drift that
specs accumulate — TBDs, weasel words, dangling references, near-duplicate
sections, requirements that lack acceptance hooks — before the spec lands
in main.

- **Format-agnostic IR.** Parses markdown into headings / links / claims /
  terms, plus a small `spec.yml` manifest per folder.
- **Three tiers.** Static (regex/AST), semantic (embeddings, deterministic),
  LLM (pre-wired, no default rules in v1). Each tier gracefully skips if
  its dependency isn't installed.
- **Plugin-friendly.** Team rule packages register via entry points and
  override built-ins last-package-wins, with an audit log.
- **PR-native.** GitHub Action posts a sticky comment, drops inline
  annotations on the diff, drives build status off severity.

## Install

```bash
pip install speclint                  # core (Tier 1 only)
pip install 'speclint[semantic]'      # + Tier 2 embeddings (BAAI/bge-small-en-v1.5)
pip install 'speclint[anthropic]'     # + Anthropic transport for Tier 3
pip install 'speclint[openai]'        # + OpenAI transport for Tier 3
pip install 'speclint[all]'           # everything
```

## First run

```bash
speclint check .
```

Against the bundled `specs/example-spec/`:

```
[semantic] fastembed not installed; skipping 2 rule(s): claim-redundancy, heading-redundancy
           hint: pip install 'speclint[semantic]'

[spec] example-spec
   warn  no-weasel-words           README.md:8  Weasel word: 'fast'
          hint: Replace with a measurable threshold or metric.
   warn  no-tbd                    README.md:13  Unresolved marker: TBD
          hint: Resolve, move to open-questions.md, or set status: draft.
   warn  refs-resolve              spec.yml  `references` glob matches no files: src/example/**
          hint: Remove the entry, fix the path, or move the spec to status: deprecated.

summary: 0 error(s), 7 warn(s), 0 info across 1 spec(s), 5 rule(s) evaluated
```

Exit code is driven by `--fail-on` (`error` | `warn` | `never`, default
`error`). `7 warn` with default settings → exit 0; pass `--fail-on warn`
to make warnings fail the build.

## Spec folder layout

A spec is a directory under `specs/`:

```
specs/
  payments/
    spec.yml          # required manifest
    README.md         # main body
    api.md            # supplementary docs
    open-questions.md
```

`spec.yml`:

```yaml
id: payments
status: accepted          # draft | accepted | implemented | deprecated
owner: payments-team
references:               # globs of code/tests this spec owns
  - "src/payments/**"
  - "tests/payments/**"
related:                  # other spec ids
  - billing
```

## What it checks

| Rule | Tier | Default | Fires when |
|---|---|---|---|
| `manifest-valid` | static | error | `spec.yml` is missing required fields or has an invalid `status` |
| `no-tbd` | static | warn | A `TBD` / `FIXME` / `XXX` marker is left in spec text |
| `no-weasel-words` | static | warn | Vague qualifiers (`fast`, `robust`, `scalable`, `user-friendly`, …) appear in prose |
| `refs-resolve` | static | warn | A `references:` glob in `spec.yml` matches zero files in the repo |
| `refs-coupling` | static | warn | The diff touches files under `references:` but the spec folder itself isn't updated (Path B) |
| `heading-redundancy` | semantic | info | Two headings in the same file have near-identical embeddings (likely duplicate sections) |
| `claim-redundancy` | semantic | info | Two MUST/SHALL claims in the same file have near-identical embeddings (likely duplicate requirement) |

`refs-coupling` is **Path B** — it's only meaningful with a diff context.
Pass `--base origin/main` (or `--changed-files-from file.txt`) to enable
it. The GitHub Action does this automatically.

## Configuration

Drop a `.speclint.yml` at the repo root. Everything is optional — the
shape below is the full reference. A working starter is in
[`.speclint.yml.example`](.speclint.yml.example).

```yaml
# Rule packages to load, in order. Later packages override earlier ones
# by rule id. Each package registers via the `speclint.rules` entry point.
packages:
  - default
  - my-team-rules           # your plugin's entry-point name

# Globs (relative to repo root) that mark spec folders.
specs:
  - "specs/*/"
  - "docs/rfcs/*/"

# Markdown files inside each spec folder to include / ignore.
include:
  - "**/*.md"
ignore:
  - "**/scratch/**"

# Per-rule overrides. A bare string sets severity; a mapping passes options.
rules:
  no-tbd: error                       # bump severity
  no-weasel-words: off                # disable
  heading-redundancy:
    severity: warn
    threshold: 0.92                   # raise the similarity bar
  claim-redundancy:
    threshold: 0.90

# Exit-code threshold. `error` (default) fails only on errors; `warn`
# fails on any warning; `never` always exits 0.
fail_on: error

# Tier 3 LLM rules (none ship by default — opt-in only).
llm:
  enabled: true
  transport: auto                     # auto | api | openai | cli | codex
  model: null                         # null = each transport's default
  cache: .speclint-cache/llm/
```

## CLI

```
speclint check [PATH] [OPTIONS]

  --format human|json|markdown|github   Output format. Default: human.
  --fail-on error|warn|never            Override config fail_on threshold.
  --base REF                            Diff against this git ref to enable
                                        Path B coupling rules.
  --changed-files-from FILE             Read changed paths from a file
                                        instead of running git. Mutually
                                        exclusive with --base.

speclint llm doctor                     Probe LLM transports and show what
                                        auto-resolution would pick.

speclint --version
```

Format cheat sheet:

| Format | Use case |
|---|---|
| `human` | Local dev. Colored-ish summary with hints. |
| `json` | Programmatic consumers, dashboards. |
| `markdown` | PR comment bodies. Includes the sticky-comment marker. |
| `github` | `::warning file=…,line=…::` workflow commands for inline PR annotations. |

## CI integration

The repo ships a composite GitHub Action. Paste:

```yaml
# .github/workflows/spec-lint.yml
name: spec-lint
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write       # required for the sticky PR comment

jobs:
  speclint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0     # so Path B can diff against the base ref
      - uses: <owner>/spec-lint@v0.1
        with:
          fail-on: error
```

What it does:

1. Installs `speclint`.
2. Resolves the base ref — PR base on `pull_request`, `HEAD~1` on `push`.
3. Emits inline annotations to the PR diff (`--format github`).
4. Renders the markdown report and posts a **sticky** PR comment — the
   next run edits the same comment instead of stacking new ones.
5. Re-runs to drive build status off `--fail-on`.

Action inputs: `path`, `base-ref`, `fail-on`, `comment`, `python-version`,
`speclint-version`, `github-token`. See [`action.yml`](action.yml).

## Plugins — overriding and extending rules

speclint discovers rule packages via Python entry points. To add or
override rules, ship a small package:

`pyproject.toml`:

```toml
[project]
name = "my-team-rules"
dependencies = ["speclint"]

[project.entry-points."speclint.rules"]
my-team = "my_team_rules:register"
```

`my_team_rules/__init__.py`:

```python
from speclint.rules.registry import collect_rules_from, rule
from speclint.rules.types import Finding, Fixture, ExpectedFinding


@rule(
    id="no-tbd",                      # same id → overrides built-in
    version="2.0.0",
    tier="static",
    default_severity="error",         # we're stricter
    rationale="Internal policy: no TBDs survive merge to main.",
    fixtures=[
        Fixture(
            name="fires-on-tbd",
            files={
                "spec.yml": "id: x\nstatus: accepted\n",
                "README.md": "# x\n\nTBD: figure this out\n",
            },
            expects=(ExpectedFinding(message_contains="TBD"),),
        ),
    ],
)
def check(ir, config):
    findings = []
    for path, text in ir.raw_text.items():
        for i, line in enumerate(text.splitlines(), start=1):
            if "TBD" in line:
                findings.append(Finding(
                    rule_id="no-tbd", severity=config.get("severity", "error"),
                    file=path, line=i, message="Unresolved marker: TBD",
                ))
    return findings


def register():
    from . import __init__ as self_mod
    return collect_rules_from(self_mod)
```

Activate it in `.speclint.yml`:

```yaml
packages:
  - default
  - my-team       # entry-point name; later packages win
```

speclint logs every override:

```
override log:
  no-tbd: default:1.0.0 -> my-team:2.0.0
```

## Optional tiers — graceful skip

speclint installs the core tier (Tier 1) unconditionally. Tier 2 and
Tier 3 are opt-in extras that **fail soft**:

- **Tier 2 (semantic)** needs `fastembed`. Without it, semantic rules are
  silently dropped from the run with a one-line note explaining how to
  install. The check doesn't fail.
- **Tier 3 (LLM)** needs one of: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `claude` CLI on PATH, `codex` CLI on PATH. Same graceful-skip pattern.
  Run `speclint llm doctor` to inspect what would be picked.

This means CI works on minimal runners, contributors don't need API
keys, and stricter checks can be layered on without rewriting config.

## Path A vs Path B

Two complementary modes:

- **Path A — quality.** Inspect a spec on its own. Runs on every push,
  every PR, locally with `speclint check .`. Most rules live here.
- **Path B — coupling.** Inspect a spec *against the diff*. Catches
  "you edited the code but not the spec" and "you edited the spec but
  the referenced code didn't move." Activated by `--base <ref>` or
  `--changed-files-from <file>`. `refs-coupling` lives here.

## Status

v0.1.0 — platform is feature-complete. Seven rules ship; the
infrastructure for adding more is small and well-tested (162 tests,
94% coverage). Tier 3 is wired but ships zero default LLM rules by
design — teams opt in via plugins.

## License

MIT.
