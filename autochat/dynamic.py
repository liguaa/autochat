"""DeepSeek proposes; Jev judges; DeepSeek only writes the chosen reply direction."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Callable

from . import cli
from .hypotheses import (PROMPT_VERSION, QUESTION_VERSION, TENSION_VERSION,
                         dynamic_payload, judged_hypotheses, proposal_request, validate_hypotheses)
from .jev import MODEL, QUESTION_VERSION as FIXED_VERSION, analysis_payload, call_jev
from .jev_reply import (QUESTION_VERSION as REPLY_DIRECTION_VERSION, attach_reply_direction,
                        result_from_jev, validate_wording, wording_brief, wording_request)

SCHEMA_VERSION = "jev-dynamic-v2.3"
REPLY_PROMPT_VERSION = "jev-grounded-wording-v4"


def config_fingerprint(data: dict, *, thinking: str, replay_detail: bool) -> str:
    settings = {"context_version": cli.context_version(data), "mode": "jev-dynamic",
                "thinking": thinking, "replay_detail": replay_detail, "schema": SCHEMA_VERSION,
                "proposal": PROMPT_VERSION, "fixed": FIXED_VERSION, "dynamic": QUESTION_VERSION,
                "tension": TENSION_VERSION, "reply": REPLY_PROMPT_VERSION,
                "reply_direction": REPLY_DIRECTION_VERSION}
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:20]


def _stage() -> dict:
    return {"status": "pending", "latency_seconds": None, "usage": None, "attempt_usages": [],
            "request_count": 0, "model": None, "error": None}


def run_dynamic(data: dict, *, thinking: str = "disabled", replay_detail: bool = False,
                progress: Callable[[dict], None] | None = None,
                cancelled: Callable[[], bool] | None = None,
                propose_call: Callable[[dict], dict] | None = None,
                judge_call: Callable[[dict], dict] | None = None,
                reply_call: Callable[..., dict] | None = None,
                request_id: str | None = None) -> dict:
    if thinking not in {"enabled", "disabled"}:
        raise ValueError("thinking 必须是 enabled 或 disabled")
    propose_call = propose_call or cli.call_deepseek_json
    judge_call = judge_call or call_jev
    reply_call = reply_call or cli.call_deepseek_json
    started = time.perf_counter()
    version = cli.context_version(data)
    request_id = request_id or uuid.uuid4().hex
    meta = {"schema_version": SCHEMA_VERSION, "pipeline_mode": "jev-dynamic",
            "context_version": version, "request_id": request_id,
            "config_fingerprint": config_fingerprint(data, thinking=thinking, replay_detail=replay_detail),
            "proposal_model": "deepseek-flash", "judge_model": MODEL, "reply_model": "deepseek-flash",
            "proposal_prompt_version": PROMPT_VERSION, "fixed_question_version": FIXED_VERSION,
            "dynamic_question_version": QUESTION_VERSION, "tension_scale_version": TENSION_VERSION,
            "reply_prompt_version": REPLY_PROMPT_VERSION,
            "reply_direction_question_version": REPLY_DIRECTION_VERSION,
            "subtext_judge": "Jev", "wording_model": "deepseek-flash",
            "reply_receives_jev_direction": True, "reply_receives_jev_scores": True,
            "reply_receives_jev_hypotheses": True, "reply_receives_jev_distributions": False,
            "thinking": thinking, "fallback_reason": None, "discarded_hypotheses": [],
            "relationship_tension_context_status": "unjudged",
            "questions": None, "questions_sha256": None,
            "stages": {name: _stage() for name in ("propose", "judge", "reply")},
            "total_latency_seconds": None}
    output = {"context_version": version, "pipeline_mode": "jev-dynamic", "request_id": request_id,
              "config_fingerprint": meta["config_fingerprint"], "small_model": None,
              "model": "deepseek-flash", "scorecard_status": "experimental_uncalibrated",
              "display_score_source": "DeepSeek", "send_status": "not_sent", "thinking": thinking,
              "jev": None, "ranking": None, "subtext_hypotheses": [], "dynamic_analysis": meta,
              "result": None, "candidates": []}

    def publish() -> None:
        meta["total_latency_seconds"] = round(time.perf_counter() - started, 3)
        if progress:
            progress(json.loads(json.dumps(output, ensure_ascii=False)))

    def abort_if_cancelled() -> bool:
        if cancelled and cancelled():
            meta["fallback_reason"] = "cancelled"
            for stage in meta["stages"].values():
                if stage["status"] == "pending":
                    stage["status"] = "cancelled"
            publish()
            return True
        return False

    if abort_if_cancelled():
        return output
    proposed = []
    proposal_stage = meta["stages"]["propose"]
    proposal_stage["status"] = "running"
    publish()
    for repair in (False, True):
        proposal_stage["request_count"] += 1
        phase_start = time.perf_counter()
        try:
            raw = propose_call(proposal_request(data, thinking, repair))
            proposal_stage["model"] = raw.get("model", "deepseek-flash")
            proposal_stage["usage"] = raw.get("usage", {})
            proposal_stage["attempt_usages"].append(raw.get("usage", {}))
            proposed, rejected = validate_hypotheses(raw["content"], data)
            meta["discarded_hypotheses"].extend(rejected)
            if proposed or (isinstance(raw["content"], dict) and raw["content"].get("hypotheses") == []):
                proposal_stage["status"] = "complete"
                break
            proposal_stage["error"] = "无有效假设"
        except (ValueError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
            proposal_stage["error"] = str(exc)
        finally:
            elapsed = time.perf_counter() - phase_start
            proposal_stage["latency_seconds"] = round((proposal_stage["latency_seconds"] or 0) + elapsed, 3)
        if abort_if_cancelled():
            return output
    if proposal_stage["status"] != "complete":
        proposal_stage["status"] = "failed"
        meta["fallback_reason"] = "proposal_invalid_fixed_jev"
    elif not proposed:
        meta["fallback_reason"] = "no_hypotheses_fixed_jev"
    output["subtext_hypotheses"] = judged_hypotheses(proposed, None)
    publish()

    if abort_if_cancelled():
        return output
    payload = attach_reply_direction(dynamic_payload(data, proposed) if proposed else analysis_payload(data))
    meta["questions"] = payload["questions"]
    meta["questions_sha256"] = hashlib.sha256(json.dumps(payload["questions"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    judge_stage = meta["stages"]["judge"]
    judge_stage["status"] = "running"
    judge_stage["request_count"] = 1
    publish()
    phase_start = time.perf_counter()
    jev = None
    try:
        jev = judge_call(payload)
        judge_stage.update(status="complete", model=jev["model"], usage=jev.get("usage", {}),
                           request_count=jev.get("request_count", 1))
        judge_stage["attempt_usages"].append(jev.get("usage", {}))
        output["jev"] = jev
        output["small_model"] = "Jev"
        output["display_score_source"] = "Jev"
        output["subtext_hypotheses"] = judged_hypotheses(proposed, jev["answers"])
        meta["relationship_tension_context_status"] = (
            "needs_review" if jev["answers"]["ambiguous"]["noul"] >= 0.5
            or any(row["status"] != "judged" for row in output["subtext_hypotheses"])
            else "evaluated")
    except (ValueError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
        jev = None
        judge_stage.update(status="failed", error=str(exc),
                           request_count=getattr(exc, "request_count", 1))
        meta["fallback_reason"] = "jev_failed_no_reply"
    finally:
        judge_stage["latency_seconds"] = round(time.perf_counter() - phase_start, 3)
    publish()

    if abort_if_cancelled():
        return output
    reply_stage = meta["stages"]["reply"]
    if jev is None:
        reply_stage.update(status="failed", error="Jev 判断未完成，不能由 DeepSeek 代判潜台词")
        publish()
        return output
    reply_stage["status"] = "running"
    publish()
    phase_start = time.perf_counter()
    try:
        result = result_from_jev(data, jev, output["subtext_hypotheses"], replay_detail=replay_detail)
        direction = jev["answers"]["reply_direction"]["choice"]
        output["jev_reply_direction"] = {"choice": direction,
            "probabilities": jev["answers"]["reply_direction"]["probabilities"],
            "confidence": jev["answers"]["reply_direction"]["confidence"]}
        if direction != "wait":
            brief = wording_brief(data, jev, output["subtext_hypotheses"])
            output["wording_brief"] = brief
            reply_stage["request_count"] = 1
            raw = reply_call(wording_request(data, direction, thinking, jev_brief=brief))
            result["candidates"] = validate_wording(raw["content"])
            reply_stage.update(model=raw.get("model", "deepseek-flash"), usage=raw.get("usage", {}),
                               request_count=raw.get("request_count", 1))
            reply_stage["attempt_usages"].append(raw.get("usage", {}))
        else:
            reply_stage.update(model="Jev", usage={}, request_count=0)
            output["model"] = "Jev"
        history_guard_applied = cli.enforce_history_guard(data, result)
        result = cli.validate_result(result)
        if replay_detail:
            cli.validate_replay_detail(result)
            cli.ground_replay_detail(data, result)
        output.update(result=result, candidates=result["candidates"],
                      usage=reply_stage["usage"], history_guard_applied=history_guard_applied)
        reply_stage["status"] = "complete"
    except (ValueError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
        reply_stage.update(status="failed", error=str(exc))
        meta["fallback_reason"] = meta["fallback_reason"] or "reply_failed"
    finally:
        reply_stage["latency_seconds"] = round(time.perf_counter() - phase_start, 3)
    publish()
    return output
