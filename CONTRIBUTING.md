# Contributing

Use Python 3.10 or newer and install `uv`. Run `make test` while developing and `make verify` before opening a pull request. New behavior should include a regression test; terminal UI changes should assert equal terminal-cell widths for every frame.

Please keep hooks fast, asynchronous, private by default, and tolerant of malformed or missing event payloads. Do not add prompt or transcript storage.
