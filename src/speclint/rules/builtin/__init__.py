"""Default rule package shipped with speclint.

Exposed via the `speclint.rules` = `default` entry point. `register()` is
re-entrant: import-cache safe, no module-level mutation.
"""

from ..registry import collect_rules_from


def register() -> dict:
    from . import (
        claim_redundancy,
        heading_redundancy,
        no_tbd,
        no_weasel_words,
        refs_coupling,
        refs_resolve,
    )

    return collect_rules_from(
        claim_redundancy,
        heading_redundancy,
        no_tbd,
        no_weasel_words,
        refs_coupling,
        refs_resolve,
    )
