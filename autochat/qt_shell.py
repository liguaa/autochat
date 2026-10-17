"""Qt desktop shell. Run ``python -m autochat.qt_shell`` after building frontend."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from tempfile import TemporaryDirectory
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox, QWidget

from .credentials import SERVICE, TYPESAFE_SERVICE, USERNAME
from .ui_bridge import UIBridge
from .ui_service import ROOT, UIService


DIST = (Path(getattr(sys, "_MEIPASS", ROOT)) / "frontend" / "dist" / "index.html")
PREFERENCES = ROOT / "data" / "qt_preferences.json"


class LocalPage(QWebEnginePage):
    def acceptNavigationRequest(self, url: QUrl, kind, is_main_frame: bool) -> bool:
        if url.scheme() in {"file", "qrc"}:
            if url.scheme() == "file" and not Path(url.toLocalFile()).resolve().is_relative_to(DIST.parent.resolve()):
                return False
            return True
        if kind == QWebEnginePage.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
        return False


class WebWindow(QMainWindow):
    def __init__(self, *, panel: bool, service: UIService, command):
        super().__init__()
        self.panel = panel
        self.setWindowTitle("AutoChat · 轻面板" if panel else "AutoChat · 对话工作台")
        self.setMinimumSize(360, 480) if panel else self.setMinimumSize(720, 520)
        self.resize(420, 700) if panel else self.resize(1280, 840)
        self.ensure_visible()
        self.view = QWebEngineView(self)
        self.page = LocalPage(self.view)
        self.view.setPage(self.page)
        self.channel = QWebChannel(self.page)
        self.bridge = UIBridge(service, command)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel)
        self.setCentralWidget(self.view)
        url = QUrl.fromLocalFile(str(DIST))
        url.setQuery("view=panel" if panel else "view=workbench")
        self.page.setUrl(url)

    def ensure_visible(self):
        screens = QApplication.screens()
        if not screens:
            return
        center = self.frameGeometry().center()
        screen = QApplication.screenAt(center) or min(
            screens, key=lambda item: (item.availableGeometry().center() - center).manhattanLength())
        area = screen.availableGeometry()
        width = min(self.width(), area.width())
        height = min(self.height(), area.height())
        self.resize(width, height)
        self.move(max(area.left(), min(self.x(), area.right() - width + 1)),
                  max(area.top(), min(self.y(), area.bottom() - height + 1)))


class Bubble(QWidget):
    def __init__(self, command):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.command = command
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(54, 54)
        self._press: QPoint | None = None
        self._origin: QPoint | None = None
        self._moved = False
        self.visual_state = "idle"
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - 86, screen.bottom() - 110)
        try:
            saved = json.loads(PREFERENCES.read_text(encoding="utf-8"))
            if isinstance(saved.get("x"), int) and isinstance(saved.get("y"), int):
                self.move(saved["x"], saved["y"])
            if saved.get("always_on_top") is False:
                self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        except (OSError, ValueError, TypeError):
            pass
        self._clamp()

    def _clamp(self):
        screens = QApplication.screens()
        if not screens:
            return
        point = self.pos()
        screen = min(screens, key=lambda item: abs(item.availableGeometry().center().x() - point.x()) +
                     abs(item.availableGeometry().center().y() - point.y()))
        area = screen.availableGeometry()
        self.move(max(area.left(), min(point.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(point.y(), area.bottom() - self.height() + 1)))

    def _save(self):
        PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
        PREFERENCES.write_text(json.dumps({"x": self.x(), "y": self.y(),
                                           "always_on_top": bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)}),
                               encoding="utf-8")

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#176448"))
        painter.setPen(QPen(QColor("#176448")))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 17, 17)
        painter.setPen(QColor("#ffffff"))
        font = painter.font()
        font.setFamily("Segoe UI Variable")
        font.setPointSize(16)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "ac")
        if self.visual_state != "idle":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor({"busy": "#e8c886", "ready": "#ccefd6", "error": "#e89d94"}[self.visual_state]))
            painter.drawEllipse(43, 42, 7, 7)

    def set_visual_state(self, state: str):
        self.visual_state = state
        self.setToolTip({"idle": "AutoChat · 手动聊天助手", "busy": "AutoChat · 正在分析",
                         "ready": "AutoChat · 分析完成", "error": "AutoChat · 分析失败"}[state])
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._origin = self.pos()
            self._moved = False

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._press and self._origin and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press
            if delta.manhattanLength() > 3:
                self._moved = True
                self.move(self._origin + delta)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._press:
            if self._moved:
                self._clamp()
                self._save()
            else:
                self.command("open_panel")
            self._press = None

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        top = menu.addAction("始终置顶")
        top.setCheckable(True)
        top.setChecked(bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        reset = menu.addAction("重置位置")
        menu.addSeparator()
        quit_action = menu.addAction("退出 AutoChat")
        chosen = menu.exec(event.globalPos())
        if chosen == top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, top.isChecked())
            self.show()
            self._save()
        elif chosen == reset:
            area = QApplication.primaryScreen().availableGeometry()
            self.move(area.right() - 86, area.bottom() - 110)
            self._save()
        elif chosen == quit_action:
            self.command("quit")


class EventRelay(QObject):
    event = Signal(object)

    def __init__(self, handler):
        super().__init__()
        self.handler = handler
        self.event.connect(self.deliver, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def deliver(self, event: dict):
        self.handler(event)


class QtShell:
    def __init__(self, app: QApplication, sessions_dir: Path | None = None):
        self.app = app
        self.relay = EventRelay(self._handle_event)
        self.service = UIService(emit=self._publish, sessions_dir=sessions_dir)
        self.workbench: WebWindow | None = None
        self.panel: WebWindow | None = None
        self.bubble = Bubble(self.command)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.screenRemoved.connect(lambda _screen: QTimer.singleShot(0, self._keep_windows_visible))

    def _keep_windows_visible(self):
        self.bubble._clamp()
        for window in (self.workbench, self.panel):
            if window:
                window.ensure_visible()

    def _publish(self, event: dict):
        self.relay.event.emit(event)

    def _handle_event(self, event: dict):
        kind = event["type"]
        if kind == "analysis.started":
            self.bubble.set_visual_state("busy")
        elif kind in {"analysis.completed", "analysis.error"}:
            state = "ready" if kind == "analysis.completed" else "error"
            self.bubble.set_visual_state(state)
            QTimer.singleShot(1500, lambda: self.bubble.set_visual_state("idle") if self.bubble.visual_state == state else None)
        elif kind == "analysis.cancelled":
            self.bubble.set_visual_state("idle")
        for window in (self.workbench, self.panel):
            if window:
                window.bridge.publish(event)

    def command(self, command: str):
        if command == "open_workbench":
            if self.workbench is None:
                self.workbench = WebWindow(panel=False, service=self.service, command=self.command)
            self.workbench.ensure_visible()
            self.workbench.show()
            self.workbench.raise_()
            self.workbench.activateWindow()
            if self.panel:
                self.panel.hide()
        elif command == "open_panel":
            if self.panel is None:
                self.panel = WebWindow(panel=True, service=self.service, command=self.command)
            self.panel.ensure_visible()
            self.panel.show()
            self.panel.raise_()
            self.panel.activateWindow()
        elif command == "collapse":
            if self.panel:
                self.panel.hide()
            if self.workbench:
                self.workbench.hide()
            self.bubble.show()
        elif command == "quit":
            self.service.close()
            self.app.quit()
        elif command in {"configure_deepseek", "configure_typesafe"}:
            label = "DeepSeek" if command == "configure_deepseek" else "TypeSafe"
            service = SERVICE if command == "configure_deepseek" else TYPESAFE_SERVICE
            parent = self.panel if self.panel and self.panel.isVisible() else self.workbench
            value, accepted = QInputDialog.getText(parent, f"配置 {label}", f"输入 {label} API 密钥：",
                                                    QLineEdit.EchoMode.Password)
            if accepted and value.strip():
                try:
                    import keyring
                    keyring.set_password(service, USERNAME, value.strip())
                    with self.service.lock:
                        self.service._event("state.changed")
                    QMessageBox.information(parent, "已保存", f"{label} 密钥已保存到 Windows 凭据管理器。")
                except Exception:
                    QMessageBox.warning(parent, "保存失败", "无法保存到 Windows 凭据管理器，请使用终端配置。")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--worker-cli":
        from .cli import main as cli_main
        return cli_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Load local pages, print startup timing and exit")
    parser.add_argument("--smoke-report", type=Path, help="Write the smoke result as JSON")
    args = parser.parse_args(argv)
    if not DIST.exists():
        parser.error("前端尚未构建；请先在 frontend 目录运行 npm install 和 npm run build")
    started = time.perf_counter()
    app = QApplication(sys.argv[:1])
    smoke_storage = TemporaryDirectory(prefix="autochat-qt-smoke-") if args.smoke else None
    shell = QtShell(app, sessions_dir=Path(smoke_storage.name) if smoke_storage else None)
    if args.smoke:
        shell.bubble.show()
        bubble_image = shell.bubble.grab().toImage()
        bubble_painted = not bubble_image.isNull() and bubble_image.pixelColor(10, 27).alpha() > 0
        shell.workbench = WebWindow(panel=False, service=shell.service, command=shell.command)
        shell.panel = WebWindow(panel=True, service=shell.service, command=shell.command)
        completed: dict[str, bool | None] = {"workbench": None, "panel": None}
        result_code = {"value": 1}
        timed_out = {"value": False}

        def loaded(name: str, ok: bool):
            completed[name] = bool(ok)
            if all(value is not None for value in completed.values()):
                if all(completed.values()):
                    result_code["value"] = 0 if bubble_painted else 1
                    shell.service.dispatch("message.append", {
                        "session_id": shell.service.selected_id, "role": "contact", "text": "Smoke check."})
                    QTimer.singleShot(400, finish)
                else:
                    finish()

        def finish():
            def done(value):
                try:
                    dom = json.loads(value) if isinstance(value, str) else None
                except ValueError:
                    dom = None
                memory_mb = None
                try:
                    import psutil
                    current = psutil.Process()
                    memory_mb = round(sum(item.memory_info().rss for item in [current, *current.children(recursive=True)]) / 1048576, 1)
                except (ImportError, OSError):
                    pass
                report = {"loaded": completed, "dom": dom,
                          "bubble_painted": bubble_painted,
                          "startup_seconds": round(time.perf_counter() - started, 3),
                          "process_tree_memory_mb": memory_mb,
                          "sandbox_disabled": bool(os.environ.get("QTWEBENGINE_DISABLE_SANDBOX")),
                          "timeout": timed_out["value"]}
                if args.smoke_report:
                    args.smoke_report.parent.mkdir(parents=True, exist_ok=True)
                    args.smoke_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(report, ensure_ascii=False), flush=True)
                app.quit()
            if result_code["value"] == 0:
                shell.workbench.page.runJavaScript("JSON.stringify({url:document.URL,ready:document.readyState,root:!!document.getElementById('root')?.firstChild,messageRows:document.querySelectorAll('.message-row').length,captionCodes:Array.from(document.querySelector('.brand-caption')?.textContent||'').slice(0,4).map(c=>c.codePointAt(0)),channel:typeof QWebChannel,demo:!!document.querySelector('.demo-ribbon')})", done)
            else:
                done(None)

        shell.workbench.view.loadFinished.connect(lambda ok: loaded("workbench", ok))
        shell.panel.view.loadFinished.connect(lambda ok: loaded("panel", ok))
        shell.workbench.show()
        shell.panel.show()
        def timeout():
            timed_out["value"] = True
            finish()
        QTimer.singleShot(15000, timeout)
    else:
        shell.bubble.show()
        shell.command("open_panel" if args.panel else "open_workbench")
    code = app.exec()
    shell.service.close()
    if smoke_storage:
        smoke_storage.cleanup()
    return result_code["value"] if args.smoke else code


if __name__ == "__main__":
    raise SystemExit(main())
