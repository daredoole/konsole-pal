import json
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
        flake = (root / "flake.nix").read_text(encoding="utf-8")
        nix_version = re.search(r'^\s*version = "([^"]+)";', flake, re.MULTILINE)
        self.assertIsNotNone(nix_version)
        self.assertEqual(nix_version.group(1), __version__)
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

    def test_uv_and_nix_release_inputs_are_reproducible(self):
        root = Path(__file__).resolve().parents[1]
        lock = json.loads((root / "flake.lock").read_text(encoding="utf-8"))
        nixpkgs = lock["nodes"]["nixpkgs"]["locked"]
        self.assertRegex(nixpkgs["rev"], r"^[0-9a-f]{40}$")
        self.assertTrue(nixpkgs["narHash"].startswith("sha256-"))

        ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("cachix/install-nix-action@630ae543", ci)
        self.assertIn("nix flake check --print-build-logs", ci)

        locked_commands = "\n".join(
            (root / path).read_text(encoding="utf-8")
            for path in (
                "Makefile",
                ".github/workflows/ci.yml",
                ".github/workflows/release.yml",
            )
        )
        self.assertNotRegex(locked_commands, r"uv run (?![^\n]*--locked)")
        manifest = (root / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("flake.lock", manifest)


if __name__ == "__main__":
    unittest.main()
