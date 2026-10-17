"""State and version checks for a manual, stepwise conversation replay."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .cli import context_version, normalize_conversation


class ReplaySession:
    def __init__(self, transcript: dict | None = None) -> None:
        if transcript is None:
            transcript = {"messages": [], "confirmed_facts": []}
        if transcript["messages"]:
            normalized = normalize_conversation(transcript, require_contact=False)
        else:
            normalized = {"messages": [], "confirmed_facts": transcript.get("confirmed_facts", [])}
            if transcript.get("style_profile") is not None:
                normalized["style_profile"] = transcript["style_profile"]
        self.messages: list[dict] = [dict(message) for message in normalized["messages"]]
        self.message_ids: list[str] = [f"msg_{uuid.uuid4().hex}" for _ in self.messages]
        self.revoked: dict[str, str] = {}
        self.revision = 0
        self.facts_pending_review = False
        self.reviewed_fact_indices: set[int] = set()
        self.confirmed_facts: list[str] = list(normalized["confirmed_facts"])
        self.style_profile: dict | None = normalized.get("style_profile")
        self.cursor = 0
        self.cards: dict[int, dict] = {}
        self.dismissed_cards: set[int] = set()
        self.latest_requests: dict[int, str] = {}

    @classmethod
    def from_file(cls, path: Path) -> "ReplaySession":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not raw.get("messages"):
            raise ValueError("回放文件必须包含非空 messages 数组")
        return cls(raw)

    @property
    def visible_messages(self) -> list[dict]:
        return self.messages[:self.cursor]

    @property
    def active_visible_messages(self) -> list[dict]:
        return [row for index, row in enumerate(self.visible_messages)
                if self.message_ids[index] not in self.revoked]

    @property
    def last_active_index(self) -> int | None:
        return next((index for index in range(self.cursor - 1, -1, -1)
                     if self.message_ids[index] not in self.revoked), None)

    @property
    def last_active_message(self) -> dict | None:
        index = self.last_active_index
        return self.messages[index] if index is not None else None

    def visible_index(self, message_id: str) -> int:
        try:
            index = self.message_ids.index(message_id)
        except ValueError:
            raise ValueError("消息不存在或不属于该会话") from None
        if index >= self.cursor:
            raise ValueError("消息尚未回放")
        return index

    def display_messages(self) -> list[dict]:
        return [{"id": self.message_ids[index], "role": row["role"],
                 "state": "revoked", "recall_id": self.revoked[self.message_ids[index]]}
                if self.message_ids[index] in self.revoked else
                {"id": self.message_ids[index], "role": row["role"], "text": row["text"],
                 "state": "active", "recall_id": None}
                for index, row in enumerate(self.visible_messages)]

    def recall(self, message_id: str) -> tuple[int, str, int]:
        index = self.visible_index(message_id)
        if message_id in self.revoked:
            raise ValueError("这条记录已经撤回，请刷新界面")
        recall_id = f"recall_{uuid.uuid4().hex}"
        self.revoked[message_id] = recall_id
        return index, recall_id, self.invalidate_context()

    def restore(self, message_id: str, recall_id: str) -> tuple[int, int]:
        index = self.visible_index(message_id)
        if not recall_id or self.revoked.get(message_id) != recall_id:
            raise ValueError("恢复入口已过期，请刷新界面")
        del self.revoked[message_id]
        return index, self.invalidate_context()

    def invalidate_context(self) -> int:
        affected = len(self.cards)
        self.cards.clear()
        self.latest_requests.clear()
        self.revision += 1
        if self.confirmed_facts:
            self.facts_pending_review = True
            self.reviewed_fact_indices.clear()
        return affected

    @property
    def has_next(self) -> bool:
        return self.cursor < len(self.messages)

    def next_message(self) -> tuple[int, dict]:
        if not self.has_next:
            raise ValueError("没有待回放的消息")
        index = self.cursor
        self.cursor += 1
        self.revision += 1
        return index, self.messages[index]

    def append(self, role: str, message: str) -> tuple[int, dict]:
        if self.has_next:
            raise ValueError("请先回放完已导入的消息，再手动添加")
        candidate = {"role": role, "text": message.strip()}
        normalize_conversation({"messages": [candidate], "confirmed_facts": self.confirmed_facts},
                               require_contact=False)
        if len(self.active_visible_messages) >= 30:
            raise ValueError("有效消息最多 30 条，请先撤回误录记录")
        if len(self.messages) >= 500:
            raise ValueError("本次会话记录已达上限，请建立新会话")
        self.messages.append(candidate)
        self.message_ids.append(f"msg_{uuid.uuid4().hex}")
        return self.next_message()

    def analysis_data(self, *, confirmed_facts: list[str] | None = None,
                      style_profile: dict | None = None) -> dict:
        data = {"messages": self.active_visible_messages,
                "confirmed_facts": self.confirmed_facts if confirmed_facts is None else confirmed_facts}
        if self.facts_pending_review:
            data["confirmed_facts"] = [fact for index, fact in enumerate(self.confirmed_facts)
                                       if index in self.reviewed_fact_indices]
        profile = self.style_profile if style_profile is None else style_profile
        if profile is not None:
            data["style_profile"] = profile
        return normalize_conversation(data)

    def begin_request(self, index: int, data: dict, token: str) -> None:
        if index != self.last_active_index or self.messages[index]["role"] != "contact" or not token:
            raise ValueError("分析请求对应的消息已过期")
        self.latest_requests[index] = token
        self.dismissed_cards.discard(index)

    def delete_card(self, index: int) -> None:
        """Remove the visible analysis card without altering chat or saved files."""
        if index not in self.cards:
            raise ValueError("该消息没有可删除的分析卡片")
        del self.cards[index]
        self.latest_requests.pop(index, None)
        self.dismissed_cards.add(index)

    def record_result(self, index: int, data: dict, output: dict, path: Path,
                      request_token: str | None = None) -> None:
        if index != self.last_active_index or self.messages[index]["role"] != "contact":
            raise ValueError("分析结果对应的消息已过期")
        expected = context_version(data)
        if output.get("context_version") != expected:
            raise ValueError("分析结果与当前上下文版本不一致")
        if request_token is not None and self.latest_requests.get(index) != request_token:
            raise ValueError("分析请求已被更新的请求取代")
        if output.get("pipeline_mode") == "jev-dynamic":
            from .dynamic import config_fingerprint
            if output.get("config_fingerprint") != config_fingerprint(data, thinking="disabled", replay_detail=True):
                raise ValueError("动态分析配置指纹与当前请求不一致")
        self.cards[index] = {"state": "complete", "context_version": expected,
                             "output": output, "path": str(path), "request_token": request_token}

    def record_error(self, index: int, data: dict, message: str, request_token: str | None = None) -> None:
        if request_token is not None and self.latest_requests.get(index) != request_token:
            return
        if index == self.last_active_index and self.messages[index]["role"] == "contact":
            self.cards[index] = {"state": "error", "context_version": context_version(data),
                                 "message": message}

    def is_current(self, index: int, data: dict | None = None) -> bool:
        if index != self.last_active_index or self.messages[index]["role"] != "contact":
            return False
        if data is None:
            return True
        card = self.cards.get(index)
        return card is not None and card["context_version"] == context_version(data)

    def history_notice(self) -> str:
        count = len(self.active_visible_messages)
        if count == 0:
            return "尚未加入消息。"
        if count == 1:
            return "当前只有一条消息，未提供前文；涉及过去约定时请核对原始聊天。"
        return (f"当前分析只使用已回放的 {count} 条有效消息和已确认事实。"
                "未导入的更早聊天记录无法核实。")
