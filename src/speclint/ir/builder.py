from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import yaml
from markdown_it import MarkdownIt

from .types import Claim, Heading, Link, Manifest, SpecIR, Term

_MODAL_RE = re.compile(r"\b(MUST(?: NOT)?|SHALL(?: NOT)?|SHOULD(?: NOT)?|MAY)\b")
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


def discover_spec_folders(root: Path, globs: list[str]) -> list[Path]:
    """Return spec folder paths matching any of the given globs.

    A 'spec folder' is a directory that matches a glob ending in '/'.
    """
    folders: set[Path] = set()
    for pattern in globs:
        pattern = pattern.rstrip("/")
        for p in root.glob(pattern):
            if p.is_dir():
                folders.add(p.resolve())
    return sorted(folders)


def build_spec_ir(folder: Path, *, include: list[str] | None = None,
                  ignore: list[str] | None = None,
                  repo_root: Path | None = None,
                  changed_paths: tuple[str, ...] | None = None,
                  embedder=None) -> SpecIR:
    """Build a SpecIR for a single spec folder.

    Reads spec.yml (optional), all .md files matching include patterns, and
    extracts headings, links, claims, and terms.

    `repo_root` and `changed_paths` carry Path B coupling context. Pass them
    when running coupling rules (e.g., from `git diff --name-only`). Path A
    rules ignore them.
    """
    include = include or ["**/*.md"]
    ignore = ignore or []

    manifest, manifest_errors = _load_manifest(folder)
    ir = SpecIR(
        folder=folder,
        manifest=manifest,
        manifest_errors=manifest_errors,
        repo_root=repo_root,
        changed_paths=changed_paths,
        embedder=embedder,
    )

    md_files = _collect_md_files(folder, include, ignore)
    ir.files = [str(p.relative_to(folder)) for p in md_files]

    md = MarkdownIt("commonmark")
    term_index: dict[str, list[tuple[str, int]]] = {}

    for path in md_files:
        rel = str(path.relative_to(folder))
        text = path.read_text(encoding="utf-8", errors="replace")
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

    # Re-evaluate verification hooks now that we have all claims + headings
    ir.claims = [_attach_hook(c, ir) for c in ir.claims]
    return ir


def _load_manifest(folder: Path) -> tuple[Manifest | None, list[str]]:
    path = folder / "spec.yml"
    if not path.exists():
        return None, []
    errors: list[str] = []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return None, [f"spec.yml: invalid YAML: {e}"]

    if not isinstance(raw, dict):
        return None, ["spec.yml: top level must be a mapping"]

    spec_id = raw.get("id")
    status = raw.get("status")
    if not isinstance(spec_id, str) or not spec_id:
        errors.append("spec.yml: missing required string field `id`")
    if not isinstance(status, str) or not status:
        errors.append("spec.yml: missing required string field `status`")

    valid_status = {"draft", "accepted", "implemented", "deprecated"}
    if isinstance(status, str) and status and status not in valid_status:
        errors.append(
            f"spec.yml: status `{status}` not in {sorted(valid_status)}"
        )

    refs = raw.get("references", [])
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        errors.append("spec.yml: `references` must be a list of strings")
        refs = []

    related = raw.get("related", [])
    if not isinstance(related, list) or not all(isinstance(r, str) for r in related):
        errors.append("spec.yml: `related` must be a list of strings")
        related = []

    owner = raw.get("owner")
    if owner is not None and not isinstance(owner, str):
        errors.append("spec.yml: `owner` must be a string")
        owner = None

    if errors and (not isinstance(spec_id, str) or not isinstance(status, str)):
        return None, errors

    return (
        Manifest(
            id=spec_id if isinstance(spec_id, str) else "",
            status=status if isinstance(status, str) else "",
            owner=owner,
            references=tuple(refs),
            related=tuple(related),
            raw=raw,
        ),
        errors,
    )


def _collect_md_files(folder: Path, include: list[str], ignore: list[str]) -> list[Path]:
    matches: set[Path] = set()
    for pattern in include:
        for p in folder.glob(pattern):
            if p.is_file():
                matches.add(p.resolve())
    for pattern in ignore:
        for p in folder.glob(pattern):
            matches.discard(p.resolve())
    return sorted(matches)


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
                # Capture link text
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
        if m or is_bullet_imperative:
            yield Claim(
                file=rel,
                line=i,
                text=stripped,
                modal=m.group(1) if m else None,
                has_verification_hook=False,  # filled in later
            )


def _extract_terms(text: str, rel: str) -> Iterable[tuple[str, int]]:
    for i, line in enumerate(text.splitlines(), start=1):
        for m in _TERM_RE.finditer(line):
            yield m.group(1).lower(), i


def _attach_hook(claim: Claim, ir: SpecIR) -> Claim:
    # Heuristic: a claim has a verification hook if the same file contains any
    # hook token within ±3 lines, OR if a heading like "Acceptance" exists in
    # any file.
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
