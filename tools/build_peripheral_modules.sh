#!/bin/bash
# Build only external RAM trial modules; never modify or install on a device.
set -eu
PROJECT=$(cd "$(dirname "$0")/.." && pwd)
OPENWRT=${OPENWRT_TREE:-/home/dell/m3100-openwrt/openwrt}
KERNEL=${KERNEL_TREE:-$OPENWRT/build_dir/target-arm_cortex-a7+neon-vfpv4_musl_eabi/linux-sdx_generic/linux-6.12.112}
export STAGING_DIR="$OPENWRT/staging_dir"
export PATH="$STAGING_DIR/toolchain-arm_cortex-a7+neon-vfpv4_gcc-14.3.0_musl_eabi/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
test -s "$KERNEL/Module.symvers"
MODULE_BUILD=$(mktemp -d /tmp/m3100-peripheral-modules.XXXXXX)
trap 'rm -f "$MODULE_BUILD/.m3100-build-active"' EXIT
touch "$MODULE_BUILD/.m3100-build-active"
cp "$PROJECT/tools/lcd-qpic.c" "$PROJECT/tools/lcd-qpic-init.h" \
   "$PROJECT/tools/lcd-qpic-pio.h" "$PROJECT/tools/m3100-qg-telemetry.c" \
   "$PROJECT/tools/bt-m3100-power.c" "$MODULE_BUILD/"
printf '%s\n' 'obj-m += lcd-qpic.o' 'obj-m += m3100-qg-telemetry.o' \
  'obj-m += bt-m3100-power.o' > "$MODULE_BUILD/Kbuild"
make -j4 -C "$KERNEL" M="$MODULE_BUILD" ARCH=arm \
  CROSS_COMPILE=arm-openwrt-linux-muslgnueabi- modules
mkdir -p "$PROJECT/analysis/peripheral-modules"
cp "$MODULE_BUILD/lcd-qpic.ko" "$MODULE_BUILD/m3100-qg-telemetry.ko" \
   "$MODULE_BUILD/bt-m3100-power.ko" \
   "$PROJECT/analysis/peripheral-modules/"
sha256sum "$PROJECT/analysis/peripheral-modules/"*.ko
printf 'Build retained at %s\n' "$MODULE_BUILD"
