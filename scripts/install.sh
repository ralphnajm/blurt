#!/bin/sh
# Installs (or updates to) the latest Blurt release: downloads the .deb or .rpm
# for this distribution from GitHub, installs it with apt or dnf, then deletes
# the download.
#
#   scripts/install.sh
#
# The repository is private, so the download goes through the GitHub CLI when
# it's logged in (`gh auth login`), and through the public API otherwise.
set -eu

REPO=ralphnajm/blurt

fail() {
    echo "blurt: $*" >&2
    exit 1
}

# Everything runs from here, so a download cut short runs nothing.
main() {
    [ "$(uname -m)" = x86_64 ] || fail "packages are only built for x86_64; build from source with make install"

    # The package format, from the distribution or else the package manager it has.
    like=""
    if [ -r /etc/os-release ]; then
        like=$(. /etc/os-release && echo " ${ID:-} ${ID_LIKE:-} ")
    fi
    case $like in
        *" debian "* | *" ubuntu "*) format=deb ;;
        *" fedora "* | *" rhel "*) format=rpm ;;
        *)
            if command -v apt-get > /dev/null; then
                format=deb
            elif command -v dnf > /dev/null; then
                format=rpm
            else
                fail "no package for this distribution; build from source with make install"
            fi
            ;;
    esac

    if [ "$(id -u)" -eq 0 ]; then
        sudo=""
    elif command -v sudo > /dev/null; then
        sudo=sudo
    else
        fail "installing needs root: run this as root or install sudo"
    fi

    dir=$(mktemp -d)
    trap 'rm -rf "$dir"' EXIT
    trap 'exit 1' INT TERM
    # Readable by apt's unprivileged downloader.
    chmod 755 "$dir"

    echo "Downloading the latest release of Blurt…"
    if command -v gh > /dev/null && gh auth status > /dev/null 2>&1; then
        gh release download --repo "$REPO" --pattern "*.$format" --dir "$dir" ||
            fail "the latest release has no .$format package"
    else
        command -v curl > /dev/null || fail "curl or the GitHub CLI is needed to download the package"
        url=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" |
            sed -n "s/.*\"browser_download_url\": *\"\([^\"]*\.$format\)\".*/\1/p" | head -n 1) ||
            fail "could not reach the release; for a private repository, log in with gh auth login"
        [ -n "$url" ] || fail "the latest release has no .$format package"
        curl -fL --progress-bar -o "$dir/${url##*/}" "$url"
    fi
    package=$(find "$dir" -name "*.$format" | head -n 1)
    [ -n "$package" ] || fail "the latest release has no .$format package"
    chmod 644 "$package"

    echo "Installing ${package##*/}…"
    case $format in
        deb) $sudo apt-get install -y "$package" ;;
        rpm) $sudo dnf install -y "$package" ;;
    esac
    echo "Blurt is installed: open it from your apps and click Set up, or run blurt setup."
}

main "$@"
