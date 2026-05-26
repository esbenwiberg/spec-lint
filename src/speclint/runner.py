from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .ir import build_spec_ir, discover_spec_folders
from .ir.types import SpecIR
from .llm import TransportNotAvailable, select_transport
from .rules import Finding
from .rules.registry import RuleRegistry, load_rule_packages
from .rules.types import SEVERITY_RANK
from .semantic import Embedder, FastembedEmbedder, fastembed_available

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    findings: list[Finding] = field(default_factory=list)
    specs_checked: list[str] = field(default_factory=list)
    rules_evaluated: int = 0
    rules_skipped_llm: list[str] = field(default_factory=list)
    rules_skipped_semantic: list[str] = field(default_factory=list)
    transport_chosen: str | None = None
    embedder_chosen: str | None = None
    package_order: list[str] = field(default_factory=list)
    override_log: list[dict] = field(default_factory=list)

    @property
    def max_severity_rank(self) -> int:
        if not self.findings:
            return -1
        return max(SEVERITY_RANK[f.severity] for f in self.findings if f.severity != "off")


def run(repo_root: Path, config: Config,
        changed_paths: tuple[str, ...] | None = None) -> RunResult:
    """Run speclint against the configured spec folders. Pure function over
    config + filesystem; no side effects beyond reading files.

    `changed_paths`, if provided, is the set of repo-root-relative paths
    treated as modified — populates SpecIR.changed_paths for Path B rules.
    None means no diff context (coupling rules short-circuit silently).
    """

    registry = load_rule_packages(config.packages)
    result = RunResult(
        package_order=registry.package_order,
        override_log=[
            {"rule_id": o.rule_id, "from": o.from_package, "to": o.to_package}
            for o in registry.overrides
        ],
    )

    # LLM transport resolution — only relevant if any loaded rule has tier=llm.
    llm_rules = [r for r in registry.all() if r.tier == "llm"]
    if llm_rules and config.llm.enabled:
        try:
            chosen = select_transport(config.llm.transport)
        except TransportNotAvailable as e:
            # Explicit pref unavailable → loud failure, but only if it would
            # actually matter (LLM rules exist). Treat as info-finding rather
            # than crashing the whole run.
            log.warning("LLM transport explicit pref failed: %s", e)
            chosen = None
        result.transport_chosen = chosen
        if chosen is None:
            result.rules_skipped_llm = [r.id for r in llm_rules]
            registry = _drop_llm_rules(registry)
    elif llm_rules and not config.llm.enabled:
        result.rules_skipped_llm = [r.id for r in llm_rules]
        registry = _drop_llm_rules(registry)

    # Semantic-tier resolution — analogous graceful-skip pattern. If any
    # semantic rule is loaded and fastembed is unavailable, drop them with
    # a skip log entry so the run doesn't crash.
    embedder = _resolve_embedder(registry, result)

    folders = discover_spec_folders(repo_root, config.specs)
    for folder in folders:
        ir = build_spec_ir(
            folder,
            include=config.include,
            ignore=config.ignore,
            repo_root=repo_root,
            changed_paths=changed_paths,
            embedder=embedder,
        )
        result.specs_checked.append(folder.name)
        result.findings.extend(_run_rules_on_ir(ir, registry, config))

    result.rules_evaluated = len(registry.rules)
    return result


def _run_rules_on_ir(ir: SpecIR, registry: RuleRegistry, config: Config) -> list[Finding]:
    findings: list[Finding] = []
    for r in registry.all():
        opts = config.rule_options(r.id)
        if opts.get("severity") == "off":
            continue
        try:
            for f in r.check(ir, opts):
                # Stamp spec name + apply config severity override
                applied_severity = opts.get("severity", f.severity)
                findings.append(
                    Finding(
                        rule_id=f.rule_id,
                        severity=applied_severity,
                        file=f.file,
                        line=f.line,
                        message=f.message,
                        hint=f.hint,
                        spec=ir.name,
                    )
                )
        except Exception as e:  # noqa: BLE001
            log.exception("rule %s raised", r.id)
            findings.append(
                Finding(
                    rule_id=r.id,
                    severity="error",
                    file=str(ir.folder),
                    line=None,
                    message=f"rule crashed: {e}",
                    hint="please file a bug",
                    spec=ir.name,
                )
            )
    return findings


def _drop_llm_rules(registry: RuleRegistry) -> RuleRegistry:
    return RuleRegistry(
        rules={rid: r for rid, r in registry.rules.items() if r.tier != "llm"},
        overrides=registry.overrides,
        package_order=registry.package_order,
    )


def _resolve_embedder(registry: RuleRegistry, result: RunResult) -> Embedder | None:
    """Pick an embedder for semantic-tier rules. Mutates `registry` (in
    place via `result.rules_skipped_semantic`) and `result` to reflect any
    skipped rules. Returns None when no semantic rules are loaded OR no
    embedder backend is available."""
    semantic_rules = [r for r in registry.all() if r.tier == "semantic"]
    if not semantic_rules:
        return None

    if not fastembed_available():
        result.rules_skipped_semantic = [r.id for r in semantic_rules]
        # Drop them so they don't run with a None embedder
        for rid in result.rules_skipped_semantic:
            del registry.rules[rid]
        return None

    embedder = FastembedEmbedder()
    result.embedder_chosen = embedder.name
    return embedder


def exit_code_for(result: RunResult, fail_on: str) -> int:
    if fail_on == "never":
        return 0
    threshold = {"error": SEVERITY_RANK["error"], "warn": SEVERITY_RANK["warn"]}[fail_on]
    return 1 if result.max_severity_rank >= threshold else 0
