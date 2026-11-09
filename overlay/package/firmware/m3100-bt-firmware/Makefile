include $(TOPDIR)/rules.mk

PKG_NAME:=m3100-bt-firmware
PKG_RELEASE:=1
PKG_LICENSE:=QualcommAtheros-firmware
PKG_LICENSE_FILES:=files/LICENSE.QualcommAtheros_ath10k files/NOTICE.qca

include $(INCLUDE_DIR)/package.mk

define Package/m3100-bt-firmware
  SECTION:=firmware
  CATEGORY:=Firmware
  TITLE:=M3100 WCN685x ROM0201 Bluetooth trial firmware
endef

define Package/m3100-bt-firmware/description
  Original-name official WCN685x UART patch and GF NVM candidate selected
  from the observed M3100 ROM tuple. Generic NVM board calibration and
  unique address provisioning remain to be verified on the device.
endef

define Build/Compile
endef

define Package/m3100-bt-firmware/install
	$(INSTALL_DIR) $(1)/lib/firmware/qca
	$(INSTALL_DATA) ./files/wcnhpbtfw21.tlv $(1)/lib/firmware/qca/
	$(INSTALL_DATA) ./files/wcnhpnv21g.bin $(1)/lib/firmware/qca/
	$(INSTALL_DIR) $(1)/usr/share/licenses/m3100-bt-firmware
	$(INSTALL_DATA) ./files/LICENSE.QualcommAtheros_ath10k ./files/NOTICE.qca $(1)/usr/share/licenses/m3100-bt-firmware/
endef

$(eval $(call BuildPackage,m3100-bt-firmware))
