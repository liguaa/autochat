import json
import unittest
from io import BytesIO
from unittest.mock import patch

from autochat.cli import build_request
from autochat.jev import MODEL, analysis_payload, call_jev, rank_payload, summarize_analysis, summarize_ranking


DATA = {"messages": [{"role": "contact", "text": "能先转我两千块吗？"}], "confirmed_facts": []}


class JevTests(unittest.TestCase):
    def test_questions_separate_money_risk_from_relationship_tension(self):
        payload = analysis_payload(DATA)
        self.assertEqual(payload["model"], MODEL)
        self.assertEqual(payload["questions"]["risk_money"]["type"], "noul")
        self.assertIn("钱款", payload["questions"]["risk_money"]["instructions"])
        self.assertIn("其他或不明", payload["questions"]["intent"]["criteria"])

    def test_response_contract_and_summary(self):
        payload = analysis_payload(DATA)
        answers = {}
        for name, question in payload["questions"].items():
            if question["type"] == "noul":
                answers[name] = {"type": "noul", "noul": 0.8 if name == "risk_money" else 0.1}
            else:
                choice = next(iter(question["criteria"]))
                probabilities = {item: float(item == choice) for item in question["criteria"]}
                answers[name] = {"type": "choice", "choice": choice, "probabilities": probabilities, "confidence": 1.0}
        response = {"model": MODEL, "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 20}}
        with patch("autochat.jev.get_typesafe_api_key", return_value="test-key"), patch("autochat.jev.urllib.request.urlopen", return_value=BytesIO(json.dumps(response).encode())):
            parsed = call_jev(payload)
        self.assertEqual(summarize_analysis(parsed)["risk"]["money"], 0.8)
        self.assertEqual(parsed["usage"]["input_tokens"], 100)

    def test_ranking_can_reject_all_candidates(self):
        payload = rank_payload(DATA, [{"text": "给你转了", "style": "短"}, {"text": "明天给你钱", "style": "短"}])
        self.assertIn("none", payload["questions"]["preferred"]["criteria"])
        response = {"answers": {"preferred": {"choice": "0", "probabilities": {"0": 0.9, "1": 0.05, "none": 0.05}},
                                "usable_0": {"noul": 0.2}, "usable_1": {"noul": 0.1}}}
        summary = summarize_ranking(response, 2)
        self.assertTrue(summary["all_rejected"])
        self.assertIsNone(summary["selected_index"])

    def test_deepseek_experiment_disables_thinking_explicitly(self):
        a = build_request(DATA, thinking="disabled")
        b = build_request(DATA, jev_analysis={"intent": "提问"}, thinking="disabled")
        self.assertEqual(a["thinking"], {"type": "disabled"})
        self.assertEqual(b["thinking"], a["thinking"])
        self.assertEqual(b["model"], a["model"])
        self.assertEqual(b["max_tokens"], a["max_tokens"])


if __name__ == "__main__":
    unittest.main()
