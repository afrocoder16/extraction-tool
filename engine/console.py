"""Small command-line compatibility helpers."""

from __future__ import annotations

import sys


def configure_utf8_stdio() -> None:
    """Make Ethiopic output work in the default Windows PowerShell console.

    Windows PowerShell commonly gives Python a cp1252 stdout stream even when the
    terminal can display Unicode.  Reconfiguring the existing streams avoids a
    ``UnicodeEncodeError`` without requiring every command to use ``-X utf8``.
    The capability check keeps this harmless under redirected/test streams.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
