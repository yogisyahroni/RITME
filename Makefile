# RITME — Makefile (sama standar dengan LANCAR)
# Python: venv_311 (Windows) / .venv (Linux/Mac)
PY ?= ./venv_311/Scripts/python.exe

.PHONY: test test-server test-clip lint pre-push

test:
	$(PY) tests/test_roadmap_features.py

test-server:
	$(PY) tests/test_roadmap_features.py --with-server

test-clip:
	$(PY) tests/test_roadmap_features.py --with-clip

lint:
	$(PY) -m py_compile server.py config.py job_manager.py pipeline/*.py
	@echo "py_compile OK"

pre-push: lint test-server
	@echo "Semua lulus — siap push ✅"
