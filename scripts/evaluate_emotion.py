"""Evaluate primary-emotion Choice output on balanced synthetic Chinese messages."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from evaluate_nanojev import ROOT, load_upstream


LABELS = {
    "高兴": "对方主要表现出开心、兴奋、期待或喜悦。",
    "难过": "对方主要表现出悲伤、失落、伤心或低落。",
    "焦虑": "对方主要表现出紧张、担忧、害怕或不安。",
    "生气": "对方主要表现出愤怒、恼火、烦躁或不满。",
    "平静": "对方没有明显强烈情绪，主要是平静陈述事实或确认信息。",
}
ACTIVE_LABELS = [label for label in LABELS if label != "平静"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "data" / "synthetic_emotions.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=["choice", "boolean"], default="choice")
    args = parser.parse_args()
    output = args.output or ROOT / "results" / f"nanojev_emotion_{args.mode}.json"
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("cases must be a nonempty array with unique IDs")
    if any(c["emotion"] not in LABELS for c in cases):
        raise ValueError("unexpected emotion label")
    states = []
    for case in cases:
        if args.mode == "choice":
            questions = {"emotion": {
                "type": "choice",
                "instructions": "判断对方最新消息表达的最主要情绪。只选一个；普通事实陈述选平静。",
                "criteria": LABELS,
            }}
        else:
            questions = {label: {
                "type": "boolean",
                "instructions": f"对方最新消息是否主要表达{label}情绪？",
                "criteria": {"true": LABELS[label], "false": f"没有明显的{label}情绪。"},
            } for label in ACTIVE_LABELS}
        states.append({
            "id": case["id"],
            "state": f"前文：{case['context'] or '（无前文）'}\n对方最新消息：{case['message']}",
            "questions": questions,
        })
    payload = {"states": states}
    upstream = load_upstream()
    upstream.validate_request(payload)
    start = time.perf_counter()
    response = upstream.DecisionPredictor(args.checkpoint_dir).predict(payload, batch_questions=1)
    elapsed = time.perf_counter() - start
    answers = {state["id"]: state["answers"] for state in response["states"]}
    rows = []
    for case in cases:
        if args.mode == "choice":
            answer = answers[case["id"]]["emotion"]
            predicted = answer["choice"]
            scores = answer["probabilities"]
        else:
            scores = {label: answers[case["id"]][label]["p_true"] for label in ACTIVE_LABELS}
            strongest = max(scores, key=scores.get)
            predicted = strongest if scores[strongest] >= 0.5 else "平静"
        rows.append({"id": case["id"], "expected": case["emotion"],
                     "predicted": predicted, "scores": scores})
    result = {
        "notice": "Balanced synthetic smoke set only; probabilities are uncalibrated.",
        "mode": args.mode,
        "case_count": len(rows),
        "accuracy": sum(row["expected"] == row["predicted"] for row in rows) / len(rows),
        "prediction_counts": dict(Counter(row["predicted"] for row in rows)),
        "elapsed_seconds": round(elapsed, 3),
        "execution": response["execution"],
        "cases": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "accuracy": result["accuracy"],
                      "prediction_counts": result["prediction_counts"],
                      "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
