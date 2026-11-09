include $(TOPDIR)/rules.mk
PKG_NAME:=m3100-kernel-console
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
include $(INCLUDE_DIR)/kernel.mk
include $(INCLUDE_DIR)/package.mk

define KernelPackage/m3100-kernel-console
  SUBMENU:=USB Support
  TITLE:=M3100 explicit built-in USB serial kernel console diagnostic
  KCONFIG:=CONFIG_USB_G_SERIAL=y CONFIG_U_SERIAL_CONSOLE=y
endef

define Build/Compile
endef
$(eval $(call KernelPackage,m3100-kernel-console))
