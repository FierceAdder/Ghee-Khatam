TOOLKIT ?= ../Secret-Wards-Toolkit
INPUT   ?= $(TOOLKIT)/dev/input
OUTPUT  ?= ./my_output
PYTHON  ?= python3
COMPOSE ?= docker compose

export TOOLKIT_DIR := $(abspath $(TOOLKIT))
export INPUT_DIR   := $(abspath $(INPUT))
export OUTPUT_DIR  := $(abspath $(OUTPUT))
export HOST_UID    := $(shell id -u)
export HOST_GID    := $(shell id -g)

.DEFAULT_GOAL := help
.PHONY: help build run test dev run-local test-local dev-local check clean secret-set clean-output

help:
	@echo "Docker (recommended)"
	@echo "  make build                         build the images"
	@echo "  make run  [INPUT=dir OUTPUT=dir]   sanitise a folder (needs RAKSHA_SECRET)"
	@echo "  make test                          run the test suite"
	@echo "  make dev  [SHOW=20]                run + validate + self-check + determinism"
	@echo
	@echo "Without Docker"
	@echo "  make run-local / test-local / dev-local"
	@echo "  make check                         the organisers' pre-submit check"
	@echo "  make clean                         remove local outputs and caches"

build:
	$(COMPOSE) --profile test build

secret-set:
	@test -n "$$RAKSHA_SECRET" || { echo "set RAKSHA_SECRET first, e.g. export RAKSHA_SECRET=\$$(openssl rand -hex 32)"; exit 2; }

# Removes only what a previous run wrote into OUTPUT.
clean-output:
	@test -n "$(strip $(OUTPUT))" || { echo "OUTPUT must not be empty"; exit 2; }
	rm -rf "$(OUTPUT_DIR)/sanitized" "$(OUTPUT_DIR)/report.json" "$(OUTPUT_DIR)/records.jsonl" \
		"$(OUTPUT_DIR)/logs/run.jsonl"
	mkdir -p "$(OUTPUT_DIR)"

run: secret-set clean-output
	$(COMPOSE) run --rm --build firewall

test:
	$(COMPOSE) --profile test run --rm --build test

dev:
	$(COMPOSE) --profile dev run --rm --build dev

run-local: secret-set clean-output
	$(PYTHON) main.py --input "$(INPUT_DIR)" --output "$(OUTPUT_DIR)"

test-local:
	$(PYTHON) -B -m pytest -q

dev-local:
	PYTHON="$(PYTHON)" tools/run_dev.sh

check:
	$(PYTHON) "$(TOOLKIT_DIR)/check_submission.py" --repo . --run --input "$(INPUT_DIR)"

clean:
	rm -rf my_output
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
