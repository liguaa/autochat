"""UI-independent state and CLI orchestration for the new desktop workbench."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from .analysis_gate import gate
from .cli import context_version, validate_replay_detail, validate_result
from .credentials import get_api_key, get_typesafe_api_key
from .dynamic import config_fingerprint
from .personas import PRESETS
from .persona_files import load_personas, persona_directory
from .replay import ReplaySession
from .session_store import SessionStore


ROOT = (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AutoChat"
        if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1])
MODES = {"jev-dynamic", "llm-only", "jev", "jev-rank"}


class UIService:
    """Owns sessions, request identity and result validity for every Qt view."""

    def __init__(self, emit: Callable[[dict], None] | None = None, results_dir: Path | None = None,
                 personas_dir: Path | None = None, sessions_dir: Path | None = None):
        self.emit = emit or (lambda _event: None)
        self.results_dir = results_dir or ROOT / "results"
        self.lock = threading.RLock()
        self.session_store = SessionStore(sessions_dir if sessions_dir is not None else
                                         ((results_dir / "sessions") if results_dir is not None else ROOT / "data" / "sessions"))
        self.sessions, self.storage_load_errors = self.session_store.load()
        self.storage_save_errors: dict[str, str] = {}
        self.selected_id = next(iter(self.sessions)) if self.sessions else self._new_session("本地会话 1")
        self.mode = "jev-dynamic"
        self.style = "natural"
        self.samples: list[str] = []
        self.personas_dir = personas_dir if personas_dir is not None else persona_directory()
        self.personas, self.persona_errors = load_personas(self.personas_dir)
        self.active: dict | None = None
        self.process: subprocess.Popen | None = None
        self.config_revision = 0
        self.operations: dict[str, OrderedDict[str, tuple[float, dict, dict]]] = {}
        self.ending = False
        self.sequence = 0
        self.status = "粘贴一段对话，从原文开始。"
        self._save_sessions()

    def _save_sessions(self) -> None:
        for sid, (name, session) in self.sessions.items():
            try:
                self.session_store.save(sid, name, session)
                self.storage_save_errors.pop(sid, None)
            except (OSError, ValueError) as exc:
                self.storage_save_errors[sid] = f"{name}：保存失败，{exc}"

    def _new_session(self, name: str) -> str:
        name = name.strip()
        if not 1 <= len(name) <= 40 or any(row[0] == name for row in self.sessions.values()):
            raise ValueError("会话名称应为 1 到 40 字且不能重复")
        session_id = uuid.uuid4().hex[:12]
        self.sessions[session_id] = (name, ReplaySession())
        return session_id

    def _session(self, session_id: str | None = None) -> ReplaySession:
        key = session_id or self.selected_id
        if key not in self.sessions:
            raise ValueError("本地会话不存在")
        return self.sessions[key][1]

    def _data(self, session: ReplaySession) -> dict:
        profile = {"preset": self.style, "samples": self.samples}
        if self.style.startswith("skill:"):
            if self.style not in self.personas:
                raise ValueError("所选人格文件不可用，请修复并重新加载，或选择其他人格")
            profile["skill"] = self.personas[self.style]
        return session.analysis_data(confirmed_facts=session.confirmed_facts,
                                     style_profile=profile)

    def snapshot(self) -> dict:
        with self.lock:
            session = self._session()
            try:
                current_version = context_version(self._data(session))
            except ValueError:
                current_version = None
            cards = {}
            for index, card in session.cards.items():
                cards[session.message_ids[index]] = {key: value for key, value in card.items() if key != "path"}
            active = self.active
            return {"schema_version": 2, "sequence": self.sequence, "selected_id": self.selected_id,
                    "session_revision": session.revision, "config_revision": self.config_revision,
                    "sessions": [{"id": sid, "name": name, "count": len(item.active_visible_messages)}
                                 for sid, (name, item) in self.sessions.items()],
                    "messages": session.display_messages(),
                    "active_message_count": len(session.active_visible_messages),
                    "ending": self.ending,
                    "cards": cards, "context_version": current_version,
                    "confirmed_facts": list(session.confirmed_facts),
                    "facts_pending_review": session.facts_pending_review,
                    "facts_reviewed_indices": sorted(session.reviewed_fact_indices),
                    "mode": self.mode, "style": self.style, "samples": list(self.samples),
                    "active": {key: active[key] for key in ("session_id", "index", "message_id", "request_id", "context_version", "mode")}
                    if active else None,
                    "status": self.status,
                    "storage_location": str(self.results_dir),
                    "session_storage_location": str(self.session_store.root),
                    "session_storage_errors": [*self.storage_load_errors, *self.storage_save_errors.values()],
                    "session_storage_pending": bool(self.storage_save_errors),
                    "credentials": {"deepseek": bool(get_api_key()), "typesafe": bool(get_typesafe_api_key())},
                    "persona_directory": str(self.personas_dir),
                    "persona_errors": list(self.persona_errors),
                    "persona_preview": dict(self.personas[self.style]) if self.style in self.personas else None,
                    "styles": ([{"id": key, "label": value["label"]} for key, value in PRESETS.items()]
                               + [{"id": key, "label": value["label"] + " · 文件人格"}
                                  for key, value in self.personas.items()]
                               + ([{"id": self.style, "label": self.style + "（不可用）"}]
                                  if self.style.startswith("skill:") and self.style not in self.personas else []))}

    def _event(self, kind: str, payload: dict | None = None, request: dict | None = None,
               session_id: str | None = None) -> None:
        if kind in {"state.changed", "message.recalled", "message.restored", "session.deleted"}:
            self._save_sessions()
        self.sequence += 1
        target_id = request["session_id"] if request else (session_id or self.selected_id)
        event = {"schema_version": 2, "sequence": self.sequence, "type": kind,
                 "session_id": target_id,
                 "session_revision": self._session(target_id).revision if target_id in self.sessions else None,
                 "config_revision": self.config_revision,
                 "context_version": request["context_version"] if request else None,
                 "request_id": request["request_id"] if request else None,
                 "config_fingerprint": request["config_fingerprint"] if request else None,
                 "payload": payload or {}}
        self.emit(event)

    def dispatch(self, action: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        with self.lock:
            changed_session: str | None = None
            if action == "session.create":
                self.selected_id = self._new_session(str(payload.get("name", "")))
                self.status = "已建立本地会话。"
            elif action == "session.select":
                session_id = str(payload.get("session_id", ""))
                self._session(session_id)
                self.selected_id = session_id
                self.status = "已切换本地会话。"
            elif action == "sessions.save":
                if payload:
                    raise ValueError("重试保存不接受参数")
                self.status = "已重试保存，请检查存储状态。"
            elif action == "session.delete":
                if set(payload) != {"session_id", "expected_revision"}:
                    raise ValueError("删除会话参数无效")
                sid = payload["session_id"]
                if not isinstance(sid, str):
                    raise ValueError("会话 ID 无效")
                session = self._required_session(sid)
                if type(payload["expected_revision"]) is not int or payload["expected_revision"] != session.revision:
                    raise ValueError("会话已变化，请刷新后再删除")
                self.session_store.save(sid, self.sessions[sid][0], session)
                self.session_store.archive(sid)
                self._invalidate_running(sid)
                del self.sessions[sid]
                self.operations.pop(sid, None)
                self.storage_save_errors.pop(sid, None)
                if self.selected_id == sid:
                    self.selected_id = next(iter(self.sessions)) if self.sessions else self._new_session("本地会话 1")
                self.status = "会话已移至回收目录；历史分析结果文件仍保留。"
            elif action == "message.append":
                changed_session = str(payload.get("session_id", ""))
                session = self._session(changed_session)
                role = payload.get("role")
                if role not in {"contact", "me"}:
                    raise ValueError("说话方无效")
                if role == "me" and payload.get("confirmed_sent") is not True:
                    raise ValueError("请确认这段文字已经由你实际发送")
                session.append(role, str(payload.get("text", "")))
                session.cards.clear()
                session.latest_requests.clear()
                self._invalidate_running(changed_session)
                self.status = "消息已加入；分析需要你主动点击。" if role == "contact" else "已记录你确认发出的文字。"
            elif action in {"message.recall", "message.restore"}:
                return self._change_message(action, payload)
            elif action == "personas.reload":
                if payload:
                    raise ValueError("重新加载人格不接受路径或其他参数")
                previous = self.personas.get(self.style)
                self.personas, self.persona_errors = load_personas(self.personas_dir)
                if previous != self.personas.get(self.style):
                    self.config_revision += 1
                    for sid, (_, item) in self.sessions.items():
                        item.cards.clear()
                        item.latest_requests.clear()
                        self._invalidate_running(sid)
                self.status = (f"已加载 {len(self.personas)} 个文件人格。"
                               + ("部分文件有错误，请查看设置。" if self.persona_errors else ""))
            elif action == "preferences.update":
                allowed = {"mode", "style", "samples", "confirmed_facts"}
                if not set(payload) <= allowed:
                    raise ValueError("包含不支持的设置")
                mode = payload.get("mode", self.mode)
                style = payload.get("style", self.style)
                samples = payload.get("samples", self.samples)
                facts = payload.get("confirmed_facts", self._session().confirmed_facts)
                if mode not in MODES or (style not in PRESETS and style not in self.personas):
                    raise ValueError("分析模式或回复偏好无效")
                if not isinstance(samples, list) or len(samples) > 6 or any(not isinstance(x, str) or not x.strip() or len(x) > 150 for x in samples):
                    raise ValueError("本人语气示例格式无效")
                if style == "mine" and len(samples) < 2:
                    raise ValueError("按我的语气至少需要 2 条本人消息示例")
                if not isinstance(facts, list) or len(facts) > 20 or any(not isinstance(x, str) or not x.strip() or len(x) > 500 for x in facts):
                    raise ValueError("确认事实格式无效")
                session = self._session()
                config_changed = (mode, style, samples) != (self.mode, self.style, self.samples)
                facts_changed = facts != session.confirmed_facts
                if config_changed:
                    self.config_revision += 1
                    for sid, (_, item) in self.sessions.items():
                        item.cards.clear()
                        item.latest_requests.clear()
                        self._invalidate_running(sid)
                if facts_changed:
                    session.confirmed_facts = list(facts)
                    session.facts_pending_review = False
                    session.invalidate_context()
                    session.facts_pending_review = False
                    session.reviewed_fact_indices.clear()
                    self._invalidate_running(self.selected_id)
                self.mode, self.style, self.samples = mode, style, list(samples)
                self.status = "设置已更新；旧候选如受影响会标为过期。"
            elif action == "facts.confirm":
                changed_session = str(payload.get("session_id", ""))
                session = self._required_session(changed_session)
                if payload.get("expected_revision") != session.revision:
                    raise ValueError("会话已变化，请刷新后重试")
                if session.facts_pending_review:
                    index = payload.get("index")
                    if index is not None and (type(index) is not int or not 0 <= index < len(session.confirmed_facts)):
                        raise ValueError("事实编号无效")
                    if index is None:
                        session.reviewed_fact_indices = set(range(len(session.confirmed_facts)))
                    else:
                        session.reviewed_fact_indices.add(index)
                    session.facts_pending_review = len(session.reviewed_fact_indices) < len(session.confirmed_facts)
                    session.cards.clear()
                    session.latest_requests.clear()
                    session.revision += 1
                    self._invalidate_running(changed_session)
                self.status = "事实已复核，重新分析后生效。"
            elif action == "analysis.start":
                return self._start(payload)
            elif action == "analysis.cancel":
                self._cancel(str(payload.get("request_id", "")))
            elif action == "candidate.copy":
                return self._copy(payload)
            elif action == "message.copy":
                session = self._required_session(str(payload.get("session_id", "")))
                message_id = str(payload.get("message_id", ""))
                index = session.visible_index(message_id)
                if message_id in session.revoked:
                    raise ValueError("记录已撤回，请刷新界面")
                return {"text": session.messages[index]["text"], "message": "原文已复制。"}
            elif action == "card.dismiss":
                changed_session = str(payload.get("session_id", ""))
                session = self._required_session(changed_session)
                index = (session.visible_index(str(payload["message_id"])) if "message_id" in payload
                         else int(payload.get("index", -1)))
                session.delete_card(index)
                self.status = "分析卡片已从界面移除；本地结果文件仍保留。"
            else:
                raise ValueError("未知动作")
            self._event("state.changed", session_id=changed_session)
            return self.snapshot()

    def _required_session(self, session_id: str) -> ReplaySession:
        if not session_id or session_id not in self.sessions:
            raise ValueError("本地会话不存在，请刷新界面")
        return self.sessions[session_id][1]

    def _invalidate_running(self, session_id: str) -> bool:
        request = self.active
        if not request or request["session_id"] != session_id:
            return False
        request["cancelled"] = True
        self.active = None
        self.ending = True
        if self.process:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
            except OSError:
                pass
        return True

    def _change_message(self, action: str, payload: dict) -> dict:
        allowed = {"session_id", "message_id", "expected_revision", "operation_id"}
        if action == "message.restore":
            allowed.add("recall_id")
        if set(payload) != allowed or any(not isinstance(payload[key], str) or not payload[key]
                                          for key in ("session_id", "message_id", "operation_id")):
            raise ValueError("撤回操作参数无效")
        sid = payload["session_id"]
        session = self._required_session(sid)
        operation_id = payload["operation_id"]
        history = self.operations.setdefault(sid, OrderedDict())
        now = time.monotonic()
        for old_id, (created, _, _) in list(history.items()):
            if now - created >= 86400:
                del history[old_id]
        signature = {"action": action, **payload}
        if operation_id in history:
            _, old_signature, receipt = history[operation_id]
            if old_signature != signature:
                raise ValueError("操作编号已用于其他请求")
            return receipt
        if type(payload["expected_revision"]) is not int or payload["expected_revision"] != session.revision:
            raise ValueError("会话已变化，请刷新后重试")
        message_id = payload["message_id"]
        if action == "message.recall":
            index, recall_id, affected = session.recall(message_id)
        else:
            if not isinstance(payload["recall_id"], str):
                raise ValueError("恢复编号无效")
            index, affected = session.restore(message_id, payload["recall_id"])
            recall_id = payload["recall_id"]
        cancelled = self._invalidate_running(sid)
        self.status = ("已撤回这条记录，不再用于后续分析。" if action == "message.recall"
                       else "记录已恢复，请重新分析。")
        self._event("message.recalled" if action == "message.recall" else "message.restored",
                    {"message_id": message_id, "recall_id": recall_id}, session_id=sid)
        self._event("state.changed", session_id=sid)
        receipt = {"operation_id": operation_id, "session_id": sid, "message_id": message_id,
                   "index": index, "revision": session.revision, "recall_id": recall_id,
                   "affected_cards": affected, "cancel_requested": cancelled}
        history[operation_id] = (now, signature, receipt)
        while len(history) > 128:
            history.popitem(last=False)
        return receipt

    def _start(self, payload: dict) -> dict:
        if self.ending:
            raise ValueError("上一任务正在结束，请稍后再分析")
        if self.active is not None:
            raise ValueError("已有分析正在进行，请等待或取消")
        session_id = str(payload.get("session_id", ""))
        if session_id != self.selected_id:
            raise ValueError("会话已经切换")
        session = self._session(session_id)
        index = session.last_active_index
        if index is None:
            raise ValueError("当前没有可分析的消息")
        if session.messages[index]["role"] != "contact":
            raise ValueError("加入新的对方消息后再分析")
        data = self._data(session)
        version = context_version(data)
        if payload.get("context_version") != version:
            raise ValueError("对话或设置已变化，请重新检查")
        if not get_api_key():
            raise ValueError("未配置 DeepSeek 密钥")
        if self.mode != "llm-only" and not get_typesafe_api_key():
            raise ValueError("未配置 TypeSafe 密钥")
        if not gate.acquire(self):
            raise ValueError("另一个 AutoChat 窗口正在分析；请等待或取消")
        source_ids = [session.message_ids[i] for i in range(session.cursor)
                      if session.message_ids[i] not in session.revoked]
        request = {"session_id": session_id, "index": index,
                   "message_id": session.message_ids[index],
                   "source_message_ids": {f"m{i:03d}": mid for i, mid in enumerate(source_ids, 1)},
                   "session_revision": session.revision, "config_revision": self.config_revision,
                   "context_version": version, "mode": self.mode,
                   "request_id": uuid.uuid4().hex,
                   "config_fingerprint": config_fingerprint(data, thinking="disabled", replay_detail=True)
                   if self.mode == "jev-dynamic" else None, "data": data, "cancelled": False}
        try:
            session.begin_request(request["index"], data, request["request_id"])
            self.results_dir.mkdir(parents=True, exist_ok=True)
            stem = f"workbench_{session_id}_{session.cursor}_{self.mode}_{version}_{request['request_id'][:10]}"
            request["input"] = self.results_dir / f"{stem}_input.json"
            request["output"] = self.results_dir / f"{stem}_reply.json"
            request["progress"] = self.results_dir / f"{stem}_progress.json" if self.mode == "jev-dynamic" else None
            request["input"].write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            gate.release(self)
            raise
        self.active = request
        self.status = "正在整理可能解释…" if self.mode == "jev-dynamic" else "正在分析…"
        self._event("analysis.started", request=request)
        threading.Thread(target=self._run, args=(request,), daemon=True).start()
        return self.snapshot()

    def _run(self, request: dict) -> None:
        entry = [sys.executable, "--worker-cli"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "autochat.cli"]
        command = [*entry, "--mode", request["mode"],
                   "--thinking", "disabled", "--replay-detail", "--request-id", request["request_id"],
                   "--input", str(request["input"]),
                   "--output", str(request["output"])]
        if request["progress"]:
            command += ["--progress-file", str(request["progress"])]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        stamp = 0
        try:
            proc = subprocess.Popen(command, cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=flags)
            with self.lock:
                self.process = proc
                if request["cancelled"]:
                    proc.terminate()
            while proc.poll() is None:
                try:
                    progress = request["progress"]
                    if progress and progress.exists():
                        current_stamp = progress.stat().st_mtime_ns
                        if current_stamp != stamp:
                            stamp = current_stamp
                            self._accept_progress(request, json.loads(progress.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
                try:
                    proc.wait(timeout=0.15)
                except subprocess.TimeoutExpired:
                    pass
            stdout, stderr = proc.communicate()
            if request["cancelled"]:
                return
            if proc.returncode != 0:
                raise RuntimeError((stderr or stdout).decode("utf-8", errors="replace").strip() or "分析失败")
            output = json.loads(request["output"].read_text(encoding="utf-8"))
            self._accept_result(request, output)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            self._accept_error(request, str(exc))
        finally:
            for path in (request["input"], request["progress"]):
                if path:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            with self.lock:
                if self.active is request:
                    self.active = None
                if self.process is locals().get("proc"):
                    self.process = None
                if request["cancelled"]:
                    self.ending = False
                gate.release(self)
                self._event("state.changed")

    def _identity_ok(self, request: dict, output: dict) -> bool:
        session = self._session(request["session_id"])
        if (session.revision != request.get("session_revision", session.revision)
                or self.config_revision != request.get("config_revision", self.config_revision)
                or session.message_ids[request["index"]] != request.get("message_id", session.message_ids[request["index"]])
                or session.latest_requests.get(request["index"]) != request["request_id"]):
            return False
        if output.get("context_version") != request["context_version"] or output.get("pipeline_mode") != request["mode"]:
            return False
        if output.get("request_id") != request["request_id"]:
            return False
        if request["mode"] == "jev-dynamic":
            return output.get("config_fingerprint") == request["config_fingerprint"]
        return True

    def _accept_progress(self, request: dict, output: dict) -> None:
        with self.lock:
            if self.active is not request or request["cancelled"] or not self._identity_ok(request, output):
                return
            self._session(request["session_id"]).cards[request["index"]] = {
                "state": "progress", "context_version": request["context_version"], "output": output,
                "request_token": request["request_id"],
                "source_message_ids": request.get("source_message_ids", {}),
                "session_revision": request.get("session_revision"),
                "config_revision": request.get("config_revision"),
                "message_id": request.get("message_id")}
            stages = (output.get("dynamic_analysis") or {}).get("stages") or {}
            if stages.get("judge", {}).get("status") == "complete":
                self.status = "原文依据已核对；正在写回复…"
            elif stages.get("judge", {}).get("status") == "running":
                self.status = "正在核对原文依据…"
            self._event("analysis.progress", {"output": output}, request)

    def _accept_result(self, request: dict, output: dict) -> None:
        with self.lock:
            if self.active is not request or request["cancelled"] or not self._identity_ok(request, output):
                return
            if output.get("result") is not None:
                validate_result(output["result"])
                validate_replay_detail(output["result"])
            elif request["mode"] != "jev-dynamic":
                raise ValueError("结果缺少回复")
            session = self._session(request["session_id"])
            session.record_result(request["index"], request["data"], output,
                                  request["output"], request["request_id"])
            session.cards[request["index"]].update(
                source_message_ids=request.get("source_message_ids", {}),
                session_revision=request.get("session_revision", session.revision),
                config_revision=request.get("config_revision", self.config_revision),
                message_id=request.get("message_id", session.message_ids[request["index"]]))
            self.status = "分析已完成；候选仍需核对，尚未发送。"
            self._event("analysis.completed", {"output": output}, request)

    def _accept_error(self, request: dict, message: str) -> None:
        with self.lock:
            if self.active is not request or request["cancelled"] or not self._identity_ok(request, {
                "context_version": request["context_version"], "pipeline_mode": request["mode"],
                "request_id": request["request_id"], "config_fingerprint": request["config_fingerprint"]}):
                return
            session = self._session(request["session_id"])
            session.record_error(request["index"], request["data"], message, request["request_id"])
            if request["index"] in session.cards:
                session.cards[request["index"]].update(
                    session_revision=request.get("session_revision"),
                    config_revision=request.get("config_revision"),
                    message_id=request.get("message_id"),
                    source_message_ids=request.get("source_message_ids", {}))
            self.status = "分析失败：" + message
            self._event("analysis.error", {"message": message}, request)

    def _cancel(self, request_id: str) -> None:
        if not self.active or self.active["request_id"] != request_id:
            raise ValueError("没有匹配的分析任务")
        request = self.active
        request["cancelled"] = True
        session = self._session(request["session_id"])
        session.latest_requests.pop(request["index"], None)
        session.cards.pop(request["index"], None)
        self.active = None
        self.ending = True
        if self.process:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
            except OSError:
                pass
        self.status = "已取消；已发给服务商的请求及费用可能无法撤销。"
        self._event("analysis.cancelled", request=request)

    def _copy(self, payload: dict) -> dict:
        session_id = str(payload.get("session_id", ""))
        if session_id != self.selected_id:
            raise ValueError("会话已切换，候选不可复制")
        session = self._session(session_id)
        index = (session.visible_index(str(payload["message_id"])) if "message_id" in payload
                 else int(payload.get("index", -1)))
        card = session.cards.get(index)
        if not card or card.get("state") != "complete" or index != session.last_active_index:
            raise ValueError("候选已过期，请重新分析")
        if (session.message_ids[index] in session.revoked
                or card.get("session_revision", session.revision) != session.revision
                or card.get("config_revision", self.config_revision) != self.config_revision
                or ("session_revision" in card and payload.get("session_revision") != session.revision)
                or ("config_revision" in card and payload.get("config_revision") != self.config_revision)
                or ("message_id" in card and payload.get("message_id") != session.message_ids[index])
                or (card.get("request_token") and session.latest_requests.get(index) != card["request_token"])):
            raise ValueError("候选已过期，请重新分析")
        if card["context_version"] != context_version(self._data(session)) or card["output"].get("pipeline_mode") != self.mode:
            raise ValueError("对话或设置已变化，请重新分析")
        if (payload.get("request_id") != card["output"].get("request_id")
                or payload.get("context_version") != card["context_version"]
                or payload.get("mode") != self.mode):
            raise ValueError("候选身份或版本不匹配，请重新分析")
        candidates = (card["output"].get("result") or {}).get("candidates") or []
        candidate_index = int(payload.get("candidate_index", -1))
        if not 0 <= candidate_index < len(candidates):
            raise ValueError("候选不存在")
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("回复草稿必须是 1 到 4000 字符")
        return {"text": text, "message": "已复制，去微信粘贴并核对后发送。"}

    def close(self) -> None:
        with self.lock:
            if self.active:
                self._cancel(self.active["request_id"])
            self._save_sessions()
