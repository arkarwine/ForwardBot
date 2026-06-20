import unittest

from forwardbot.copier import PublicTelegramTextParser


class PublicTelegramTextParserTests(unittest.TestCase):
    def test_extracts_public_message_text_with_line_breaks(self) -> None:
        parser = PublicTelegramTextParser("beatsdev/105516")
        parser.feed(
            """
            <div class="tgme_widget_message_wrap" data-post="beatsdev/105516">
              <div class="tgme_widget_message_text js-message_text" dir="auto">
                🎉 Ekstraksi link berhasil<br><br>
                Tugas: ⚡#105516<br>
                Email: ah***d@gmail.com<br>
                Total berhasil pengguna: 3<br>
                Total berhasil bot: 27467<br><br>
                Link promo telah dikirim ke pengguna.
              </div>
            </div>
            """
        )

        self.assertEqual(
            parser.text,
            "🎉 Ekstraksi link berhasil\n\n"
            "Tugas: ⚡#105516\n"
            "Email: ah***d@gmail.com\n"
            "Total berhasil pengguna: 3\n"
            "Total berhasil bot: 27467\n\n"
            "Link promo telah dikirim ke pengguna.",
        )


if __name__ == "__main__":
    unittest.main()
