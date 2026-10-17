"""Recall identity, model projection and stale-result regression tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autochat.cli import context_version
from autochat.analysis_gate import gate
from autochat.replay import ReplaySession
from autochat.ui_service import UIService


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events = []
        self.keys = patch.multiple("autochat.ui_service", get_api_key=lambda: "configured",
                                   get_typesafe_api_key=lambda: "configured")
        self.keys.start()
        self.service = UIService(emit=self.events.append, results_dir=Path(self.tmp.name))
        self.sid = self.service.selected_id

    def tearDown(self):
        self.keys.stop()
        self.tmp.cleanup()

    def add(self, role, text):
        self.service.dispatch("message.append", {"session_id": self.sid, "role": role,
                                                  "text": text, "confirmed_sent": role == "me"})
        return self.service._required_session(self.sid).message_ids[-1]

    def recall(self, message_id, operation_id="op-1"):
        session = self.service._required_session(self.sid)
        return self.service.dispatch("message.recall", {"session_id": self.sid,
            "message_id": message_id, "expected_revision": session.revision,
            "operation_id": operation_id})

    def test_duplicate_text_recall_is_by_id_and_hidden_from_model_snapshot(self):
        first = self.add("contact", "同一句")
        second = self.add("contact", "同一句")
        receipt = self.recall(first)
        session = self.service._required_session(self.sid)
        self.assertEqual(receipt["message_id"], first)
        self.assertEqual(session.analysis_data()["messages"], [{"role": "contact", "text": "同一句"}])
        snap = self.service.snapshot()
        self.assertEqual(snap["messages"][0], {"id": first, "role": "contact",
                                                "state": "revoked", "recall_id": receipt["recall_id"]})
        self.assertEqual(snap["messages"][1]["id"], second)
        self.assertEqual(snap["active_message_count"], 1)
        self.assertNotIn("text", snap["messages"][0])

    def test_restore_preserves_position_and_old_recall_id_cannot_restore_again(self):
        first = self.add("contact", "甲")
        second = self.add("me", "乙")
        earlier = self.recall(first)
        session = self.service._required_session(self.sid)
        restored = self.service.dispatch("message.restore", {"session_id": self.sid,
            "message_id": first, "recall_id": earlier["recall_id"],
            "expected_revision": session.revision, "operation_id": "op-2"})
        self.assertEqual([row["id"] for row in self.service.snapshot()["messages"]], [first, second])
        self.assertEqual(restored["revision"], session.revision)
        newer = self.recall(first, "op-3")
        with self.assertRaisesRegex(ValueError, "过期"):
            self.service.dispatch("message.restore", {"session_id": self.sid,
                "message_id": first, "recall_id": earlier["recall_id"],
                "expected_revision": session.revision, "operation_id": "op-4"})
        self.assertNotEqual(earlier["recall_id"], newer["recall_id"])

    def test_idempotency_conflict_and_unplayed_target(self):
        target = self.add("contact", "甲")
        session = self.service._required_session(self.sid)
        payload = {"session_id": self.sid, "message_id": target,
                   "expected_revision": session.revision, "operation_id": "same"}
        first = self.service.dispatch("message.recall", payload)
        self.assertEqual(self.service.dispatch("message.recall", payload), first)
        self.assertEqual(session.revision, first["revision"])
        with self.assertRaisesRegex(ValueError, "操作编号"):
            self.service.dispatch("message.recall", {**payload, "message_id": "other"})
        with self.assertRaisesRegex(ValueError, "刷新"):
            self.service.dispatch("message.recall", {**payload, "operation_id": "new"})
        with self.assertRaisesRegex(ValueError, "参数无效"):
            self.service.dispatch("message.recall", {**payload, "session_id": ""})

        replay = ReplaySession({"messages": [{"role": "contact", "text": "尚未回放"}]})
        with self.assertRaisesRegex(ValueError, "尚未回放"):
            replay.recall(replay.message_ids[0])

    def test_recall_replay_prefix_does_not_expose_future_and_keeps_cursor(self):
        replay = ReplaySession({"messages": [
            {"role": "contact", "text": "先前"}, {"role": "contact", "text": "当前"},
            {"role": "contact", "text": "未来"}]})
        replay.next_message()
        replay.next_message()
        replay.recall(replay.message_ids[0])
        self.assertEqual(replay.cursor, 2)
        self.assertEqual(replay.analysis_data()["messages"], [{"role": "contact", "text": "当前"}])
        replay.restore(replay.message_ids[0], replay.revoked[replay.message_ids[0]])
        self.assertEqual(replay.cursor, 2)

    def test_facts_need_review_and_can_be_confirmed_one_at_a_time(self):
        target = self.add("contact", "原话")
        self.service.dispatch("preferences.update", {"confirmed_facts": ["事实甲", "事实乙"]})
        self.recall(target)
        self.add("contact", "新话")
        session = self.service._required_session(self.sid)
        self.assertTrue(self.service.snapshot()["facts_pending_review"])
        self.assertEqual(self.service._data(session)["confirmed_facts"], [])
        self.service.dispatch("facts.confirm", {"session_id": self.sid,
            "expected_revision": session.revision, "index": 0})
        self.assertEqual(self.service._data(session)["confirmed_facts"], ["事实甲"])
        self.service.dispatch("facts.confirm", {"session_id": self.sid,
            "expected_revision": session.revision})
        self.assertEqual(self.service._data(session)["confirmed_facts"], ["事实甲", "事实乙"])

    def test_recall_invalidates_copy_and_late_progress_even_after_restore(self):
        target = self.add("contact", "原话")
        session = self.service._required_session(self.sid)
        data = self.service._data(session)
        version = context_version(data)
        session.begin_request(0, data, "worker-token")
        self.service.mode = "llm-only"
        session.cards[0] = {"state": "complete", "context_version": version,
            "session_revision": session.revision, "config_revision": self.service.config_revision,
            "message_id": target, "request_token": "worker-token",
            "output": {"request_id": "worker-token", "pipeline_mode": "llm-only",
                       "result": {"candidates": [{"text": "回复"}]}}}
        copy = {"session_id": self.sid, "message_id": target, "index": 0,
                "session_revision": session.revision, "config_revision": self.service.config_revision,
                "request_id": "worker-token", "context_version": version, "mode": "llm-only",
                "candidate_index": 0, "text": "回复"}
        self.assertEqual(self.service.dispatch("candidate.copy", copy)["text"], "回复")
        request = {"session_id": self.sid, "index": 0, "message_id": target,
            "request_id": "worker-token", "context_version": version, "mode": "llm-only",
            "config_fingerprint": None, "session_revision": session.revision,
            "config_revision": self.service.config_revision, "data": data, "cancelled": False}
        self.service.active = request
        recalled = self.recall(target)
        self.assertTrue(recalled["cancel_requested"])
        self.assertIsNone(self.service.snapshot()["active"])
        self.service._accept_progress(request, {"request_id": "worker-token",
            "context_version": version, "pipeline_mode": "llm-only"})
        self.assertEqual(session.cards, {})
        self.service.dispatch("message.restore", {"session_id": self.sid,
            "message_id": target, "recall_id": recalled["recall_id"],
            "expected_revision": session.revision, "operation_id": "restore"})
        self.assertEqual(context_version(self.service._data(session)), version)
        with self.assertRaisesRegex(ValueError, "过期"):
            self.service.dispatch("candidate.copy", copy)

    def test_other_session_request_survives(self):
        target = self.add("contact", "甲")
        self.service.dispatch("session.create", {"name": "第二会话"})
        other = self.service.selected_id
        running = {"session_id": other, "request_id": "other", "cancelled": False}
        self.service.active = running
        self.recall(target)
        self.assertIs(self.service.active, running)
        self.assertFalse(running["cancelled"])
        self.assertEqual(self.events[-1]["session_id"], self.sid)

    def test_start_uses_last_active_target_and_source_map(self):
        first = self.add("contact", "第一句")
        second = self.add("contact", "误录")
        self.recall(second)
        session = self.service._required_session(self.sid)
        self.service.mode = "llm-only"
        with patch("autochat.ui_service.threading.Thread"):
            try:
                self.service.dispatch("analysis.start", {"session_id": self.sid,
                    "context_version": context_version(self.service._data(session))})
                request = self.service.active
                self.assertEqual(request["index"], 0)
                self.assertEqual(request["message_id"], first)
                self.assertEqual(request["source_message_ids"], {"m001": first})
                self.assertIn("第一句", request["input"].read_text(encoding="utf-8"))
                self.assertNotIn("误录", request["input"].read_text(encoding="utf-8"))
            finally:
                self.service.active = None
                gate.release(self.service)


if __name__ == "__main__":
    unittest.main()
