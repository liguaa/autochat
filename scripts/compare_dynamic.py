"""Run or inspect A/B/C modes on one explicitly supplied conversation.

Only --run makes API calls. This script does not select private results automatically.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path


MODES = ("llm-only", "jev", "jev-dynamic")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--run", action="store_true", help="对指定文件逐个调用真实 API")
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error("输入文件不存在")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    run_id = uuid.uuid4().hex[:10]
    for mode in MODES:
        path = args.output_dir / f"comparison_{args.input.stem}_{run_id}_{mode}.json"
        command = [sys.executable, "-m", "autochat.cli", "--input", str(args.input), "--mode", mode,
                   "--thinking", "disabled", "--replay-detail", "--output", str(path)]
        if args.run:
            subprocess.run(command, check=True)
        if path.exists():
            output = json.loads(path.read_text(encoding="utf-8"))
            stages = (output.get("dynamic_analysis") or {}).get("stages")
            rows.append({"mode": mode, "request_id": output.get("request_id"),
                         "context_version": output.get("context_version"),
                         "hypotheses": len(output.get("subtext_hypotheses", [])),
                         "action": (output.get("result") or {}).get("action"),
                         "candidates": len((output.get("result") or {}).get("candidates", [])),
                         "total_latency_seconds": output.get("dynamic_analysis", {}).get("total_latency_seconds")
                         if stages else (output.get("jev") or {}).get("latency_seconds", 0) + output.get("latency_seconds", 0),
                         "stage_status": {key: value["status"] for key, value in stages.items()} if stages else None,
                         "path": str(path)})
        else:
            rows.append({"mode": mode, "command": command})
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
