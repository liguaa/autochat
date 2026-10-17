import unittest

from autochat.scorecard import display_scorecard, format_scorecard


def result_with_deepseek_scores():
    return {"pipeline_mode": "jev", "result": {
        "action": "reply",
        "analysis": {"emotion": "开心", "intent": "邀约", "uncertainty": "无", "risk_signals": []},
        "scorecard": {key: {"label": "DeepSeek 标签", "score": 99, "reason": "测试"}
                      for key in ("intent", "emotion", "reply_need", "reply_urgency", "risk")},
        "candidates": [{"style": "短", "text": "你好"}, {"style": "长", "text": "你好呀"}],
        "assumptions": [], "needs_user_review": False,
    }}


class ScorecardDisplayTests(unittest.TestCase):
    def test_jev_mode_displays_raw_probabilities_with_distinct_meanings(self):
        output = result_with_deepseek_scores()
        output["jev"] = {"answers": {
            "intent": {"choice": "提问", "probabilities": {"提问": 0.71, "邀约": 0.29}},
            "emotion": {"choice": "平静或中性", "probabilities": {"平静或中性": 0.64, "开心": 0.36}},
            "needs_reply": {"noul": 0.82},
            "urgent": {"choice": "普通", "probabilities": {"普通": 0.9, "较急": 0.1}},
            "risk_safety": {"noul": 0.03}, "risk_money": {"noul": 0.42},
            "risk_privacy": {"noul": 0.12}, "risk_commitment": {"noul": 0.24},
        }}
        rows, note = display_scorecard(output)
        self.assertEqual(rows[0][1], "提问  71%")
        self.assertEqual(rows[2][1], "需要回复  82%")
        self.assertEqual(rows[3][1], "普通  90%")
        self.assertEqual(rows[4][1], "钱款  42%")
        self.assertIn("不是总体风险概率", note)
        self.assertNotIn("99%", format_scorecard(output))

    def test_llm_only_keeps_deepseek_scores(self):
        output = result_with_deepseek_scores()
        output["pipeline_mode"] = "llm-only"
        rows, note = display_scorecard(output)
        self.assertEqual(rows[0][1], "DeepSeek 标签  99%")
        self.assertIn("DeepSeek", note)


if __name__ == "__main__":
    unittest.main()
