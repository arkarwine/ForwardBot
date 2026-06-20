import unittest

from forwardbot.links import parse_message_link


class LinkParserTests(unittest.TestCase):
    def test_public_link(self) -> None:
        link = parse_message_link("https://t.me/examplechannel/123")

        self.assertEqual(link.chat_ref, "examplechannel")
        self.assertEqual(link.message_id, 123)
        self.assertFalse(link.is_private_internal)

    def test_private_internal_link(self) -> None:
        link = parse_message_link("https://t.me/c/123456789/42")

        self.assertEqual(link.chat_ref, -100123456789)
        self.assertEqual(link.message_id, 42)
        self.assertTrue(link.is_private_internal)

    def test_bad_link(self) -> None:
        with self.assertRaises(ValueError):
            parse_message_link("https://example.com/nope")


if __name__ == "__main__":
    unittest.main()
