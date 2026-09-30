#!/usr/bin/env bash
set -euo pipefail

die() {
    echo "build-rpm: $*" >&2
    exit 1
}

cd "$(dirname "$0")/.."
root="$(pwd)"
tag="${1:-$(git tag --list 'v[0-9]*' --sort=-v:refname | head -1)}"
[[ -n "$tag" ]] || die "no release tag yet; cut one with scripts/release.sh X.Y.Z \"notes\""
git rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "unknown tag $tag"
version="${tag#v}"
tagged="$(git show "$tag:pyproject.toml" | sed -n 's/^version = "\(.*\)"$/\1/p')"
[[ "$tagged" == "$version" ]] || die "pyproject.toml at $tag says $tagged, not $version"
command -v rpmbuild >/dev/null ||
    die "rpmbuild missing: sudo dnf install rpm-build systemd-rpm-macros python3-devel pyproject-rpm-macros python3-poetry-core desktop-file-utils"

top="$(mktemp -d)"
trap 'rm -rf "$top"' EXIT
mkdir -p "$top"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}
git archive --format=tar.gz --prefix="blurt-$version/" -o "$top/SOURCES/blurt-$version.tar.gz" "$tag"
git show "$tag:packaging/blurt.spec" > "$top/SPECS/blurt.spec"

while read -r release; do
    [[ "$(printf '%s\n%s\n' "${release#v}" "$version" | sort -V | tail -1)" == "$version" ]] || continue
    git for-each-ref "refs/tags/$release" \
        --format='* %(taggerdate:format:%a %b %d %Y) %(taggername) %(taggeremail) - %(refname:strip=2)-1' |
        sed 's/ - v/ - /'
    git tag --list --format='%(contents:body)' "$release" | sed '/^$/d; /BEGIN PGP/,$d; s/^/- /'
    echo
done < <(git tag --list 'v[0-9]*' --sort=-v:refname) > "$top/SPECS/changelog"

rpmbuild -bb \
    --define "_topdir $top" \
    --define "_binary_payload w3T0.zstdio" \
    --define "blurt_version $version" \
    --define "blurt_changelog $top/SPECS/changelog" \
    "$top/SPECS/blurt.spec"

mkdir -p "$root/dist"
find "$top/RPMS" -name '*.rpm' -exec cp {} "$root/dist/" \;
ls -1 "$root"/dist/blurt-"$version"-*.rpm
