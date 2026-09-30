VERSION ?=
NOTES ?=
TAG ?=
LATEST_RPM = $(shell ls -1 dist/blurt-*.rpm 2>/dev/null | sort -V | tail -1)

.PHONY: check release rpm install icons build-deps

check:
	poetry run ruff format --check blurt scripts
	poetry run ruff check blurt scripts
	poetry run mypy blurt

release:
	scripts/release.sh "$(VERSION)" "$(NOTES)"

rpm:
	scripts/build-rpm.sh $(TAG)

install:
	@test -n "$(LATEST_RPM)" || { echo "No RPM in dist/, run make rpm first"; exit 1; }
	sudo dnf install -y ./$(LATEST_RPM)

icons:
	poetry run python scripts/make-icons.py

build-deps:
	sudo dnf install -y rpm-build systemd-rpm-macros python3-devel pyproject-rpm-macros python3-poetry-core desktop-file-utils
