from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Callable, Iterable

from .types import Fixture, Rule, Severity, Tier

log = logging.getLogger(__name__)

_RULE_ATTR = "_speclint_rule"


def rule(
    *,
    id: str,
    version: str,
    tier: Tier,
    default_severity: Severity,
    rationale: str,
    fixtures: list[Fixture] | None = None,
) -> Callable[[Callable], Callable]:
    """Decorator. Attaches a Rule object to the decorated function as
    `fn._speclint_rule`. No global state — register() in each package
    walks its modules and collects rules explicitly.
    """

    def wrap(fn: Callable) -> Callable:
        r = Rule(
            id=id,
            version=version,
            tier=tier,
            default_severity=default_severity,
            rationale=rationale,
            check=fn,
            fixtures=list(fixtures or []),
        )
        setattr(fn, _RULE_ATTR, r)
        return fn

    return wrap


def collect_rules_from(*modules) -> dict[str, Rule]:
    """Walk module attributes, return all rule-decorated functions as
    {rule_id: Rule}. Re-callable: doesn't mutate any shared state.
    """
    out: dict[str, Rule] = {}
    for mod in modules:
        for name in dir(mod):
            obj = getattr(mod, name)
            r = getattr(obj, _RULE_ATTR, None)
            if isinstance(r, Rule):
                out[r.id] = r
    return out


@dataclass
class OverrideEvent:
    rule_id: str
    from_package: str
    to_package: str


@dataclass
class RuleRegistry:
    rules: dict[str, Rule] = field(default_factory=dict)
    overrides: list[OverrideEvent] = field(default_factory=list)
    package_order: list[str] = field(default_factory=list)

    def all(self) -> list[Rule]:
        return list(self.rules.values())


def load_rule_packages(package_names: Iterable[str]) -> RuleRegistry:
    """Load rules from entry-point-registered packages in declared order.

    Later packages override earlier ones by rule id. Returns a RuleRegistry
    with full override audit trail.
    """
    package_names = list(package_names)
    reg = RuleRegistry(package_order=package_names)

    for pkg_name in package_names:
        registered = _load_package(pkg_name)
        for rid, r in registered.items():
            # Stamp package onto a fresh copy so multi-load doesn't mutate
            # the cached Rule from a prior package.
            stamped = Rule(
                id=r.id,
                version=r.version,
                tier=r.tier,
                default_severity=r.default_severity,
                rationale=r.rationale,
                check=r.check,
                fixtures=list(r.fixtures),
                package=pkg_name,
            )
            if rid in reg.rules:
                reg.overrides.append(
                    OverrideEvent(
                        rule_id=rid,
                        from_package=reg.rules[rid].package or "?",
                        to_package=pkg_name,
                    )
                )
            reg.rules[rid] = stamped

    return reg


def _load_package(pkg_name: str) -> dict[str, Rule]:
    eps = entry_points(group="speclint.rules")
    matching = [e for e in eps if e.name == pkg_name]
    if not matching:
        raise SpecLintError(
            f"rule package `{pkg_name}` not found via entry-points group "
            f"`speclint.rules`. Install a package that exposes it, or remove "
            f"it from `packages:` in .speclint.yml."
        )
    register = matching[0].load()
    if not callable(register):
        raise SpecLintError(
            f"rule package `{pkg_name}` entry-point did not resolve to a callable"
        )
    result = register()
    if not isinstance(result, dict):
        raise SpecLintError(
            f"rule package `{pkg_name}` register() must return dict[str, Rule]"
        )
    return result


class SpecLintError(Exception):
    """User-facing speclint error. CLI catches and prints without traceback."""
