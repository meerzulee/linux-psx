#!/usr/bin/env bash
# Build pinned upstream Linux plus the selected console's ordered patches.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET=6.18-ps4-baikal
CLEAN=false
PATCHES_ONLY=false
JOBS=
REFERENCE_TARGET=

usage() {
    cat <<EOF
Linux psX kernel builder
Usage: $0 [-t TARGET] [-j JOBS] [--clean] [--patches-only] [--reference-target NAME]

Default target: 6.18-ps4-baikal (Linux 6.18.44, PS4 Baikal)
  -t, --target NAME   Select targets/NAME.env
  -j, --jobs N        Parallel build jobs (default: 80% of online CPUs)
  -c, --clean         Reclone this target's generated source and remove outputs
  -p, --patches-only  Fetch the pinned source and check/apply its entire series
  --reference-target NAME
                     Seed a new clone from another marked generated target
  -h, --help          Show help

Native compilation requires Linux. On macOS ARM64 use scripts/build-kernel-orbstack.
Source under src/TARGET is generated and disposable. Outputs: output/TARGET/.
EOF
}
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--target) [[ $# -ge 2 ]] || fail "missing target"; TARGET="$2"; shift 2 ;;
        -j|--jobs) [[ $# -ge 2 ]] || fail "missing job count"; JOBS="$2"; shift 2 ;;
        -c|--clean) CLEAN=true; shift ;;
        -p|--patches-only) PATCHES_ONLY=true; shift ;;
        --reference-target) [[ $# -ge 2 ]] || fail "missing reference target"; [[ -n "$2" ]] || fail "missing reference target"; REFERENCE_TARGET="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown option: $1 (use --help)" ;;
    esac
done
[[ "$TARGET" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || fail "invalid target name"
if [[ -n "$REFERENCE_TARGET" ]]; then
    [[ "$REFERENCE_TARGET" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || fail "invalid reference target name"
    [[ "$REFERENCE_TARGET" != "$TARGET" ]] || fail "reference target must differ from selected target"
fi
[[ -f "$REPO/targets/$TARGET.env" ]] || fail "unknown target: $TARGET"
[[ "$(uname -s)" == Linux ]] || fail "use scripts/build-kernel-orbstack on macOS ARM64"
if [[ -z "$JOBS" ]]; then
    JOBS=$(( $(nproc) * 80 / 100 ))
    [[ "$JOBS" -ge 1 ]] || JOBS=1
fi
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || fail "jobs must be a positive integer"
# Target definitions are versioned shell inputs; do not source downloaded env files.
# shellcheck disable=SC1090
source "$REPO/targets/$TARGET.env"
[[ "$TARGET_NAME" == "$TARGET" ]] || fail "target name does not match definition"
[[ "$BASE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "target must pin a full upstream commit"
[[ "${COMPILER:-gcc}" == gcc ]] || fail "this builder currently supports GCC targets"
[[ "${KERNEL_ARCH:-x86_64}" == x86_64 ]] || fail "this target requires ARCH=x86_64"
[[ "${SOURCE_DATE_EPOCH:-}" =~ ^[0-9]+$ ]] || fail "missing source timestamp"
SERIES="$REPO/$SERIES_FILE"
PATCH_ROOT="$REPO/$PATCHES_DIR"
CONFIG="$REPO/$CONFIG_FILE"
[[ -f "$SERIES" && -f "$CONFIG" ]] || fail "missing target series or config"
grep -qx 'CONFIG_EXTRA_FIRMWARE=""' "$CONFIG" || fail "device firmware embedding is outside this target"
BUILD_ROOT="${PSX_KERNEL_BUILD_ROOT:-$REPO}"
mkdir -p "$BUILD_ROOT/src"
BUILD_ROOT="$(cd "$BUILD_ROOT" && pwd -P)"
[[ "$(cd "$BUILD_ROOT/src" && pwd -P)" == "$BUILD_ROOT/src" ]] || fail "generated source parent must not be a symlink"
SOURCE="$BUILD_ROOT/src/$TARGET"
MARKER="$BUILD_ROOT/src/.$TARGET.linux-psx-generated"
OUTPUT="$REPO/output/$TARGET"
[[ ! -L "$SOURCE" && ! -L "$MARKER" ]] || fail "generated source and marker must not be symlinks"
if [[ -e "$MARKER" ]]; then
    [[ -f "$MARKER" && "$(cat "$MARKER")" == "$TARGET" ]] || fail "invalid generated source marker"
fi
if [[ -e "$SOURCE" ]]; then
    [[ -d "$SOURCE" && -f "$MARKER" && -d "$SOURCE/.git" && ! -L "$SOURCE/.git" ]] ||
        fail "refusing to reset an unrecognized source directory: $SOURCE"
fi
if [[ -n "$REFERENCE_TARGET" && ( ! -d "$SOURCE" || "$CLEAN" == true ) ]]; then
    REFERENCE_SOURCE="$BUILD_ROOT/src/$REFERENCE_TARGET"
    REFERENCE_MARKER="$BUILD_ROOT/src/.$REFERENCE_TARGET.linux-psx-generated"
    [[ -d "$REFERENCE_SOURCE" && ! -L "$REFERENCE_SOURCE" && -f "$REFERENCE_MARKER" && ! -L "$REFERENCE_MARKER" &&
       "$(cat "$REFERENCE_MARKER")" == "$REFERENCE_TARGET" && -d "$REFERENCE_SOURCE/.git" && ! -L "$REFERENCE_SOURCE/.git" ]] ||
        fail "reference must be a marked generated source directory: $REFERENCE_SOURCE"
    [[ "$(git -C "$REFERENCE_SOURCE" rev-parse --absolute-git-dir)" == "$REFERENCE_SOURCE/.git" ]] ||
        fail "reference Git directory differs from its generated source"
    [[ "$(git -C "$REFERENCE_SOURCE" remote get-url origin)" == "$BASE_REPO" ]] ||
        fail "reference origin differs from selected upstream repository"
    [[ ! -s "$REFERENCE_SOURCE/.git/objects/info/alternates" && ! -L "$REFERENCE_SOURCE/.git/objects/info/alternates" ]] ||
        fail "reference must not borrow Git objects from another repository"
fi
if [[ "$CLEAN" == true ]]; then
    rm -rf "$SOURCE"
    if [[ "$PATCHES_ONLY" == false ]]; then rm -rf "$OUTPUT"; fi
fi
if [[ ! -d "$SOURCE" ]]; then
    if [[ -z "$REFERENCE_TARGET" ]]; then
        git clone --depth="$BASE_DEPTH" --branch "$BASE_REF" "$BASE_REPO" "$SOURCE"
        printf '%s\n' "$TARGET" > "$MARKER"
    else
        # Copy objects without alternates/hardlinks, then fetch only into the
        # new clone. The reference's worktree, refs and object store stay intact.
        REFERENCE_TEMP="$(mktemp -d "$BUILD_ROOT/src/.$TARGET.reference.XXXXXXXX")"
        trap 'rm -rf -- "$REFERENCE_TEMP"' EXIT
        git clone --no-hardlinks --no-checkout "$REFERENCE_SOURCE" "$REFERENCE_TEMP/source"
        git -C "$REFERENCE_TEMP/source" remote set-url origin "$BASE_REPO"
        git -C "$REFERENCE_TEMP/source" fetch --depth=1 origin "$BASE_REF"
        FETCHED_BASE="$(git -C "$REFERENCE_TEMP/source" rev-parse 'FETCH_HEAD^{commit}')"
        [[ "$FETCHED_BASE" == "$BASE_COMMIT" ]] || fail "fetched upstream commit mismatch: $FETCHED_BASE"
        git -C "$REFERENCE_TEMP/source" checkout --detach "$BASE_COMMIT"
        [[ ! -e "$SOURCE" && ! -L "$SOURCE" ]] || fail "generated source destination appeared: $SOURCE"
        # GNU mv refuses to nest into or replace a destination that appears.
        mv --no-target-directory --no-clobber -- "$REFERENCE_TEMP/source" "$SOURCE"
        [[ ! -e "$REFERENCE_TEMP/source" ]] || fail "generated source destination appeared: $SOURCE"
        if [[ -e "$MARKER" || -L "$MARKER" ]]; then
            [[ -f "$MARKER" && ! -L "$MARKER" && "$(cat "$MARKER")" == "$TARGET" ]] ||
                fail "generated source marker changed during clone"
        else
            printf '%s\n' "$TARGET" > "$REFERENCE_TEMP/marker"
            ln "$REFERENCE_TEMP/marker" "$MARKER"
        fi
        rm -rf -- "$REFERENCE_TEMP"
        trap - EXIT
    fi
fi
ACTUAL_BASE="$(git -C "$SOURCE" rev-parse HEAD)"
[[ "$ACTUAL_BASE" == "$BASE_COMMIT" ]] || fail "upstream commit mismatch: $ACTUAL_BASE"
printf 'Target: %s\nUpstream: %s\nSource: %s\n' "$TARGET" "$BASE_COMMIT" "$SOURCE"
# Only the marked, generated clone is reset. Never use this source tree for edits.
git -C "$SOURCE" reset --hard "$BASE_COMMIT"
git -C "$SOURCE" clean -fdx -q
cd "$SOURCE"
COUNT=0
PATCHSET="$(mktemp)"
trap 'rm -f "$PATCHSET"' EXIT
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    patch="$PATCH_ROOT/$line"
    [[ -f "$patch" ]] || fail "missing patch: $line"
    git apply --check --whitespace=nowarn "$patch"
    git apply --whitespace=nowarn "$patch"
    printf '%s  %s\n' "$(sha256sum "$patch" | awk '{print $1}')" "$line" >> "$PATCHSET"
    COUNT=$((COUNT + 1))
    printf 'Applied %d: %s\n' "$COUNT" "$line"
done < "$SERIES"
if [[ "$PATCHES_ONLY" == true ]]; then
    printf 'PASS: %d ordered patches applied to %s\n' "$COUNT" "$ACTUAL_BASE"
    exit 0
fi

MAKE_BASE=(ARCH=x86_64 LOCALVERSION=)
if [[ -n "${CROSS_COMPILE:-}" ]]; then MAKE_BASE+=("CROSS_COMPILE=$CROSS_COMPILE"); fi
export SOURCE_DATE_EPOCH
export KBUILD_BUILD_TIMESTAMP="$KBUILD_RELEASE_TIMESTAMP"
export KBUILD_BUILD_USER="$KBUILD_RELEASE_USER"
export KBUILD_BUILD_HOST="$KBUILD_RELEASE_HOST"
export KBUILD_BUILD_VERSION="$KBUILD_RELEASE_VERSION"
cp "$CONFIG" .config
for name in ${CONFIG_FRAGMENTS:-}; do
    [[ -f "$REPO/$name" ]] || fail "missing config fragment: $name"
    ./scripts/kconfig/merge_config.sh -m .config "$REPO/$name"
done
make "${MAKE_BASE[@]}" olddefconfig
grep -qx 'CONFIG_EXTRA_FIRMWARE=""' .config || fail "resolved config embeds device firmware"
VERSION="$(make "${MAKE_BASE[@]}" -s kernelrelease)"
[[ "$VERSION" == "$KERNEL_RELEASE" ]] || fail "unexpected release: $VERSION"
printf 'Building %s with %s jobs\n' "$VERSION" "$JOBS"
make "${MAKE_BASE[@]}" "-j$JOBS" bzImage modules
mkdir -p "$OUTPUT"
cp arch/x86/boot/bzImage "$OUTPUT/bzImage"
cp .config "$OUTPUT/config"
cp System.map "$OUTPUT/System.map"
printf '%s\n' "$VERSION" > "$OUTPUT/version.txt"
STAGING="$OUTPUT/modules"
rm -rf "$STAGING"
make "${MAKE_BASE[@]}" INSTALL_MOD_PATH="$STAGING" INSTALL_MOD_STRIP=1 modules_install
rm -f "$STAGING/lib/modules/$VERSION/build" "$STAGING/lib/modules/$VERSION/source"
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
    --zstd -C "$STAGING" -cf "$OUTPUT/modules-$VERSION.tar.zst" lib
cat > "$OUTPUT/manifest.txt" <<EOF
target=$TARGET
platform=$PLATFORM
kernel_release=$VERSION
base_repo=$BASE_REPO
base_ref=$BASE_REF
base_commit=$ACTUAL_BASE
series_sha256=$(sha256sum "$SERIES" | awk '{print $1}')
patchset_sha256=$(sha256sum "$PATCHSET" | awk '{print $1}')
config_sha256=$(sha256sum "$OUTPUT/config" | awk '{print $1}')
active_patch_count=$COUNT
compiler=$(${CROSS_COMPILE:-}gcc --version | head -1)
builder_image=${PSX_KERNEL_BUILDER_IMAGE:-native-linux}
builder_image_id=${PSX_KERNEL_BUILDER_IMAGE_ID:-unspecified}
source_date_epoch=$SOURCE_DATE_EPOCH
kbuild_identity=$KBUILD_BUILD_USER@$KBUILD_BUILD_HOST
kbuild_version=$KBUILD_BUILD_VERSION
embedded_firmware=false
EOF
cd "$OUTPUT"
sha256sum bzImage config System.map "modules-$VERSION.tar.zst" manifest.txt version.txt > SHA256SUMS
printf 'PASS: %s\nArtifacts: %s\n' "$VERSION" "$OUTPUT"
