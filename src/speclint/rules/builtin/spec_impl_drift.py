"""Tier 3 rule: flag where the implementation contradicts a claim in
the spec.

The spec text and the files it nominates as acceptance evidence (via
``required_facts[*].artifact.path`` or a configurable analogue) are
fed to a small model in one batched prompt. The model returns a JSON
list of high-confidence mismatches; each becomes one ``info`` finding
anchored on the spec's sidecar.

This rule complements ``refs-resolve`` (which checks artifacts exist)
and ``claims-have-hooks`` (which checks claims have coverage entries)
by asking the question those rules can't: *does the code actually do
what the spec says it does?* No static check can answer that.

Cost discipline is baked in:
- Skip when there's no sidecar, no artifacts field, or no readable
  artifact files. No transport, no LLM call.
- Cap the number of artifacts read per spec (``max_artifacts``,
  default 3) and the lines per artifact (``max_lines``, default 200).
- One LLM call per spec, batched. Cached by the transport layer.

Findings default to ``info`` because LLMs hallucinate. Users who've
built trust can promote to ``warn``."""
from __future__ import annotations

import json
import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture
from ...rules.types import Patch
from ._evidence import find_line
from ._metadata import extract_strings


_PROMPT_HEADER = (
    "You are a careful code reviewer comparing a spec to its "
    "implementation. The spec describes intended behavior; the files "
    "are the acceptance evidence the spec nominates. List ONLY "
    "high-confidence contradictions where the code clearly does "
    "something the spec says it should not (or fails to do something "
    "the spec requires).\n\n"
    "Return ONLY a JSON object with one key, `mismatches`, whose value "
    "is an array of objects with keys:\n"
    "  - `claim`: short excerpt of the spec claim, <=120 chars\n"
    "  - `artifact`: the path you found the contradiction in\n"
    "  - `evidence`: a VERBATIM substring of one line from the "
    "artifact that demonstrates the mismatch — copy it exactly, "
    "<=120 chars\n"
    "  - `mismatch`: one sentence describing the divergence\n"
    "  - `fix_old` (OPTIONAL): include ONLY when the drift is a "
    "mechanical rename or typo that can be closed by a literal "
    "find/replace. The exact string in the artifact that should be "
    "replaced. Omit entirely if the fix needs human judgment.\n"
    "  - `fix_new` (OPTIONAL, paired with `fix_old`): the replacement "
    "string.\n"
    "  - `fix_all` (OPTIONAL boolean, default false): true ONLY for "
    "renames where every occurrence in the file should be replaced.\n"
    "If nothing contradicts the spec, return {\"mismatches\": []}. "
    "No prose, no markdown, just JSON.\n\n"
    "Do not flag absent features the spec doesn't mention. Do not "
    "flag minor stylistic differences. Do not flag tests that pass — "
    "passing tests are evidence the spec holds. Only flag concrete, "
    "named contradictions you can point at. Only suggest `fix_old`/"
    "`fix_new` when the fix is OBVIOUSLY safe — when in doubt, omit.\n\n"
)


_FIXTURES = [
    Fixture(
        name="real-mismatch-anchors-to-artifact-line",
        files={"brief.md": "# Login\n\nThe API MUST reject empty passwords.\n"},
        metadata={
            "required_facts": [
                {"artifact": {"path": "src/auth.py"}},
            ],
        },
        repo_files={
            "src/auth.py": "def login(user, password):\n    return True  # accepts empty password\n",
        },
        options={
            "_llm_call": lambda prompt: json.dumps({
                "mismatches": [{
                    "claim": "API MUST reject empty passwords",
                    "artifact": "src/auth.py",
                    "evidence": "return True",
                    "mismatch": "login() returns True without validating password is non-empty",
                }],
            }),
        },
        expects=(
            ExpectedFinding(
                file="src/auth.py",
                line=2,
                message_contains="src/auth.py",
            ),
        ),
    ),
    Fixture(
        name="missing-evidence-falls-back-to-sidecar",
        files={"brief.md": "# x\n\nMUST do thing.\n"},
        metadata={"required_facts": [{"artifact": {"path": "src/x.py"}}]},
        repo_files={"src/x.py": "def x(): pass\n"},
        options={
            "_llm_call": lambda prompt: json.dumps({
                "mismatches": [{
                    "claim": "MUST do thing",
                    "artifact": "src/x.py",
                    "mismatch": "doesn't do thing",
                }],
            }),
        },
        expects=(
            ExpectedFinding(
                file="contract.yaml",
                line=None,
                message_contains="src/x.py",
            ),
        ),
    ),
    Fixture(
        name="unresolvable-evidence-falls-back-to-sidecar",
        files={"brief.md": "# x\n\nMUST do thing.\n"},
        metadata={"required_facts": [{"artifact": {"path": "src/x.py"}}]},
        repo_files={"src/x.py": "def x(): pass\n"},
        options={
            "_llm_call": lambda prompt: json.dumps({
                "mismatches": [{
                    "claim": "MUST do thing",
                    "artifact": "src/x.py",
                    "evidence": "totally hallucinated string nowhere in file",
                    "mismatch": "doesn't do thing",
                }],
            }),
        },
        expects=(
            ExpectedFinding(
                file="contract.yaml",
                line=None,
                message_contains="src/x.py",
            ),
        ),
    ),
    Fixture(
        name="no-mismatches-no-findings",
        files={"brief.md": "# x\n\nMUST be 200ms p95.\n"},
        metadata={"required_facts": [{"artifact": {"path": "src/x.py"}}]},
        repo_files={"src/x.py": "def x(): pass\n"},
        options={
            "_llm_call": lambda prompt: '{"mismatches": []}',
        },
        expects=(),
    ),
    Fixture(
        name="no-sidecar-no-llm-call",
        files={"brief.md": "# x\n"},
        options={
            "_llm_call": lambda prompt: (_ for _ in ()).throw(
                AssertionError("rule should not call LLM with no sidecar")
            ),
        },
        expects=(),
    ),
    Fixture(
        name="no-llm-transport-graceful-skip",
        files={"brief.md": "# x\n"},
        metadata={"required_facts": [{"artifact": {"path": "src/x.py"}}]},
        repo_files={"src/x.py": "x = 1\n"},
        expects=(),
    ),
    Fixture(
        name="missing-artifact-files-skip",
        files={"brief.md": "# x\n\nMUST do X.\n"},
        metadata={"required_facts": [{"artifact": {"path": "src/gone.py"}}]},
        options={
            "_llm_call": lambda prompt: (_ for _ in ()).throw(
                AssertionError("rule should not call LLM with no readable artifacts")
            ),
        },
        expects=(),
    ),
    Fixture(
        name="malformed-response-graceful",
        files={"brief.md": "# x\n\nMUST do X.\n"},
        metadata={"required_facts": [{"artifact": {"path": "src/x.py"}}]},
        repo_files={"src/x.py": "x = 1\n"},
        options={"_llm_call": lambda prompt: "not json"},
        expects=(),
    ),
    Fixture(
        name="multiple-mismatches-fire-separately",
        files={"brief.md": "# x\n\nMUST validate. MUST log.\n"},
        metadata={
            "required_facts": [
                {"artifact": {"path": "src/a.py"}},
                {"artifact": {"path": "src/b.py"}},
            ],
        },
        repo_files={
            "src/a.py": "def a(): pass\n",
            "src/b.py": "def b(): pass\n",
        },
        options={
            "_llm_call": lambda prompt: json.dumps({
                "mismatches": [
                    {"claim": "MUST validate", "artifact": "src/a.py",
                     "evidence": "def a(): pass", "mismatch": "no validation"},
                    {"claim": "MUST log", "artifact": "src/b.py",
                     "evidence": "def b(): pass", "mismatch": "no logging"},
                ],
            }),
        },
        expects=(
            ExpectedFinding(file="src/a.py", line=1, message_contains="src/a.py"),
            ExpectedFinding(file="src/b.py", line=1, message_contains="src/b.py"),
        ),
    ),
    Fixture(
        name="ac-style-schema-via-defaults",
        files={"brief.md": "# x\n\nThe API MUST validate inputs.\n"},
        metadata={
            "acceptance_criteria": [
                {"id": "ac-1", "test": ["tests/test_api.py"]},
            ],
        },
        repo_files={"tests/test_api.py": "def test_api(): pass\n"},
        options={
            "_llm_call": lambda prompt: (
                json.dumps({"mismatches": [{
                    "claim": "API MUST validate inputs",
                    "artifact": "tests/test_api.py",
                    "evidence": "def test_api(): pass",
                    "mismatch": "test does not assert validation",
                }]})
                if "tests/test_api.py" in prompt
                else (_ for _ in ()).throw(
                    AssertionError(f"AC-style artifact path missing from prompt:\n{prompt}")
                )
            ),
        },
        expects=(
            ExpectedFinding(file="tests/test_api.py", line=1, message_contains="tests/test_api.py"),
        ),
    ),
    Fixture(
        name="custom-artifacts-field-list",
        files={"brief.md": "# x\n\nMUST do thing.\n"},
        metadata={
            "evidence": [{"path": "src/foo.go"}],
            "examples": [{"file": "examples/bar.go"}],
        },
        repo_files={
            "src/foo.go": "package main\n",
            "examples/bar.go": "package examples\n",
        },
        options={
            "artifacts_field": ["evidence[*].path", "examples[*].file"],
            "_llm_call": lambda prompt: (
                '{"mismatches": []}'
                if ("src/foo.go" in prompt and "examples/bar.go" in prompt)
                else (_ for _ in ()).throw(
                    AssertionError(f"both custom paths should be in prompt:\n{prompt}")
                )
            ),
        },
        expects=(),
    ),
    Fixture(
        name="rename-fix-emits-replace-all-patch",
        files={"brief.md": "# rename\n\nClass `started-lock` was renamed to `progress-lock`.\n"},
        metadata={"required_facts": [{"artifact": {"path": "src/timeline.css"}}]},
        repo_files={
            "src/timeline.css": ".started-lock { color: red; }\n.started-lock-bar { x: 1; }\n",
        },
        options={
            "_llm_call": lambda prompt: json.dumps({
                "mismatches": [{
                    "claim": "renamed to progress-lock",
                    "artifact": "src/timeline.css",
                    "evidence": ".started-lock",
                    "mismatch": "still uses started-lock class name",
                    "fix_old": "started-lock",
                    "fix_new": "progress-lock",
                    "fix_all": True,
                }],
            }),
        },
        expects=(
            ExpectedFinding(
                file="src/timeline.css",
                line=1,
                has_fix=True,
            ),
        ),
    ),
    Fixture(
        name="ambiguous-fix-without-fix_old-skipped",
        files={"brief.md": "# x\n\nMUST validate input.\n"},
        metadata={"required_facts": [{"artifact": {"path": "src/y.py"}}]},
        repo_files={"src/y.py": "def y(): pass\n"},
        options={
            "_llm_call": lambda prompt: json.dumps({
                "mismatches": [{
                    "claim": "MUST validate input",
                    "artifact": "src/y.py",
                    "evidence": "def y(): pass",
                    "mismatch": "no validation",
                }],
            }),
        },
        expects=(
            ExpectedFinding(file="src/y.py", line=1, has_fix=False),
        ),
    ),
    Fixture(
        name="hallucinated-fix_old-dropped",
        files={"brief.md": "# x\n\nMUST validate.\n"},
        metadata={"required_facts": [{"artifact": {"path": "src/y.py"}}]},
        repo_files={"src/y.py": "def y(): pass\n"},
        options={
            "_llm_call": lambda prompt: json.dumps({
                "mismatches": [{
                    "claim": "MUST validate",
                    "artifact": "src/y.py",
                    "evidence": "def y(): pass",
                    "mismatch": "no validation",
                    "fix_old": "never-appeared-in-the-file",
                    "fix_new": "anything",
                }],
            }),
        },
        expects=(
            ExpectedFinding(file="src/y.py", line=1, has_fix=False),
        ),
    ),
    Fixture(
        name="respects-max-artifacts-cap",
        files={"brief.md": "# x\n\nMUST do X.\n"},
        metadata={
            "required_facts": [
                {"artifact": {"path": f"src/f{i}.py"}} for i in range(5)
            ],
        },
        repo_files={f"src/f{i}.py": "x = 1\n" for i in range(5)},
        options={
            "max_artifacts": 2,
            # Assert the prompt only mentioned the first 2 paths.
            "_llm_call": lambda prompt: (
                '{"mismatches": []}'
                if ("src/f0.py" in prompt and "src/f1.py" in prompt and "src/f2.py" not in prompt)
                else (_ for _ in ()).throw(
                    AssertionError(f"prompt should be capped to first 2 artifacts; got:\n{prompt}")
                )
            ),
        },
        expects=(),
    ),
]


@rule(
    id="spec-impl-drift",
    version="1.0.0",
    tier="llm",
    default_severity="info",
    rationale=(
        "Static rules can verify a claim has a hook and a hook points "
        "at a real file. They can't verify the code at that path "
        "actually does what the spec says. This rule asks a model to "
        "compare. Off unless metadata is configured AND a transport "
        "is available."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    llm_call = config.get("_llm_call")
    if llm_call is None:
        return []

    field = config.get(
        "artifacts_field",
        [
            "required_facts[*].artifact.path",
            "acceptance_criteria[*].test[*]",
            "test_cases[*].file",
            "tests[*].path",
        ],
    )
    field_paths = [field] if isinstance(field, str) else list(field)
    artifact_paths: list[str] = []
    seen_paths: set[str] = set()
    for fp in field_paths:
        for p in extract_strings(ir.metadata, fp):
            if p not in seen_paths:
                seen_paths.add(p)
                artifact_paths.append(p)
    if not artifact_paths:
        return []

    repo_root = ir.repo_root
    if repo_root is None:
        return []

    max_artifacts = int(config.get("max_artifacts", 3))
    max_lines = int(config.get("max_lines", 200))

    snippets: list[tuple[str, str]] = []
    for path in artifact_paths[:max_artifacts]:
        text = _read_truncated(repo_root, path, max_lines)
        if text is not None:
            snippets.append((path, text))
    if not snippets:
        return []

    spec_text = _gather_spec_text(ir)
    if not spec_text.strip():
        return []

    prompt = _build_prompt(spec_text, snippets)
    try:
        raw = llm_call(prompt)
    except Exception:
        return []

    mismatches = _parse_mismatches(raw)
    if not mismatches:
        return []

    severity = config.get("severity", "info")
    sidecar_name = _guess_sidecar(ir)
    snippet_by_path = dict(snippets)
    findings: list[Finding] = []
    for m in mismatches:
        artifact = m.get("artifact", "")
        claim = m.get("claim", "")
        body = m.get("mismatch", "")
        evidence = m.get("evidence", "")
        if not (artifact and body):
            continue

        # Anchor to artifact:line via the quoted evidence. Falls back to
        # the sidecar (line=None) only when the LLM gave us no usable
        # quote — drops the precision but keeps the finding visible.
        anchor_file = sidecar_name
        anchor_line: int | None = None
        anchor_scope: str = "spec"
        snippet = snippet_by_path.get(artifact)
        if snippet and evidence:
            line = find_line(snippet, evidence)
            if line is not None:
                anchor_file = artifact
                anchor_line = line
                anchor_scope = "repo"

        fix = _build_patch(m, artifact, snippet)

        findings.append(
            Finding(
                rule_id="spec-impl-drift",
                severity=severity,
                file=anchor_file,
                line=anchor_line,
                anchor=anchor_scope,  # type: ignore[arg-type]
                message=f"Spec/impl drift in {artifact}: {body}",
                hint=(
                    f"Claim: {claim!r}. Either update the spec to match "
                    "the implementation, or update the implementation "
                    "to honor the spec."
                ),
                fix=fix,
            )
        )
    return findings


def _build_patch(m: dict[str, Any], artifact: str, snippet: str | None) -> Patch | None:
    """Construct a Patch only when the LLM proposed a mechanically safe
    find/replace AND the `fix_old` actually appears in the snippet we
    showed it. Silently drops anything dubious — the finding stays, the
    fix doesn't."""
    fix_old = m.get("fix_old")
    fix_new = m.get("fix_new")
    if not isinstance(fix_old, str) or not isinstance(fix_new, str):
        return None
    if not fix_old or fix_old == fix_new:
        return None
    if snippet is None or fix_old not in snippet:
        return None
    return Patch(
        path=artifact,
        old=fix_old,
        new=fix_new,
        replace_all=bool(m.get("fix_all", False)),
    )


def _read_truncated(repo_root, rel_path: str, max_lines: int) -> str | None:
    try:
        target = (repo_root / rel_path).resolve()
        # Stay inside the repo — refuse paths that escape via `..`.
        target.relative_to(repo_root.resolve())
    except (OSError, ValueError):
        return None
    if not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... [truncated, {len(text.splitlines()) - max_lines} more lines]"]
    return "\n".join(lines)


def _gather_spec_text(ir: SpecIR) -> str:
    return "\n\n".join(
        f"--- {rel} ---\n{text}" for rel, text in ir.raw_text.items()
    )


def _build_prompt(spec_text: str, snippets: list[tuple[str, str]]) -> str:
    artifact_blocks = []
    for path, body in snippets:
        artifact_blocks.append(f"=== artifact: {path} ===\n{body}")
    return (
        _PROMPT_HEADER
        + "SPEC:\n"
        + spec_text
        + "\n\nARTIFACTS:\n"
        + "\n\n".join(artifact_blocks)
        + "\n"
    )


def _parse_mismatches(raw: str) -> list[dict[str, Any]]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        nl = cleaned.find("\n")
        if nl != -1:
            cleaned = cleaned[nl + 1 :]
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    items = obj.get("mismatches")
    if not isinstance(items, list):
        return []
    return [m for m in items if isinstance(m, dict)]


def _guess_sidecar(ir: SpecIR) -> str:
    try:
        for p in ir.folder.iterdir():
            if p.suffix in {".yml", ".yaml"} and p.is_file():
                return p.name
    except OSError:
        pass
    return "<metadata>"
