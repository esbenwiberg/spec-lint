from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Literal

from ..rules.registry import SpecLintError

Transport = Literal["api", "openai", "cli", "codex", "auto"]

# Mirrors repofit's defaults (packages/engine/src/evidence/subsystems/judge.ts:17-19).
DEFAULT_MODELS: dict[str, str] = {
    "api": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "cli": "claude-haiku-4-5",
    "codex": "codex-cli-default",
}

# Fixed auto priority — same order repofit uses (judge.ts:129-137).
_AUTO_PRIORITY: tuple[Transport, ...] = ("api", "openai", "cli", "codex")


@dataclass(frozen=True)
class TransportProbe:
    name: Transport
    available: bool
    reason: str


def probe_transports(*, claude_bin: str = "claude", codex_bin: str = "codex") -> list[TransportProbe]:
    """Probe every concrete transport without selecting one. Used by
    `speclint llm doctor` and verbose mode to surface why a particular
    transport was (or wasn't) picked.
    """
    out: list[TransportProbe] = []

    if os.getenv("ANTHROPIC_API_KEY"):
        out.append(TransportProbe("api", True, "ANTHROPIC_API_KEY set"))
    else:
        out.append(TransportProbe("api", False, "no ANTHROPIC_API_KEY"))

    if os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"):
        bits = []
        if os.getenv("OPENAI_API_KEY"):
            bits.append("OPENAI_API_KEY")
        if os.getenv("OPENAI_BASE_URL"):
            bits.append("OPENAI_BASE_URL")
        out.append(TransportProbe("openai", True, " + ".join(bits) + " set"))
    else:
        out.append(TransportProbe("openai", False, "no OPENAI_API_KEY, no OPENAI_BASE_URL"))

    claude_path = shutil.which(claude_bin)
    if claude_path:
        out.append(TransportProbe("cli", True, f"{claude_bin} on PATH at {claude_path}"))
    else:
        out.append(TransportProbe("cli", False, f"{claude_bin} not on PATH"))

    codex_path = shutil.which(codex_bin)
    if codex_path:
        out.append(TransportProbe("codex", True, f"{codex_bin} on PATH at {codex_path}"))
    else:
        out.append(TransportProbe("codex", False, f"{codex_bin} not on PATH"))

    return out


class TransportNotAvailable(SpecLintError):
    """Raised when an explicitly-requested transport is unavailable."""


def select_transport(
    pref: Transport,
    *,
    claude_bin: str = "claude",
    codex_bin: str = "codex",
) -> Transport | None:
    """Resolve a transport preference into a concrete transport.

    - `auto` returns the first available, or `None` if nothing is available
      (graceful skip). This is spec-lint's deliberate divergence from repofit,
      which throws.
    - Explicit transports (`api`/`openai`/`cli`/`codex`) raise
      TransportNotAvailable if the required env/binary is missing — if you
      typed it, you meant it.
    """
    if pref == "auto":
        probes = probe_transports(claude_bin=claude_bin, codex_bin=codex_bin)
        by_name = {p.name: p for p in probes}
        for name in _AUTO_PRIORITY:
            if by_name[name].available:
                return name
        return None  # graceful skip

    if pref == "api":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise TransportNotAvailable(
                "transport `api` requested but ANTHROPIC_API_KEY is not set"
            )
        return "api"

    if pref == "openai":
        if not (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL")):
            raise TransportNotAvailable(
                "transport `openai` requested but neither OPENAI_API_KEY "
                "nor OPENAI_BASE_URL is set"
            )
        return "openai"

    if pref == "cli":
        if not shutil.which(claude_bin):
            raise TransportNotAvailable(
                f"transport `cli` requested but `{claude_bin}` is not on PATH"
            )
        return "cli"

    if pref == "codex":
        if not shutil.which(codex_bin):
            raise TransportNotAvailable(
                f"transport `codex` requested but `{codex_bin}` is not on PATH"
            )
        return "codex"

    raise SpecLintError(f"unknown transport: {pref}")
