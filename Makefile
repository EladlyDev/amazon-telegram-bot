.PHONY: setup run dev clean

VENV := venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@test -f .env || cp .env.example .env
	@mkdir -p data logs
	@echo "✅ Setup complete. Activate venv with: source $(VENV)/bin/activate"

run:
	$(PYTHON) -m src.main

dev:
	LOG_LEVEL=DEBUG $(PYTHON) -m src.main

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "🧹 Cleaned up."
