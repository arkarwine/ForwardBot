import os
import unittest
from unittest.mock import patch

from forwardbot.config import Settings


class SettingsTests(unittest.TestCase):
    def test_requires_default_user_session_string(self) -> None:
        with patch("forwardbot.config.load_dotenv"):
            with patch.dict(
                os.environ,
                {
                    "API_ID": "12345",
                    "API_HASH": "hash",
                    "BOT_TOKEN": "token",
                },
                clear=True,
            ):
                with self.assertRaises(RuntimeError) as exc:
                    Settings.load()

        self.assertIn("DEFAULT_USER_SESSION_STRING", str(exc.exception))

    def test_loads_default_user_session_string(self) -> None:
        with patch("forwardbot.config.load_dotenv"):
            with patch.dict(
                os.environ,
                {
                    "API_ID": "12345",
                    "API_HASH": "hash",
                    "BOT_TOKEN": "token",
                    "DEFAULT_USER_SESSION_STRING": "session-string",
                },
                clear=True,
            ):
                settings = Settings.load()

        self.assertEqual(settings.default_user_session, "default")
        self.assertEqual(settings.default_user_session_string, "session-string")


if __name__ == "__main__":
    unittest.main()
