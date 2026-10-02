VERSION ?=
NOTES ?=
TAG ?=
LATEST_RPM = $(shell ls -1 dist/blurt-*.rpm 2>/dev/null | sort -V | tail -1)
LATEST_DEB = $(shell ls -1 dist/blurt_*.deb 2>/dev/null | sort -V | tail -1)
APT = $(shell command -v apt-get 2>/dev/null)

.PHONY: check release rpm deb install icons build-deps

check:
	poetry run ruff format --check blurt scripts
	poetry run ruff check blurt scripts
	poetry run mypy blurt

release:
	scripts/release.sh "$(VERSION)" "$(NOTES)"

rpm:
	scripts/build-rpm.sh $(TAG)

deb:
	scripts/build-deb.sh $(TAG)

ifneq ($(APT),)
install:
	@test -n "$(LATEST_DEB)" || { echo "No .deb in dist/, run make deb first"; exit 1; }
	sudo apt-get install -y ./$(LATEST_DEB)

build-deps:
	sudo apt-get install -y python3-pip desktop-file-utils
else
install:
	@test -n "$(LATEST_RPM)" || { echo "No RPM in dist/, run make rpm first"; exit 1; }
	sudo dnf install -y ./$(LATEST_RPM)

build-deps:
	sudo dnf install -y rpm-build systemd-rpm-macros python3-devel pyproject-rpm-macros python3-poetry-core desktop-file-utils
endif

icons:
	poetry run python scripts/make-icons.py
