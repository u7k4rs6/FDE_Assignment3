PY ?= .venv/bin/python

.PHONY: setup verify run test e2e eval public-evals

setup:            ## create venv + install runtime and dev deps + Playwright browser
	python3 -m venv .venv
	$(PY) -m pip install -q -r requirements-dev.txt
	$(PY) -m playwright install chromium
	@test -f .env || cp .env.example .env

verify:
	$(PY) verify_setup.py

run:              ## mock API + Streamlit UI (http://127.0.0.1:8501)
	$(PY) run_local.py

test:             ## unit tests (offline, no LLM)
	$(PY) -m pytest -q

e2e:              ## browser tests against the live UI (uses the LLM)
	RUN_E2E=1 $(PY) -m pytest tests/e2e -q

public-evals:     ## grader's public runner for both architectures (auto-starts the mock API)
	$(PY) scripts/run_public_evals.py --architecture single
	$(PY) scripts/run_public_evals.py --architecture staged

eval:             ## full comparison: gold + perturbations, both architectures + rules control, 3 repeats
	$(PY) scripts/run_full_eval.py
