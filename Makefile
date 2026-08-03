.PHONY: test integration lint security build verify

test:
	uv run --extra dev python -m unittest discover -s tests -v

integration:
	KNSOLE_PAL_INTEGRATION=1 uv run --extra dev python -m unittest discover -s tests -v

lint:
	uv run --extra dev ruff check src tests

security:
	uvx bandit -r src -q
	uvx detect-secrets scan --all-files --exclude-files '(^|/)(\.git|\.venv|build|dist|graphify-out|\.pytest_cache|\.ruff_cache)/' | python -c 'import json, sys; data=json.load(sys.stdin); findings=sum(map(len, data["results"].values())); print(f"secret findings: {findings}"); raise SystemExit(findings != 0)'
	uvx --with tomli pip-audit --local --skip-editable

build:
	uvx --with packaging validate-pyproject pyproject.toml
	uvx check-manifest
	uv build
	uv run --extra dev twine check dist/*
	uvx check-wheel-contents dist/*.whl

verify: security lint integration build
	uv run --extra dev python -m compileall -q src tests
