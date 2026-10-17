"""One analysis task at a time across windows in the same Tk process."""

from __future__ import annotations

import threading


class AnalysisGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: object | None = None

    def acquire(self, owner: object) -> bool:
        with self._lock:
            if self._owner is not None:
                return False
            self._owner = owner
            return True

    def release(self, owner: object) -> None:
        with self._lock:
            if self._owner is owner:
                self._owner = None


gate = AnalysisGate()
