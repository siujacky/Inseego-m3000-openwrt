include $(TOPDIR)/rules.mk

PKG_NAME:=m3100-tc956x
PKG_RELEASE:=1
PKG_SOURCE_PROTO:=git
PKG_SOURCE_URL:=https://github.com/strongtz/tc956x-dkms.git
PKG_SOURCE_VERSION:=ddd83149bb0e274615b4b4a7d17ad9df026191b8
PKG_SOURCE_DATE:=2026-06-10
PKG_MIRROR_HASH:=skip
PKG_LICENSE:=GPL-2.0-only
PKG_BUILD_PARALLEL:=1

include $(INCLUDE_DIR)/kernel.mk
include $(INCLUDE_DIR)/package.mk

TC956X_DIR:=$(PKG_BUILD_DIR)/drivers/net/ethernet/toshiba/tc956x

define KernelPackage/m3100-tc956x
  SUBMENU:=Network Devices
  TITLE:=M3100 TC956x PCIe Ethernet (experimental)
  DEPENDS:=@PCI_SUPPORT +kmod-phylink +kmod-phy-realtek +kmod-ptp
  FILES:=$(TC956X_DIR)/tc956x_pcie_eth.ko
endef

define KernelPackage/m3100-tc956x/description
  Experimental TC956x/QPS615 1179:0220 driver for RAM-only M3100 trials.
  Requires board-specific PHY DT wiring and TC956x CM3 firmware.
  Deliberately not autoloaded until those requirements are verified.
endef

define Build/Compile
	+$(KERNEL_MAKE) M="$(TC956X_DIR)" \
		CONFIG_TC956X_NET=m CONFIG_TC956X_PF=y CONFIG_TC956X_VF=n \
		CONFIG_TC956X_PLATFORM_SUPPORT=y \
		CONFIG_TC956X_IOCTL_REG_RD_WR_ENABLE=n modules
endef

$(eval $(call KernelPackage,m3100-tc956x))
