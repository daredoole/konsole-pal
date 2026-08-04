# Distribution assets

`flake.nix` provides a native Nix package directly from the repository.

Tagged releases build the Python wheel and source archive, calculate the source
SHA-256, and attach generated, checksum-pinned Homebrew and AUR recipes to the
GitHub release. `scripts/render-packages.py` is deterministic and refuses to
generate a recipe unless exactly one matching source distribution exists.

Publishing to Homebrew Core or the AUR still requires their normal independent
review and maintainer credentials; no repository secret or personal path is
embedded in the generated files.
