import tempfile
import unittest
from pathlib import Path

from forwardbot.db import Database


class DatabaseSessionTests(unittest.TestCase):
    def test_session_string_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(Path(tmp) / "forwardbot.sqlite3")
            database.init()
            database.upsert_session(
                "member_42", 42, "+15551234567", "saved-session-string"
            )

            session = database.get_session("member_42")
            database.close()

        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.session_string, "saved-session-string")
        self.assertEqual(session.owner_id, 42)


if __name__ == "__main__":
    unittest.main()
