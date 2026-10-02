#!/usr/bin/env bash
set -euo pipefail

die() {
    echo "build-deb: $*" >&2
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
for tool in dpkg-deb desktop-file-validate; do
    command -v "$tool" >/dev/null || die "$tool missing: sudo apt-get install python3-pip desktop-file-utils"
done
python3 -m pip --version >/dev/null 2>&1 ||
    die "pip missing: sudo apt-get install python3-pip desktop-file-utils"

top="$(mktemp -d)"
trap 'rm -rf "$top"' EXIT
git archive --format=tar --prefix=src/ "$tag" | tar -x -C "$top"
src="$top/src"
[[ -d "$src/packaging/deb" ]] || die "$tag has no packaging/deb; cut a release that includes it"

# Blurt and sounddevice (not packaged by Ubuntu) live in a private directory;
# everything else comes from apt through the Depends in packaging/deb/control.
pkg="$top/pkg"
lib="$pkg/usr/lib/blurt"
mkdir -p "$lib"
cp -r "$src/blurt" "$lib/"
mkdir "$lib/blurt-$version.dist-info"
printf 'Metadata-Version: 2.1\nName: blurt\nVersion: %s\n' "$version" \
    > "$lib/blurt-$version.dist-info/METADATA"
sounddevice="$(sed -n '/^name = "sounddevice"$/{n;s/^version = "\(.*\)"$/\1/p}' "$src/poetry.lock")"
[[ -n "$sounddevice" ]] || die "sounddevice not found in poetry.lock at $tag"
python3 -m pip download -q --no-deps --only-binary=:all: --dest "$top/wheels" \
    "sounddevice==$sounddevice"
python3 -m zipfile -e "$top"/wheels/sounddevice-*.whl "$lib"
python3 -m compileall -q --invalidation-mode unchecked-hash -s "$pkg" -p / "$lib"

install -Dm755 "$src/packaging/deb/blurt" "$pkg/usr/bin/blurt"
"$src/packaging/install-data.sh" "$pkg"
desktop-file-validate "$pkg/usr/share/applications/dev.blurt.Blurt.desktop"
chmod -R u=rwX,go=rX "$pkg"

mkdir "$pkg/DEBIAN"
sed -e "s/@VERSION@/$version-1/" -e "s/@INSTALLED_SIZE@/$(du -sk "$pkg" | cut -f1)/" \
    "$src/packaging/deb/control" > "$pkg/DEBIAN/control"
install -m755 "$src/packaging/deb/postinst" "$src/packaging/deb/prerm" "$pkg/DEBIAN/"

mkdir -p "$root/dist"
dpkg-deb --root-owner-group -Zxz --build "$pkg" "$root/dist/blurt_${version}-1_all.deb" >/dev/null
ls -1 "$root/dist/blurt_${version}-1_all.deb"
