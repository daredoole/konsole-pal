.PHONY: test integration lint build verify

test:
	uv run --extra dev python -m unittest discover -s tests -v

integration:
	KNSOLE_PAL_INTEGRATION=1 uv run --extra dev python -m unittest discover -s tests -v

lint:
	uv run --extra dev ruff check src tests

build:
	uv build
	uv run --extra dev twine check dist/*

verify: lint integration build
	uv run --extra dev python -m compileall -q src tests
