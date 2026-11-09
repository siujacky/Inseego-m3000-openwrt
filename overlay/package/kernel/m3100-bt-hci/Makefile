include $(TOPDIR)/rules.mk

PKG_NAME:=m3100-bt-hci
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/kernel.mk
include $(INCLUDE_DIR)/package.mk

define KernelPackage/m3100-bt-hci
  SUBMENU:=Other modules
  TITLE:=M3100 manually loaded QCA Bluetooth UART trial
  DEPENDS:=+kmod-bluetooth
  KCONFIG:=CONFIG_BT_HCIUART CONFIG_BT_QCA \
	CONFIG_BT_HCIUART_H4=y CONFIG_BT_HCIUART_QCA=y \
	CONFIG_BT_HCIUART_BCM=n CONFIG_BT_HCIUART_INTEL=n \
	CONFIG_BT_HCIUART_NOKIA=n \
	CONFIG_RAVE_SP_CORE=n \
	CONFIG_BT_HCIUART_SERDEV=y CONFIG_SERIAL_DEV_BUS=y \
	CONFIG_SERIAL_DEV_CTRL_TTYPORT=y
  FILES:=$(LINUX_DIR)/drivers/bluetooth/hci_uart.ko \
	$(LINUX_DIR)/drivers/bluetooth/btqca.ko
endef

define KernelPackage/m3100-bt-hci/description
  Board-scoped M3100 serdev HCI trial. Physical power belongs exclusively
  to the separate manual board helper. Transport modules are stored outside
  the automatic module search directory and have no autoload entry.
endef

define KernelPackage/m3100-bt-hci/install
	$(INSTALL_DIR) $(1)/usr/lib/m3100-bt-manual
	mv $(1)/$(MODULES_SUBDIR)/hci_uart.ko $(1)/usr/lib/m3100-bt-manual/
	mv $(1)/$(MODULES_SUBDIR)/btqca.ko $(1)/usr/lib/m3100-bt-manual/
endef

define Build/Compile
endef

$(eval $(call KernelPackage,m3100-bt-hci))
