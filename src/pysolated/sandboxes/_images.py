"""Shared image-name derivation.

Container providers default their image tag to `pysolated:<sanitized-host-dirname>`
so the same tag the `pysolated <engine> build-image` CLI produces lines up with
what `<engine>(image=...)` expects by default.
"""

from __future__ import annotations

import os
import re

_IMAGE_NAME_FALLBACK = "local"


def _derive_default_image_name(cwd: str | None = None) -> str:
    """Derive `pysolated:<sanitized-dirname>` from `cwd` (or `os.getcwd()`).

    The last path segment is lowercased and sanitized to `[a-z0-9_.-]` (other
    runs collapse to a single `-`). Leading/trailing `.` and `-` are then
    trimmed, since a Docker/OCI tag must start with an alphanumeric or `_`
    (e.g. a `.pysolated` scratch dir would otherwise yield the invalid tag
    `pysolated:.pysolated`). An empty result — e.g. running from `/` — falls
    back to `pysolated:local`.
    """
    base = os.path.basename(os.path.abspath(cwd if cwd is not None else os.getcwd()))
    sanitized = re.sub(r"[^a-z0-9_.-]+", "-", base.lower()).strip(".-")
    return f"pysolated:{sanitized or _IMAGE_NAME_FALLBACK}"
