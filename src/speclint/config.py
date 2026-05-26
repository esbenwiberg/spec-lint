from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .discovery import DEFAULT_ROOTS
from .rules.registry import SpecLintError


@dataclass
class LLMConfig:
    enabled: bool = True
    transport: str = "auto"
    model: str | None = None
    cache: str = ".speclint-cache/llm/"


@dataclass
class MetadataConfig:
    """Optional sidecar-metadata loader. Off unless declared in .speclint.yml.

    When set, each discovered spec is checked for a sidecar file named
    ``sidecar`` in its folder (e.g. ``contract.yaml``). If present, the
    file's YAML is parsed and exposed on ``SpecIR.metadata``. Rules that
    need metadata read from this dict — they silently skip when keys are
    absent, so the whole layer remains opt-in."""

    sidecar: str | None = None     # e.g. "contract.yaml", "spec.yml", "meta.yml"


@dataclass
class RuleConfig:
    severity: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    packages: list[str] = field(default_factory=lambda: ["default"])
    roots: list[str] = field(default_factory=lambda: list(DEFAULT_ROOTS))
    extra_roots: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    rules: dict[str, RuleConfig] = field(default_factory=dict)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    fail_on: str = "error"  # error | warn | never

    def rule_options(self, rule_id: str) -> dict[str, Any]:
        rc = self.rules.get(rule_id)
        if not rc:
            return {}
        opts = dict(rc.options)
        if rc.severity is not None:
            opts["severity"] = rc.severity
        return opts


def load_config(repo_root: Path) -> Config:
    """Load `.speclint.yml` from repo root if present, else return defaults."""
    path = repo_root / ".speclint.yml"
    if not path.exists():
        return Config()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise SpecLintError(f".speclint.yml: invalid YAML: {e}")

    cfg = Config()
    if not isinstance(raw, dict):
        raise SpecLintError(".speclint.yml: top level must be a mapping")

    if "packages" in raw:
        cfg.packages = _require_list_of_str(raw, "packages")
    if "roots" in raw:
        cfg.roots = _require_list_of_str(raw, "roots")
    if "extra_roots" in raw:
        cfg.extra_roots = _require_list_of_str(raw, "extra_roots")
    if "ignore" in raw:
        cfg.ignore = _require_list_of_str(raw, "ignore")
    if "fail_on" in raw:
        val = raw["fail_on"]
        if val not in {"error", "warn", "never"}:
            raise SpecLintError(f".speclint.yml: fail_on must be one of error|warn|never, got {val!r}")
        cfg.fail_on = val

    md_raw = raw.get("metadata", {})
    if md_raw:
        if not isinstance(md_raw, dict):
            raise SpecLintError(".speclint.yml: `metadata` must be a mapping")
        sidecar = md_raw.get("sidecar")
        if sidecar is not None and not isinstance(sidecar, str):
            raise SpecLintError(".speclint.yml: `metadata.sidecar` must be a string")
        cfg.metadata = MetadataConfig(sidecar=sidecar)

    rules_raw = raw.get("rules", {})
    if rules_raw:
        if not isinstance(rules_raw, dict):
            raise SpecLintError(".speclint.yml: `rules` must be a mapping")
        for rid, val in rules_raw.items():
            if isinstance(val, str):
                cfg.rules[rid] = RuleConfig(severity=val)
            elif isinstance(val, dict):
                sev = val.get("severity")
                opts = {k: v for k, v in val.items() if k != "severity"}
                cfg.rules[rid] = RuleConfig(severity=sev, options=opts)
            else:
                raise SpecLintError(
                    f".speclint.yml: rules.{rid} must be a string or mapping"
                )

    llm_raw = raw.get("llm", {})
    if llm_raw:
        if not isinstance(llm_raw, dict):
            raise SpecLintError(".speclint.yml: `llm` must be a mapping")
        cfg.llm = LLMConfig(
            enabled=bool(llm_raw.get("enabled", True)),
            transport=str(llm_raw.get("transport", "auto")),
            model=llm_raw.get("model"),
            cache=str(llm_raw.get("cache", ".speclint-cache/llm/")),
        )

    return cfg


def _require_list_of_str(raw: dict, key: str) -> list[str]:
    val = raw[key]
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise SpecLintError(f".speclint.yml: `{key}` must be a list of strings")
    return val
