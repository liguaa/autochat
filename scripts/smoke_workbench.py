"""Opt-in live smoke of the new UI service using a synthetic message."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from autochat.ui_service import UIService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call DeepSeek with synthetic text")
    args = parser.parse_args()
    if not args.live:
        parser.error("Add --live to authorize the synthetic DeepSeek call")
    with TemporaryDirectory(prefix="autochat-smoke-") as directory:
        service = UIService(sessions_dir=Path(directory))
        try:
            return run_smoke(service)
        finally:
            service.close()


def run_smoke(service: UIService) -> int:
    service.dispatch("preferences.update", {"mode": "llm-only"})
    sid = service.selected_id
    service.dispatch("message.append", {"session_id": sid, "role": "contact",
                                        "text": "今天有点累，先不聊工作了。"})
    service.dispatch("analysis.start", {"session_id": sid,
                                        "context_version": service.snapshot()["context_version"]})
    deadline = time.monotonic() + 120
    while service.active and time.monotonic() < deadline:
        time.sleep(.2)
    if service.active:
        service.close()
        print(json.dumps({"state": "timeout"}))
        return 1
    first_message_id = service.snapshot()["messages"][0]["id"]
    card = service.snapshot()["cards"].get(first_message_id, {})
    output = card.get("output") or {}
    print(json.dumps({"state": card.get("state"),
                      "candidate_count": len((output.get("result") or {}).get("candidates") or []),
                      "pipeline_mode": output.get("pipeline_mode"),
                      "send_status": output.get("send_status"),
                      "error": card.get("message")}, ensure_ascii=False))
    return 0 if card.get("state") == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
