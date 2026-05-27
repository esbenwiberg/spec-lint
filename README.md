# speclint

ESLint-shaped linter for markdown spec folders. Catches the drift that
specs accumulate — TBDs, weasel words, dangling references, near-duplicate
sections, requirements that lack acceptance hooks — before the spec lands
in main.

- **Format-agnostic.** No schema, no required manifest. Drop spec-lint in
  any repo and it lints whatever markdown layout you already have under
  conventional roots (`specs/`, `docs/specs/`, `features/`, `rfcs/`,
  `.kiro/specs/`, `openspec/`, and more).
- **Three tiers.** Static (regex/AST), semantic (embeddings,
  deterministic), LLM (Anthropic/OpenAI/CLI transports). Each tier
  gracefully skips if its dependency isn't installed.
- **Auto-fix.** Rules can emit a `Patch` alongside their finding.
  `speclint check --fix` applies safe mechanical rewrites (renames,
  typos); ambiguous patches are refused. Inspired by ESLint `--fix`.
- **Opt-in metadata.** Rules that need structured data (status,
  references) read from an optional sidecar YAML you nominate
  (`contract.yaml`, `spec.yml`, `meta.yml`, anything). No schema is
  imposed — field names are configurable per rule.
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
   …

summary: 0 error(s), 6 warn(s), 0 info across 1 spec(s), 4 rule(s) evaluated
```

Exit code is driven by `--fail-on` (`error` | `warn` | `never`, default
`error`). With defaults, warns don't fail the build — pass
`--fail-on warn` if they should.

## Spec discovery

A *spec* is one of two things:

- A folder containing one or more direct `.md` files. Its body is just
  those direct `.md` files (sub-folders become their own specs).
- A `.md` file sitting directly under a known root, with no folder of
  its own. Common for ADR-style and PEP-style flat layouts.

speclint walks a curated list of conventional roots out of the box:

```
specs/           spec/            docs/specs/       docs/spec/
.kiro/specs/     openspec/specs/  openspec/changes/
features/        feats/           docs/features/    docs/feats/
briefs/          docs/briefs/     rfcs/             docs/rfcs/
```

Override or extend the list in `.speclint.yml` (see Configuration).

No manifest is required. speclint adapts to *your* spec layout, not the
other way around.

## Metadata sidecar (optional)

Some rules — `refs-resolve`, `refs-coupling`, the draft-status skip in
`no-tbd` — need structured data the markdown can't supply. They read it
from an optional YAML sidecar in each spec folder. You pick the
filename; speclint imposes no schema.

`.speclint.yml`:

```yaml
metadata:
  sidecar: contract.yaml      # or spec.yml, meta.yml, anything
```

Then a spec folder can look like:

```
specs/
  payments/
    contract.yaml             # any YAML mapping; speclint reads keys on demand
    README.md
    api.md
```

`contract.yaml` (example — your fields, your call):

```yaml
status: accepted              # used by no-tbd to skip draft specs
references:                   # used by refs-resolve / refs-coupling
  - "src/payments/**"
  - "tests/payments/**"
owner: payments-team          # ignored by core; available to plugins
```

### Reading nested fields

If your sidecar already has reference paths buried in a nested
structure — common for SDD-style contract docs — you don't have to
flatten them. Point `refs-resolve` / `refs-coupling` at a dotted path
with `[*]` for list expansion:

```yaml
# .speclint.yml
metadata:
  sidecar: contract.yaml
rules:
  refs-resolve:
    field: required_facts[*].artifact.path
  refs-coupling:
    field: required_facts[*].artifact.path
```

Against a contract like:

```yaml
# specs/foo/contract.yaml
required_facts:
  - id: schema-test
    artifact:
      path: packages/shared/src/schemas/foo.test.ts
      change: update
```

…the rules read `["packages/shared/src/schemas/foo.test.ts"]` and lint
exactly as if you'd written a flat `references:` list.

When the sidecar is absent, metadata-driven rules silently no-op for
that spec. Everything else (TBD/weasel/heading-redundancy/…) keeps
working on the markdown alone.

## What it checks

| Rule | Tier | Default | Fires when |
|---|---|---|---|
| `no-tbd` | static | warn | A `TBD` / `TODO` / `FIXME` / `???` marker is left in spec text. Skipped when sidecar declares `status: draft`. |
| `no-weasel-words` | static | warn / info | Vague qualifiers split by signal: high-signal (`scalable`, `robust`, `user-friendly`, `intuitive`, `performant`) fire as `warn`; low-signal (`fast`, `just`, `simply`, `easy`, `modern`) fire as `info` because they have a real false-positive rate in code-adjacent prose. |
| `claims-have-hooks` | static | warn | A claim in the sidecar (e.g. `scenarios[*].id`) is never referenced by any hook entry (e.g. `required_facts[*].proves[*]`). Catches orphan claims that no test or fact covers. Both claim and hook field paths are configurable; accepts list of paths so heterogeneous schemas work. |
| `refs-resolve` | static | warn | A reference glob in the sidecar matches zero files in the repo. Opt-in via metadata. Field path supports nesting (e.g. `required_facts[*].artifact.path`). |
| `refs-coupling` | static | warn | The diff touches files under the sidecar's reference list but the spec folder itself isn't updated (Path B). Opt-in via metadata. Same nested-field syntax as `refs-resolve`. |
| `refs-infer-coupling` | static | info | Path B without a sidecar. Scans the spec's prose for path-shaped tokens, filters to paths that actually exist in the repo, and fires when any of those files change while the spec folder is untouched. Noisier than `refs-coupling`; defaults to `info`. |
| `heading-redundancy` | semantic | info | Two headings in the same file have near-identical embeddings (likely duplicate sections). |
| `claim-redundancy` | semantic | info | Two MUST/SHALL claims in the same file have near-identical embeddings (likely duplicate requirement). |
| `no-weasel-words-llm` | llm | warn | Second-pass classifier: promotes the `info`-tier weasel hits to `warn` when a small model confirms they're real unmeasurable claims (not benign context like product names or code identifiers). Skipped silently when no transport is available. |
| `spec-impl-drift` | llm | info | Reads the spec's nominated artifacts and asks a model whether the code actually does what the spec promises. Anchors findings at artifact-file:line via verbatim evidence quotes. May emit auto-fix patches for mechanical drifts (renames, typos) — apply with `--fix`. Skipped when no sidecar/artifacts or no transport. |
| `internal-contradiction` | llm | info | Batches every spec file into one model call; flags pairs of statements that cannot both be true (e.g. `purpose.md` says "OAuth-only", `design.md` says "API keys remain supported"). Findings anchor on `file_a:line` of the contradicting line. |
| `testability-of-claims` | llm | warn | Classifies each extracted claim (MUST/SHALL/SHOULD/MAY + Gherkin) as TESTABLE or VAGUE. Catches structurally-valid but unmeasurable claims that slip past `no-weasel-words` ("errors are handled gracefully", "supports high concurrency"). |

`refs-coupling` and `refs-infer-coupling` are **Path B** — they're only
meaningful with a diff context. Pass `--base origin/main` (or
`--changed-files-from file.txt`) to enable them. The GitHub Action does
this automatically. Use `refs-coupling` when you have a metadata sidecar
declaring covered paths; `refs-infer-coupling` works without one by
mining prose mentions (lossier, hence `info` by default).

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

# Override the discovery roots entirely. Default = a curated list of
# conventional SDD-framework folders (see Spec discovery).
# roots:
#   - "specs"
#   - "docs/rfcs"

# Add roots without replacing the defaults — for ADR-style layouts, etc.
extra_roots:
  - "adrs"

# fnmatch patterns (relative to repo root) to skip during discovery.
ignore:
  - "**/scratch/**"

# Opt-in metadata sidecar — see "Metadata sidecar (optional)" above.
metadata:
  sidecar: contract.yaml

# Per-rule overrides. A bare string sets severity; a mapping passes options.
rules:
  no-tbd: error                       # bump severity
  no-weasel-words: off                # disable
  heading-redundancy:
    severity: warn
    threshold: 0.92                   # raise the similarity bar
  refs-resolve:
    field: owns                       # rename the metadata key you read from
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

Full walkthrough: [`docs/writing-rules.md`](docs/writing-rules.md) —
end-to-end plugin skeleton, IR reference, testing harness, and an
honest comparison with ESLint's plugin model.

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

`my_team_rules/no_tbd.py`:

```python
from speclint.rules.registry import rule
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
            files={"README.md": "# x\n\nTBD: figure this out\n"},
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
```

`my_team_rules/__init__.py`:

```python
from speclint.rules.registry import collect_rules_from


def register():
    from . import no_tbd      # import each rule module here
    return collect_rules_from(no_tbd)
```

Same pattern as the built-in `default` package — see
[`docs/writing-rules.md`](docs/writing-rules.md) for the full
walkthrough.

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

A plugin rule that reads metadata follows the same pattern as the
built-in `refs-resolve` — read `ir.metadata.get(field)` with a
configurable key name, and silently no-op when it's absent so users
who haven't opted into a sidecar aren't penalized.

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
  "you edited the code but not the spec." Activated by `--base <ref>`
  or `--changed-files-from <file>`. `refs-coupling` lives here and
  requires metadata sidecar opt-in.

## Status

v0.5.0 — 12 rules ship across all three tiers:

- **Static (Tier 1)** — `no-weasel-words`, `no-tbd`, `claims-have-hooks`,
  `refs-resolve`, `refs-coupling`, `refs-infer-coupling`.
- **Semantic (Tier 2)** — `claim-redundancy`, `heading-redundancy`.
- **LLM (Tier 3)** — `no-weasel-words-llm`, `spec-impl-drift`,
  `internal-contradiction`, `testability-of-claims`.

Tier 3 rules anchor findings to source lines via verbatim evidence
quotes (LLM-friendly precision) and can emit `Patch` objects for
mechanical fixes the `--fix` flag will apply. Path B coupling reads
nested sidecar fields, so contract-style schemas work out of the box.

## License

MIT.
