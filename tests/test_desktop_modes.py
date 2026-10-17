import unittest
from pathlib import Path

from autochat.desktop import pipeline_command


class DesktopModeTests(unittest.TestCase):
    def test_pipeline_command_uses_selected_model_path(self):
        input_path = Path("input.json")
        output_path = Path("output.json")
        dual = pipeline_command("dual", input_path, output_path)
        direct = pipeline_command("llm-only", input_path, output_path)
        self.assertEqual(dual[2], "autochat.dual")
        self.assertEqual(direct[2], "autochat.cli")
        self.assertEqual(direct[-4:], ["--mode", "llm-only", "--thinking", "disabled"])
        self.assertNotIn("autochat.dual", direct)

    def test_jev_modes_pin_non_thinking_generation(self):
        input_path = Path("input.json")
        output_path = Path("output.json")
        for mode in ("jev", "jev-rank"):
            command = pipeline_command(mode, input_path, output_path)
            self.assertEqual(command[2], "autochat.cli")
            self.assertEqual(command[-4:], ["--mode", mode, "--thinking", "disabled"])


if __name__ == "__main__":
    unittest.main()
