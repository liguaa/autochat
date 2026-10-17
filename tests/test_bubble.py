import unittest
import json
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

from autochat.analysis_gate import AnalysisGate
from autochat.bubble_state import BubbleState, clamp_position
from autochat.cli import context_version
from autochat.bubble import BubbleApp
from autochat.replay_desktop import MODES


class BubbleStateTests(unittest.TestCase):
    def test_position_stays_in_nearest_workarea(self):
        areas = [(0, 0, 800, 600), (800, 0, 1600, 900)]
        self.assertEqual(clamp_position(-500, -20, 54, 54, areas), (0, 0))
        self.assertEqual(clamp_position(1570, 880, 54, 54, areas), (1546, 846))
        self.assertEqual(clamp_position(900, 200, 54, 54, areas), (900, 200))

    def test_session_switch_and_changed_settings_invalidate_request(self):
        state = BubbleState()
        state.session.append("contact", "那你说")
        original = state.session.analysis_data()
        request = state.begin(original, "jev-dynamic")
        self.assertTrue(state.matches(request, original, "jev-dynamic"))
        self.assertFalse(state.matches(request, original, "llm-only"))
        changed = state.session.analysis_data(confirmed_facts=["已确认见面时间"])
        self.assertNotEqual(context_version(original), context_version(changed))
        self.assertFalse(state.matches(request, changed, "jev-dynamic"))
        state.add_session("朋友 B")
        self.assertFalse(state.matches(request, original, "jev-dynamic"))
        self.assertEqual(state.session_for(request).messages[0]["text"], "那你说")
        state.cancel()
        state.select_session("本地会话 1")
        self.assertFalse(state.matches(request, original, "jev-dynamic"))

    def test_manual_input_does_not_start_request(self):
        state = BubbleState()
        state.session.append("contact", "你好")
        self.assertIsNone(state.active)
        self.assertEqual(state.session.cards, {})
        state.session.append("me", "我已发送的回复")
        self.assertIsNone(state.active)
        self.assertEqual(state.session.visible_messages[-1]["role"], "me")

    def test_same_process_window_gate(self):
        gate = AnalysisGate()
        first, second = object(), object()
        self.assertTrue(gate.acquire(first))
        self.assertFalse(gate.acquire(second))
        gate.release(second)
        self.assertFalse(gate.acquire(second))
        gate.release(first)
        self.assertTrue(gate.acquire(second))


class BubbleGuiTests(unittest.TestCase):
    def test_synthetic_message_analysis_and_copy_without_network(self):
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("没有可用的图形桌面")
        done = threading.Event()
        scorecard = {name: {"label": "测试", "score": 20, "reason": "合成数据"}
                     for name in ("intent", "emotion", "reply_need", "reply_urgency", "risk")}
        result = {"action": "reply", "analysis": {"emotion": "未知", "intent": "问候",
                  "uncertainty": "无", "risk_signals": [], "literal": "问候", "subtext": "无法判断",
                  "need": "回应", "relationship_tension": "未知", "history_gap": "无前文",
                  "evidence": ["你好"]}, "scorecard": scorecard,
                  "candidates": [{"style": "简短", "text": "你好呀"},
                                 {"style": "自然", "text": "你好，最近怎么样？"}],
                  "assumptions": [], "needs_user_review": False}

        def fake_pipeline(app, request, _command, _input_path, output_path, _progress_path, _cancelled):
            output = {"context_version": request.context_version, "pipeline_mode": request.mode,
                      "request_id": "synthetic-request", "result": result, "send_status": "not_sent"}
            output_path.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
            app.events.put(("complete", request, output_path))
            done.set()

        with tempfile.TemporaryDirectory() as directory, \
                patch("autochat.bubble.ROOT", Path(directory)), \
                patch("autochat.bubble.PREFERENCES", Path(directory) / "prefs.json"), \
                patch("autochat.bubble.get_api_key", return_value="test-only"), \
                patch.object(BubbleApp, "run_pipeline", fake_pipeline):
            app = BubbleApp(root)
            try:
                app.mode_var.set(MODES["llm-only"])
                app.message_text.insert("1.0", "你好")
                app.add_message()
                self.assertIsNone(app.state.active)
                app.analyze()
                self.assertTrue(done.wait(3))
                app.poll()
                root.update()
                self.assertEqual(app.state.session.cards[0]["state"], "complete")
                app.copy_candidate("你好呀", 0, context_version(app.current_data()), "llm-only")
                self.assertEqual(root.clipboard_get(), "你好呀")
                self.assertEqual(len(app.state.session.messages), 1)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
