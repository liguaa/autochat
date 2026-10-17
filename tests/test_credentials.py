import os
import unittest
from unittest.mock import patch

from autochat.credentials import get_api_key


class CredentialTests(unittest.TestCase):
    def test_process_environment_has_priority(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "from-process"}):
            with patch("autochat.credentials._registry_value") as read_registry:
                self.assertEqual(get_api_key(), "from-process")
                read_registry.assert_not_called()

    def test_user_environment_is_read_when_process_is_stale(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("autochat.credentials._registry_value", side_effect=["from-user"]):
                self.assertEqual(get_api_key(), "from-user")


if __name__ == "__main__":
    unittest.main()
