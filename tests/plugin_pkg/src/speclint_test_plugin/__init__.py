"""Test plugin package — overrides `no-tbd` and adds a custom rule.

Exists only to exercise the rule-package override semantics in tests. Not
shipped to PyPI, not part of the production install.
"""
from speclint.rules.registry import collect_rules_from


def register() -> dict:
    from . import frontmatter_owner, override_no_tbd

    return collect_rules_from(frontmatter_owner, override_no_tbd)
