"""Thin LLM call helper for Tier 3 rules.

This is the bridge between ``select_transport()`` (which decides *which*
backend to use) and the rules (which need to actually send a prompt).
Each transport has a separate, deliberately-small implementation —
adding a backend means writing one ~20-line function, not extending an
abstraction. The shared shape is ``call(prompt, *, model) -> str``.

Caching: when ``cache_dir`` is set, responses are content-addressed by
(transport, model, prompt) hash and persisted as JSON. This keeps CI
deterministic across re-runs and reduces cost on iterative local dev.

cli/codex transports are not yet implemented — they raise
``NotImplementedError`` with a clear message. The runner's graceful-skip
path drops LLM rules entirely if no transport resolves, so this matters
only when a user explicitly picks ``transport: cli``/``codex``."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from .transport import DEFAULT_MODELS, Transport


LLMCall = Callable[[str], str]
"""A bound caller: ``llm(prompt) -> response``. Rules receive this so they
don't have to know about transports or models."""


def make_caller(
    transport: Transport,
    *,
    model: str | None = None,
    cache_dir: Path | str | None = None,
) -> LLMCall:
    """Bind a transport+model into a single-arg callable.

    The returned callable is what Tier 3 rules see (passed via
    ``opts['_llm_call']``). Rules don't need to know which transport
    answered — that's a runner-level decision."""
    resolved_model = model or DEFAULT_MODELS.get(transport)
    if resolved_model is None:
        raise ValueError(f"no default model for transport {transport!r}")

    cache = _Cache(cache_dir) if cache_dir else None

    def _call(prompt: str) -> str:
        if cache is not None:
            cached = cache.get(transport, resolved_model, prompt)
            if cached is not None:
                return cached
        out = _dispatch(transport, prompt, model=resolved_model)
        if cache is not None:
            cache.put(transport, resolved_model, prompt, out)
        return out

    return _call


def _dispatch(transport: Transport, prompt: str, *, model: str) -> str:
    if transport == "api":
        return _call_anthropic(prompt, model=model)
    if transport == "openai":
        return _call_openai(prompt, model=model)
    if transport == "cli":
        return _call_claude_cli(prompt, model=model)
    if transport == "codex":
        raise NotImplementedError(
            "transport `codex` is wired for selection but call dispatch is "
            "not yet implemented. Use transport `api` or `openai`."
        )
    raise ValueError(f"unknown transport: {transport!r}")


def _call_anthropic(prompt: str, *, model: str) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "transport `api` requires `pip install 'speclint[anthropic]'`"
        ) from e

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    # Concatenate text blocks; the JSON-output rules parse the string.
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return "".join(parts)


def _call_claude_cli(prompt: str, *, model: str, claude_bin: str = "claude") -> str:
    """Shell out to the `claude` CLI. Stdin gets the prompt; stdout is text.

    Mirrors repofit's `spawnClaude` invocation pattern (judge.ts:401), minus
    the JSON-schema arg — Tier 3 rules parse text responses themselves so
    failures degrade to "no promotion" instead of "rule crashed."""
    try:
        result = subprocess.run(
            [claude_bin, "-p", "--output-format", "text", "--model", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"`{claude_bin}` not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"`{claude_bin}` timed out after 120s") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip() or "(no stderr)"
        raise RuntimeError(f"`{claude_bin}` exited {e.returncode}: {stderr}") from e
    return result.stdout


def _call_openai(prompt: str, *, model: str) -> str:
    try:
        import openai
    except ImportError as e:
        raise RuntimeError(
            "transport `openai` requires `pip install 'speclint[openai]'`"
        ) from e

    client = openai.OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


class _Cache:
    """Content-addressed JSON cache. One file per (transport, model, prompt)."""

    def __init__(self, cache_dir: Path | str) -> None:
        self.dir = Path(cache_dir)

    def _path(self, transport: str, model: str, prompt: str) -> Path:
        h = hashlib.sha256(f"{transport}|{model}|{prompt}".encode("utf-8")).hexdigest()
        return self.dir / f"{h}.json"

    def get(self, transport: str, model: str, prompt: str) -> str | None:
        p = self._path(transport, model, prompt)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))["response"]
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    def put(self, transport: str, model: str, prompt: str, response: str) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(transport, model, prompt).write_text(
                json.dumps({"response": response}), encoding="utf-8"
            )
        except OSError:
            pass
