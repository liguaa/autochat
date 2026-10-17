"""Pure session, request and window-position state for the manual bubble UI."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from .cli import context_version
from .replay import ReplaySession


@dataclass(frozen=True)
class Request:
    session_id: str
    index: int
    context_version: str
    mode: str
    token: str


def clamp_position(x: int, y: int, width: int, height: int,
                   workareas: list[tuple[int, int, int, int]]) -> tuple[int, int]:
    """Keep the whole bubble visible in the nearest monitor work area."""
    if not workareas:
        return x, y
    def distance(area: tuple[int, int, int, int]) -> int:
        left, top, right, bottom = area
        dx = max(left - x, 0, x - (right - width))
        dy = max(top - y, 0, y - (bottom - height))
        return dx * dx + dy * dy
    left, top, right, bottom = min(workareas, key=distance)
    return max(left, min(x, right - width)), max(top, min(y, bottom - height))


class BubbleState:
    def __init__(self) -> None:
        self.sessions: dict[str, ReplaySession] = {}
        self.session_ids: dict[str, str] = {}
        self.current_name = "本地会话 1"
        self.add_session(self.current_name)
        self.active: Request | None = None

    @property
    def session(self) -> ReplaySession:
        return self.sessions[self.current_name]

    def add_session(self, name: str) -> None:
        name = name.strip()
        if not name or len(name) > 40 or name in self.sessions:
            raise ValueError("会话名称应为 1 到 40 字且不能重复")
        self.sessions[name] = ReplaySession()
        self.session_ids[name] = uuid.uuid4().hex[:12]
        self.current_name = name

    def select_session(self, name: str) -> None:
        if name not in self.sessions:
            raise ValueError("本地会话不存在")
        self.current_name = name

    def begin(self, data: dict, mode: str) -> Request:
        index = self.session.cursor - 1
        token = uuid.uuid4().hex
        self.session.begin_request(index, data, token)
        self.active = Request(self.session_ids[self.current_name], index,
                              context_version(data), mode, token)
        return self.active

    def matches(self, request: Request, data: dict, mode: str) -> bool:
        return (self.active == request and self.session_ids[self.current_name] == request.session_id
                and self.session.cursor - 1 == request.index
                and context_version(data) == request.context_version and mode == request.mode)

    def cancel(self) -> None:
        self.active = None

    def session_for(self, request: Request) -> ReplaySession:
        for name, session_id in self.session_ids.items():
            if session_id == request.session_id:
                return self.sessions[name]
        raise ValueError("请求所属会话不存在")
