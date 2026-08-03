import random
import string
import unicodedata
import unittest

from knsole_pal.core import (
    FINAL_ICONS,
    LABEL_MAX_CELLS,
    WORKING_FRAMES,
    KonsoleTarget,
    SessionState,
    sanitize_label,
    text_cell_width,
    title_for,
)


class TabVisualContractTests(unittest.TestCase):
    def setUp(self):
        self.target = KonsoleTarget("service", "/Sessions/1", "/Windows/1")

    def test_every_icon_occupies_exactly_four_terminal_cells(self):
        icons = (*WORKING_FRAMES, *FINAL_ICONS.values())
        self.assertEqual({text_cell_width(icon) for icon in icons}, {4})

    def test_animation_anchors_the_rabbit_and_contains_no_padding_or_dust(self):
        positions = [frame.index("🐇") for frame in WORKING_FRAMES]
        names = [
            unicodedata.name(char, "")
            for frame in WORKING_FRAMES
            for char in frame
        ]
        self.assertEqual(set(positions), {0})
        self.assertEqual({len(frame) for frame in WORKING_FRAMES}, {2})
        has_whitespace = any(
            char.isspace() for frame in WORKING_FRAMES for char in frame
        )
        self.assertFalse(has_whitespace)
        self.assertTrue(all("BRAILLE" not in name for name in names))

    def test_label_starts_at_the_same_cell_in_every_frame_and_state(self):
        labels = []
        for frame in range(len(WORKING_FRAMES)):
            title = title_for(
                SessionState("working", "marker", "codex", self.target), frame
            )
            labels.append(text_cell_width(title[: title.index("marker")]))
        for state in FINAL_ICONS:
            title = title_for(SessionState(state, "marker", "codex", self.target))
            labels.append(text_cell_width(title[: title.index("marker")]))
        self.assertEqual(set(labels), {5})

    def test_long_or_hostile_labels_cannot_resize_tabs_without_bound(self):
        labels = (
            "x" * 1000,
            " very\twide\nlabel ",
            "repo\x1b]0;stolen\x07",
            "项目" * 100,
            "🐇✨" * 100,
            "\x00\x01\x7f",
        )
        for label in labels:
            with self.subTest(label=repr(label[:20])):
                cleaned = sanitize_label(label)
                self.assertLessEqual(text_cell_width(cleaned), LABEL_MAX_CELLS)
                self.assertFalse(
                    any(unicodedata.category(c).startswith("C") for c in cleaned)
                )
                for state in ("working", *FINAL_ICONS):
                    title = title_for(
                        SessionState(state, label, "codex", self.target)
                    )
                    self.assertLessEqual(
                        text_cell_width(title), 5 + LABEL_MAX_CELLS
                    )

    def test_seeded_label_fuzz_preserves_width_and_control_contract(self):
        randomizer = random.Random(20260803)
        alphabet = string.printable + "项目🐇✨\x00\x1b\u200b"
        for _ in range(1000):
            raw = "".join(randomizer.choices(alphabet, k=randomizer.randrange(200)))
            cleaned = sanitize_label(raw)
            self.assertLessEqual(text_cell_width(cleaned), LABEL_MAX_CELLS)
            self.assertFalse(
                any(unicodedata.category(c).startswith("C") for c in cleaned)
            )


if __name__ == "__main__":
    unittest.main()
