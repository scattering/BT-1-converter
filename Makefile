PYTHON ?= python3
BT1_ARCHIVE_ROOT ?= /Users/williamratcliff/Documents/Codex/2026-11-03/task-4
BT1_AUDIT_ROOT ?= $(BT1_ARCHIVE_ROOT)/bt1_audit
BT1_REAL_FIXTURE_DIR ?= $(BT1_ARCHIVE_ROOT)/bt1_retrieval
BT1_LEGACY_FIXTURE_DIR ?= /Users/williamratcliff/Library/CloudStorage/Box-Box/gformat

.PHONY: help test test-full
help:
	@echo 'make test: available unit checks; optional fixtures may skip'
	@echo 'make test-full: include external Fortran oracle and real fixtures'
	@echo 'Select an interpreter with PYTHON=.venv/bin/python'

test:
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" -m unittest discover -s tests -p '*test*.py' -v

test-full:
	@test -d "$(BT1_AUDIT_ROOT)" || { echo 'BT1_AUDIT_ROOT is missing'; exit 1; }
	@test -d "$(BT1_REAL_FIXTURE_DIR)" || { echo 'BT1_REAL_FIXTURE_DIR is missing'; exit 1; }
	@test -d "$(BT1_LEGACY_FIXTURE_DIR)" || { echo 'BT1_LEGACY_FIXTURE_DIR is missing'; exit 1; }
	BT1_AUDIT_ROOT="$(BT1_AUDIT_ROOT)" BT1_REAL_FIXTURE_DIR="$(BT1_REAL_FIXTURE_DIR)" BT1_LEGACY_FIXTURE_DIR="$(BT1_LEGACY_FIXTURE_DIR)" PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" -m unittest discover -s tests -p '*test*.py' -v
