#!/usr/bin/env bash
# Private incremental build using committed f0 config/make/module/package tail.
set -euo pipefail
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET=7.2-ps4-baikal-uvd-ready-dev3
JOBS=12
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]]
[[ -z "${CROSS_COMPILE:-}" ]]
source "$REPO/targets/$TARGET.env"
SOURCE="$REPO/src/$TARGET"
OUTPUT="$REPO/output/$TARGET"
SERIES="$REPO/$SERIES_FILE"
CONFIG="$REPO/$CONFIG_FILE"
ACTUAL_BASE="$(git -C "$SOURCE" rev-parse HEAD)"
[[ "$ACTUAL_BASE" == "$BASE_COMMIT" ]]
[[ "$(sha256sum "$SOURCE/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c" | awk '{print $1}')" == 203a633fd66b4dddd1daaacae43e62970c5e167a01939b13cd06c2f1cedf63d9 ]]
PATCHSET="$(mktemp)"
trap 'rm -f "$PATCHSET"' EXIT
COUNT=0
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    patch="$REPO/$PATCHES_DIR/$line"
    [[ -f "$patch" ]]
    printf '%s  %s\n' "$(sha256sum "$patch" | awk '{print $1}')" "$line" >> "$PATCHSET"
    COUNT=$((COUNT + 1))
done < "$SERIES"
[[ "$COUNT" == 66 ]]
cd "$SOURCE"
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
# Refresh copied generated metadata before asserting the distinct release.
make "${MAKE_BASE[@]}" syncconfig
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
