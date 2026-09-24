import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from forwardbot.copier import _finalize_download_path, build_download_path


class UploadPathTests(unittest.TestCase):
    def make_message(self, **overrides: object) -> SimpleNamespace:
        fields = {
            "photo": None,
            "video": None,
            "animation": None,
            "audio": None,
            "voice": None,
            "video_note": None,
            "sticker": None,
            "document": None,
            "location": None,
            "venue": None,
            "contact": None,
            "poll": None,
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def test_photo_download_path_uses_jpg_extension(self) -> None:
        message = self.make_message(photo=SimpleNamespace())

        path = build_download_path(message, Path("downloads"))

        self.assertEqual(path.parent, Path("downloads"))
        self.assertEqual(path.suffix, ".jpg")
        self.assertTrue(path.name.startswith("photo_"))

    def test_static_sticker_download_path_uses_webp_extension(self) -> None:
        message = self.make_message(
            sticker=SimpleNamespace(is_animated=False, is_video=False)
        )

        path = build_download_path(message, Path("downloads"))

        self.assertEqual(path.suffix, ".webp")
        self.assertTrue(path.name.startswith("sticker_"))

    def test_animated_sticker_download_path_uses_tgs_extension(self) -> None:
        message = self.make_message(
            sticker=SimpleNamespace(is_animated=True, is_video=False)
        )

        path = build_download_path(message, Path("downloads"))

        self.assertEqual(path.suffix, ".tgs")

    def test_document_download_path_preserves_file_extension(self) -> None:
        message = self.make_message(
            document=SimpleNamespace(
                file_name="report.pdf", mime_type="application/pdf"
            )
        )

        path = build_download_path(message, Path("downloads"))

        self.assertEqual(path.suffix, ".pdf")
        self.assertTrue(path.name.startswith("document_"))

    def test_finalize_download_path_renames_file_when_suffix_is_wrong(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            downloaded = tmp_path / "telegram-file"
            desired = tmp_path / "photo_fixed.jpg"
            downloaded.write_bytes(b"image-bytes")

            final_path = _finalize_download_path(downloaded, desired)

            self.assertEqual(final_path, desired)
            self.assertTrue(desired.exists())
            self.assertFalse(downloaded.exists())
            self.assertEqual(desired.read_bytes(), b"image-bytes")


if __name__ == "__main__":
    unittest.main()
