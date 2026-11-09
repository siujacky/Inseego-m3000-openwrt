# SPDX-License-Identifier: GPL-2.0-only
# RAM-only profile. Deliberately produces no sysupgrade image.
define Build/m3100-bootimg
	python3 $(TOPDIR)/target/linux/sdx/image/m3100-bootimg.py $@ $@.new "noinitrd ro root=/dev/ram0 console=ttyMSM0,115200n8 loglevel=7"
	mv $@.new $@
endef

define Device/inseego_m3100-ram
  DEVICE_VENDOR := Inseego
  DEVICE_MODEL := M3100
  DEVICE_VARIANT := Experimental USB RAM console
  DEVICE_DTS_DIR := $$(DTS_DIR)/qcom
  DEVICE_DTS := qcom-sdx65-inseego-m3100-ram
  DEVICE_DTC_FLAGS := -@
  SUPPORTED_DEVICES := inseego,m3100
  KERNEL_NAME := zImage
  KERNEL_SIZE := 16384k
  KERNEL_INITRAMFS := kernel-bin | append-dtb | m3100-bootimg | check-size $$(KERNEL_SIZE)
  IMAGES :=
endef
TARGET_DEVICES += inseego_m3100-ram

# A small kernel envelope; tools/firmware_trailer.py adds the verified firmware
# archive in the reserved download buffer for one RAM-downloadable image.
define Device/inseego_m3100-full
  $(Device/inseego_m3100-ram)
  DEVICE_VARIANT := Self-contained RAM firmware trial
  DEVICE_DTS := qcom-sdx65-inseego-m3100-full
endef
TARGET_DEVICES += inseego_m3100-full

# Modem-disabled peripheral trial; no stock firmware payload is needed for
# PCIe enumeration. Preserve the console and original RAM-only profile.
define Device/inseego_m3100-peripherals
  $(Device/inseego_m3100-ram)
  DEVICE_VARIANT := Peripheral RAM trial
  DEVICE_DTS := qcom-sdx65-inseego-m3100-peripherals
endef
TARGET_DEVICES += inseego_m3100-peripherals
