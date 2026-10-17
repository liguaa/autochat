import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from autochat.cli import build_request, call_deepseek, context_version, load_nanojev_analysis, main, read_conversation, validate_result


def example_scorecard():
    return {key: {"label": "一般", "score": 50, "reason": "测试依据"}
            for key in ("intent", "emotion", "reply_need", "reply_urgency", "risk")}


class ConversationTests(unittest.TestCase):
    def test_context_version_changes_when_new_message_arrives(self):
        data = {"messages": [{"role": "contact", "text": "你好"}], "confirmed_facts": []}
        first = context_version(data)
        data["messages"].append({"role": "contact", "text": "在吗？"})
        self.assertNotEqual(first, context_version(data))

    def test_last_message_must_be_contact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.json"
            path.write_text(json.dumps({"messages": [{"role": "me", "text": "你好"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "最后一条"):
                read_conversation(path)

    def test_risk_cannot_bypass_user_review(self):
        result = {
            "action": "reply",
            "analysis": {"emotion": "未知", "intent": "请求帮助", "uncertainty": "无", "risk_signals": ["钱款"]},
            "scorecard": example_scorecard(),
            "candidates": [{"style": "简短", "text": "稍后看"}, {"style": "询问", "text": "能说下详情吗？"}],
            "assumptions": [],
            "needs_user_review": False,
        }
        validated = validate_result(result)
        self.assertTrue(validated["needs_user_review"])

    def test_scores_must_be_bounded_integers(self):
        result = {
            "action": "wait",
            "analysis": {"emotion": "平静", "intent": "结束话题", "uncertainty": "无", "risk_signals": []},
            "scorecard": example_scorecard(),
            "candidates": [],
            "assumptions": [],
            "needs_user_review": False,
        }
        result["scorecard"]["reply_urgency"]["score"] = 101
        with self.assertRaisesRegex(ValueError, "0 到 100"):
            validate_result(result)
        result["scorecard"]["reply_urgency"]["score"] = True
        with self.assertRaisesRegex(ValueError, "整数"):
            validate_result(result)

    def test_unknown_assumptions_require_review(self):
        result = {
            "action": "reply",
            "analysis": {"emotion": "焦虑", "intent": "解释", "uncertainty": "关系未知", "risk_signals": []},
            "scorecard": example_scorecard(),
            "candidates": [{"style": "简短", "text": "我知道了"}, {"style": "温和", "text": "慢慢说"}],
            "assumptions": ["假设用户是老师"],
            "needs_user_review": False,
        }
        self.assertTrue(validate_result(result)["needs_user_review"])

    def test_api_response_is_parsed_but_never_sent(self):
        data = {"messages": [{"role": "contact", "text": "你好"}], "confirmed_facts": []}
        model_result = {
            "action": "reply",
            "analysis": {"emotion": "平静", "intent": "问候", "uncertainty": "无", "risk_signals": []},
            "scorecard": example_scorecard(),
            "candidates": [{"style": "简短", "text": "你好！"}, {"style": "温和", "text": "你好，今天怎么样？"}],
            "assumptions": [],
            "needs_user_review": False,
        }
        api_response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(model_result)}}], "usage": {"total_tokens": 42}}
        with patch("autochat.cli.get_api_key", return_value="test-key"), patch("autochat.cli.urllib.request.urlopen", return_value=BytesIO(json.dumps(api_response).encode())):
            parsed = call_deepseek(build_request(data))
        self.assertEqual(parsed["result"]["candidates"][0]["text"], "你好！")
        self.assertEqual(parsed["usage"]["total_tokens"], 42)

    def test_stale_small_model_analysis_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.json"
            path.write_text(json.dumps({"context_version": "old", "model": "NanoJev", "signals": {}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "版本不一致"):
                load_nanojev_analysis(path, "new")

    def test_large_model_request_marks_small_model_as_unvalidated(self):
        data = {"messages": [{"role": "contact", "text": "我受伤了"}], "confirmed_facts": []}
        small = {"model": "NanoJev", "chat_domain_validated": False, "signals": {"risk": {"value": True, "p_true": 0.8}}}
        request = build_request(data, small)
        self.assertIn("未经聊天领域校准", request["messages"][1]["content"])
        self.assertIn("我受伤了", request["messages"][1]["content"])

    def test_llm_only_mode_skips_nanojev_and_marks_output(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.json"
            output_path = Path(directory) / "reply.json"
            input_path.write_text(json.dumps({"messages": [{"role": "contact", "text": "在吗"}]}), encoding="utf-8")
            model_result = {
                "action": "reply",
                "analysis": {"emotion": "平静", "intent": "询问", "uncertainty": "无", "risk_signals": []},
                "scorecard": example_scorecard(),
                "candidates": [{"style": "简短", "text": "在呀"}, {"style": "随口", "text": "在呢，怎么啦？"}],
                "assumptions": [], "needs_user_review": False,
            }
            with patch("autochat.cli.call_deepseek", return_value={"result": model_result, "usage": {}}) as call:
                self.assertEqual(main(["--input", str(input_path), "--output", str(output_path),
                                       "--mode", "llm-only"]), 0)
            self.assertNotIn("nanojev_analysis", call.call_args.args[0]["messages"][1]["content"])
            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(output["pipeline_mode"], "llm-only")
            self.assertIsNone(output["small_model"])
            self.assertEqual(output["send_status"], "not_sent")

    def test_personal_style_examples_are_in_request(self):
        data = {"messages": [{"role": "contact", "text": "在吗"}], "confirmed_facts": [],
                "style_profile": {"preset": "mine", "samples": ["在，咋了", "我晚点看哈"]}}
        request = build_request(data)
        self.assertIn("我晚点看哈", request["messages"][1]["content"])
        self.assertIn("只模仿表达方式", request["messages"][1]["content"])

    def test_doubao_style_reaches_model_without_changing_score_rules(self):
        data = {"messages": [{"role": "contact", "text": "今天作业没写完"}], "confirmed_facts": [],
                "style_profile": {"preset": "doubao", "samples": []}}
        request = build_request(data)
        self.assertIn("豆包人格（纯糊弄·纯讨好）", request["messages"][0]["content"])
        self.assertIn("先附和并捧着对方说", request["messages"][0]["content"])
        self.assertIn("reply_urgency 表示需要多快回应", request["messages"][0]["content"])

    def test_personal_style_requires_two_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.json"
            path.write_text(json.dumps({"messages": [{"role": "contact", "text": "在吗"}],
                                        "style_profile": {"preset": "mine", "samples": ["在"]}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "至少需要 2 条"):
                read_conversation(path)


if __name__ == "__main__":
    unittest.main()
