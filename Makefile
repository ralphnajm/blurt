# Build, check, install and package Blurt.
#
#   make check                         format, lint, test, validate data files and release notes
#   make install PREFIX=~/.local       install for the current user
#   sudo make install                  install system-wide into /usr/local
#   make rpm / make deb                build a Fedora / Ubuntu package
#   make update                        install the latest GitHub release with dnf or apt

APP_ID    := dev.blurt.Blurt
BIN       := blurt
EXTENSION := blurt@dev.blurt
PREFIX    ?= /usr/local
DESTDIR   ?=
CARGO     ?= cargo

BINDIR    := $(DESTDIR)$(PREFIX)/bin
DATADIR   := $(DESTDIR)$(PREFIX)/share
# systemd looks for user units in ~/.local/share/systemd/user and <prefix>/lib/systemd/user.
ifeq ($(PREFIX),$(HOME)/.local)
UNITDIR   := $(DATADIR)/systemd/user
else
UNITDIR   := $(DESTDIR)$(PREFIX)/lib/systemd/user
endif
ICON_SIZES := 16x16 24x24 32x32 48x48 64x64 128x128 256x256 512x512

.PHONY: all build release run test fmt fmt-check lint validate check-version check install uninstall rpm deb update clean

all: release

build:
	$(CARGO) build

release:
	$(CARGO) build --release --locked

run:
	$(CARGO) run -- $(ARGS)

test:
	$(CARGO) test --locked

fmt:
	$(CARGO) fmt

fmt-check:
	$(CARGO) fmt --check

lint:
	$(CARGO) clippy --all-targets --locked -- -D warnings

validate:
	desktop-file-validate data/$(APP_ID).desktop
	appstreamcli validate --no-net data/$(APP_ID).metainfo.xml

# The current version must have CHANGELOG.md notes and an AppStream <release> entry.
check-version:
	scripts/check-version.sh

check: fmt-check lint test validate check-version

install: release
	install -Dm755 target/release/$(BIN) $(BINDIR)/$(BIN)
	install -d $(UNITDIR)
	sed 's|^ExecStart=.*|ExecStart=$(PREFIX)/bin/$(BIN) daemon|' data/blurt.service > $(UNITDIR)/blurt.service
	chmod 644 $(UNITDIR)/blurt.service
	install -Dm644 data/$(APP_ID).desktop $(DATADIR)/applications/$(APP_ID).desktop
	install -Dm644 data/$(APP_ID).metainfo.xml $(DATADIR)/metainfo/$(APP_ID).metainfo.xml
	for size in $(ICON_SIZES); do \
		install -Dm644 data/icons/hicolor/$$size/apps/$(APP_ID).png $(DATADIR)/icons/hicolor/$$size/apps/$(APP_ID).png; \
	done
	install -d $(DATADIR)/gnome-shell/extensions/$(EXTENSION)/icons
	install -m644 data/gnome-shell/$(EXTENSION)/*.* $(DATADIR)/gnome-shell/extensions/$(EXTENSION)/
	install -m644 data/gnome-shell/$(EXTENSION)/icons/* $(DATADIR)/gnome-shell/extensions/$(EXTENSION)/icons/
	@if [ -z "$(DESTDIR)" ]; then \
		gtk-update-icon-cache -qtf $(DATADIR)/icons/hicolor 2>/dev/null || true; \
		update-desktop-database -q $(DATADIR)/applications 2>/dev/null || true; \
	fi

uninstall:
	rm -f $(BINDIR)/$(BIN) $(UNITDIR)/blurt.service
	rm -f $(DATADIR)/applications/$(APP_ID).desktop $(DATADIR)/metainfo/$(APP_ID).metainfo.xml
	for size in $(ICON_SIZES); do rm -f $(DATADIR)/icons/hicolor/$$size/apps/$(APP_ID).png; done
	rm -rf $(DATADIR)/gnome-shell/extensions/$(EXTENSION)

# Requires `cargo install cargo-generate-rpm`.
# RPM versions can't contain '-': a pre-release (1.0.0-alpha.1) becomes 1.0.0~alpha.1,
# which RPM sorts before 1.0.0.
rpm: release
	$(CARGO) generate-rpm --set-metadata 'version = "$(shell scripts/version.sh | tr - '~')"'

# Requires `cargo install cargo-deb`.
deb: release
	$(CARGO) deb --no-build

update:
	scripts/install.sh

clean:
	$(CARGO) clean
