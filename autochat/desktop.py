"""Standalone Windows sidecar for reviewing a copied WeChat message.

The window never reads WeChat directly or sends a WeChat message. The user
copies text into it and starts each DeepSeek request explicitly.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import messagebox, ttk

from .cli import context_version, read_conversation, validate_result
from .analysis_gate import gate
from .credentials import get_api_key, get_typesafe_api_key
from .dynamic_ui import render_dynamic_card
from .personas import LABEL_TO_KEY
from .scorecard import DISPLAY_NAMES, display_scorecard


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_LABELS = {
    "dual": "NanoJev + DeepSeek",
    "llm-only": "仅 DeepSeek",
    "jev": "Jev 判断 + DeepSeek",
    "jev-rank": "Jev 判断 + DeepSeek + Jev 排序",
    "jev-dynamic": "DS 提问 → Jev 判断 → DS 写措辞",
}


def pipeline_command(mode: str, input_path: Path, output_path: Path) -> list[str]:
    """Select the requested analysis CLI path."""
    if mode == "dual":
        module, extra = "autochat.dual", []
    elif mode == "llm-only":
        module, extra = "autochat.cli", ["--mode", "llm-only", "--thinking", "disabled"]
    elif mode in {"jev", "jev-rank", "jev-dynamic"}:
        module, extra = "autochat.cli", ["--mode", mode, "--thinking", "disabled"]
    else:
        raise ValueError(f"未知分析模式: {mode}")
    return [sys.executable, "-m", module, "--input", str(input_path),
            "--output", str(output_path), *extra]


class DesktopApp:
    def __init__(self, root: tk.Tk, initial_result: Path | None = None) -> None:
        self.root = root
        self.root.title("微信回复助手 · 本地侧栏")
        self.root.geometry("800x680")
        self.root.minsize(640, 500)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.busy = False
        self.closed = False
        self.progress_path: Path | None = None
        self.progress_mtime = 0
        self.active_version: str | None = None
        self.active_mode: str | None = None
        self.displayed_request_id: str | None = None
        self.bubble_app = None

        canvas = tk.Canvas(root, highlightthickness=0)
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        outer = ttk.Frame(canvas, padding=16)
        canvas_window = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(canvas_window, width=event.width))
        canvas.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"))
        ttk.Label(outer, text="微信回复助手", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="在微信中复制一条消息，再粘贴到这里。Jev 模式会将消息发送给 TypeSafe 和 DeepSeek；候选不会发送到微信。",
            wraplength=740,
        ).pack(anchor="w", pady=(6, 12))

        editor = ttk.LabelFrame(outer, text="对方最新消息", padding=8)
        editor.pack(fill="x")
        self.message_text = tk.Text(editor, height=5, wrap="word", font=("Microsoft YaHei UI", 10))
        self.message_text.pack(fill="x")
        controls = ttk.Frame(editor)
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="粘贴剪贴板", command=self.paste_clipboard).pack(side="left")
        self.analyze_button = ttk.Button(controls, text="分析并生成候选", command=self.analyze)
        self.analyze_button.pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="多轮回放", command=self.open_replay).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="悬浮气泡", command=self.open_bubble).pack(side="left", padx=(8, 0))
        self.topmost = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="窗口置顶", variable=self.topmost, command=self.set_topmost).pack(side="right")

        mode_frame = ttk.LabelFrame(outer, text="分析模式", padding=8)
        mode_frame.pack(fill="x", pady=(10, 0))
        self.mode_var = tk.StringVar(value=PIPELINE_LABELS["llm-only"])
        ttk.Combobox(mode_frame, textvariable=self.mode_var, values=list(PIPELINE_LABELS.values()),
                     state="readonly", width=42).pack(anchor="w")
        ttk.Label(mode_frame, text="Jev 模式使用固定版本 jev-1.13.0；排序模式会额外调用一次 Jev。所有候选仍由你审核。",
                  wraplength=720).pack(anchor="w", pady=(7, 0))

        facts = ttk.LabelFrame(outer, text="已确认的背景事实（可选，每行一条）", padding=8)
        facts.pack(fill="x", pady=(10, 0))
        self.facts_text = tk.Text(facts, height=2, wrap="word", font=("Microsoft YaHei UI", 9))
        self.facts_text.pack(fill="x")

        style_frame = ttk.LabelFrame(outer, text="回复语气", padding=8)
        style_frame.pack(fill="x", pady=(10, 0))
        self.style_var = tk.StringVar(value="自然短句")
        ttk.Combobox(style_frame, textvariable=self.style_var, values=list(LABEL_TO_KEY), state="readonly", width=30).pack(anchor="w")
        ttk.Label(
            style_frame,
            text="可填你自己发过的短消息，每行一条；选“按我的语气”时至少填 2 条。示例会随分析请求发送给 DeepSeek。",
            wraplength=720,
        ).pack(anchor="w", pady=(7, 3))
        self.samples_text = tk.Text(style_frame, height=3, wrap="word", font=("Microsoft YaHei UI", 9))
        self.samples_text.pack(fill="x")

        self.status = tk.StringVar(value="等待输入。")
        ttk.Label(outer, textvariable=self.status, wraplength=740).pack(anchor="w", pady=(12, 8))

        result_frame = ttk.LabelFrame(outer, text="聊天分析卡片 · 模型判断分数未经校准", padding=10)
        result_frame.pack(fill="both", expand=True)
        self.result_frame = result_frame
        self.delete_card_button = ttk.Button(result_frame, text="删除卡片", command=self.delete_card)
        self.delete_card_button.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.score_labels: dict[str, ttk.Label] = {}
        self.score_name_labels: dict[str, ttk.Label] = {}
        for row, (key, name) in enumerate(DISPLAY_NAMES):
            name_label = ttk.Label(result_frame, text=name + "：", width=24)
            name_label.grid(row=row, column=0, sticky="w", pady=2)
            label = ttk.Label(result_frame, text="—", font=("Microsoft YaHei UI", 10, "bold"))
            label.grid(row=row, column=1, sticky="w", pady=2)
            self.score_name_labels[key] = name_label
            self.score_labels[key] = label
        result_frame.columnconfigure(1, weight=1)
        self.score_note_label = ttk.Label(result_frame, text="", wraplength=710, justify="left")
        self.score_note_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.review_label = ttk.Label(result_frame, text="候选状态：—")
        self.review_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 6))

        self.dynamic_frame = ttk.Frame(result_frame)
        self.dynamic_frame.grid(row=7, column=0, columnspan=2, sticky="ew")

        ttk.Separator(result_frame).grid(row=8, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(result_frame, text="候选回复（点击复制后可在微信里自行粘贴）").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(2, 5)
        )
        self.candidates_frame = ttk.Frame(result_frame)
        self.candidates_frame.grid(row=10, column=0, columnspan=2, sticky="nsew")
        result_frame.rowconfigure(10, weight=1)

        self.root.after(200, self.poll_events)
        if initial_result is not None:
            self.show_result(initial_result)

    def set_topmost(self) -> None:
        self.root.attributes("-topmost", self.topmost.get())

    def open_replay(self) -> None:
        from .replay_desktop import ReplayApp

        ReplayApp(tk.Toplevel(self.root))

    def open_bubble(self) -> None:
        from .bubble import BubbleApp

        if self.bubble_app is None or self.bubble_app.closed:
            self.bubble_app = BubbleApp(tk.Toplevel(self.root))
        self.bubble_app.expand()

    def paste_clipboard(self) -> None:
        try:
            message = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showerror("无法粘贴", "剪贴板中没有可用的文字。")
            return
        if not message.strip():
            messagebox.showerror("无法粘贴", "剪贴板中的文字为空。")
            return
        self.message_text.delete("1.0", "end")
        self.message_text.insert("1.0", message)
        self.status.set("已粘贴。请核对内容，再点击分析。")

    def analyze(self) -> None:
        if self.busy:
            return
        message = self.message_text.get("1.0", "end-1c").strip()
        facts = [line.strip() for line in self.facts_text.get("1.0", "end-1c").splitlines() if line.strip()]
        samples = [line.strip() for line in self.samples_text.get("1.0", "end-1c").splitlines() if line.strip()]
        mode = next(key for key, label in PIPELINE_LABELS.items() if label == self.mode_var.get())
        payload = {"messages": [{"role": "contact", "text": message}], "confirmed_facts": facts,
                   "style_profile": {"preset": LABEL_TO_KEY[self.style_var.get()], "samples": samples}}
        try:
            version = context_version(read_conversation_from_payload(payload))
            if not get_api_key():
                raise ValueError("未找到 DEEPSEEK_API_KEY；请先配置 Windows 用户环境变量或凭据管理器。")
            if mode in {"jev", "jev-rank", "jev-dynamic"} and not get_typesafe_api_key():
                raise ValueError("未找到 TYPESAFE_API_KEY；请先配置 Windows 用户环境变量或凭据管理器。")
            if not gate.acquire(self):
                raise ValueError("另一个 AutoChat 窗口正在分析；请等待或取消。")
        except (ValueError, OSError) as exc:
            gate.release(self)
            messagebox.showerror("无法分析", str(exc))
            return

        try:
            results = ROOT / "results"
            results.mkdir(parents=True, exist_ok=True)
            run_id = uuid.uuid4().hex[:12]
            input_path = results / f"desktop_conversation_{version}_{run_id}.json"
            output_path = results / f"desktop_reply_{mode}_{version}_{run_id}.json"
            self.progress_path = results / f"desktop_progress_{version}_{run_id}.json" if mode == "jev-dynamic" else None
            self.progress_mtime = 0
            self.active_version = version
            self.active_mode = mode
            input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            gate.release(self)
            messagebox.showerror("无法保存分析输入", str(exc))
            return
        self.busy = True
        self.result_frame.pack(fill="both", expand=True)
        self.clear_card_view()
        self.delete_card_button.state(["disabled"])
        self.review_label.configure(text="候选状态：本次分析进行中")
        self.analyze_button.state(["disabled"])
        self.status.set(f"正在运行 {PIPELINE_LABELS[mode]}，请稍候。")
        threading.Thread(target=self.run_pipeline, args=(mode, input_path, output_path, self.progress_path), daemon=True).start()

    def run_pipeline(self, mode: str, input_path: Path, output_path: Path, progress_path: Path | None = None) -> None:
        command = pipeline_command(mode, input_path, output_path)
        if progress_path:
            command += ["--progress-file", str(progress_path)]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                command, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
            )
            stdout, stderr = self.process.communicate(timeout=300)
            if self.process.returncode != 0:
                raise RuntimeError((stderr or stdout).strip() or "分析流程失败")
            self.events.put(("result", output_path))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, subprocess.TimeoutExpired) and self.process is not None:
                self.process.kill()
            self.events.put(("error", str(exc)))
        finally:
            self.process = None

    def poll_events(self) -> None:
        if self.closed:
            return
        if self.busy and self.progress_path and self.progress_path.exists():
            try:
                stamp = self.progress_path.stat().st_mtime_ns
                if stamp != self.progress_mtime:
                    self.progress_mtime = stamp
                    snapshot = json.loads(self.progress_path.read_text(encoding="utf-8"))
                    self.show_dynamic_progress(snapshot)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        try:
            while True:
                kind, value = self.events.get_nowait()
                self.busy = False
                gate.release(self)
                self.analyze_button.state(["!disabled"])
                self.delete_card_button.state(["!disabled"])
                if kind == "result":
                    try:
                        self.show_result(value)
                    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                        self.status.set(f"结果无法显示：{exc}")
                else:
                    self.status.set(f"分析失败：{value}")
        except queue.Empty:
            pass
        self.root.after(200, self.poll_events)

    def show_result(self, path: Path) -> None:
        output = json.loads(path.read_text(encoding="utf-8"))
        if self.active_version and output.get("context_version") != self.active_version:
            self.status.set("结果已过期：上下文版本变化。")
            return
        if self.active_mode and output.get("pipeline_mode") != self.active_mode:
            self.status.set("结果已过期：分析模式变化。")
            return
        if self.active_version and (self.current_context_version() != self.active_version
                                    or self.mode_var.get() != PIPELINE_LABELS[self.active_mode]):
            self.status.set("结果已保存，但消息或分析设置已变化；候选已过期。")
            return
        self.displayed_request_id = output.get("request_id")
        if output.get("pipeline_mode") == "jev-dynamic":
            self.show_dynamic_progress(output)
            if output.get("result") is None:
                self.review_label.configure(text="候选状态：回复生成失败或已取消；微信未发送")
                return
        result = validate_result(output["result"])
        mode = output.get("pipeline_mode", "dual")
        mode_label = PIPELINE_LABELS.get(mode, mode)
        rows, note = display_scorecard(output)
        for (key, _), (name, value) in zip(DISPLAY_NAMES, rows, strict=True):
            self.score_name_labels[key].configure(text=name + "：")
            self.score_labels[key].configure(text=value)
        self.score_note_label.configure(text=note)
        review = "需要人工审核" if result["needs_user_review"] else "可供选择，仍需你确认"
        ranking = output.get("ranking")
        if isinstance(ranking, dict):
            selected = ranking.get("selected_index")
            review += " · Jev 未选出可用候选" if selected is None else f" · Jev 首选第 {selected + 1} 条（待人工审核）"
        self.review_label.configure(text=f"候选状态：{review} · 微信未发送")
        for child in self.candidates_frame.winfo_children():
            child.destroy()
        for index, candidate in enumerate(result["candidates"], 1):
            row = ttk.Frame(self.candidates_frame)
            row.pack(fill="x", pady=4)
            text = f"{index}. {candidate['style']}：{candidate['text']}"
            ttk.Label(row, text=text, wraplength=620, justify="left").pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="复制", command=lambda value=candidate["text"]: self.copy_candidate(value)).pack(side="right")
        self.status.set(f"{mode_label} 分析完成。结果保存在 {path.name}；未发送微信消息。")

    def clear_card_view(self) -> None:
        for label in self.score_labels.values():
            label.configure(text="—")
        self.score_note_label.configure(text="")
        for child in self.candidates_frame.winfo_children():
            child.destroy()
        for child in self.dynamic_frame.winfo_children():
            child.destroy()
        self.displayed_request_id = None

    def delete_card(self) -> None:
        if self.busy:
            return
        self.clear_card_view()
        self.result_frame.pack_forget()
        self.review_label.configure(text="候选状态：卡片已删除")
        self.status.set("当前聊天分析卡片已删除；输入和本地结果文件仍保留。")

    def show_dynamic_progress(self, output: dict) -> None:
        for child in self.dynamic_frame.winfo_children():
            child.destroy()
        render_dynamic_card(self.dynamic_frame, output, wraplength=680)
        stages = output["dynamic_analysis"]["stages"]
        if stages["propose"]["status"] == "running":
            self.status.set("正在提出可能解释…")
        elif stages["judge"]["status"] == "running":
            self.status.set("正在由 Jev 判断原文支持度…")
        elif stages["reply"]["status"] == "running":
            self.status.set("Jev 判断已可查看；DS 正在按 Jev 方向撰写措辞…")
        elif stages["reply"]["status"] == "failed":
            self.status.set("回复生成失败；已完成的分析已保留。")

    def copy_candidate(self, value: str) -> None:
        if self.active_version and (self.current_context_version() != self.active_version
                                    or self.mode_var.get() != PIPELINE_LABELS[self.active_mode]):
            self.status.set("候选已过期：消息或分析设置已变化，请重新分析。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.status.set("候选已复制。请在微信中核对联系人和文字后自行发送。")

    def current_context_version(self) -> str | None:
        payload = {"messages": [{"role": "contact", "text": self.message_text.get("1.0", "end-1c").strip()}],
                   "confirmed_facts": [x.strip() for x in self.facts_text.get("1.0", "end-1c").splitlines() if x.strip()],
                   "style_profile": {"preset": LABEL_TO_KEY[self.style_var.get()],
                                     "samples": [x.strip() for x in self.samples_text.get("1.0", "end-1c").splitlines() if x.strip()]}}
        try:
            return context_version(read_conversation_from_payload(payload))
        except (OSError, ValueError, KeyError):
            return None

    def close(self) -> None:
        self.closed = True
        gate.release(self)
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self.root.destroy()


def read_conversation_from_payload(payload: dict) -> dict:
    """Apply the same input rules as the CLI before starting a worker."""
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        path = Path(directory) / "input.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return read_conversation(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, help="启动时展示已有结果，不调用模型")
    args = parser.parse_args()
    root = tk.Tk()
    DesktopApp(root, args.result)
    root.mainloop()


if __name__ == "__main__":
    main()
