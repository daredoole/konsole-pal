import io
import unittest
from unittest.mock import patch

from konsole_pal.presentation import brand, paint, rows


class TtyBuffer(io.StringIO):
    def isatty(self):
        return True


class PresentationTests(unittest.TestCase):
    def test_non_tty_output_contains_no_ansi_sequences(self):
        stream = io.StringIO()
        output = brand(stream=stream)
        self.assertIn("Konsole Pal", output)
        self.assertNotIn("\x1b[", output)

    def test_no_color_disables_ansi_even_for_tty(self):
        stream = TtyBuffer()
        with patch.dict("os.environ", {"TERM": "xterm", "NO_COLOR": "1"}, clear=False):
            self.assertEqual(paint("ready", "clover", stream=stream), "ready")

    def test_color_adapts_to_terminal_capability(self):
        stream = TtyBuffer()
        with patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True):
            self.assertIn("\x1b[32m", paint("ready", "clover", stream=stream))
        with patch.dict(
            "os.environ",
            {"TERM": "xterm-256color", "COLORTERM": "truecolor"},
            clear=True,
        ):
            self.assertIn("38;2;115;201;139", paint("ready", "clover", stream=stream))

    def test_status_rows_align_labels_before_values(self):
        output = rows(
            [("Tab", "Ready", "ok"), ("Longer label", "Calm", "info")],
            stream=io.StringIO(),
        )
        lines = output.splitlines()
        self.assertEqual(lines[0].index("Ready"), lines[1].index("Calm"))


if __name__ == "__main__":
    unittest.main()
