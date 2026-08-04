#!/usr/bin/env python3
"""Render checksum-pinned Homebrew and AUR assets from a built sdist."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    if not match:
        raise SystemExit("version missing from pyproject.toml")
    version = match.group(1)
    candidates = sorted((root / "dist").glob(f"konsole_pal-{version}.tar.gz"))
    if len(candidates) != 1:
        raise SystemExit(f"expected one sdist for {version}; run uv build first")
    digest = hashlib.sha256(candidates[0].read_bytes()).hexdigest()
    url = (
        "https://files.pythonhosted.org/packages/source/k/konsole-pal/"
        f"konsole_pal-{version}.tar.gz"
    )
    output = root / "packaging" / "generated"
    output.mkdir(mode=0o755, parents=True, exist_ok=True)
    formula = f'''class KonsolePal < Formula
  include Language::Python::Virtualenv

  desc "Tiny local-first attention router for terminal AI agents"
  homepage "https://github.com/daredoole/konsole-pal"
  url "{url}"
  sha256 "{digest}"
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "{version}", shell_output("#{{bin}}/konsole-pal --version")
  end
end
'''
    pkgbuild = f'''pkgname=konsole-pal
pkgver={version}
pkgrel=1
pkgdesc="Tiny local-first attention router for terminal AI agents"
arch=('any')
url="https://github.com/daredoole/konsole-pal"
license=('MIT')
depends=('python')
makedepends=('python-build' 'python-installer' 'python-wheel')
source=("$pkgname-$pkgver.tar.gz::{url}")
sha256sums=('{digest}')

build() {{
  cd "$srcdir/konsole_pal-$pkgver"
  python -m build --wheel --no-isolation
}}

package() {{
  cd "$srcdir/konsole_pal-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}}
'''
    (output / "konsole-pal.rb").write_text(formula, encoding="utf-8")
    (output / "PKGBUILD").write_text(pkgbuild, encoding="utf-8")
    (output / "SHA256SUMS").write_text(
        f"{digest}  {candidates[0].name}\n", encoding="ascii"
    )
    print(f"Rendered packaging assets for {version} ({digest[:12]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
