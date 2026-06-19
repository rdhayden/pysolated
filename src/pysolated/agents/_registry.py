"""Agent registry scaffold.

The name → factory registry and the `build_agent` CLI-builder land in a later
slice (issue #32). This module exists so the package layout is in place when
that slice arrives, and so future cross-module imports have a stable home.
"""

from __future__ import annotations
