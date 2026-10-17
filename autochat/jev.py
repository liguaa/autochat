"""Pinned TypeSafe Jev decisions for Chinese chat reply experiments."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request

from .credentials import get_typesafe_api_key


MODEL = "jev-1.13.0"
QUESTION_VERSION = "chat-zh-v1"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class JevCallError(RuntimeError):
    def __init__(self, message: str, request_count: int) -> None:
        super().__init__(message)
        self.request_count = request_count


class JevResponseError(ValueError):
    def __init__(self, message: str, request_count: int) -> None:
        super().__init__(message)
        self.request_count = request_count

INTENTS = {
    "提问": "向对方询问信息或征求答案，不含具体帮助请求",
    "请求帮助": "请求对方提供帮助、资源、行动或承诺",
    "邀约": "邀请对方见面、参加活动或共同做事",
    "倾诉": "主要分享困扰、感受或经历，未明确请求行动",
    "确认": "确认已知信息、收到内容或表示同意",
    "拒绝": "拒绝请求、邀约或提议",
    "结束话题": "道别或结束当前交流",
    "信息分享": "分享普通事实、近况或消息，没有明确问题",
    "其他或不明": "无法可靠归入上述类别，含反讽、歧义或信息不足",
}
EMOTIONS = {
    "平静或中性": "没有明确的强烈情绪",
    "开心": "明确表达高兴、期待、满足",
    "难过": "明确表达悲伤、失落、疲惫或无助",
    "焦虑": "明确表达担心、紧张或焦虑",
    "生气": "明确表达愤怒、不满或抱怨",
    "复杂或不明": "混合情绪、反讽或信息不足以判断",
}


def analysis_payload(data: dict) -> dict:
    return {
        "state": {"messages": data["messages"], "confirmed_facts": data["confirmed_facts"]},
        "model": MODEL,
        "questions": {
            "intent": {"type": "choice", "instructions": "仅判断最后一条 contact 消息的主要交际意图。以前文消歧；若不确定，选其他或不明。", "criteria": INTENTS},
            "emotion": {"type": "choice", "instructions": "仅判断最后一条 contact 消息明确体现的主要情绪；不要从单纯提问猜测情绪。", "criteria": EMOTIONS},
            "needs_reply": {"type": "noul", "instructions": "最后一条 contact 消息是否明确要求答复，或在当前上下文中适合继续接话？这不表示允许自动发送。", "criteria": {"true": "提问、请求、待回应的倾诉或明显继续对话", "false": "自然结束、无需回应或信息不足"}},
            "urgent": {"type": "choice", "instructions": "最后一条消息要求多快回应？仅依据明确时限或紧急情境；情绪强烈本身不等于紧急。", "criteria": {"普通": "没有明确时限或迫切情境", "较急": "明确希望尽快处理，但并非立即", "立即关注": "存在迫在眉睫的人身风险或明确的立即时限", "信息不足": "无法从文字判断"}},
            "risk_safety": {"type": "noul", "instructions": "最后一条消息是否明确涉及人身安全、自伤、伤害或可能需要核实的身体受伤？"},
            "risk_money": {"type": "noul", "instructions": "最后一条消息是否明确涉及转账、借款、付款、财务损失或钱款承诺？"},
            "risk_privacy": {"type": "noul", "instructions": "最后一条消息是否请求或披露他人的私人联系方式、身份信息、账户或其他隐私？"},
            "risk_commitment": {"type": "noul", "instructions": "最后一条消息是否要求回复者作出重要日程、合同、权限或其他无法从已确认事实保证的承诺？"},
            "ambiguous": {"type": "noul", "instructions": "最后一条消息是否有会明显影响回复的指代、反讽或含义歧义，且前文不足以消歧？"},
        },
    }


def rank_payload(data: dict, candidates: list[dict]) -> dict:
    if not 2 <= len(candidates) <= 3:
        raise ValueError("Jev 排序需要 2 到 3 条候选")
    choices = {str(i): f"候选 {i + 1}" for i in range(len(candidates))}
    choices["none"] = "所有候选均不适合，应由用户重写或重新生成"
    questions = {
        "preferred": {"type": "choice", "instructions": "根据最后一条 contact 消息及已确认事实，哪条回复最自然、贴切且无事实或越权承诺问题？若都不合适，选 none。", "criteria": choices},
    }
    for i in range(len(candidates)):
        questions[f"usable_{i}"] = {"type": "noul", "instructions": f"候选 {i + 1} 能否在用户人工确认后作为给最后一条 contact 消息的回复？若编造事实、越权承诺、泄露隐私或语气明显不当，回答否。"}
    return {
        "state": {"messages": data["messages"], "confirmed_facts": data["confirmed_facts"], "candidates": [item["text"] for item in candidates]},
        "model": MODEL,
        "questions": questions,
    }


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value <= 1 or not math.isfinite(float(value)):
        raise ValueError("Jev 概率格式无效")
    return float(value)


def validate_answers(payload: dict, body: dict) -> None:
    if not isinstance(body, dict) or body.get("model") != payload["model"] or not isinstance(body.get("answers"), dict):
        raise ValueError("Jev 响应模型版本或 answers 无效")
    if set(body["answers"]) != set(payload["questions"]):
        raise ValueError("Jev 响应缺少判断题")
    for name, question in payload["questions"].items():
        answer = body["answers"][name]
        kind = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise ValueError(f"Jev {name} 类型无效")
        if kind == "noul":
            _number(answer.get("noul"))
        elif kind == "choice":
            criteria = question["criteria"]
            probabilities = answer.get("probabilities")
            if (not isinstance(answer.get("choice"), str) or answer["choice"] not in criteria
                    or not isinstance(probabilities, dict)
                    or set(probabilities) != set(criteria)):
                raise ValueError(f"Jev {name} 选项无效")
            if abs(sum(_number(value) for value in probabilities.values()) - 1) > 0.01:
                raise ValueError(f"Jev {name} 概率和无效")
            _number(answer.get("confidence"))
        elif kind == "score":
            criteria = question["criteria"]
            keys = {str(i) for i in range(len(criteria))}
            score = answer.get("score")
            probabilities = answer.get("probabilities")
            if (isinstance(score, bool) or not isinstance(score, (int, float))
                    or not 0 <= score <= len(criteria) - 1 or not math.isfinite(float(score))
                    or not isinstance(probabilities, dict)
                    or {str(k) for k in probabilities} != keys):
                raise ValueError(f"Jev {name} 量表无效")
            if abs(sum(_number(value) for value in probabilities.values()) - 1) > 0.01:
                raise ValueError(f"Jev {name} 概率和无效")
            weighted_score = sum(int(key) * _number(value) for key, value in probabilities.items())
            if abs(score - weighted_score) > 0.02:
                raise ValueError(f"Jev {name} 分数与概率分布不一致")
            _number(answer.get("confidence"))
            if "legend" in answer:
                legend = answer["legend"]
                if not isinstance(legend, dict) or {str(k): v for k, v in legend.items()} != {str(i): v for i, v in enumerate(criteria)}:
                    raise ValueError(f"Jev {name} 量表说明不一致")
        else:
            raise ValueError(f"Jev {name} 未知题型")


def call_jev(payload: dict) -> dict:
    key = get_typesafe_api_key()
    if not key:
        raise RuntimeError("缺少 TypeSafe API 密钥；请设置 TYPESAFE_API_KEY 或使用凭据管理器")
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    total_start = time.perf_counter()
    for attempt in range(3):
        try:
            start = time.perf_counter()
            with urllib.request.urlopen(request, timeout=45) as response:
                body = json.load(response)
            elapsed = time.perf_counter() - start
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise JevCallError(f"TypeSafe API 返回 HTTP {exc.code}", attempt + 1) from None
        except urllib.error.URLError as exc:
            raise JevCallError(f"TypeSafe API 连接失败：{exc.reason}", attempt + 1) from None
    try:
        validate_answers(payload, body)
    except ValueError as exc:
        raise JevResponseError(str(exc), attempt + 1) from None
    return {"model": body["model"], "question_version": QUESTION_VERSION,
            "answers": body["answers"], "usage": body.get("usage", {}),
            "latency_seconds": round(time.perf_counter() - total_start, 3), "request_count": attempt + 1}


def summarize_analysis(response: dict) -> dict:
    answers = response["answers"]
    risk = {name.removeprefix("risk_"): answers[name]["noul"] for name in ("risk_safety", "risk_money", "risk_privacy", "risk_commitment")}
    return {"intent": answers["intent"]["choice"], "emotion": answers["emotion"]["choice"],
            "needs_reply": answers["needs_reply"]["noul"], "urgency": answers["urgent"]["choice"],
            "risk": risk, "ambiguous": answers["ambiguous"]["noul"],
            "chat_domain_validated": False}


def summarize_ranking(response: dict, candidate_count: int) -> dict:
    answers = response["answers"]
    usable = [answers[f"usable_{i}"]["noul"] for i in range(candidate_count)]
    choice = answers["preferred"]["choice"]
    selected = int(choice) if choice != "none" and usable[int(choice)] >= 0.5 else None
    return {"selected_index": selected, "all_rejected": selected is None,
            "relative_choice": choice, "relative_probabilities": answers["preferred"]["probabilities"],
            "usable_probabilities": usable, "chat_domain_validated": False}
