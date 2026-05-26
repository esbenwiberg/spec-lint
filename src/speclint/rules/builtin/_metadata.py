"""Tiny accessor for reading list-of-strings out of metadata, with
optional nested traversal.

Why this exists: ``refs-resolve`` and ``refs-coupling`` originally read
a flat ``references: [glob, glob]`` field. Real-world contract sidecars
(autopod-style ``required_facts[*].artifact.path``, intent-doc
``acceptance_criteria[*].covers[*]``, etc.) put the same information in
a nested shape. Rather than impose a schema, this accessor takes a
dotted path with optional ``[*]`` list-expansion so users can point a
rule at whatever shape they already have.

Path grammar:
- segments separated by ``.``
- ``[*]`` after a segment iterates that list
- leaf must resolve to a string (or list of strings if last segment is
  a list); anything else is silently skipped

Examples:
- ``references``               → top-level list of strings
- ``owns.code``                → ``meta["owns"]["code"]`` as list
- ``required_facts[*].artifact.path``
                               → flat list of ``artifact.path`` strings
- ``scenarios[*].then[*]``     → flatten then-clauses across scenarios
"""
from __future__ import annotations

import re
from typing import Any

_SEGMENT = re.compile(r"^([^.\[]+)(\[\*\])?$")


def extract_strings(metadata: dict[str, Any], path: str) -> list[str]:
    """Resolve ``path`` against ``metadata`` and return a flat list of
    strings. Returns ``[]`` for any miss — missing key, wrong type, empty
    list. Never raises on shape mismatches; mismatches mean "this rule
    has nothing to say about this spec.\""""
    if not path:
        return []
    cursors: list[Any] = [metadata]
    for raw in path.split("."):
        m = _SEGMENT.match(raw)
        if not m:
            return []
        key, expand = m.group(1), m.group(2)
        nxt: list[Any] = []
        for cur in cursors:
            if not isinstance(cur, dict):
                continue
            val = cur.get(key)
            if val is None:
                continue
            if expand:
                if not isinstance(val, list):
                    continue
                nxt.extend(val)
            else:
                nxt.append(val)
        cursors = nxt
        if not cursors:
            return []

    out: list[str] = []
    for v in cursors:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            out.extend(x for x in v if isinstance(x, str))
    return out
