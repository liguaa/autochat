"""Atomic local conversation storage, with recoverable deletion and write conflicts."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .cli import normalize_conversation
from .replay import ReplaySession

MAX_BYTES = 8 * 1024 * 1024


def encode_session(sid: str, name: str, session: ReplaySession) -> bytes:
    body = {"schema_version": 1, "id": sid, "name": name,
            "messages": session.messages, "message_ids": session.message_ids,
            "cursor": session.cursor, "revoked": session.revoked, "revision": session.revision,
            "confirmed_facts": session.confirmed_facts,
            "facts_pending_review": session.facts_pending_review,
            "reviewed_fact_indices": sorted(session.reviewed_fact_indices)}
    return (json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def decode_session(raw: bytes, sid: str) -> tuple[str, ReplaySession]:
    body = json.loads(raw.decode("utf-8"))
    if not isinstance(body, dict) or body.get("schema_version") != 1 or body.get("id") != sid:
        raise ValueError("会话文件版本或 ID 不匹配")
    name = body.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise ValueError("会话名称无效")
    messages, ids, facts = body.get("messages"), body.get("message_ids"), body.get("confirmed_facts")
    if not isinstance(messages, list) or len(messages) > 500:
        raise ValueError("会话记录数量无效")
    if (not isinstance(ids, list) or len(ids) != len(messages)
            or any(not isinstance(mid, str) or not mid or len(mid) > 100 for mid in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("会话消息 ID 无效")
    # Validate facts even when the session is empty; validate each message without
    # imposing the 30-active-message limit on revoked records retained for restore.
    normalize_conversation({"messages": [{"role": "contact", "text": "validation"}], "confirmed_facts": facts})
    for row in messages:
        normalize_conversation({"messages": [row]}, require_contact=False)
    cursor, revision = body.get("cursor"), body.get("revision")
    if type(cursor) is not int or not 0 <= cursor <= len(messages) or type(revision) is not int or revision < 0:
        raise ValueError("会话位置或修订号无效")
    revoked, reviewed = body.get("revoked"), body.get("reviewed_fact_indices")
    if (not isinstance(revoked, dict) or not set(revoked) <= set(ids[:cursor])
            or any(not isinstance(v, str) or not v or len(v) > 100 for v in revoked.values())):
        raise ValueError("撤回记录无效")
    if (type(body.get("facts_pending_review")) is not bool or not isinstance(reviewed, list)
            or any(type(i) is not int or not 0 <= i < len(facts) for i in reviewed)):
        raise ValueError("事实复核状态无效")
    session = ReplaySession()
    session.messages, session.message_ids = messages, ids
    session.cursor, session.revision = cursor, revision
    session.revoked, session.confirmed_facts = revoked, facts
    session.facts_pending_review = body["facts_pending_review"]
    session.reviewed_fact_indices = set(reviewed)
    if len(session.active_visible_messages) > 30:
        raise ValueError("有效消息超过 30 条")
    # Cards, workers and request tokens never resume automatically after restart.
    return name.strip(), session


class SessionStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.known: dict[str, str] = {}

    def _path(self, sid: str) -> Path:
        if not isinstance(sid, str) or not re.fullmatch(r"[0-9a-f]{12}", sid):
            raise ValueError("会话文件 ID 无效")
        path = self.root / f"{sid}.json"
        if path.resolve().parent != self.root:
            raise ValueError("会话文件不能指向目录之外")
        return path

    @staticmethod
    def _read(path: Path) -> bytes:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("会话文件过大")
        return raw

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".write.lock"
        if lock_path.resolve().parent != self.root:
            raise ValueError("存储锁路径无效")
        with lock_path.open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self) -> tuple[dict[str, tuple[str, ReplaySession]], list[str]]:
        sessions, errors = {}, []
        try:
            paths = sorted(self.root.glob("*.json"))
            for path in paths:
                try:
                    raw = self._read(self._path(path.stem))
                    name, session = decode_session(raw, path.stem)
                    if any(item[0] == name for item in sessions.values()):
                        raise ValueError("会话名称重复，请修复文件")
                    sessions[path.stem] = (name, session)
                    self.known[path.stem] = hashlib.sha256(raw).hexdigest()
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    errors.append(f"{path.name}：{exc}")
        except OSError as exc:
            errors.append(f"读取会话目录失败：{exc}")
        return sessions, errors

    def _check_disk(self, sid: str, path: Path) -> None:
        current = hashlib.sha256(self._read(path)).hexdigest() if path.exists() else None
        if current != self.known.get(sid):
            raise ValueError("会话文件已被其他实例或外部修改；未覆盖，请保留未保存文字并重启")

    def save(self, sid: str, name: str, session: ReplaySession) -> None:
        raw = encode_session(sid, name, session)
        if len(raw) > MAX_BYTES:
            raise ValueError("会话内容超过保存上限")
        digest = hashlib.sha256(raw).hexdigest()
        if digest == self.known.get(sid):
            return
        path = self._path(sid)
        with self._lock():
            self._check_disk(sid, path)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.root, prefix=".session-", suffix=".tmp", delete=False) as handle:
                    temp_path = Path(handle.name)
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
                self.known[sid] = digest
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

    def archive(self, sid: str) -> Path:
        path = self._path(sid)
        with self._lock():
            self._check_disk(sid, path)
            trash = self.root / "trash"
            if trash.resolve().parent != self.root:
                raise ValueError("会话回收目录路径无效")
            trash.mkdir(exist_ok=True)
            target = trash / f"{sid}-{uuid.uuid4().hex}.json"
            os.replace(path, target)
            self.known.pop(sid, None)
            return target
