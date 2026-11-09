# Feelgood WebUI recreation

Version 2026.11.08.1. The offline UI supports Dark, Light and Colourful palettes, Horizontal/Vertical desktop navigation and a mobile drawer. The native pages cover cellular/SMS, Wi-Fi, network interfaces, DHCP/leases, firewall, system settings, services, logs and hardware/GNSS observations.

Run `Preview-WebUI.cmd` on Windows with Python 3.8+ installed. The localhost native preview uses the synthetic password `preview-admin`; `--stock` uses synthetic password `admin`. No device is connected by the preview. Use `--port 18089` if the default 18088 is occupied.

`web/` contains complete offline assets. `server/m3100ui/services/ui_pages.py` selects the native page manifest. Existing authentication, CSRF, tier confirmations and native validation remain in the backend. Image integration must preserve the additional touchscreen API registration from `touchscreen/backend/m3100ui/services/__init__.py`; do not replace a running device server wholesale with a preview copy.

No firmware image, user backup, provisioned credential, original filesystem, runtime capture or deploy key is distributed here. Build and deployment evidence is kept locally. Follow exact-unit/RAM-only guards for hardware trials and preserve stock recovery.

Feelgood provenance and license are in `web/THIRD-PARTY-NOTICES.txt` and `web/licenses/Apache-2.0.txt`.
