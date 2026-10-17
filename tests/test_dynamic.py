import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autochat.cli import context_version, enforce_history_guard, main
from autochat.dynamic import config_fingerprint, run_dynamic
from autochat.hypotheses import dynamic_payload, judged_hypotheses, validate_hypotheses
from autochat.jev import MODEL, validate_answers
from autochat.jev_reply import result_from_jev, validate_wording, wording_brief, wording_request
from autochat.replay import ReplaySession


DATA = {"messages": [{"role": "contact", "text": "昨天我说了什么？"},
                     {"role": "me", "text": "有点记不清了"},
                     {"role": "contact", "text": "那你说"}], "confirmed_facts": []}


def hypothesis(identifier="h1", quote="那你说", source="m003"):
    return {"id": identifier, "target_message_id": "m003", "hypothesis": "要求复述此前内容",
            "kind": "intent", "evidence_refs": [{"source_type": "message", "source_id": source, "quote": quote}],
            "missing_context": ["此前说过的具体内容"]}


def jev_response(payload, evidence="sufficient", support=0.8):
    answers = {}
    for name, q in payload["questions"].items():
        if q["type"] == "noul":
            answers[name] = {"type": "noul", "noul": support if name.startswith("hyp_") else 0.1}
        elif q["type"] == "choice":
            choice = evidence if name.endswith("_evidence") else next(iter(q["criteria"]))
            answers[name] = {"type": "choice", "choice": choice,
                             "probabilities": {k: float(k == choice) for k in q["criteria"]}, "confidence": 1.0}
        else:
            answers[name] = {"type": "score", "score": 1.0, "confidence": 1.0,
                             "probabilities": {str(i): float(i == 1) for i in range(len(q["criteria"]))},
                             "legend": {str(i): value for i, value in enumerate(q["criteria"])}}
    validate_answers(payload, {"model": MODEL, "answers": answers})
    return {"model": MODEL, "answers": answers, "usage": {"input_tokens": 10}, "request_count": 1}


def reply_result():
    return {"action": "reply", "analysis": {"emotion": "未知", "intent": "核实", "uncertainty": "缺少前文",
            "risk_signals": [], "history_gap": "缺少昨天说过的原话"},
            "scorecard": {name: {"label": "未知", "score": 50, "reason": "测试"}
                          for name in ("intent", "emotion", "reply_need", "reply_urgency", "risk")},
            "candidates": [{"style": "简短", "text": "你再提醒我一下？"},
                           {"style": "核对", "text": "我怕记错，能再说一遍吗？"}],
            "assumptions": [], "needs_user_review": False}


class HypothesisTests(unittest.TestCase):
    def test_grounding_id_dedup_quantity_and_types(self):
        valid, errors = validate_hypotheses({"hypotheses": [hypothesis(), hypothesis(),
            hypothesis("h2", "昨天我说了什么？", "m003"),
            {**hypothesis("h3"), "kind": "diagnosis"},
            {**hypothesis("h4"), "hypothesis": ""},
            hypothesis("h5", "那你说", "m999")]}, DATA)
        self.assertEqual([h["id"] for h in valid], ["h1"])
        self.assertEqual(len(errors), 5)
        self.assertIn("引用", errors[1])
        self.assertEqual(validate_hypotheses({"hypotheses": [hypothesis(f"h{i}") for i in range(1, 7)]}, DATA)[0].__len__(), 1)

    def test_invalid_structure_and_missing_refs(self):
        self.assertFalse(validate_hypotheses({"hypotheses": "bad"}, DATA)[0])
        item = {**hypothesis(), "evidence_refs": []}
        self.assertFalse(validate_hypotheses({"hypotheses": [item]}, DATA)[0])

    def test_one_batch_questions_and_uncertain_status(self):
        items = [hypothesis()]
        payload = dynamic_payload(DATA, items)
        self.assertNotIn("hypotheses", payload["state"])
        self.assertIn("intent", payload["questions"])
        self.assertIn("relationship_tension", payload["questions"])
        self.assertIn("hyp_h1_supported", payload["questions"])
        self.assertIn("hyp_h1_evidence", payload["questions"])
        self.assertIn("消歧前文", payload["questions"]["hyp_h1_evidence"]["instructions"])
        self.assertIn("我受伤了", payload["questions"]["emotion"]["instructions"])
        result = jev_response(payload, evidence="insufficient", support=0.98)
        row = judged_hypotheses(items, result["answers"])[0]
        self.assertEqual(row["support_probability"], 0.98)
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(judged_hypotheses(items, jev_response(payload, evidence="conflicting")["answers"])[0]["status"], "uncertain")
        self.assertEqual(judged_hypotheses(items, jev_response(payload, evidence="sufficient")["answers"])[0]["status"], "judged")

    def test_ds_wording_cannot_supply_judgment_fields(self):
        care_request = wording_request({"messages": [{"role": "contact", "text": "我受伤了"}],
                                        "confirmed_facts": []}, "care", "disabled")
        self.assertIn("别乱动", care_request["messages"][0]["content"])
        with self.assertRaisesRegex(ValueError, "只能包含 candidates"):
            validate_wording({"candidates": [{"style": "短", "text": "你好"}],
                              "analysis": {"subtext": "DS 擅自判断"}})
        payload = dynamic_payload(DATA, [hypothesis()])
        response = jev_response(payload)
        rows = judged_hypotheses([hypothesis()], response["answers"])
        # The summary is assembled from Jev's validated answers, not a DS reply.
        response["answers"]["reply_direction"] = {"choice": "clarify_history"}
        result = result_from_jev(DATA, response, rows, replay_detail=True)
        self.assertEqual(result["analysis"]["intent"], response["answers"]["intent"]["choice"])


class JevContractTests(unittest.TestCase):
    def test_rejects_nonfinite_distribution_and_missing_questions(self):
        payload = dynamic_payload(DATA, [hypothesis()])
        body = {"model": MODEL, "answers": jev_response(payload)["answers"]}
        variants = []
        a = copy.deepcopy(body); a["answers"]["hyp_h1_supported"]["noul"] = True; variants.append(a)
        a = copy.deepcopy(body); a["answers"]["hyp_h1_supported"]["noul"] = math.nan; variants.append(a)
        a = copy.deepcopy(body); a["answers"]["hyp_h1_evidence"]["probabilities"]["sufficient"] = 0.5; variants.append(a)
        a = copy.deepcopy(body); a["answers"]["relationship_tension"]["score"] = math.inf; variants.append(a)
        a = copy.deepcopy(body); a["answers"]["relationship_tension"]["score"] = 2.0; variants.append(a)
        a = copy.deepcopy(body); a["answers"]["relationship_tension"]["legend"]["0"] = "wrong"; variants.append(a)
        a = copy.deepcopy(body); del a["answers"]["intent"]; variants.append(a)
        for variant in variants:
            with self.subTest(variant=variant):
                with self.assertRaises(ValueError):
                    validate_answers(payload, variant)


class PipelineTests(unittest.TestCase):
    def test_order_counts_and_metadata(self):
        order, snapshots = [], []
        def propose(payload):
            order.append("propose")
            self.assertEqual(payload["thinking"], {"type": "disabled"})
            return {"content": {"hypotheses": [hypothesis()]}, "usage": {"input_tokens": 5}}
        def judge(payload):
            order.append("judge")
            return jev_response(payload)
        def reply(payload):
            order.append("reply")
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("jev_analysis", serialized)
            material = json.loads(payload["messages"][1]["content"])
            brief = material["jev_brief"]
            self.assertEqual(brief["hypotheses"][0]["support_probability"], 0.8)
            self.assertEqual(brief["hypotheses"][0]["hypothesis"], "要求复述此前内容")
            self.assertEqual(brief["hypotheses"][0]["evidence_refs"], hypothesis()["evidence_refs"])
            self.assertEqual(brief["hypotheses"][0]["wording_use"], "possible_interpretation")
            self.assertEqual(material["confirmed_facts"], [])
            self.assertNotIn("probabilities", serialized)
            self.assertNotIn("hyp_h1_supported", serialized)
            self.assertIn("那你说", serialized)
            self.assertIn("clarify_history", serialized)
            return {"content": {"candidates": [{"style": "短句", "text": "你再提醒我一下？"},
                                               {"style": "核对", "text": "我怕记错，能再说一遍吗？"}]},
                    "usage": {"output_tokens": 5}}
        output = run_dynamic(DATA, propose_call=propose, judge_call=judge, reply_call=reply,
                             progress=snapshots.append)
        self.assertEqual(order, ["propose", "judge", "reply"])
        self.assertEqual([output["dynamic_analysis"]["stages"][x]["request_count"] for x in order], [1, 1, 1])
        self.assertEqual(output["send_status"], "not_sent")
        self.assertEqual(output["subtext_hypotheses"][0]["support_probability"], 0.8)
        self.assertTrue(output["dynamic_analysis"]["questions_sha256"])
        self.assertEqual(output["dynamic_analysis"]["subtext_judge"], "Jev")
        self.assertTrue(output["dynamic_analysis"]["reply_receives_jev_scores"])
        self.assertTrue(output["dynamic_analysis"]["reply_receives_jev_hypotheses"])
        self.assertEqual(output["wording_brief"]["hypotheses"][0]["hypothesis"], "要求复述此前内容")
        self.assertEqual(output["result"]["analysis"]["intent"], output["jev"]["answers"]["intent"]["choice"])
        self.assertTrue(any(s["dynamic_analysis"]["stages"]["judge"]["status"] == "complete"
                            and s["dynamic_analysis"]["stages"]["reply"]["status"] == "pending" for s in snapshots))

    def test_wording_uses_evidence_status_before_high_support(self):
        for evidence, support, confidence, usage in (
            ("insufficient", 0.99, 1.0, "uncertain_do_not_assume"),
            ("conflicting", 0.99, 1.0, "uncertain_do_not_assume"),
            ("sufficient", 0.99, 0.4, "uncertain_do_not_assume"),
            ("sufficient", 0.1, 1.0, "unsupported_do_not_assume"),
        ):
            with self.subTest(evidence=evidence, support=support, confidence=confidence):
                def judge(payload):
                    response = jev_response(payload, evidence=evidence, support=support)
                    response["answers"]["hyp_h1_evidence"]["confidence"] = confidence
                    return response
                def reply(payload):
                    material = json.loads(payload["messages"][1]["content"])
                    row = material["jev_brief"]["hypotheses"][0]
                    self.assertEqual(row["wording_use"], usage)
                    self.assertEqual(row["evidence_status"], evidence)
                    self.assertEqual(row["proposed_missing_context"], hypothesis()["missing_context"])
                    self.assertEqual(material["confirmed_facts"], [])
                    return {"content": {"candidates": reply_result()["candidates"]}}
                output = run_dynamic(DATA, propose_call=lambda _: {"content": {"hypotheses": [hypothesis()]}},
                                     judge_call=judge, reply_call=reply)
                self.assertEqual(output["dynamic_analysis"]["stages"]["reply"]["status"], "complete")

    def test_brief_preserves_distinct_hypotheses_and_rejects_bad_evidence(self):
        items = [hypothesis(), {**hypothesis("h2"), "hypothesis": "希望转移话题"}]
        response = jev_response(dynamic_payload(DATA, items))
        response["answers"]["hyp_h2_supported"]["noul"] = 0.1
        rows = copy.deepcopy(judged_hypotheses(items, response["answers"]))
        before = copy.deepcopy(rows)
        brief = wording_brief(DATA, response, rows)
        self.assertEqual([r["wording_use"] for r in brief["hypotheses"]],
                         ["possible_interpretation", "unsupported_do_not_assume"])
        self.assertEqual(rows, before)
        rows[0]["evidence_refs"][0]["source_id"] = "m999"
        with self.assertRaisesRegex(ValueError, "原文引用"):
            wording_brief(DATA, response, rows)
        with self.assertRaisesRegex(ValueError, "未经 Jev"):
            wording_brief(DATA, response, judged_hypotheses(items, None))

    def test_wait_skips_wording_and_partial_judge_failure_never_calls_ds(self):
        def wait_judge(payload):
            response = jev_response(payload)
            answer = response["answers"]["reply_direction"]
            answer.update(choice="wait", probabilities={key: float(key == "wait") for key in answer["probabilities"]})
            return response
        goodbye = {"messages": [{"role": "contact", "text": "先这样，晚安"}], "confirmed_facts": []}
        output = run_dynamic(goodbye, propose_call=lambda _: {"content": {"hypotheses": []}},
                             judge_call=wait_judge, reply_call=lambda _: self.fail("wait 不能调用 DS"))
        self.assertEqual(output["candidates"], [])
        self.assertEqual(output["dynamic_analysis"]["stages"]["reply"]["request_count"], 0)
        def broken_judge(payload):
            response = jev_response(payload)
            del response["answers"]["hyp_h1_supported"]
            return response
        output = run_dynamic(DATA, propose_call=lambda _: {"content": {"hypotheses": [hypothesis()]}},
                             judge_call=broken_judge, reply_call=lambda _: self.fail("判断组装失败不能调用 DS"))
        self.assertEqual(output["dynamic_analysis"]["fallback_reason"], "jev_failed_no_reply")

    def test_proposal_repair_fallback_jev_failure_reply_failure(self):
        calls = []
        def invalid(_):
            calls.append("propose")
            return {"content": {"hypotheses": [hypothesis("h1", "not in source")]}}
        def judge(payload):
            calls.append("judge")
            self.assertNotIn("hyp_h1_supported", payload["questions"])
            return jev_response(payload)
        output = run_dynamic(DATA, propose_call=invalid, judge_call=judge,
                             reply_call=lambda _: {"content": {"candidates": [{"style": "a", "text": "提醒我一下？"},
                                                                                {"style": "b", "text": "能再说一遍吗？"}]}})
        self.assertEqual(calls, ["propose", "propose", "judge"])
        self.assertEqual(output["dynamic_analysis"]["fallback_reason"], "proposal_invalid_fixed_jev")
        self.assertFalse(output["subtext_hypotheses"])

        output = run_dynamic(DATA, propose_call=lambda _: {"content": {"hypotheses": [hypothesis()]}},
                             judge_call=lambda _: (_ for _ in ()).throw(RuntimeError("offline")),
                             reply_call=lambda _: self.fail("Jev 失败后不能交给 DS 代判"))
        self.assertEqual(output["dynamic_analysis"]["fallback_reason"], "jev_failed_no_reply")
        self.assertEqual(output["subtext_hypotheses"][0]["status"], "unjudged")
        self.assertIsNone(output["result"])

        output = run_dynamic(DATA, propose_call=lambda _: {"content": {"hypotheses": [hypothesis()]}},
                             judge_call=jev_response,
                             reply_call=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual(output["dynamic_analysis"]["stages"]["reply"]["status"], "failed")
        self.assertIsNone(output["result"])
        self.assertEqual(output["candidates"], [])
        self.assertEqual(output["send_status"], "not_sent")

    def test_history_chain_and_real_basis(self):
        result = reply_result()
        self.assertTrue(enforce_history_guard(DATA, result))
        self.assertEqual(result["action"], "clarify")
        grounded = {"messages": [{"role": "contact", "text": "昨天说好三点见面"}, *DATA["messages"]],
                    "confirmed_facts": []}
        self.assertFalse(enforce_history_guard(grounded, reply_result()))
        unrelated = {**DATA, "confirmed_facts": ["我们认识很多年了"]}
        self.assertTrue(enforce_history_guard(unrelated, reply_result()))

    def test_request_identity_and_offline_check(self):
        a = run_dynamic(DATA, propose_call=lambda _: {"content": {"hypotheses": []}},
                        judge_call=jev_response, reply_call=lambda _: {"content": {"candidates": [{"style": "a", "text": "再说一遍？"},
                                                                                          {"style": "b", "text": "提醒我一下？"}]}})
        b = run_dynamic(DATA, propose_call=lambda _: {"content": {"hypotheses": []}},
                        judge_call=jev_response, reply_call=lambda _: {"content": {"candidates": [{"style": "a", "text": "再说一遍？"},
                                                                                          {"style": "b", "text": "提醒我一下？"}]}})
        self.assertNotEqual(a["request_id"], b["request_id"])
        self.assertNotEqual(config_fingerprint(DATA, thinking="disabled", replay_detail=True),
                            config_fingerprint(DATA, thinking="enabled", replay_detail=True))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(DATA), encoding="utf-8")
            with patch("autochat.dynamic.run_dynamic") as run:
                self.assertEqual(main(["--input", str(path), "--mode", "jev-dynamic", "--check"]), 0)
            run.assert_not_called()

    def test_cancel_after_proposal_starts_no_jev_or_reply(self):
        state = {"cancel": False}
        def propose(_):
            state["cancel"] = True
            return {"content": {"hypotheses": [hypothesis()]}}
        with patch("autochat.dynamic.call_jev") as judge, patch("autochat.cli.call_deepseek_json") as reply:
            output = run_dynamic(DATA, propose_call=propose, cancelled=lambda: state["cancel"])
        judge.assert_not_called()
        reply.assert_not_called()
        self.assertEqual(output["dynamic_analysis"]["fallback_reason"], "cancelled")
        self.assertIsNone(output["result"])

    def test_replay_excludes_future_and_does_not_record_candidates(self):
        session = ReplaySession({"messages": DATA["messages"] + [{"role": "me", "text": "我发出的原话"}],
                                 "confirmed_facts": []})
        session.next_message()
        self.assertEqual(len(session.visible_messages), 1)
        self.assertEqual(len(session.analysis_data()["messages"]), 1)
        before = copy.deepcopy(session.messages)
        self.assertEqual(session.messages, before)

    def test_replay_late_same_context_result_cannot_replace_new_request(self):
        session = ReplaySession(DATA)
        for _ in DATA["messages"]:
            session.next_message()
        data = session.analysis_data()
        output = {"context_version": context_version(data),
                  "pipeline_mode": "llm-only", "result": reply_result()}
        session.begin_request(2, data, "first")
        session.begin_request(2, data, "second")
        with self.assertRaisesRegex(ValueError, "更新的请求"):
            session.record_result(2, data, output, Path("old.json"), request_token="first")
        session.record_result(2, data, output, Path("new.json"), request_token="second")
        self.assertEqual(session.cards[2]["request_token"], "second")
        original_messages = copy.deepcopy(session.messages)
        session.delete_card(2)
        self.assertNotIn(2, session.cards)
        self.assertIn(2, session.dismissed_cards)
        self.assertEqual(session.messages, original_messages)
        with self.assertRaisesRegex(ValueError, "更新的请求"):
            session.record_result(2, data, output, Path("late.json"), request_token="second")
        session.begin_request(2, data, "third")
        self.assertNotIn(2, session.dismissed_cards)

    def test_delete_card_keeps_saved_result_file(self):
        session = ReplaySession(DATA)
        for _ in DATA["messages"]:
            session.next_message()
        data = session.analysis_data()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text("{}", encoding="utf-8")
            session.begin_request(2, data, "saved")
            session.record_result(2, data, {"context_version": context_version(data),
                                            "pipeline_mode": "llm-only"}, path, request_token="saved")
            session.delete_card(2)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
