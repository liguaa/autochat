"""Run a small, synthetic Chinese chat smoke evaluation against upstream NanoJev.

This is a compatibility check, not a calibrated model-quality benchmark.
No contact data is loaded or sent to a remote service.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / ".vendor" / "NanoJev" / "scripts" / "predict_toy_decisions.py"
INTENTS = {
    "倾诉": "对方在表达自己的感受或遭遇，重点是被倾听。",
    "提问": "对方在询问信息或答案。",
    "请求帮助": "对方在请求我做一件事或提供帮助。",
    "邀约": "对方邀请我参加某件活动或见面。",
    "确认": "对方确认已经收到、理解或同意前文。",
    "拒绝": "对方明确拒绝提议或请求。",
    "结束话题": "对方以道别等方式结束本轮聊天。",
    "其他": "消息语义不明确，或不属于以上主要意图。",
}


def load_upstream():
    if not UPSTREAM.is_file():
        raise FileNotFoundError(f"NanoJev source missing: {UPSTREAM}")
    spec = importlib.util.spec_from_file_location("nanojev_predict", UPSTREAM)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load NanoJev predictor")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_request(cases):
    states = []
    for case in cases:
        context = case["context"].strip() or "（无前文）"
        states.append({
            "id": case["id"],
            "state": f"前文：{context}\n对方最新消息：{case['message']}",
            "questions": {
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
        })
    return {"states": states}


def summarize(cases, response, elapsed):
    by_id = {s["id"]: s["answers"] for s in response["states"]}
    rows = []
    for case in cases:
        answers = by_id[case["id"]]
        rows.append({
            "id": case["id"],
            "expected": {k: case[k] for k in ("intent", "needs_reply", "risk")},
            "predicted": {
                "intent": answers["intent"]["choice"],
                "needs_reply": answers["needs_reply"]["value"],
                "risk": answers["risk"]["value"],
            },
            "scores": {
                "intent": answers["intent"]["probabilities"],
                "needs_reply": answers["needs_reply"]["p_true"],
                "risk": answers["risk"]["p_true"],
            },
        })
    accuracy = {
        key: sum(row["expected"][key] == row["predicted"][key] for row in rows) / len(rows)
        for key in ("intent", "needs_reply", "risk")
    }
    return {
        "notice": "Synthetic smoke set only; scores are uncalibrated and cannot authorize auto-send.",
        "checkpoint": response["checkpoint"],
        "execution": response["execution"],
        "elapsed_seconds": round(elapsed, 3),
        "case_count": len(rows),
        "accuracy": accuracy,
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "data" / "synthetic_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "nanojev_smoke.json")
    parser.add_argument("--batch-questions", type=int, default=1)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a nonempty array")
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("case ids must be unique")
    upstream = load_upstream()
    payload = make_request(cases)
    upstream.validate_request(payload)
    start = time.perf_counter()
    predictor = upstream.DecisionPredictor(args.checkpoint_dir)
    response = predictor.predict(payload, batch_questions=args.batch_questions)
    result = summarize(cases, response, time.perf_counter() - start)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "accuracy": result["accuracy"], "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
