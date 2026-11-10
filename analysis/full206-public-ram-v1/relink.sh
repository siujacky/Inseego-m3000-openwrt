#!/bin/bash
set -eu
export TZ=UTC
export LC_ALL=C
export STAGING_DIR=/home/dell/m3100-openwrt/openwrt/staging_dir
export PATH="$STAGING_DIR/toolchain-arm_cortex-a7+neon-vfpv4_gcc-14.3.0_musl_eabi/bin:$STAGING_DIR/host/bin:$STAGING_DIR/hostpkg/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
make -j8 -C /home/dell/m3100-openwrt/trials/full206-public-v1-linux-6.12.112 KCFLAGS="-fmacro-prefix-map=/home/dell/m3100-openwrt/openwrt/build_dir/target-arm_cortex-a7+neon-vfpv4_musl_eabi=target-arm_cortex-a7+neon-vfpv4_musl_eabi -fno-caller-saves " HOSTCFLAGS="-O2 -I/home/dell/m3100-openwrt/openwrt/staging_dir/host/include -Wall -Wmissing-prototypes -Wstrict-prototypes" CROSS_COMPILE="arm-openwrt-linux-muslgnueabi-" ARCH="arm" KBUILD_HAVE_NLS=no KBUILD_BUILD_USER="builder" KBUILD_BUILD_HOST="buildhost" KBUILD_BUILD_TIMESTAMP="Sat Oct 17 21:45:38 2026" KBUILD_BUILD_VERSION="0" KBUILD_HOSTLDFLAGS="-L/home/dell/m3100-openwrt/openwrt/staging_dir/host/lib" CONFIG_SHELL="bash" V=''  cmd_syscalls=  CC="arm-openwrt-linux-muslgnueabi-gcc" KERNELRELEASE=6.12.112-full206  CONFIG_INITRAMFS_SOURCE=/mnt/c/RC/nRF9151_Feather/m3100-openwrt/analysis/full206-public-ram-v1/bootstrap.cpio zImage
