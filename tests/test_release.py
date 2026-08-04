import re
import unittest
from pathlib import Path

from konsole_pal import __version__


class ReleaseContractTests(unittest.TestCase):
    def test_runtime_metadata_changelog_and_package_version_match(self):
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), __version__)
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {__version__} -", changelog)

    def test_readme_keeps_privacy_and_attention_contract_visible(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        for phrase in (
            "No network access",
            "attention inbox",
            "without scraping terminal output",
            "never stored",
        ):
            self.assertIn(phrase, readme)


if __name__ == "__main__":
    unittest.main()
