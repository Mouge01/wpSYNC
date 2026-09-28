PYTHON ?= /usr/bin/python3

.PHONY: help check test dist

help:
	@echo "make check  Validate schemas, syntax, and isolated tests (normal user)"
	@echo "make dist   Build a reproducible source archive and SHA-256 checksum"
	@echo "./install.sh --plan  Inspect the native installer without installing"

check:
	$(PYTHON) -B tools/check.py

test: check

dist:
	$(PYTHON) -B tools/build_release.py
