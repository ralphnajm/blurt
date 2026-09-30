#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: scripts/release.sh X.Y.Z \"What changed\"" >&2
    exit 1
}

die() {
    echo "release: $*" >&2
    exit 1
}

version="${1:-}"
notes="${2:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || usage
[[ -n "$notes" ]] || usage
cd "$(dirname "$0")/.."

[[ "$(git branch --show-current)" == "main" ]] || die "releases are cut from main"
[[ -z "$(git status --porcelain)" ]] || die "working tree is not clean"
git rev-parse -q --verify "refs/tags/v$version" >/dev/null && die "tag v$version already exists"

current="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)"
latest="$(git tag --list 'v[0-9]*' --sort=-v:refname | head -1)"
if [[ -n "$latest" ]]; then
    newest="$(printf '%s\n%s\n' "${latest#v}" "$version" | sort -V | tail -1)"
    [[ "$newest" == "$version" && "${latest#v}" != "$version" ]] || die "$version is not newer than $latest"
fi

if [[ "$current" != "$version" ]]; then
    sed -i "s/^version = \"$current\"$/version = \"$version\"/" pyproject.toml
    git commit -q -m "chore: release $version" pyproject.toml
fi
git tag -a "v$version" -m "Blurt $version" -m "$notes"

echo "Tagged v$version at $(git rev-parse --short HEAD)."
echo "Next: git push --follow-tags && make rpm && make install"
