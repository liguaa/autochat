"""Manual multi-turn replay with one analysis card per contact message."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .cli import context_version, validate_replay_detail, validate_result
from .analysis_gate import gate
from .credentials import get_api_key, get_typesafe_api_key
from .dynamic_ui import render_dynamic_card
from .personas import LABEL_TO_KEY, PRESETS
from .replay import ReplaySession
from .scorecard import display_scorecard


ROOT = Path(__file__).resolve().parents[1]
MODES = {"jev": "Jev 判断 + DeepSeek", "llm-only": "仅 DeepSeek", "jev-rank": "Jev 判断 + DeepSeek + Jev 排序",
         "jev-dynamic": "DS 提问 → Jev 判断 → DS 写措辞"}
ROLE_LABELS = {"contact": "对方", "me": "我（确认已发送）"}


class ReplayApp:
    def __init__(self, root: tk.Tk | tk.Toplevel) -> None:
        self.root = root
        self.root.title("微信回复助手 · 多轮回放")
        self.root.geometry("980x780")
        self.root.minsize(700, 540)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.session = ReplaySession()
        self.session_id = uuid.uuid4().hex[:10]
        self.events: queue.Queue[tuple[str, int, dict, object, str]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.busy = False
        self.closed = False
        self.progress_path: Path | None = None
        self.progress_mtime = 0
        self.progress_snapshot: dict | None = None

        canvas = tk.Canvas(root, highlightthickness=0)
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        outer = ttk.Frame(canvas, padding=16)
        canvas_window = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(canvas_window, width=event.width))

        ttk.Label(outer, text="多轮对话回放", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(outer, text="逐条加入双方消息；对方消息会按当前前文生成分析卡片。此窗口不读取或发送微信消息。",
                  wraplength=900).pack(anchor="w", pady=(4, 10))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x")
        self.import_button = ttk.Button(toolbar, text="导入聊天 JSON", command=self.import_file)
        self.import_button.pack(side="left")
        self.next_button = ttk.Button(toolbar, text="下一条并分析", command=self.next_message)
        self.next_button.pack(side="left", padx=8)
        self.progress = ttk.Label(toolbar, text="0 / 0 条")
        self.progress.pack(side="left", padx=8)

        entry = ttk.LabelFrame(outer, text="手动加入下一条消息", padding=8)
        entry.pack(fill="x", pady=(10, 0))
        self.role_var = tk.StringVar(value=ROLE_LABELS["contact"])
        ttk.Combobox(entry, textvariable=self.role_var, values=list(ROLE_LABELS.values()),
                     state="readonly", width=18).pack(anchor="w")
        self.message_text = tk.Text(entry, height=3, wrap="word", font=("Microsoft YaHei UI", 10))
        self.message_text.pack(fill="x", pady=(6, 0))
        entry_controls = ttk.Frame(entry)
        entry_controls.pack(fill="x", pady=(7, 0))
        ttk.Button(entry_controls, text="粘贴剪贴板", command=self.paste_clipboard).pack(side="left")
        self.add_button = ttk.Button(entry_controls, text="加入消息并分析", command=self.add_message)
        self.add_button.pack(side="left", padx=8)
        ttk.Label(entry_controls, text="只有确实发出的本人消息才选“我”；复制候选不会自动加入历史。",
                  wraplength=560).pack(side="left", padx=8)

        settings = ttk.LabelFrame(outer, text="分析设置", padding=8)
        settings.pack(fill="x", pady=(10, 0))
        self.mode_var = tk.StringVar(value=MODES["jev"])
        ttk.Combobox(settings, textvariable=self.mode_var, values=list(MODES.values()),
                     state="readonly", width=40).pack(anchor="w")
        ttk.Label(settings, text="已确认事实（可选，每行一条；不能把模型推测写成事实）").pack(anchor="w", pady=(7, 1))
        self.facts_text = tk.Text(settings, height=2, wrap="word")
        self.facts_text.pack(fill="x")
        ttk.Label(settings, text="回复语气及本人短消息示例（每行一条）").pack(anchor="w", pady=(7, 1))
        self.style_var = tk.StringVar(value=PRESETS["natural"]["label"])
        ttk.Combobox(settings, textvariable=self.style_var, values=list(LABEL_TO_KEY),
                     state="readonly", width=30).pack(anchor="w")
        self.samples_text = tk.Text(settings, height=2, wrap="word")
        self.samples_text.pack(fill="x", pady=(4, 0))

        self.history_label = ttk.Label(outer, text="尚未加入消息。", wraplength=900)
        self.history_label.pack(anchor="w", pady=(10, 3))
        self.status_var = tk.StringVar(value="可导入聊天 JSON，或手动加入消息。")
        ttk.Label(outer, textvariable=self.status_var, wraplength=900).pack(anchor="w", pady=(0, 8))
        self.transcript_frame = ttk.Frame(outer)
        self.transcript_frame.pack(fill="both", expand=True)

        self.root.after(200, self.poll_events)
        self.render()

    def selected_mode(self) -> str:
        return next(key for key, label in MODES.items() if label == self.mode_var.get())

    def current_settings(self) -> tuple[list[str], dict]:
        facts = [line.strip() for line in self.facts_text.get("1.0", "end-1c").splitlines() if line.strip()]
        samples = [line.strip() for line in self.samples_text.get("1.0", "end-1c").splitlines() if line.strip()]
        return facts, {"preset": LABEL_TO_KEY[self.style_var.get()], "samples": samples}

    def analysis_data(self) -> dict:
        facts, profile = self.current_settings()
        return self.session.analysis_data(confirmed_facts=facts, style_profile=profile)

    def import_file(self) -> None:
        if self.busy:
            return
        name = filedialog.askopenfilename(title="选择聊天记录 JSON", filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not name:
            return
        try:
            session = ReplaySession.from_file(Path(name))
            if session.style_profile and session.style_profile["preset"] not in PRESETS:
                raise ValueError("文件人格请使用 Qt 工作台或 CLI；旧版 Tk 回放仅支持内置人格")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            messagebox.showerror("无法导入", str(exc))
            return
        self.session = session
        self.session_id = uuid.uuid4().hex[:10]
        self.facts_text.delete("1.0", "end")
        self.facts_text.insert("1.0", "\n".join(session.confirmed_facts))
        profile = session.style_profile or {"preset": "natural", "samples": []}
        self.style_var.set(PRESETS[profile["preset"]]["label"])
        self.samples_text.delete("1.0", "end")
        self.samples_text.insert("1.0", "\n".join(profile["samples"]))
        self.status_var.set(f"已导入 {len(session.messages)} 条消息；点击“下一条并分析”逐步回放。")
        self.render()

    def next_message(self) -> None:
        if self.busy:
            return
        try:
            _, message = self.session.next_message()
        except ValueError as exc:
            messagebox.showinfo("回放", str(exc))
            return
        self.render()
        if message["role"] == "contact":
            self.analyze_current()
        else:
            self.status_var.set("已加入本人消息；仅将确实发出的文字作为后续上下文。")

    def add_message(self) -> None:
        if self.busy:
            return
        role = next(key for key, label in ROLE_LABELS.items() if label == self.role_var.get())
        text = self.message_text.get("1.0", "end-1c").strip()
        try:
            _, message = self.session.append(role, text)
        except ValueError as exc:
            messagebox.showerror("无法加入", str(exc))
            return
        self.message_text.delete("1.0", "end")
        self.render()
        if message["role"] == "contact":
            self.analyze_current()
        else:
            self.status_var.set("已记录你确认实际发出的消息；下一轮分析会使用这条原文。")

    def paste_clipboard(self) -> None:
        try:
            value = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showerror("无法粘贴", "剪贴板中没有可用文字。")
            return
        self.message_text.delete("1.0", "end")
        self.message_text.insert("1.0", value)

    def analyze_current(self) -> None:
        if self.busy:
            return
        index = self.session.cursor - 1
        try:
            data = self.analysis_data()
            if not get_api_key():
                raise ValueError("未找到 DeepSeek API 密钥。")
            mode = self.selected_mode()
            if mode != "llm-only" and not get_typesafe_api_key():
                raise ValueError("未找到 TypeSafe API 密钥。")
            if not gate.acquire(self):
                raise ValueError("另一个 AutoChat 窗口正在分析；请等待或取消。")
        except (ValueError, OSError) as exc:
            gate.release(self)
            self.status_var.set(f"无法分析第 {index + 1} 条：{exc}")
            self.render()
            return

        try:
            version = context_version(data)
            results = ROOT / "results"
            results.mkdir(parents=True, exist_ok=True)
            stem = f"replay_{self.session_id}_{index + 1}_{mode}_{version}_{uuid.uuid4().hex[:10]}"
            input_path = results / f"{stem}_input.json"
            output_path = results / f"{stem}_reply.json"
            self.session.begin_request(index, data, stem)
            input_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            gate.release(self)
            self.status_var.set(f"无法保存分析输入：{exc}")
            return
        command = [sys.executable, "-m", "autochat.cli", "--mode", mode, "--thinking", "disabled",
                   "--replay-detail", "--input", str(input_path), "--output", str(output_path)]
        self.progress_path = results / f"{stem}_progress.json" if mode == "jev-dynamic" else None
        self.progress_mtime = 0
        self.progress_snapshot = None
        if self.progress_path:
            command += ["--progress-file", str(self.progress_path)]
        self.busy = True
        self.status_var.set(f"正在分析第 {index + 1} 条对方消息（{MODES[mode]}）…")
        self.render()
        threading.Thread(target=self.run_pipeline, args=(index, data, command, output_path, stem), daemon=True).start()

    def run_pipeline(self, index: int, data: dict, command: list[str], output_path: Path, token: str) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(command, cwd=ROOT, env=env, text=True, encoding="utf-8",
                                            errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            creationflags=flags)
            stdout, stderr = self.process.communicate(timeout=300)
            if self.process.returncode != 0:
                raise RuntimeError((stderr or stdout).strip() or "分析失败")
            self.events.put(("complete", index, data, output_path, token))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, subprocess.TimeoutExpired) and self.process is not None:
                self.process.kill()
            self.events.put(("error", index, data, str(exc), token))
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
                    self.progress_snapshot = json.loads(self.progress_path.read_text(encoding="utf-8"))
                    self.render()
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        try:
            while True:
                kind, index, data, value, token = self.events.get_nowait()
                self.busy = False
                gate.release(self)
                if kind == "complete":
                    try:
                        output = json.loads(Path(value).read_text(encoding="utf-8"))
                        if output.get("result") is not None:
                            validate_result(output["result"])
                            validate_replay_detail(output["result"])
                        elif output.get("pipeline_mode") != "jev-dynamic":
                            raise ValueError("结果缺少回复")
                        self.session.record_result(index, data, output, Path(value), request_token=token)
                        self.status_var.set(f"第 {index + 1} 条分析已保存；微信未发送。")
                    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                        self.session.record_error(index, data, str(exc), request_token=token)
                        self.status_var.set(f"第 {index + 1} 条结果无效：{exc}")
                else:
                    self.session.record_error(index, data, str(value), request_token=token)
                    self.status_var.set(f"第 {index + 1} 条 API 失败：{value}。可重试当前消息。")
                self.render()
                self.progress_snapshot = None
        except queue.Empty:
            pass
        self.root.after(200, self.poll_events)

    def render(self) -> None:
        self.progress.configure(text=f"{self.session.cursor} / {len(self.session.messages)} 条")
        self.history_label.configure(text=self.session.history_notice())
        self.next_button.state(["disabled"] if self.busy or not self.session.has_next else ["!disabled"])
        self.import_button.state(["disabled"] if self.busy else ["!disabled"])
        self.add_button.state(["disabled"] if self.busy or self.session.has_next else ["!disabled"])
        for child in self.transcript_frame.winfo_children():
            child.destroy()
        if not self.session.visible_messages:
            ttk.Label(self.transcript_frame, text="还没有消息。可导入 JSON 或手动加入双方消息。", foreground="#666666").pack(anchor="w", pady=8)
            return
        for index, message in enumerate(self.session.visible_messages):
            mine = message["role"] == "me"
            block = ttk.Frame(self.transcript_frame)
            block.pack(fill="x", pady=(8, 0))
            anchor = "e" if mine else "w"
            ttk.Label(block, text=f"{index + 1}. {'我' if mine else '对方'}", foreground="#666666").pack(anchor=anchor)
            bubble = tk.Label(block, text=message["text"], wraplength=730, justify="left",
                              bg="#e8f4e8" if mine else "#f0f0f0", padx=12, pady=9,
                              font=("Microsoft YaHei UI", 10))
            bubble.pack(anchor=anchor, pady=(3, 0))
            if not mine:
                self.render_card(block, index)

    def render_card(self, parent: ttk.Frame, index: int) -> None:
        current = index == self.session.cursor - 1
        if index in self.session.dismissed_cards:
            if current and not self.busy:
                ttk.Button(parent, text="聊天卡片已删除 · 重新分析", command=self.analyze_current).pack(anchor="w", pady=(6, 0))
            return
        frame = ttk.LabelFrame(parent, text=f"聊天分析卡片 · 第 {index + 1} 条对方消息", padding=8)
        frame.pack(fill="x", padx=(0, 32), pady=(7, 0))
        card = self.session.cards.get(index)
        if current and self.busy and self.progress_snapshot:
            render_dynamic_card(frame, self.progress_snapshot, wraplength=800)
            ttk.Label(frame, text="阶段分析进行中；候选尚不可用。", foreground="#9a5b1b").pack(anchor="w")
            return
        if card and not self.busy:
            ttk.Button(frame, text="删除卡片", command=lambda card_index=index: self.delete_card(card_index)).pack(anchor="e")
        if not card:
            status = "分析中…" if current and self.busy else "待分析" if current else "未分析"
            ttk.Label(frame, text=status).pack(anchor="w")
        elif card["state"] == "error":
            ttk.Label(frame, text="API 或解析失败：" + card["message"], foreground="#a3332b",
                      wraplength=820).pack(anchor="w")
        else:
            output = card["output"]
            result = output.get("result")
            stale = not current or output.get("pipeline_mode") != self.selected_mode()
            if current and not stale:
                try:
                    stale = card["context_version"] != context_version(self.analysis_data())
                except ValueError:
                    stale = True
            ttk.Label(frame, text="历史快照 · 候选已过期" if stale else "当前消息分析 · 候选待人工审核",
                      foreground="#9a5b1b" if stale else "#225b38").pack(anchor="w")
            if output.get("history_guard_applied"):
                ttk.Label(frame, text="已启用历史缺口保护：候选改为请求核对，不沿用无依据的记忆或约定。",
                          foreground="#a3332b", wraplength=820).pack(anchor="w", pady=(3, 0))
            if output.get("pipeline_mode") == "jev-dynamic":
                render_dynamic_card(frame, output, wraplength=800)
            if result is None:
                ttk.Label(frame, text="当前无可复制候选；分析结果已保留。", foreground="#a3332b").pack(anchor="w")
                if current and not self.busy:
                    ttk.Button(frame, text="用当前设置重新分析", command=self.analyze_current).pack(anchor="w", pady=(5, 0))
                return
            analysis = result["analysis"]
            analysis_parent = frame
            if output.get("pipeline_mode") == "jev-dynamic":
                analysis_parent = ttk.LabelFrame(frame, text="Jev 判断摘要 · DS 未参与潜台词判断", padding=6)
                analysis_parent.pack(fill="x", pady=(4, 3))
            for key, title in (("literal", "字面意思"), ("subtext", "可能潜台词"),
                               ("need", "对方需求"), ("relationship_tension", "关系紧张"),
                               ("history_gap", "历史缺口")):
                ttk.Label(analysis_parent, text=f"{title}：{analysis[key]}", wraplength=820, justify="left").pack(anchor="w", pady=1)
            if analysis["evidence"]:
                ttk.Label(analysis_parent, text="依据：" + "；".join(analysis["evidence"]), wraplength=820,
                          justify="left").pack(anchor="w", pady=1)
            rows, note = display_scorecard(output)
            for name, value in rows:
                ttk.Label(frame, text=f"{name}：{value}", wraplength=820).pack(anchor="w")
            ttk.Label(frame, text=note, wraplength=820, foreground="#666666", justify="left").pack(anchor="w", pady=(3, 0))
            review = "需人工审核" if result["needs_user_review"] else "仍需你确认"
            ttk.Label(frame, text=f"建议动作：{result['action']} · {review}").pack(anchor="w", pady=(5, 1))
            ranking = output.get("ranking")
            if isinstance(ranking, dict):
                selected = ranking.get("selected_index")
                ttk.Label(frame, text="Jev 排序：全部拒绝" if selected is None else f"Jev 排序：首选第 {selected + 1} 条（仅供参考）").pack(anchor="w")
            candidates_parent = frame
            if output.get("pipeline_mode") == "jev-dynamic":
                candidates_parent = ttk.LabelFrame(frame, text="候选回复 · 仅供人工审核，未发送", padding=6)
                candidates_parent.pack(fill="x", pady=(5, 0))
            for number, candidate in enumerate(result["candidates"], 1):
                row = ttk.Frame(candidates_parent)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=f"{number}. {candidate['style']}：{candidate['text']}", wraplength=690,
                          justify="left").pack(side="left", fill="x", expand=True)
                if not stale:
                    ttk.Button(row, text="复制", command=lambda value=candidate["text"], card_index=index,
                               card_version=card["context_version"], card_mode=output["pipeline_mode"]:
                               self.copy_candidate(value, card_index, card_version, card_mode)).pack(side="right")
        if current and not self.busy:
            ttk.Button(frame, text="分析当前消息" if not card else "用当前设置重新分析",
                       command=self.analyze_current).pack(anchor="w", pady=(5, 0))

    def copy_candidate(self, value: str, index: int, version: str, mode: str) -> None:
        try:
            current_version = context_version(self.analysis_data())
        except ValueError:
            current_version = None
        if index != self.session.cursor - 1 or version != current_version or mode != self.selected_mode():
            self.status_var.set("候选已过期：消息或分析设置已变化，请重新分析当前消息。")
            self.render()
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.status_var.set("候选已复制；它尚未发送，也没有加入对话历史。")

    def delete_card(self, index: int) -> None:
        self.session.delete_card(index)
        self.status_var.set(f"已删除第 {index + 1} 条消息的分析卡片；聊天原文和本地结果文件仍保留。")
        self.render()

    def close(self) -> None:
        self.closed = True
        gate.release(self)
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ReplayApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
