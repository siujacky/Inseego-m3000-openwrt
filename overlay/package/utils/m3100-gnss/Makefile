include $(TOPDIR)/rules.mk

PKG_NAME:=m3100-gnss
PKG_VERSION:=1.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)

include $(INCLUDE_DIR)/package.mk

define Package/m3100-gnss
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=M3100 QRTR service and bounded GNSS diagnostics
  DEPENDS:=+kmod-qrtr
endef

define Package/m3100-gnss/description
  QRTR discovery, read-only LOC/PDS queries and explicit bounded LOC sessions.
  No startup service or automatic GNSS session is installed.
endef

define Build/Prepare
	$(call Build/Prepare/Default)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/m3100-qrtr-gnss \
		$(PKG_BUILD_DIR)/m3100-qrtr-gnss.c $(TARGET_LDFLAGS)
endef

define Package/m3100-gnss/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/m3100-qrtr-gnss $(1)/usr/bin/
endef

$(eval $(call BuildPackage,m3100-gnss))
