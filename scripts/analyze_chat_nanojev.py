"""Produce experimental NanoJev signals for one pasted conversation.

The public game checkpoint is not validated for Chinese chat. This command
records its raw distributions; it does not authorize a reply or send anything.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluate_emotion import LABELS
from evaluate_nanojev import INTENTS, ROOT, load_upstream

from autochat.cli import context_version, read_conversation


def make_payload(data: dict) -> dict:
    lines = [f"{'我' if item['role'] == 'me' else '对方'}：{item['text']}" for item in data["messages"]]
    state = "聊天记录（按时间顺序）：\n" + "\n".join(lines)
    return {"states": [{
        "id": "conversation",
        "state": state,
        "questions": {
            "emotion": {
                "type": "choice",
                "instructions": "判断对方最新消息表达的最主要情绪。只选一个；普通事实陈述选平静。",
                "criteria": LABELS,
            },
            "intent": {
                "type": "choice",
                "instructions": "根据对方最新消息，选择最主要的沟通意图；有歧义时选其他。",
                "criteria": INTENTS,
            },
            "needs_reply": {
                "type": "boolean",
                "instructions": "对方的最新消息是否需要我继续回复？",
                "criteria": {"true": "需要我继续回复。", "false": "可以自然结束或等待。"},
            },
            "risk": {
                "type": "boolean",
                "instructions": "这条消息是否包含钱款、隐私、重要承诺、人身安全或明显歧义等需要人工核对的风险？",
                "criteria": {"true": "存在需要人工核对的风险。", "false": "没有明显需要人工核对的风险。"},
            },
        },
    }]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "nanojev_chat_analysis.json")
    args = parser.parse_args()
    data = read_conversation(args.input)
    upstream = load_upstream()
    payload = make_payload(data)
    upstream.validate_request(payload)
    response = upstream.DecisionPredictor(args.checkpoint_dir).predict(payload, batch_questions=1)
    answers = response["states"][0]["answers"]
    result = {
        "context_version": context_version(data),
        "model": "NanoJev",
        "chat_domain_validated": False,
        "note": "Game-trained checkpoint; signals are experimental and uncalibrated.",
        "signals": {
            "emotion": {"value": answers["emotion"]["choice"], "scores": answers["emotion"]["probabilities"]},
            "intent": {"value": answers["intent"]["choice"], "scores": answers["intent"]["probabilities"]},
            "needs_reply": {"value": answers["needs_reply"]["value"], "p_true": answers["needs_reply"]["p_true"]},
            "risk": {"value": answers["risk"]["value"], "p_true": answers["risk"]["p_true"]},
        },
        "execution": response["execution"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "context_version": result["context_version"],
                      "chat_domain_validated": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
