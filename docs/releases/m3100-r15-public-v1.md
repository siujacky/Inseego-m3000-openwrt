# M3100 R15 Public RAM Preview 1

Experimental **M3100 / Qualcomm SDX65** RAM boot image with the Feelgood WebUI
2026.11.08.1 and accepted touchscreen v6. M3000 compatibility is **not established**.
This is a pre-release for USB management and UI testing.

## Downloads

- `openwrt-m3100-full206-r15-public-v1-ram.bin` — temporary RAM boot image.
- `SHA256SUMS` — image checksum.
- `RAM-BOOT-README.md` — this guide.
- `VERIFICATION.json` — build, privacy and emulation results with their limits.

The image contains the R15 kernel, firmware payload, offline WebUI, native
OpenWrt backend, touchscreen frontend, and USB management networking. It maps
all 124 recovered stock QML resources to native screens/components; recovered
vendor QML assets are not included.

## Public defaults

Connect by USB, wait for initialization, then open **http://192.168.77.1:8088**.
The shared WebUI/touchscreen admin password is **`12345`**. Change it before
enabling network access. Initial payload/network setup can take about 4–5 minutes
on the previously tested hardware; an early connection refusal alone does not
mean startup failed.

- The Wi-Fi AP is **disabled**. Its example seed is `M3100-ram-test`; set your own
  Wi-Fi password before enabling it. No owner's private Wi-Fi password is bundled.
- The cellular modem is **held offline by default**. Cellular/IPA startup remains
  under investigation. This release does not provide verified cellular routing.
- SSH has **no preinstalled authorized keys**, and password authentication is
  disabled. The owner's management key is removed. Use the USB WebUI/touchscreen;
  expert recovery/configuration can use the USB serial console. No SSH access
  credential is distributed.
- USB uses generic test identity `m3100-public-ram` and locally administered test
  MAC addresses. The same identity can collide if several test units are attached.
- Settings live in RAM and are lost after power cycling.

## Temporary boot

Use only an exactly identified, compatible M3100 with an already working,
unlocked fastboot boot path and a proven return to stock. Verify the model,
board/SoC revision, original partition backups/hashes and actual bootloader
download limit before a trial. A device merely appearing in fastboot does not
establish compatibility. Do not unlock or modify storage as part of this guide.

1. Download the image and `SHA256SUMS`, then compare the SHA-256. On Windows:

   ```powershell
   Get-FileHash .\openwrt-m3100-full206-r15-public-v1-ram.bin -Algorithm SHA256
   ```

2. Enter the device's already validated fastboot mode and select its exact serial:

   ```text
   fastboot devices
   fastboot -s YOUR_SERIAL getvar unlocked
   fastboot -s YOUR_SERIAL getvar max-download-size
   fastboot -s YOUR_SERIAL boot openwrt-m3100-full206-r15-public-v1-ram.bin
   ```

   Replace `YOUR_SERIAL`; these commands are an example for a working fastboot
   client, not an automatic device/compatibility check. Some Windows fastboot
   transports require the existing guarded WinUSB workflow instead. Older local
   boot helpers can hardcode a different image or unit; inspect them before use.

3. Allow initialization, open the USB WebUI, and confirm visible display and
   physical touch operation separately. Software frame counts alone are not
   display/touch proof. The USB serial console is also available.
4. To leave RAM execution, power cycle the router using its normal recovery
   procedure. The validated development workflow returns to stock; recovery on
   other units has not been tested.

Use **`fastboot boot` only**. This is not a flash, sysupgrade, unlock, or persistent
installer image. The RAM profile hides stock MTD partitions; no stock partition
write is part of this build or release procedure.

## Validation and limits

The privately provisioned parent image booted on the development M3100, provided
working USB/WebUI access, and showed a router home/login screen confirmed by the
owner. Its modem was deliberately held offline during that run.

**This public derivative has not been physically booted.** It removes personal
credentials, changes USB test identity, disables the AP, and adds a default modem
hold. The release includes build/integrity checks and ARM emulation results;
these do not prove hardware boot, physical touch, radio operation, or traffic.

The public build preserves the R15 kernel source/configuration/ABI, device tree,
command line, all 545 original firmware files and 64 packaged modules. Only the
embedded RAM bootstrap/userspace are repackaged. The final ELF → Image → piggy →
compressed ELF → zImage → Android image and appended payload are verified.

Cellular/IPA, PCIe/Ethernet/Wi-Fi, Bluetooth controller/RF, GNSS, and long-term
stability are not qualified by this release. UI controls report backend support;
their presence is not a promise that a hardware function works.

Source is in this repository. Build helpers retain the development host layout
and need separately obtained firmware and baseline build inputs; the repository
is an overlay and UI source tree, not a standalone complete OpenWrt checkout.
Backups, firmware dumps, private keys and runtime captures remain excluded from
Git history. Generated images are distributed only as release assets.
