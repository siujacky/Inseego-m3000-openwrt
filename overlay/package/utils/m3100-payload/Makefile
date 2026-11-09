include $(TOPDIR)/rules.mk
PKG_NAME:=m3100-payload
PKG_RELEASE:=1
PKG_FILE_DEPENDS:=$(CURDIR)/src
include $(INCLUDE_DIR)/package.mk

define Package/m3100-payload
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=M3100 reserved RAM firmware payload reader
  DEPENDS:=@TARGET_sdx
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) -D_FILE_OFFSET_BITS=64 -Wall -Wextra -Werror \
		$(PKG_BUILD_DIR)/payload-scan.c -o $(PKG_BUILD_DIR)/m3100-payload-scan $(TARGET_LDFLAGS)
endef

define Package/m3100-payload/install
	$(INSTALL_DIR) $(1)/usr/libexec
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/m3100-payload-scan $(1)/usr/libexec/
endef

$(eval $(call BuildPackage,m3100-payload))
