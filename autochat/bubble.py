"""Manual desktop bubble. It never inspects or controls another application."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from .analysis_gate import gate
from .bubble_state import BubbleState, Request, clamp_position
from .cli import validate_replay_detail, validate_result
from .credentials import get_api_key, get_typesafe_api_key
from .dynamic import config_fingerprint
from .dynamic_ui import render_dynamic_card
from .personas import LABEL_TO_KEY, PRESETS
from .replay_desktop import MODES


ROOT = Path(__file__).resolve().parents[1]
PREFERENCES = ROOT / "data" / "bubble_preferences.json"
SIZE = 54


def workareas(root: tk.Misc) -> list[tuple[int, int, int, int]]:
    """Get Windows monitor work areas; fall back to Tk's available screen."""
    if os.name == "nt":
        try:
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [(name, ctypes.c_long) for name in ("left", "top", "right", "bottom")]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

            areas: list[tuple[int, int, int, int]] = []
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                                                wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

            def collect(handle, _hdc, _rect, _data):
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(info)
                if ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                    r = info.rcWork
                    areas.append((r.left, r.top, r.right, r.bottom))
                return True

            callback = callback_type(collect)
            ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback, 0)
            if areas:
                return areas
        except (AttributeError, OSError, TypeError):
            pass
    return [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]


def load_preferences() -> dict:
    try:
        data = json.loads(PREFERENCES.read_text(encoding="utf-8"))
        if isinstance(data, dict) and all(isinstance(data.get(key), int) for key in ("x", "y")):
            return data
    except (OSError, ValueError):
        pass
    return {}


class BubbleApp:
    def __init__(self, root: tk.Tk | tk.Toplevel) -> None:
        self.root = root
        self.state = BubbleState()
        self.events: queue.Queue[tuple[str, Request, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.process_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.busy = False
        self.closed = False
        self.progress_path: Path | None = None
        self.progress_mtime = 0
        self.request_data: dict | None = None
        self.request_draft = ""
        self.preferences = load_preferences()
        self.root.overrideredirect(True)
        self.root.title("AutoChat 助手")
        self.root.geometry(f"{SIZE}x{SIZE}")
        self.root.attributes("-topmost", bool(self.preferences.get("topmost", False)))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.update_idletasks()
        default_x = self.root.winfo_screenwidth() - SIZE - 20
        default_y = max(20, self.root.winfo_screenheight() // 3)
        self.place_bubble(self.preferences.get("x", default_x), self.preferences.get("y", default_y))

        self.canvas = tk.Canvas(root, width=SIZE, height=SIZE, highlightthickness=0, bg="#f6f7f8")
        self.canvas.pack()
        self.oval = self.canvas.create_oval(2, 2, SIZE - 2, SIZE - 2, fill="#244b72", outline="")
        self.label = self.canvas.create_text(SIZE // 2, SIZE // 2, text="AI", fill="white",
                                             font=("Microsoft YaHei UI", 12, "bold"))
        self.canvas.bind("<ButtonPress-1>", self.drag_start)
        self.canvas.bind("<B1-Motion>", self.drag_move)
        self.canvas.bind("<ButtonRelease-1>", self.drag_end)
        self.canvas.bind("<Button-3>", self.show_menu)
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="展开 AutoChat", command=self.expand)
        self.menu.add_command(label="重置位置", command=self.reset_position)
        self.menu.add_command(label="关闭", command=self.close)
        self.drag_origin: tuple[int, int] | None = None
        self.dragged = False

        self.panel = tk.Toplevel(root)
        self.panel.withdraw()
        self.panel.title("AutoChat 助手 · 手动聊天分析")
        self.panel.geometry("430x690")
        self.panel.minsize(360, 430)
        self.panel.protocol("WM_DELETE_WINDOW", self.collapse)
        self.panel.bind("<Configure>", self.on_panel_configure)
        self.build_panel()
        self.set_visual_state("idle")
        self.root.after(200, self.poll)
        self.root.after(1000, self.keep_visible)

    def build_panel(self) -> None:
        header = ttk.Frame(self.panel, padding=(10, 8))
        header.pack(fill="x")
        ttk.Label(header, text="AutoChat 助手", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        ttk.Button(header, text="收起", command=self.collapse).pack(side="right")
        ttk.Button(header, text="关闭", command=self.close).pack(side="right", padx=(0, 5))

        session_row = ttk.Frame(self.panel, padding=(10, 0))
        session_row.pack(fill="x")
        ttk.Label(session_row, text="本地会话：").pack(side="left")
        self.session_var = tk.StringVar(value=self.state.current_name)
        self.session_picker = ttk.Combobox(session_row, textvariable=self.session_var,
                                           values=list(self.state.sessions), state="readonly", width=19)
        self.session_picker.pack(side="left", fill="x", expand=True)
        self.session_picker.bind("<<ComboboxSelected>>", self.switch_session)
        ttk.Button(session_row, text="新建", command=self.new_session).pack(side="left", padx=(6, 0))

        self.scroll = tk.Canvas(self.panel, highlightthickness=0)
        bar = ttk.Scrollbar(self.panel, orient="vertical", command=self.scroll.yview)
        self.scroll.configure(yscrollcommand=bar.set)
        self.scroll.pack(side="left", fill="both", expand=True, padx=(10, 0))
        bar.pack(side="right", fill="y", padx=(0, 8))
        outer = ttk.Frame(self.scroll)
        window = self.scroll.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _e: self.scroll.configure(scrollregion=self.scroll.bbox("all")))
        self.scroll.bind("<Configure>", lambda e: self.scroll.itemconfigure(window, width=e.width))
        self.scroll.bind("<MouseWheel>", lambda e: self.scroll.yview_scroll(-int(e.delta / 120), "units"))

        entry = ttk.LabelFrame(outer, text="用户提供的文字", padding=8)
        entry.pack(fill="x", padx=10, pady=(8, 0))
        self.role_var = tk.StringVar(value="对方")
        ttk.Combobox(entry, textvariable=self.role_var, values=["对方", "我（确认已发送）"],
                     state="readonly", width=18).pack(anchor="w")
        self.message_text = tk.Text(entry, height=3, wrap="word", font=("Microsoft YaHei UI", 10))
        self.message_text.pack(fill="x", pady=(5, 0))
        buttons = ttk.Frame(entry)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="粘贴文字", command=self.paste).pack(side="left")
        ttk.Button(buttons, text="加入消息", command=self.add_message).pack(side="left", padx=5)
        self.analyze_button = ttk.Button(buttons, text="分析", command=self.analyze)
        self.analyze_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="取消", command=self.cancel)
        self.cancel_button.pack(side="left", padx=5)
        ttk.Label(entry, text="手动输入/粘贴 · 尚未发送；本人消息只记录确实发出的文字。",
                  wraplength=390, foreground="#626c74").pack(anchor="w", pady=(5, 0))

        settings = ttk.Frame(outer, padding=(10, 7))
        settings.pack(fill="x")
        self.mode_var = tk.StringVar(value=MODES["jev-dynamic"])
        ttk.Combobox(settings, textvariable=self.mode_var, values=list(MODES.values()),
                     state="readonly").pack(fill="x")
        self.style_var = tk.StringVar(value=PRESETS["natural"]["label"])
        ttk.Combobox(settings, textvariable=self.style_var, values=list(LABEL_TO_KEY),
                     state="readonly").pack(fill="x", pady=(4, 0))
        ttk.Label(settings, text="已确认事实（可选，每行一条）").pack(anchor="w", pady=(4, 0))
        self.facts_text = tk.Text(settings, height=2, wrap="word")
        self.facts_text.pack(fill="x")
        ttk.Label(settings, text="本人语气示例（可选，每行一条）").pack(anchor="w", pady=(4, 0))
        self.samples_text = tk.Text(settings, height=2, wrap="word")
        self.samples_text.pack(fill="x")
        ttk.Label(settings, text="点分析后，文字会发送至 DeepSeek；Jev 模式还会发送至 TypeSafe。输入与结果保存在本地 results/。",
                  wraplength=400, foreground="#626c74").pack(anchor="w", pady=(5, 0))

        self.status = tk.StringVar(value="待机 · 不读取剪贴板或屏幕")
        ttk.Label(outer, textvariable=self.status, wraplength=400).pack(fill="x", padx=10, pady=(0, 5))
        self.transcript = ttk.Frame(outer)
        self.transcript.pack(fill="x", padx=10)
        self.render()

    def place_bubble(self, x: int, y: int) -> None:
        x, y = clamp_position(int(x), int(y), SIZE, SIZE, workareas(self.root))
        self.root.geometry(f"{SIZE}x{SIZE}{x:+d}{y:+d}")

    def set_visual_state(self, state: str) -> None:
        color, label = {"idle": ("#244b72", "AI"), "busy": ("#805515", "···"),
                        "ready": ("#226546", "✓"), "error": ("#9b3930", "!")}[state]
        self.canvas.itemconfigure(self.oval, fill=color)
        self.canvas.itemconfigure(self.label, text=label)

    def save_preferences(self) -> None:
        data = {"x": self.root.winfo_x(), "y": self.root.winfo_y(),
                "topmost": bool(self.root.attributes("-topmost"))}
        PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
        PREFERENCES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def reset_position(self) -> None:
        self.place_bubble(self.root.winfo_screenwidth() - SIZE - 20, 80)
        self.save_preferences()

    def keep_visible(self) -> None:
        if self.closed:
            return
        self.place_bubble(self.root.winfo_x(), self.root.winfo_y())
        self.root.after(1000, self.keep_visible)

    def drag_start(self, event: tk.Event) -> None:
        self.drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())
        self.dragged = False

    def drag_move(self, event: tk.Event) -> None:
        if self.drag_origin:
            x, y = event.x_root - self.drag_origin[0], event.y_root - self.drag_origin[1]
            if abs(x - self.root.winfo_x()) + abs(y - self.root.winfo_y()) > 3:
                self.dragged = True
            self.place_bubble(x, y)

    def drag_end(self, _event: tk.Event) -> None:
        if self.dragged:
            self.save_preferences()
        else:
            self.expand()
        self.drag_origin = None

    def show_menu(self, event: tk.Event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def expand(self) -> None:
        self.panel.deiconify()
        self.panel.lift()
        self.message_text.focus_set()

    def collapse(self) -> None:
        self.panel.withdraw()

    def on_panel_configure(self, _event: tk.Event) -> None:
        if self.panel.state() == "normal":
            self.panel.after_idle(self.clamp_panel)

    def clamp_panel(self) -> None:
        if self.closed or self.panel.state() != "normal":
            return
        areas = workareas(self.root)
        old_x, old_y = self.panel.winfo_x(), self.panel.winfo_y()
        old_width, old_height = self.panel.winfo_width(), self.panel.winfo_height()
        area = next((a for a in areas if a[0] <= old_x < a[2] and a[1] <= old_y < a[3]), areas[0])
        width = min(old_width, max(360, area[2] - area[0] - 20))
        height = min(old_height, max(430, area[3] - area[1] - 20))
        x, y = clamp_position(old_x, old_y, width, height, [area])
        if (x, y, width, height) != (old_x, old_y, old_width, old_height):
            self.panel.geometry(f"{width}x{height}{x:+d}{y:+d}")

    def new_session(self) -> None:
        name = simpledialog.askstring("新建本地会话", "给本地会话命名：", parent=self.panel)
        if name is None:
            return
        try:
            self.state.add_session(name)
        except ValueError as exc:
            messagebox.showerror("无法新建", str(exc), parent=self.panel)
            return
        self.session_var.set(self.state.current_name)
        self.session_picker.configure(values=list(self.state.sessions))
        if not self.busy:
            self.set_visual_state("idle")
        self.status.set("已切换本地会话；请核对所属联系人。")
        self.render()

    def switch_session(self, _event: tk.Event) -> None:
        self.state.select_session(self.session_var.get())
        if not self.busy:
            self.set_visual_state("idle")
        self.status.set("已切换本地会话；旧会话的候选不会显示在这里。")
        self.render()

    def paste(self) -> None:
        try:
            value = self.root.clipboard_get(type="STRING")
        except tk.TclError:
            messagebox.showerror("无法粘贴", "剪贴板中没有文字；第一版只接受文字。", parent=self.panel)
            return
        if not value.strip():
            messagebox.showerror("无法粘贴", "剪贴板中的文字为空。", parent=self.panel)
            return
        self.message_text.delete("1.0", "end")
        self.message_text.insert("1.0", value)
        self.status.set("已粘贴文字；请核对内容和本地会话，再加入消息。")

    def add_message(self) -> None:
        role = "contact" if self.role_var.get() == "对方" else "me"
        try:
            self.state.session.append(role, self.message_text.get("1.0", "end-1c"))
        except ValueError as exc:
            messagebox.showerror("无法加入", str(exc), parent=self.panel)
            return
        self.message_text.delete("1.0", "end")
        if not self.busy:
            self.set_visual_state("idle")
        self.status.set("消息已加入本地会话；点击分析才会调用模型。" if role == "contact"
                        else "已记录你确认发出的消息；复制候选不会自动加入历史。")
        self.render()

    def current_data(self) -> dict:
        facts = [x.strip() for x in self.facts_text.get("1.0", "end-1c").splitlines() if x.strip()]
        samples = [x.strip() for x in self.samples_text.get("1.0", "end-1c").splitlines() if x.strip()]
        return self.state.session.analysis_data(
            confirmed_facts=facts,
            style_profile={"preset": LABEL_TO_KEY[self.style_var.get()], "samples": samples})

    def selected_mode(self) -> str:
        return next(key for key, label in MODES.items() if label == self.mode_var.get())

    def analyze(self) -> None:
        if self.busy:
            return
        try:
            if self.message_text.get("1.0", "end-1c").strip():
                raise ValueError("输入框还有未加入的文字；请先加入消息。")
            data = self.current_data()
            mode = self.selected_mode()
            if not get_api_key():
                raise ValueError("未配置 DeepSeek 密钥。")
            if mode != "llm-only" and not get_typesafe_api_key():
                raise ValueError("未配置 TypeSafe 密钥。")
            if not gate.acquire(self):
                raise ValueError("另一个 AutoChat 窗口正在分析；请等待或取消。")
            request = self.state.begin(data, mode)
        except (ValueError, KeyError, OSError) as exc:
            gate.release(self)
            self.status.set(f"无法分析：{exc}")
            return
        try:
            results = ROOT / "results"
            results.mkdir(parents=True, exist_ok=True)
            stem = f"bubble_{request.session_id}_{request.index + 1}_{mode}_{request.context_version}_{request.token[:10]}"
            input_path = results / f"{stem}_input.json"
            output_path = results / f"{stem}_reply.json"
            self.progress_path = results / f"{stem}_progress.json" if mode == "jev-dynamic" else None
            self.progress_mtime = 0
            input_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            self.state.cancel()
            gate.release(self)
            self.status.set(f"无法保存分析输入：{exc}")
            return
        command = [sys.executable, "-m", "autochat.cli", "--mode", mode, "--thinking", "disabled",
                   "--replay-detail", "--input", str(input_path), "--output", str(output_path)]
        if self.progress_path:
            command += ["--progress-file", str(self.progress_path)]
        self.request_data = data
        self.request_draft = self.message_text.get("1.0", "end-1c")
        self.cancel_event = threading.Event()
        self.busy = True
        self.set_visual_state("busy")
        self.status.set(f"正在分析 {self.state.current_name} 的第 {request.index + 1} 条消息…")
        self.render()
        threading.Thread(target=self.run_pipeline,
                         args=(request, command, input_path, output_path, self.progress_path,
                               self.cancel_event), daemon=True).start()

    def run_pipeline(self, request: Request, command: list[str], input_path: Path,
                     output_path: Path, progress_path: Path | None,
                     cancelled: threading.Event) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = None
        try:
            with self.process_lock:
                if cancelled.is_set():
                    return
                process = subprocess.Popen(command, cwd=ROOT, env=env, text=True, encoding="utf-8",
                                           errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           creationflags=flags)
                self.process = process
            stdout, stderr = process.communicate(timeout=300)
            if process.returncode != 0:
                raise RuntimeError((stderr or stdout).strip() or "分析失败")
            self.events.put(("complete", request, output_path))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, subprocess.TimeoutExpired) and process is not None:
                process.kill()
            self.events.put(("error", request, str(exc)))
        finally:
            with self.process_lock:
                if self.process is process:
                    self.process = None
            for temporary_path in (input_path, progress_path):
                if temporary_path is not None:
                    try:
                        temporary_path.unlink(missing_ok=True)
                    except OSError:
                        pass

    def request_is_current(self, request: Request) -> bool:
        try:
            return (self.request_data is not None
                    and self.state.matches(request, self.current_data(), self.selected_mode())
                    and self.message_text.get("1.0", "end-1c") == self.request_draft)
        except (ValueError, KeyError):
            return False

    def poll(self) -> None:
        if self.closed:
            return
        active = self.state.active
        if self.busy and active and self.progress_path and self.progress_path.exists():
            try:
                stamp = self.progress_path.stat().st_mtime_ns
                if stamp != self.progress_mtime:
                    self.progress_mtime = stamp
                    output = json.loads(self.progress_path.read_text(encoding="utf-8"))
                    if self.request_is_current(active) and self.valid_identity(active, output):
                        self.render(progress=output)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        try:
            while True:
                kind, request, value = self.events.get_nowait()
                if self.state.active != request:
                    continue
                self.busy = False
                current = self.request_is_current(request)
                origin = self.state.session_for(request)
                try:
                    if kind == "error":
                        origin.record_error(request.index, self.request_data, str(value), request.token)
                        self.status.set(f"分析失败：{value}" if current else "旧会话分析失败；当前会话未改变。")
                        self.set_visual_state("error" if current else "idle")
                    else:
                        output = json.loads(Path(value).read_text(encoding="utf-8"))
                        if not self.valid_identity(request, output):
                            raise ValueError("请求标识、上下文或配置不匹配")
                        if output.get("result") is not None:
                            validate_result(output["result"])
                            validate_replay_detail(output["result"])
                        elif request.mode != "jev-dynamic":
                            raise ValueError("结果缺少回复")
                        origin.record_result(request.index, self.request_data, output, Path(value), request.token)
                        self.status.set("分析已完成；候选仍需核对，尚未发送。" if current else
                                        "旧会话结果已保存到对应会话；当前会话未改变。")
                        self.set_visual_state("ready" if current else "idle")
                except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                    origin.record_error(request.index, self.request_data, str(exc), request.token)
                    self.status.set(f"结果无法显示：{exc}")
                    self.set_visual_state("error")
                self.state.cancel()
                gate.release(self)
                self.render()
        except queue.Empty:
            pass
        self.root.after(200, self.poll)

    def valid_identity(self, request: Request, output: dict) -> bool:
        if (output.get("context_version") != request.context_version
                or output.get("pipeline_mode") != request.mode
                or not isinstance(output.get("request_id"), str) or not output["request_id"]):
            return False
        if request.mode == "jev-dynamic":
            return output.get("config_fingerprint") == config_fingerprint(
                self.request_data, thinking="disabled", replay_detail=True)
        return True

    def cancel(self) -> None:
        if not self.busy:
            return
        self.state.cancel()
        self.busy = False
        with self.process_lock:
            self.cancel_event.set()
            process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        gate.release(self)
        self.set_visual_state("idle")
        self.status.set("已取消当前分析；已写入的本地阶段文件可能保留。")
        self.render()

    def render(self, progress: dict | None = None) -> None:
        self.analyze_button.state(["disabled"] if self.busy else ["!disabled"])
        self.cancel_button.state(["!disabled"] if self.busy else ["disabled"])
        for child in self.transcript.winfo_children():
            child.destroy()
        session = self.state.session
        if not session.visible_messages:
            ttk.Label(self.transcript, text="还没有消息。请手动输入或点击粘贴文字。",
                      wraplength=375).pack(anchor="w", pady=8)
            return
        for index, message in enumerate(session.visible_messages):
            mine = message["role"] == "me"
            block = ttk.Frame(self.transcript)
            block.pack(fill="x", pady=(7, 2))
            side = "e" if mine else "w"
            ttk.Label(block, text=f"{index + 1}. {'我 · 已确认发出' if mine else '对方'}",
                      foreground="#626c74").pack(anchor=side)
            tk.Label(block, text=message["text"], bg="#e6f2eb" if mine else "#eef0f2",
                     wraplength=340, justify="left", padx=10, pady=7,
                     font=("Microsoft YaHei UI", 10)).pack(anchor=side)
            if not mine:
                self.render_card(block, index, progress if index == session.cursor - 1 else None)

    def render_card(self, parent: ttk.Frame, index: int, progress: dict | None) -> None:
        session = self.state.session
        card = session.cards.get(index)
        if not card and not progress and index != session.cursor - 1:
            return
        frame = ttk.LabelFrame(parent, text="聊天分析 · 对应上方消息", padding=7)
        frame.pack(fill="x", pady=(4, 0))
        if progress:
            render_dynamic_card(frame, progress, wraplength=340, summary_limit=3)
            ttk.Label(frame, text="分析进行中；候选尚不可用。").pack(anchor="w")
            return
        if card is None:
            ttk.Label(frame, text="分析中…" if self.busy and index == session.cursor - 1 else "点击分析查看结果").pack(anchor="w")
            return
        if card["state"] == "error":
            ttk.Label(frame, text="分析失败：" + card["message"], wraplength=340).pack(anchor="w")
            return
        output = card["output"]
        result = output.get("result")
        stale = index != session.cursor - 1 or output.get("pipeline_mode") != self.selected_mode()
        if not stale:
            try:
                stale = card["context_version"] != self.current_data_version()
            except (ValueError, KeyError):
                stale = True
        ttk.Label(frame, text="历史结果 · 候选已过期" if stale else "当前结果 · 待人工审核",
                  foreground="#9a5b1b" if stale else "#225b38").pack(anchor="w")
        if output.get("pipeline_mode") == "jev-dynamic":
            render_dynamic_card(frame, output, wraplength=340, summary_limit=3)
        if output.get("history_guard_applied"):
            ttk.Label(frame, text="历史缺口保护已启用；候选改为请求核对。",
                      wraplength=340).pack(anchor="w")
        if result is None:
            ttk.Label(frame, text="当前无可复制候选；已完成的分析仍可查看。").pack(anchor="w")
            return
        analysis = result["analysis"]
        for key, title in (("literal", "字面意思"), ("subtext", "可能潜台词"),
                           ("history_gap", "历史缺口")):
            ttk.Label(frame, text=f"{title}：{analysis[key]}", wraplength=340,
                      justify="left").pack(anchor="w")
        ttk.Label(frame, text="回复候选 · 仅复制，尚未发送").pack(anchor="w", pady=(5, 0))
        ttk.Label(frame, text=f"所属：{self.state.current_name} · 对应原文：{session.messages[index]['text']}",
                  wraplength=340, foreground="#626c74").pack(anchor="w")
        for candidate in result["candidates"]:
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=candidate["text"], wraplength=265, justify="left").pack(side="left", fill="x", expand=True)
            if not stale:
                ttk.Button(row, text="复制", command=lambda text=candidate["text"], i=index,
                           version=card["context_version"], mode=output["pipeline_mode"]:
                           self.copy_candidate(text, i, version, mode)).pack(side="right")

    def current_data_version(self) -> str:
        from .cli import context_version
        return context_version(self.current_data())

    def copy_candidate(self, text: str, index: int, version: str, mode: str) -> None:
        try:
            current = self.current_data_version()
        except (ValueError, KeyError):
            current = None
        if (index != self.state.session.cursor - 1 or version != current or mode != self.selected_mode()
                or self.message_text.get("1.0", "end-1c").strip()):
            self.status.set("候选已过期，或输入框还有未加入的文字；请核对并重新分析。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.set(f"已复制 {self.state.current_name} 的候选；请自行核对收件人、修改并发送。")

    def close(self) -> None:
        self.closed = True
        self.cancel()
        self.save_preferences()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    BubbleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
