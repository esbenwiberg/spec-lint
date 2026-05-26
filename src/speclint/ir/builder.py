from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import yaml
from markdown_it import MarkdownIt

from ..discovery import SpecCandidate
from .types import Claim, Heading, Link, SpecIR, Term

_MODAL_RE = re.compile(r"\b(MUST(?: NOT)?|SHALL(?: NOT)?|SHOULD(?: NOT)?|MAY)\b")
# Gherkin / BDD step keywords. Conventionally capitalized at line start
# (possibly indented). Lowercase variants would collide with prose use
# ("Given the constraints, …"), so we anchor on the capitalized form.
_GHERKIN_RE = re.compile(r"^\s*(Given|When|Then|And|But)\s+\S")
_TERM_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b")
_HOOK_TOKENS = (
    "acceptance",
    "test",
    "verify",
    "verifies",
    "verified",
    "example",
    "given",
    "when",
    "then",
    "metric",
    "criterion",
    "criteria",
    "shall be tested",
)


def build_spec_ir(
    candidate: SpecCandidate,
    *,
    repo_root: Path | None = None,
    changed_paths: tuple[str, ...] | None = None,
    embedder=None,
    metadata_sidecar: str | None = None,
) -> SpecIR:
    """Build a SpecIR for one discovered spec.

    The candidate carries the files to read; this function turns those
    files into headings/links/claims/terms. Metadata is only loaded when
    ``metadata_sidecar`` is given (e.g. ``"contract.yaml"``) and the
    file exists in the spec folder — speclint imposes no schema, so any
    YAML mapping is accepted and its raw keys land in ``ir.metadata``.

    ``repo_root`` and ``changed_paths`` carry Path B coupling context.
    """
    ir = SpecIR(
        name=candidate.name,
        folder=candidate.folder,
        is_single_file=candidate.is_single_file,
        files=list(candidate.file_relpaths),
        repo_root=repo_root,
        changed_paths=changed_paths,
        embedder=embedder,
    )

    if metadata_sidecar:
        ir.metadata, ir.metadata_errors = _load_sidecar(
            candidate.folder / metadata_sidecar
        )

    md = MarkdownIt("commonmark")
    term_index: dict[str, list[tuple[str, int]]] = {}

    for rel, abs_path in zip(ir.files, candidate.md_files):
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ir.raw_text[rel] = text

        tokens = md.parse(text)
        ir.headings.extend(_extract_headings(tokens, rel))
        ir.links.extend(_extract_links(tokens, rel))

        for claim in _extract_claims(text, rel):
            ir.claims.append(claim)

        for term, line in _extract_terms(text, rel):
            term_index.setdefault(term, []).append((rel, line))

    ir.terms = [
        Term(text=t, occurrences=tuple(occ)) for t, occ in sorted(term_index.items())
    ]

    ir.claims = [_attach_hook(c, ir) for c in ir.claims]
    return ir


def _load_sidecar(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Read an optional metadata sidecar. Absent = empty dict, not an error
    (speclint imposes no manifest). Present but malformed = empty dict +
    a single error string, which an opt-in rule may surface."""
    if not path.exists():
        return {}, []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return {}, [f"{path.name}: invalid YAML: {e}"]
    if not isinstance(raw, dict):
        return {}, [f"{path.name}: top level must be a mapping"]
    return raw, []


def _extract_headings(tokens, rel: str) -> Iterable[Heading]:
    for i, t in enumerate(tokens):
        if t.type == "heading_open":
            level = int(t.tag[1])
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            text = inline.content if inline and inline.type == "inline" else ""
            line = (t.map[0] + 1) if t.map else 0
            yield Heading(
                file=rel,
                line=line,
                level=level,
                text=text.strip(),
                anchor=_slugify(text),
            )


def _extract_links(tokens, rel: str) -> Iterable[Link]:
    for parent in tokens:
        if parent.type != "inline" or not parent.children:
            continue
        line = (parent.map[0] + 1) if parent.map else 0
        for j, child in enumerate(parent.children):
            if child.type == "link_open":
                href = child.attrs.get("href", "") if child.attrs else ""
                text_parts: list[str] = []
                k = j + 1
                while k < len(parent.children) and parent.children[k].type != "link_close":
                    if parent.children[k].type == "text":
                        text_parts.append(parent.children[k].content)
                    k += 1
                text = "".join(text_parts)
                is_internal = (
                    not href.startswith(("http://", "https://", "mailto:"))
                    and href != ""
                )
                yield Link(
                    file=rel,
                    line=line,
                    text=text,
                    target=href,
                    is_internal=is_internal,
                )


def _extract_claims(text: str, rel: str) -> Iterable[Claim]:
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        m = _MODAL_RE.search(stripped)
        is_bullet_imperative = (
            stripped.startswith(("- ", "* ", "+ ")) and m is not None
        )
        is_gherkin = _GHERKIN_RE.match(line) is not None
        if m or is_bullet_imperative or is_gherkin:
            yield Claim(
                file=rel,
                line=i,
                text=stripped,
                modal=m.group(1) if m else None,
                has_verification_hook=False,
            )


def _extract_terms(text: str, rel: str) -> Iterable[tuple[str, int]]:
    for i, line in enumerate(text.splitlines(), start=1):
        for m in _TERM_RE.finditer(line):
            yield m.group(1).lower(), i


def _attach_hook(claim: Claim, ir: SpecIR) -> Claim:
    file_text = ir.raw_text.get(claim.file, "")
    lines = file_text.splitlines()
    start = max(0, claim.line - 4)
    end = min(len(lines), claim.line + 3)
    window = " ".join(lines[start:end]).lower()
    nearby = any(tok in window for tok in _HOOK_TOKENS)

    has_accept_heading = any(
        h.text.lower() in {"acceptance", "acceptance criteria", "tests", "verification"}
        for h in ir.headings
    )

    return Claim(
        file=claim.file,
        line=claim.line,
        text=claim.text,
        modal=claim.modal,
        has_verification_hook=nearby or has_accept_heading,
    )


def _slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")
