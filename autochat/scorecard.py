"""Format the displayed percentages from a saved reply result."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cli import validate_result


DISPLAY_NAMES = (
    ("intent", "意图匹配"),
    ("emotion", "情绪匹配"),
    ("reply_need", "需要回复"),
    ("reply_urgency", "回复急切程度"),
    ("risk", "风险程度"),
)

JEV_DISPLAY_NAMES = {
    "intent": "主要意图（该类概率）",
    "emotion": "情绪（该类概率）",
    "reply_need": "回复需求（Noul）",
    "reply_urgency": "急切类别（该类概率）",
    "risk": "风险信号（最高项）",
}
RISK_NAMES = {"safety": "人身安全", "money": "钱款", "privacy": "隐私", "commitment": "重要承诺"}


def _percent(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError("Jev 概率无效")
    return round(value * 100)


def display_scorecard(output: dict) -> tuple[list[tuple[str, str]], str]:
    """Return five labeled rows and a source-specific interpretation note."""
    validated = validate_result(output["result"])
    jev = output.get("jev")
    if output.get("pipeline_mode") in {"jev", "jev-rank", "jev-dynamic"} and isinstance(jev, dict):
        if not isinstance(jev, dict) or not isinstance(jev.get("answers"), dict):
            raise ValueError("Jev 模式缺少原始判断结果")
        answers = jev["answers"]

        def choice_row(key: str) -> str:
            answer = answers[key]
            choice = answer["choice"]
            return f"{choice}  {_percent(answer['probabilities'][choice])}%"

        risks = {key: _percent(answers[f"risk_{key}"]["noul"]) for key in RISK_NAMES}
        top_risk = max(risks, key=risks.get)
        rows = [
            (JEV_DISPLAY_NAMES["intent"], choice_row("intent")),
            (JEV_DISPLAY_NAMES["emotion"], choice_row("emotion")),
            (JEV_DISPLAY_NAMES["reply_need"], f"需要回复  {_percent(answers['needs_reply']['noul'])}%"),
            (JEV_DISPLAY_NAMES["reply_urgency"], choice_row("urgent")),
            (JEV_DISPLAY_NAMES["risk"], f"{RISK_NAMES[top_risk]}  {risks[top_risk]}%"),
        ]
        risk_details = "、".join(f"{RISK_NAMES[key]} {risks[key]}%" for key in RISK_NAMES)
        note = ("来源：Jev 原始输出。类别百分比是给定选项内的相对概率；风险显示四个独立信号中的最高值，"
                f"不是总体风险概率。风险分项：{risk_details}。未经中文聊天校准。")
        return rows, note

    scorecard = validated["scorecard"]
    rows = [(name, f"{scorecard[key]['label']}  {scorecard[key]['score']}%")
            for key, name in DISPLAY_NAMES]
    return rows, "来源：DeepSeek 试验性自报评分，未经统计校准，不等于真实概率。"


def format_scorecard(result: dict) -> str:
    rows, note = display_scorecard(result)
    lines = [f"{name}：{value}" for name, value in rows]
    lines.append(note)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    result = json.loads(args.input.read_text(encoding="utf-8"))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(format_scorecard(result))


if __name__ == "__main__":
    main()
