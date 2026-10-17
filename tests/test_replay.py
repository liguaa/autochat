import json
import tempfile
import unittest
from pathlib import Path

from autochat.cli import context_version, enforce_history_guard, ground_replay_detail, validate_replay_detail
from autochat.replay import ReplaySession


class ReplayTests(unittest.TestCase):
    def test_import_steps_through_both_speakers_and_stales_old_card(self):
        transcript = {"messages": [
            {"role": "contact", "text": "昨天说的事呢？"},
            {"role": "me", "text": "我不记得了，你能提醒我吗？"},
            {"role": "contact", "text": "我说的是文件。"},
        ], "confirmed_facts": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.json"
            path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
            session = ReplaySession.from_file(path)
        self.assertEqual(session.cursor, 0)
        first_index, _ = session.next_message()
        self.assertIn("未提供前文", session.history_notice())
        first_data = session.analysis_data()
        session.record_result(first_index, first_data,
                              {"context_version": context_version(first_data), "result": {}}, Path("first.json"))
        self.assertTrue(session.is_current(first_index, first_data))
        session.next_message()
        self.assertFalse(session.is_current(first_index))
        self.assertEqual(session.visible_messages[-1]["text"], "我不记得了，你能提醒我吗？")
        session.next_message()
        self.assertEqual(session.analysis_data()["messages"][-2]["role"], "me")
        self.assertFalse(session.is_current(first_index))

    def test_manual_message_requires_import_to_finish_and_result_version_to_match(self):
        session = ReplaySession({"messages": [{"role": "contact", "text": "你好"}], "confirmed_facts": []})
        with self.assertRaisesRegex(ValueError, "先回放完"):
            session.append("me", "你好")
        session.next_message()
        data = session.analysis_data()
        with self.assertRaisesRegex(ValueError, "版本不一致"):
            session.record_result(0, data, {"context_version": "wrong"}, Path("wrong.json"))
        session.append("me", "你好")
        self.assertEqual(session.visible_messages[-1], {"role": "me", "text": "你好"})
        with self.assertRaisesRegex(ValueError, "最后一条"):
            session.analysis_data()

    def test_replay_detail_requires_grounded_fields(self):
        analysis = {"literal": "问时间", "subtext": "无法判断", "need": "答复时间",
                    "relationship_tension": "未知", "history_gap": "无明确缺失", "evidence": ["几点见"]}
        validate_replay_detail({"analysis": analysis})
        del analysis["history_gap"]
        with self.assertRaisesRegex(ValueError, "history_gap"):
            validate_replay_detail({"analysis": analysis})

    def test_missing_history_replaces_unsupported_memory_claims(self):
        data = {"messages": [{"role": "contact", "text": "昨天说的时间还记得吗？"}],
                "confirmed_facts": []}
        result = {"action": "reply", "analysis": {"history_gap": "缺少昨天的记录"},
                  "candidates": [{"style": "错", "text": "记得，三点"}], "needs_user_review": False}
        self.assertTrue(enforce_history_guard(data, result))
        self.assertEqual(result["action"], "clarify")
        self.assertTrue(result["needs_user_review"])
        self.assertTrue(all("记得" not in item["text"] and "翻了" not in item["text"]
                            for item in result["candidates"]))

    def test_history_guard_keeps_evidenced_context(self):
        data = {"messages": [{"role": "me", "text": "昨天说好下午三点"},
                             {"role": "contact", "text": "昨天说的时间还记得吗？"}],
                "confirmed_facts": []}
        result = {"analysis": {"history_gap": "无明确缺失"}}
        self.assertFalse(enforce_history_guard(data, result))

    def test_same_short_message_uses_its_own_context_version(self):
        session = ReplaySession.from_file(Path(__file__).resolve().parents[1] / "data" / "replay_demo.json")
        session.next_message()
        first_index, _ = session.next_message()
        first_data = session.analysis_data()
        first_version = context_version(first_data)
        for _ in range(4):
            session.next_message()
        last_index = session.cursor - 1
        last_data = session.analysis_data()
        self.assertEqual(session.messages[first_index]["text"], session.messages[last_index]["text"])
        self.assertNotEqual(first_version, context_version(last_data))
        session.record_error(last_index, last_data, "API 暂不可用")
        self.assertEqual(session.cards[last_index]["state"], "error")

    def test_replay_card_only_keeps_verbatim_evidence(self):
        data = {"messages": [{"role": "contact", "text": "项目一直改，今天还加班了。"}], "confirmed_facts": []}
        result = {"analysis": {"subtext": "可能生气，但原文无依据", "evidence": ["项目一直改", "对方非常愤怒"]}}
        ground_replay_detail(data, result)
        self.assertEqual(result["analysis"]["evidence"], ["项目一直改"])
        self.assertEqual(result["analysis"]["subtext"], "无法判断（原文没有足够依据）")


if __name__ == "__main__":
    unittest.main()
