# Inseego OpenWrt port and router UI

M3100/SDX65 OpenWrt bring-up source, an offline general 5G router WebUI, and a Python touchscreen frontend. Despite the repository name, current hardware evidence is for **M3100**; M3000 compatibility is not established.

## RAM boot download

The experimental [M3100 R15 Public RAM Preview 1](https://github.com/siujacky/Inseego-m3000-openwrt/releases/tag/m3100-r15-public-v1) includes a RAM boot image, checksums and [boot guide](docs/releases/m3100-r15-public-v1.md). The public derivative has host/ARM validation and has not been physically booted. It defaults to USB management, modem held offline and Wi-Fi AP disabled. Admin password: `12345`. This is M3100-only evidence; M3000 compatibility is unverified.

## Source layout

- `overlay/`, `m3100-*.config`, `m3100-bringup.patch`: OpenWrt overlay, kernel/device-tree configuration and bring-up patches. This is an overlay, not a complete upstream OpenWrt checkout.
- `tools/`: hardware and image-analysis/build helpers. Diagnostic tools are experimental and may be tied to a specific board or host layout.
- `webui/`: Python backend, offline JavaScript frontend, tests and redacted fixtures. Feelgood presentation adaptation includes three palettes, two desktop navigation layouts and a mobile drawer. Native mode exposes 15 browser pages; stock mode retains 52 modules.
- `touchscreen/`: accepted v6 source, API integration, renderer/input modules and tests. It maps all 124 recovered stock QML resources to native screens/components; no recovered vendor QML assets are included.
- Selected tracked `.py`/`.sh` helpers under `analysis/`: source for the R15-based RAM-image derivation and verification. Binary inputs, build trees, dump data, logs and private manifests are intentionally absent. These scripts retain the original local build layout and require the owner's separately acquired inputs.

## Local UI preview

Install Python 3.8+ and run `webui/Preview-WebUI.cmd` on Windows, or `python3 webui/tools/preview.py` from a terminal. Open http://127.0.0.1:18088 and use the synthetic password `preview-admin`. The preview is clearly labelled Demo data and cannot execute device commands. `--stock` selects redacted stock fixtures (synthetic password `admin`). These are preview credentials, not a router's credentials.

Tests: from `webui/`, run `python -m unittest discover -s tests -t .` and `node --test tests/frontend/preferences.test.mjs`. Native Linux password-file tests require root-owned private temporary files. From the repository root, `PYTHONPATH=touchscreen python -m unittest discover -s touchscreen/tests -t .` runs the touchscreen tests; Linux is needed for the full flock/process coverage.

## Hardware status and recovery

Development uses temporary RAM boot on an exactly identified M3100. Original stock partitions and recovery paths are preserved. The latest UI image has booted with USB/SSH access; hardware functionality and physical display/touch need separate verification. Cellular/PCIe/Bluetooth/GNSS support is not certified by a successful UI or kernel boot. Do not treat this repository as a persistent flash installer.

Backups, firmware dumps, private keys, personal provisioned passwords and runtime captures are not included in Git. Generated public RAM images are separate release assets. Supply device firmware locally when using build/analysis helpers. No universal device-unlock or cross-model compatibility is promised.

Per-file upstream licenses and notices are retained. Feelgood attribution and Apache-2.0 license are in `webui/web/THIRD-PARTY-NOTICES.txt` and `webui/web/licenses/Apache-2.0.txt`.

Stock-capture provenance tests are explicitly skipped when the private captures are absent; they are not replaced with synthetic provenance evidence. Original host/device captures remain local.
