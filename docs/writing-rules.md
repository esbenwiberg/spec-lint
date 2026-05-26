# Writing custom rules

speclint rules live in **plugin packages** — small Python packages
that register themselves via an entry point. There's no in-tree rules
config (no `rules/*.py` next to your specs, no inline JS-style
plugins). This doc walks through building one end-to-end.

> **TL;DR.** Make a Python package. Decorate a function with
> `@rule(...)`. Expose a `register()` callable on the
> `speclint.rules` entry point. Add the entry-point *name* to
> `packages:` in `.speclint.yml`. Done.

---

## 1. Package skeleton

A minimum plugin is three files:

```
my-team-rules/
  pyproject.toml
  my_team_rules/
    __init__.py
    no_trailing_spaces.py
```

`pyproject.toml`:

```toml
[project]
name = "my-team-rules"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["speclint>=0.1"]

[project.entry-points."speclint.rules"]
my-team = "my_team_rules:register"
```

The `my-team` on the left is the **entry-point name** — it's what you
put in `packages:` in `.speclint.yml`. The right side is
`module:attr`, where `attr` is a callable returning
`dict[str, Rule]`.

Install in editable mode while iterating:

```bash
pip install -e ./my-team-rules
```

---

## 2. Write a rule

`my_team_rules/no_trailing_spaces.py`:

```python
from __future__ import annotations

from typing import Any

from speclint.ir.types import SpecIR
from speclint.rules.registry import rule
from speclint.rules.types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="clean-file-no-findings",
        files={"README.md": "# Title\n\nNo trailing whitespace here.\n"},
        expects=(),
    ),
    Fixture(
        name="trailing-spaces-fire",
        files={"README.md": "# Title\n\nThis line has trailing spaces.   \n"},
        expects=(ExpectedFinding(line=3, message_contains="trailing whitespace"),),
    ),
]


@rule(
    id="no-trailing-whitespace",
    version="1.0.0",
    tier="static",
    default_severity="info",
    rationale=(
        "Trailing whitespace creates noisy diffs and breaks some renderers. "
        "Strip it on save."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    severity = config.get("severity", "info")
    findings: list[Finding] = []
    for file, text in ir.raw_text.items():
        for i, line in enumerate(text.splitlines(), start=1):
            if line != line.rstrip():
                findings.append(
                    Finding(
                        rule_id="no-trailing-whitespace",
                        severity=severity,
                        file=file,
                        line=i,
                        message="Line has trailing whitespace.",
                        hint="Strip trailing spaces — most editors can do this on save.",
                    )
                )
    return findings
```

Three things to notice:

- **`@rule(...)`** stamps metadata onto the function. The registry
  picks up any function tagged this way when `collect_rules_from` walks
  the module.
- **`check(ir, config)`** is the actual entry point. `ir` is a
  `SpecIR` (one spec); `config` is the per-rule options block from
  `.speclint.yml`, with `severity` merged in.
- **Fixtures live next to the rule.** The harness writes them to a
  tmp dir, builds a real IR, runs your `check`, and asserts the
  findings. No mocks.

---

## 3. Register the package

`my_team_rules/__init__.py`:

```python
from speclint.rules.registry import collect_rules_from


def register() -> dict:
    from . import no_trailing_spaces  # import each rule module here

    return collect_rules_from(no_trailing_spaces)
```

`collect_rules_from` walks each module's attributes and pulls out
anything decorated with `@rule`. Add new rules by importing them
inside `register()` and passing them in.

> **Why not auto-discover?** Explicit registration keeps imports
> lazy (the package can sit dormant until activated in
> `.speclint.yml`) and makes the override audit log accurate when
> rule ids collide between packages.

---

## 4. Activate in `.speclint.yml`

```yaml
packages:
  - default          # built-ins
  - my-team          # entry-point name from your pyproject.toml

rules:
  no-trailing-whitespace: warn   # bump from default `info`
```

Order matters: later packages **override** earlier ones by `rule.id`.
If your plugin defines `no-tbd`, it shadows the built-in. speclint
logs every override so the swap is never invisible.

`packages:` decides *which rules exist*. `rules:` (severity / options)
just tunes them. The two are orthogonal.

---

## 5. Verify it loaded

```bash
speclint check .
```

If the entry point is wired correctly, your rule's findings show up
in the report. If it isn't:

```
rule package `my-team` not found via entry-points group `speclint.rules`.
Install a package that exposes it, or remove it from `packages:` in
.speclint.yml.
```

Usually means you forgot `pip install -e .`, or the entry-point name
in `pyproject.toml` doesn't match the name in `packages:`.

---

## 6. Test fixtures with pytest

Fixtures double as unit tests. In your plugin's test suite:

```python
# tests/test_rules.py
import pytest

from speclint.fixtures import assert_matches, run_fixture
from my_team_rules import register


RULES = register()


@pytest.mark.parametrize(
    "rule_id,fixture",
    [(r.id, f) for r in RULES.values() for f in r.fixtures],
    ids=lambda x: x.name if hasattr(x, "name") else str(x),
)
def test_fixtures(rule_id, fixture, tmp_path):
    findings = run_fixture(RULES[rule_id], fixture, tmp_path)
    assert_matches(rule_id, fixture, findings)
```

That's the same harness speclint uses for its own rules. One
parametrized test covers every fixture across every rule in your
package.

---

## What `ir` gives you

`SpecIR` (see `src/speclint/ir/types.py`) is the parsed view of one
spec. The fields you'll reach for most:

| Field | What it is |
|---|---|
| `ir.raw_text` | `dict[file → text]`. Use for regex/textual rules. |
| `ir.headings` | List of `Heading(file, line, level, text, anchor)`. |
| `ir.links` | List of `Link(file, line, text, target, is_internal)`. |
| `ir.claims` | MUST/SHALL/SHOULD/MAY statements and imperative bullets. |
| `ir.terms` | Repeated noun-phrases (for glossary-style rules). |
| `ir.metadata` | YAML sidecar parsed to a dict. Empty unless the user opted in. |
| `ir.repo_root` | Repo root `Path`. Used for resolving repo-relative globs. |
| `ir.changed_paths` | Diff context for Path B rules. `None` if no diff. |
| `ir.embedder` | Semantic-tier rules use this. `None` if Tier 2 unavailable. |

Most static rules only need `raw_text`. Reach for the parsed
collections when you'd otherwise re-implement the markdown parse.

---

## Picking a tier

- **`static`** — pure function of the spec text and metadata. No
  network, no model. Runs unconditionally. 90% of rules.
- **`semantic`** — needs embeddings (`fastembed`). speclint drops
  the rule with a one-line note if the dep isn't installed; your
  `check` must short-circuit when `ir.embedder is None`.
- **`llm`** — calls a model. The runner resolves a transport
  (Anthropic API, OpenAI API, `claude` CLI, `codex` CLI) and binds a
  caller into your rule's config as `config["_llm_call"]`. Call it
  with a prompt string, get text back; cost and caching are handled
  for you. **Always short-circuit** when `_llm_call is None` — the
  runner normally drops your rule in that case, but the defensive
  check keeps fixture tests trivial. The built-in
  `no-weasel-words-llm` is a clean reference implementation.

Pick `static` unless you genuinely need fuzzy matching or generative
judgment.

---

## Reading metadata (the opt-in sidecar)

If your rule needs structured data — owners, status, references,
acceptance criteria — read from `ir.metadata` and **silently no-op
when the field is missing**:

```python
from speclint.rules.builtin._metadata import extract_strings

def check(ir, config):
    field = config.get("field", "references")
    refs = extract_strings(ir.metadata, field)   # flat list of strings
    if not refs:
        return []                                # no metadata, no findings
    # … do work
```

`extract_strings` accepts a dotted path with `[*]` for list expansion,
so a single config can target either a flat shape (`references`) or a
nested one (`required_facts[*].artifact.path`). Out-of-shape metadata
returns `[]`, never raises.

Why silent? Because metadata is opt-in. A user who hasn't set
`metadata.sidecar:` in `.speclint.yml` shouldn't get warnings about
a field they never claimed to provide. Make the field name a
`config.get(...)` option so teams can name it whatever fits their
schema.

The built-in `refs-resolve` is a clean reference implementation.

---

## Path B (diff-aware) rules

For coupling rules — "the diff touched code under `references` but
the spec wasn't updated" — read `ir.changed_paths`. It's a tuple of
repo-relative paths, or `None` when no diff context was provided.

If it's `None`, **return `[]`**. Coupling rules are meaningless
without a diff, and firing them on every push would be noise.

```python
def check(ir, config):
    if ir.changed_paths is None:
        return []
    # … coupling logic
```

CI activates this automatically via `--base origin/main`; locally
the user runs `speclint check . --base main`.

---

## Overriding a built-in

Use the same `id` as the built-in. speclint applies packages in the
order they appear in `.speclint.yml`; later wins:

```python
@rule(
    id="no-tbd",                # same id as the built-in
    version="2.0.0",
    tier="static",
    default_severity="error",   # we're stricter
    rationale="Internal policy: no TBDs survive merge to main.",
)
def check(ir, config):
    ...
```

The override is announced in the run output:

```
override log:
  no-tbd: default:1.0.0 -> my-team:2.0.0
```

---

## How this compares to ESLint

The shape is deliberately ESLint-like, with some honest differences.

| Concern | ESLint | speclint |
|---|---|---|
| **Where rules live** | npm package (`eslint-plugin-foo`) | Python package, registered via entry points |
| **How config references them** | `plugins: ["foo"]` + `rules: { "foo/my-rule": "warn" }` | `packages: ["foo"]`, rule ids are flat (no `foo/` prefix) |
| **Rule identity** | Namespaced (`plugin/rule`) | Flat ids; last package wins on collision (with an audit log) |
| **Per-rule config** | `[severity, options]` tuple | `severity` string *or* mapping with `severity` + options |
| **Override built-ins** | Can't directly — you'd disable + add a new id | Yes: same `id` from a later package replaces it |
| **Rule API** | `create(context) → { Node: visitor }` (AST visitor) | `check(ir, config) → list[Finding]` (whole-spec function) |
| **Severity model** | `off | warn | error` | `off | info | warn | error` |
| **Test harness** | `RuleTester` with `valid` / `invalid` cases | `Fixture` declared next to the rule; `run_fixture` runs the same code path as prod |
| **Fixable findings** | `--fix` writes back transformed AST | Not yet — findings are read-only in v0.1 |
| **Discovery** | Walks `.js`/`.ts` files | Walks markdown under conventional roots (no required manifest) |

**Where it diverges, on purpose:**

- **No `plugin/rule` namespacing.** Rule ids are flat. Override is
  the explicit mechanism — if you publish `no-tbd` from your plugin
  and want to coexist with the built-in, give yours a different id
  (`my-team-no-tbd`). Conflicts produce an audit log entry, not a
  silent shadow.
- **Whole-spec check function, not AST visitors.** A spec is small
  enough that you don't need a visitor protocol. Just iterate
  `ir.raw_text`, `ir.headings`, `ir.claims` directly.
- **Fixtures are first-class.** Every rule ships its own test cases
  next to the rule definition, and the harness uses the production
  code path. ESLint's `RuleTester` is bolted on; here it's the
  contract.
- **No `--fix`.** Specs are prose; autofix has a much higher false-
  positive cost than for code. Out of scope for v0.1.

If you've written ESLint plugins before, the mental model
transfers cleanly. The rule body is simpler (no visitor protocol),
the registration is one entry point, and severity tuning works the
same way.

---

## Checklist

- [ ] `pyproject.toml` declares the entry point under `speclint.rules`
- [ ] `register()` returns the dict from `collect_rules_from(...)`
- [ ] Each rule has a stable `id`, a `version`, a `tier`, a
      `default_severity`, and a `rationale` users will read
- [ ] At least two fixtures per rule: one that fires, one that doesn't
- [ ] Metadata-reading rules silently no-op when the field is absent
- [ ] Path B rules return `[]` when `ir.changed_paths is None`
- [ ] Severity comes from `config.get("severity", default)` so the
      `.speclint.yml` override works
- [ ] `.speclint.yml` lists your entry-point name in `packages:`
