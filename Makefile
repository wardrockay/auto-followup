.PHONY: help install install-dev test test-cov lint format type-check clean run docker-build docker-run

# Default target
help:
	@echo "Available commands:"
	@echo "  make install       - Install production dependencies"
	@echo "  make install-dev   - Install development dependencies"
	@echo "  make test          - Run tests"
	@echo "  make test-cov      - Run tests with coverage"
	@echo "  make lint          - Run linters"
	@echo "  make format        - Format code with black and ruff"
	@echo "  make type-check    - Run mypy type checking"
	@echo "  make clean         - Clean build artifacts"
	@echo "  make run           - Run development server"
	@echo "  make docker-build  - Build Docker image"
	@echo "  make docker-run    - Run Docker container"

# Installation
install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"
	pre-commit install

# Testing
test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=src/auto_followup --cov-report=term-missing --cov-report=html

# Linting & Formatting
lint:
	ruff check src/ tests/
	mypy src/

format:
	black src/ tests/
	ruff check --fix src/ tests/

type-check:
	mypy src/

# Cleaning
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Development Server
run:
	ENVIRONMENT=development python -m auto_followup.app

run-gunicorn:
	gunicorn --bind :8080 --workers 1 --threads 8 "auto_followup.app:app"

# Docker
docker-build:
	docker build -t auto-followup:latest .

docker-run:
	docker run -p 8080:8080 \
		-e ENVIRONMENT=development \
		-e DRAFT_COLLECTION=email_drafts \
		-e FOLLOWUP_COLLECTION=email_followups \
		auto-followup:latest

# Pre-commit hooks
pre-commit:
	pre-commit run --all-files
