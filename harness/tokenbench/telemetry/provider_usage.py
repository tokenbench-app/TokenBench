"""Provider-reported usage collection (Claude Code part only).

The sweep reads the provider's own accounting from the JSON result that
``claude --print --output-format json`` writes to stdout. ``total_cost_usd`` is
the cost the provider reports for the whole run (main thread plus every
sub-agent); ``modelUsage`` is the per-model token rollup. Estimates are never
used: a missing or unparseable result yields no usage record.

This file is a trimmed excerpt of the harness module; parsers for other agent
CLIs are omitted because they were not used in this sweep.
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import ProviderUsage


def collect_provider_usage(run_dir: Path, agent_metadata: dict) -> ProviderUsage:
    """Return provider usage for a run, read from ``logs/agent.stdout.log``."""
    stdout_log = Path(run_dir) / "logs" / "agent.stdout.log"
    parsed = _parse_claude_json(stdout_log)
    if parsed is None:
        return ProviderUsage(available=False, source=None, raw={})
    return parsed


def _count_stream_requests(text: str) -> int | None:
    """Distinct assistant messages in a Claude stream-json transcript.

    One assistant message arrives as SEVERAL ``type == "assistant"`` events
    (one per streamed content part), all carrying the same ``message.id``;
    counting events would multiply the request count by the fragment count.
    Distinct ids are the requests. A transcript with no assistant events
    (e.g. a plain single-object JSON result) has no observable request count
    and returns None.
    """
    ids: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "assistant":
            msg = obj.get("message")
            mid = msg.get("id") if isinstance(msg, dict) else None
            if isinstance(mid, str) and mid:
                ids.add(mid)
    return len(ids) or None


def _per_request(logical: int | None, requests: int | None) -> float | None:
    if logical is None or not requests:
        return None
    return round(logical / requests, 4)


def _parse_claude_json(stdout_log: Path) -> ProviderUsage | None:
    """Parse a Claude Code JSON result log into ProviderUsage, or None.

    Handles both ``--output-format json`` (one JSON object) and
    ``--output-format stream-json`` (one JSON object per line, last ``result``
    event wins). Returns None when the log is missing, not JSON, or carries no
    usage/cost accounting.
    """
    if not stdout_log.exists():
        return None
    try:
        text = stdout_log.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None

    result = _extract_result_object(text)
    if result is None:
        return None

    usage = result.get("usage")
    cost = result.get("total_cost_usd")
    has_usage = isinstance(usage, dict)
    has_cost = isinstance(cost, (int, float))
    if not has_usage and not has_cost:
        return None

    usage = usage if has_usage else {}
    input_tokens = _as_int(usage.get("input_tokens"))
    output_tokens = _as_int(usage.get("output_tokens"))
    cache_read = _as_int(usage.get("cache_read_input_tokens"))
    cache_write = _as_int(usage.get("cache_creation_input_tokens"))

    token_parts = [t for t in (input_tokens, output_tokens, cache_read, cache_write) if t is not None]
    total_tokens = sum(token_parts) if token_parts else None

    # Claude's ``input_tokens`` EXCLUDES both cache legs, so uncached input is
    # that figure verbatim and logical input is the plain sum of the three.
    logical_input = sum(t for t in (input_tokens, cache_read, cache_write)
                        if t is not None) if token_parts else None
    all_thread = _all_thread_tokens(result, total_tokens)
    all_thread_logical = _all_thread_logical_input(result, logical_input)
    requests = _count_stream_requests(text)
    return ProviderUsage(
        available=True,
        source="claude_cli_json",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        # Claude does not report a separate reasoning-token count in this payload.
        reasoning_tokens=None,
        total_tokens=total_tokens,
        # ``usage`` is the MAIN THREAD only; ``total_cost_usd`` bills the main
        # thread and every subagent. Both bases are recorded, each labelled.
        total_tokens_basis="main_thread",
        all_thread_total_tokens=all_thread,
        subagent_tokens=(None if (all_thread is None or total_tokens is None)
                         else max(0, all_thread - total_tokens)),
        uncached_input_tokens=input_tokens,
        logical_input_tokens=logical_input,
        all_thread_logical_input_tokens=all_thread_logical,
        # Provider request count is not present in the provider result object.
        provider_request_count=None,
        observable_turn_count=requests,
        # Compatibility alias retained for existing report consumers.
        request_count=requests,
        logical_input_per_request=_per_request(logical_input, requests),
        cost_basis="all_thread" if has_cost else None,
        cost_usd=float(cost) if has_cost else None,
        raw=result,
    )


def _all_thread_tokens(result: dict, main_total: int | None) -> int | None:
    """Tokens across the main thread and every subagent, from ``modelUsage``.

    ``modelUsage`` is a per-model rollup that ALREADY includes the main thread,
    so it replaces the main-thread figure rather than adding to it -- summing
    both would double count. ``max`` guards the case where a model is missing
    from the rollup and the rollup therefore under-reports.
    """
    mu = result.get("modelUsage")
    if not isinstance(mu, dict) or not mu:
        return main_total
    total = 0
    seen = False
    for entry in mu.values():
        if not isinstance(entry, dict):
            continue
        for key in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                    "cacheCreationInputTokens"):
            v = _as_int(entry.get(key))
            if v is not None:
                total += v
                seen = True
    if not seen:
        return main_total
    return max(total, main_total) if main_total is not None else total


def _all_thread_logical_input(result: dict, main_logical: int | None) -> int | None:
    """Logical input across the main thread and every subagent (``modelUsage``)."""
    mu = result.get("modelUsage")
    if not isinstance(mu, dict) or not mu:
        return main_logical
    total = 0
    seen = False
    for entry in mu.values():
        if not isinstance(entry, dict):
            continue
        for key in ("inputTokens", "cacheReadInputTokens",
                    "cacheCreationInputTokens"):
            v = _as_int(entry.get(key))
            if v is not None:
                total += v
                seen = True
    if not seen:
        return main_logical
    return max(total, main_logical) if main_logical is not None else total


def _extract_result_object(text: str) -> dict | None:
    """Find the result object in a json or stream-json transcript."""
    # Plain --output-format json: the whole log is one object.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # stream-json: newline-delimited objects; the final type=="result" wins.
    result: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            result = obj
    return result


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None

