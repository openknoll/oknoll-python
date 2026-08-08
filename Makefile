# OpenKnoll Python workspace targets. CI reuses these — keep them the single entrypoint.
# Run `make install` after dependency changes; the other targets use --no-sync to
# keep repeated invocations fast.

.PHONY: install lint typecheck test security build smoke

install:
	uv sync --all-packages

lint:
	uv run --no-sync ruff format --check .
	uv run --no-sync ruff check .

typecheck:
	uv run --no-sync mypy packages

test:
	uv run --no-sync pytest

# Release gates (design §18.1): a failure here stops a release. Also part of `test`.
security:
	uv run --no-sync pytest \
		packages/connectors/tests/test_ssrf_corpus.py \
		packages/okf-core/tests/test_prompt_injection_corpus.py -v

build:
	uv build --all-packages

smoke:
	./scripts/smoke.sh
