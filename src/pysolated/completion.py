"""Completion-signal matching — a pure helper over accumulated agent text.

A run's iteration loop calls this once per appended chunk of stdout and stops the
moment something fires. Substring semantics; supports either a single string or
an ordered list of candidates. Returning the exact matched signal lets `run()`
report which one fired via `RunResult.completion_signal`.
"""

from __future__ import annotations

from collections.abc import Iterable


def match_completion_signal(
    content: str, signals: str | Iterable[str]
) -> str | None:
    """Return the first signal that appears in `content`, else `None`.

    `signals` may be a single string or any iterable of candidate strings;
    matching is plain substring (no regex, no whitespace normalization). When
    multiple candidates are supplied, list order picks the winner — the first
    candidate found in `content` is returned, regardless of where in `content`
    it appears.
    """
    if isinstance(signals, str):
        return signals if signals and signals in content else None
    for signal in signals:
        if signal and signal in content:
            return signal
    return None
