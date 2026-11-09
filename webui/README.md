# 5G Router web UI for the M3100

A vendor-neutral replacement for the carrier web interface of the Inseego M3100 (Verizon) 5G hotspot.
It runs **on the stock firmware**, next to the vendor daemons, and exposes every setting the hardware and
vendor software stack support — including the ones the carrier hid (band locking, 5G SA/NSA, IP
passthrough, VPN, static routes, field-test data, AT console …) and the extras from the community mod
ROMs (carrier-lock status, all-band enable, debug USB mode, telemetry off, logs in RAM, time sync, APN presets).

- Backend: Python 3.8 standard library only (`server/m3100ui`), drives the vendor REST API, vendor CLIs and the modem.
- Frontend: dependency-free ES-module single-page app (`web/`), works offline, light/dark, phone friendly.
- Safety: setters only run with verified prompt specifications; radio changes need confirmation; dangerous
  actions need the admin password; brick-risk operations are not exposed at all. See the design doc.

## Try it on a PC (demo data, no device needed)

```
cd server
python -m m3100ui --mock --port 18088 --host 127.0.0.1
```
Open http://127.0.0.1:18088/ — password `admin`.

## Install on the M3100

Prerequisites: the unit reachable over adb (`adb -s 76fe1623 shell id` shows root; see `../m3000/STATUS.txt`
for the USB mode switch).

```
python tools/device/verify_specs.py          # confirm every setter's prompts in a sandbox on the unit (nothing is applied)
python tools/deploy.py install               # side by side: http://192.168.1.1:8088/  (stock UI unchanged on :80)
python tools/deploy.py install --takeover    # new UI on http://192.168.1.1/  ; stock UI moves to :8080
python tools/deploy.py status | logs | restart | rollback
python tools/deploy.py uninstall             # everything back to stock
```

Login uses the device admin password (the same one as the stock UI). Changing it here changes it there too.

## Develop

- Read `docs/dev-guide.md` first (conventions, safety rules, templates).
- Tests: `python -m unittest discover -s tests -t .` and `uv run --no-project --python 3.8 python -m unittest discover -s tests -t .`
- Design: `docs/superpowers/specs/2026-10-31-m3100-webui-design.md`; device research: `docs/recon/`; per-area feature maps: `docs/features/`.
- `reference/` holds vendor files and third-party downloads used for research. It is never shipped.

## Rollback paths

1. `tools/deploy.py rollback` → previous release.
2. `tools/deploy.py uninstall` → stock UI on :80, no trace except `/data/m3100-webui/state` with `--keep-state`.
3. Without a PC: the stock UI stays reachable at `http://192.168.1.1:8080/` in takeover mode; removing
   `/etc/systemd/system/nvtl_webserver.service.d/50-m3100-webui.conf` and rebooting restores stock completely.
