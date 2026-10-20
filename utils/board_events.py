"""Tell the public league board that something changed.

Services and dashboard pages call :func:`league_changed` after results, voids and league edits.
The board cog subscribes and refreshes after a short pause, so a burst of changes is drawn once.
Nothing happens when the board isn't loaded (tests, scripts).
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

Listener = Callable[[Optional[float]], None]
_listeners: List[Listener] = []


def subscribe(listener: Listener) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def unsubscribe(listener: Listener) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


def league_changed(delay: Optional[float] = None) -> None:
    """Schedule a board refresh (``delay`` seconds from now; default: the board's debounce)."""
    for listener in list(_listeners):
        try:
            listener(delay)
        except Exception as exc:  # never let the board break the change that triggered it
            logger.warning("Board listener failed: %s", exc)
