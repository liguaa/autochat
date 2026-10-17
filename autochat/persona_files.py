"""Declarative persona Markdown files. No tools, includes or code execution."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

MAX_BYTES = 24_000
MAX_BODY = 8_000
NAME = re.compile(r"[a-z][a-z0-9-]{0,47}")


def persona_directory() -> Path:
    if os.environ.get("AUTOCHAT_PERSONAS_DIR"):
        return Path(os.environ["AUTOCHAT_PERSONAS_DIR"]).expanduser().resolve()
    base = (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AutoChat"
            if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1])
    return base / "personas"


def skill_digest(skill: dict) -> str:
    return hashlib.sha256(json.dumps({key: skill[key] for key in
        ("name", "label", "description", "instructions")}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_skill_snapshot(value: object) -> dict:
    fields = {"name", "label", "description", "instructions", "sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("人格内容快照格式无效")
    limits = {"name": 48, "label": 60, "description": 300, "instructions": MAX_BODY, "sha256": 64}
    if any(not isinstance(value[k], str) or not value[k].strip() or len(value[k]) > limit
           for k, limit in limits.items()):
        raise ValueError("人格字段为空或超出长度限制")
    if not NAME.fullmatch(value["name"]) or value["sha256"] != skill_digest(value):
        raise ValueError("人格名称或内容哈希无效")
    return dict(value)


def read_skill(path: Path, root: Path) -> dict:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("人格文件不能指向目录之外")
    with resolved.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("人格文件不能超过 24000 字节")
    lines = raw.decode("utf-8-sig").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("缺少 --- 元信息头")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("元信息头缺少结束 ---")
    meta = {}
    for line in lines[1:end]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep or key not in {"name", "description", "label"} or key in meta:
            raise ValueError("元信息仅支持 name、label、description，且不能重复")
        value = value.strip()
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith(("'", "|", ">", "[", "{", "&", "*", "!")):
            raise ValueError("元信息请使用单行文本或双引号 JSON 字符串")
        meta[key] = value
    if not {"name", "description"} <= set(meta):
        raise ValueError("元信息必须包含 name 和 description")
    if meta["name"] != path.parent.name:
        raise ValueError("name 必须与人格文件夹名称一致")
    skill = {"name": meta["name"], "label": meta.get("label", meta["name"]),
             "description": meta["description"], "instructions": "\n".join(lines[end + 1:]).strip()}
    skill["sha256"] = skill_digest(skill)
    return validate_skill_snapshot(skill)


def load_personas(root: Path | None = None) -> tuple[dict[str, dict], list[str]]:
    root = root or persona_directory()
    skills, errors = {}, []
    try:
        paths = sorted(root.glob("*/SKILL.md"))
        for path in paths:
            try:
                skill = read_skill(path, root)
                skills[f"skill:{skill['name']}"] = skill
            except (OSError, ValueError, TypeError, UnicodeError) as exc:
                errors.append(f"{path.parent.name}/SKILL.md：{exc}")
    except OSError as exc:
        errors.append(f"无法读取人格目录：{exc}")
    return skills, errors
