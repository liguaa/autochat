"""Restricted JSON bridge between local QWebEngine pages and UIService."""

from __future__ import annotations

import json
from typing import Callable

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication

from .ui_service import UIService


class UIBridge(QObject):
    message = Signal(str)
    outbound = Signal(str)

    def __init__(self, service: UIService, window_command: Callable[[str], None]):
        super().__init__()
        self.service = service
        self.window_command = window_command
        self.outbound.connect(self.message.emit)

    @Slot(result=str)
    def getSnapshot(self) -> str:
        return json.dumps(self.service.snapshot(), ensure_ascii=False)

    @Slot(str, result=str)
    def invoke(self, raw: str) -> str:
        try:
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or set(envelope) != {"action", "payload"}:
                raise ValueError("动作格式无效")
            action, payload = envelope["action"], envelope["payload"]
            if not isinstance(action, str) or not isinstance(payload, dict):
                raise ValueError("动作格式无效")
            if action == "window.command":
                command = payload.get("command")
                if command not in {"open_workbench", "open_panel", "collapse", "quit",
                                   "configure_deepseek", "configure_typesafe"}:
                    raise ValueError("窗口动作无效")
                self.window_command(command)
                result = self.service.snapshot()
            else:
                result = self.service.dispatch(action, payload)
                if action in {"candidate.copy", "message.copy"}:
                    QGuiApplication.clipboard().setText(result["text"])
                    result = {"message": result["message"]}
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    def publish(self, event: dict) -> None:
        self.outbound.emit(json.dumps(event, ensure_ascii=False))
