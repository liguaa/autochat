"""Run a Jev contract smoke test or a paired, human-reviewable reply comparison.

The bundled synthetic cases are suitable only for smoke testing. For a quality
comparison, pass a separate JSON list of authorized, de-identified conversations.
Nothing in this script sends a WeChat message.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from autochat.cli import build_request, call_deepseek, context_version, read_conversation
from autochat.jev import analysis_payload, call_jev, rank_payload, summarize_analysis, summarize_ranking


ROOT = Path(__file__).resolve().parents[1]


def load_cases(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("评测输入必须是非空 JSON 数组")
    cases = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 条样例不是对象")
        if "messages" in item:
            data = {key: item[key] for key in ("messages", "confirmed_facts", "style_profile") if key in item}
        else:
            data = {"messages": []}
            if item.get("context"):
                data["messages"].append({"role": "me", "text": item["context"]})
            data["messages"].append({"role": "contact", "text": item["message"]})
        # Reuse the production parser for identical input and style validation.
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            temporary = Path(directory) / "case.json"
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            normalized = read_conversation(temporary)
        cases.append({"id": str(item.get("id", index + 1)), "data": normalized,
                      "expected": {k: item[k] for k in ("intent", "needs_reply", "risk") if k in item}})
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("样例 id 必须唯一")
    return cases


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 3)


def smoke(cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        response = call_jev(analysis_payload(case["data"]))
        summary = summarize_analysis(response)
        expected = case["expected"]
        risk_flag = any(value >= 0.5 for value in summary["risk"].values())
        row = {"id": case["id"], "context_version": context_version(case["data"]),
               "expected": expected, "predicted": summary,
               "matches": {"intent": summary["intent"] == expected["intent"]} if "intent" in expected else {},
               "model": response["model"], "question_version": response["question_version"],
               "usage": response["usage"], "latency_seconds": response["latency_seconds"],
               "raw_answers": response["answers"]}
        if "needs_reply" in expected:
            row["matches"]["needs_reply"] = (summary["needs_reply"] >= 0.5) == expected["needs_reply"]
        if "risk" in expected:
            row["matches"]["risk"] = risk_flag == expected["risk"]
        rows.append(row)
        print(f"{case['id']}: intent={summary['intent']} reply={summary['needs_reply']:.2f} risk={risk_flag}", flush=True)
    return rows


def compare(cases: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    rows = []
    blind = []
    key = []
    rng = random.Random(20260929)
    for case in cases:
        data = case["data"]
        jev = call_jev(analysis_payload(data))
        a = call_deepseek(build_request(data, thinking="disabled"))
        decision = summarize_analysis(jev)
        b = call_deepseek(build_request(data, jev_analysis=decision, thinking="disabled"))
        if any(value >= 0.5 for value in decision["risk"].values()) or decision["ambiguous"] >= 0.5:
            b["result"]["needs_user_review"] = True
        rank = None
        if len(b["result"]["candidates"]) >= 2:
            rank_response = call_jev(rank_payload(data, b["result"]["candidates"]))
            rank = {**summarize_ranking(rank_response, len(b["result"]["candidates"])),
                    "model": rank_response["model"], "usage": rank_response["usage"],
                    "latency_seconds": rank_response["latency_seconds"]}
        rows.append({"id": case["id"], "context_version": context_version(data),
                     "model": "deepseek-flash", "thinking": "disabled", "jev": jev,
                     "A": a, "B": b, "C_ranking_on_B": rank, "send_status": "not_sent"})
        options = [("A", a["result"]["candidates"]), ("B", b["result"]["candidates"])]
        rng.shuffle(options)
        blind.append({"id": case["id"], "message": data["messages"][-1]["text"],
                      "sets": [{"slot": index + 1, "candidates": candidates} for index, (_, candidates) in enumerate(options)]})
        key.append({"id": case["id"], "slot_to_group": {str(index + 1): group for index, (group, _) in enumerate(options)}})
        print(f"{case['id']}: A/B/C complete", flush=True)
    return rows, blind, key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "synthetic_cases.json")
    parser.add_argument("--phase", choices=["smoke", "compare"], default="smoke")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "jev_evaluation.json")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", help="逗号分隔的样例 id，用于定向接口验证")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.input)
        if args.ids:
            requested = args.ids.split(",")
            cases = [case for case in cases if case["id"] in requested]
            if {case["id"] for case in cases} != set(requested):
                raise ValueError("--ids 中有不存在的样例 id")
        if args.limit is not None:
            if args.limit < 1:
                raise ValueError("--limit 必须大于 0")
            cases = cases[:args.limit]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.phase == "smoke":
            rows = smoke(cases)
            dimensions = ("intent", "needs_reply", "risk")
            summary = {name: {"correct": sum(row["matches"].get(name) is True for row in rows),
                              "total": sum(name in row["matches"] for row in rows)} for name in dimensions}
            result = {"phase": "synthetic_smoke", "source": str(args.input), "sample_count": len(rows),
                      "summary": summary, "latency_p50": percentile([row["latency_seconds"] for row in rows], 0.5),
                      "latency_p95": percentile([row["latency_seconds"] for row in rows], 0.95), "rows": rows}
        else:
            rows, blind, key = compare(cases)
            a_times = [r["A"]["latency_seconds"] for r in rows]
            b_times = [r["jev"]["latency_seconds"] + r["B"]["latency_seconds"] for r in rows]
            c_times = [b_times[i] + (r["C_ranking_on_B"] or {}).get("latency_seconds", 0) for i, r in enumerate(rows)]
            def tokens(usage_list: list[dict], field: str) -> int:
                return sum(usage.get(field, 0) for usage in usage_list)
            deepseek_a = [r["A"]["usage"] for r in rows]
            deepseek_b = [r["B"]["usage"] for r in rows]
            jev_b = [r["jev"]["usage"] for r in rows]
            jev_c = [r["C_ranking_on_B"]["usage"] for r in rows if r["C_ranking_on_B"]]
            result = {"phase": "paired_comparison", "source": str(args.input), "sample_count": len(rows),
                      "latency_seconds": {name: {"p50": percentile(times, 0.5), "p95": percentile(times, 0.95)}
                                          for name, times in (("A", a_times), ("B", b_times), ("C", c_times))},
                      "tokens": {"A_deepseek": tokens(deepseek_a, "total_tokens"),
                                 "B_deepseek": tokens(deepseek_b, "total_tokens"),
                                 "B_jev_input": tokens(jev_b, "input_tokens"),
                                 "C_extra_jev_input": tokens(jev_c, "input_tokens")},
                      "rows": rows}
            args.output.with_name(args.output.stem + "_blind.json").write_text(json.dumps(blind, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            args.output.with_name(args.output.stem + "_blind_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "phase": result["phase"], "sample_count": len(rows),
                          "summary": result.get("summary")}, ensure_ascii=False))
        return 0
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
