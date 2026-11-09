# M3100 RAM touchscreen userspace

This source tree adds a standalone 320×240 touchscreen client for the existing authenticated WebUI API. It uses the router's Python standard library, evdev input and `/dev/m3100-lcd`; the generated font needs no Pillow on the router. The frontend, backend extension, runtime cooperation and guarded deployment are independently testable. Host tests do not establish physical touch orientation, reliable typing, actual panel output, cellular service, GNSS or Bluetooth.

The catalog contains **77 pages** and maps all **124 recovered stock QML resources**: 66 screens, 42 reusable components, 4 carrier variants, 8 stock-disabled resources and 4 placeholders. These are resource classifications, not 124 separate working hardware features. The original saved binary is a stock M3100-family reference; its identity as M3000 firmware is not confirmed. The per-resource targets, reasons and source hashes are in [stock_coverage.json](m3100_touch/stock_coverage.json).

| Function group | Touchscreen coverage |
| --- | --- |
| Home and help | Wi-Fi, Devices, Wi-Fi credentials, Menu, Help, Status; stock settings, advanced settings, quick start, support and About |
| Wi-Fi | Primary and other AP/client interfaces; SSID, security and hidden network; password entry and explicit reveal; isolation, WPS eligibility; create/remove interfaces |
| Radios | Enable, channel, country and requested transmit power; explicit explanation for unavailable band selection |
| Devices | DHCP leases, selected-AP MAC deny-list block/unblock; policy protection and confirmation |
| Network | Interface protocol/address/DNS; QMI or ModemManager APN fields; DHCP ranges/leases and IPv6 advertisements |
| Firewall | Default policies, zones, forwarding, traffic rules and port redirects through the existing protected native API |
| Services | Installed-service controls with protected actions disabled; capability-gated cellular and SMS views/actions |
| Status and diagnostics | Hardware observations, runtime data counters, GNSS availability, system information, logs, audit and read-only API diagnostics |
| Preferences and session | Host name/time zone, WebUI preferences, English/Spanish presentation, light/dark theme, screen inactivity, admin unlock and lock/logout |

The interface preserves backend validation, CSRF, confirmation and management protections. Editable configuration does not establish Wi-Fi traffic, client isolation or MAC-filter enforcement. Devices shows reported DHCP leases; data usage shows runtime counters, not a carrier billing quota.

Unavailable stock actions have explicit reasons: charging/source policy, audio, persistent reset, firmware update, power/suspend, battery preservation, SIM PIN/PUK/unlock, carrier activation, roaming, tether-mode changes and WPS push-button sessions. Cellular/SMS/GNSS remain subject to actual backend capability and the current offline modem. File sharing and speed test remain identified as disabled in the inspected stock menu. Missing providers never return a fabricated success.

Current source/package/live qualification evidence is retained under `.superpowers/sdd/2026-11-04-m3100-stock-touchscreen/`. Consult its final review and live reports for the exact accepted bundle and boot; host tests alone do not establish physical operation.

## Accepted RAM deployment

The current accepted [v6 bundle](../analysis/full206-touch-ui/m3100-touchscreen-candidate-v6.tar.gz) is 60,534 bytes, SHA256 `e805d348067a8d7f1da5bafeac84c4ff6f10aae203a6f2764d6d5b1a24f08a17`. It passed [independent source/package review](../.superpowers/sdd/2026-11-05-touch-login-fix/reviews/final-review.md) and **180 Linux-root tests**. Archive members match the reviewed source and include the font/license; private password files are excluded. [accepted-package.json](../analysis/full206-touch-ui/accepted-package.json) identifies the current bundle for future RAM reloads. Historical per-attempt scripts may still refer to v5.

V6 fixes queued keyboard taps being discarded after the first character, and distinguishes an incorrect login password from an expired session. Text/mask editing preserves the fixed keyboard layout; page changes, Done, Cancel, navigation and authentication still invalidate stale contacts. It is installed on boot `52e560d5-1f85-4102-a813-4cef51e85abe` with the user's current RAM password preserved. All 17 installed hashes/modes, four original backups, input/LCD descriptors, display ownership and services pass. A queued numeric keypad test using the installed production ARM runtime/controller authenticated through the router's actual loopback HTTP backend and opened Menu; wrong input stayed locked with the corrected message. Physical login after this correction still awaits user feedback. Evidence: [deployment-result.json](../.superpowers/sdd/2026-11-05-touch-login-fix/reports/deployment-result.json).

The initial live qualification used boot `5d908a1d-ea4e-448a-918a-1ebb0c2abf12`. Independent live reads verified all 17 installed target hashes/modes, four original backup hashes, the input/LCD/lock file descriptors, fresh owned frames, all three services and approximately 17 MiB frontend RSS. The live catalog sweep loaded **76 pages / 130 contexts with zero unhandled read errors**. Explicit credential reveal was deliberately excluded; authenticated login, unauthenticated rejection and CSRF rejection passed. No router settings were applied by the sweep.

The exact v5 package was rolled back on the router: original bytes/modes and service states were verified, then the same package was reinstalled successfully. Static fallback after frontend stop and frontend restart also passed using the identical runtime code. These are current-boot userspace checks. The original R15 image file remains unchanged, and this installation disappears with the RAM boot.

The user has seen the touchscreen keyboard and login messages; successful physical login after v6 and the other gesture/Cancel checks remain separate acceptance observations. The modem remains offline after the existing secure IPA rejection, its status helper returns failure, and GNSS has no valid provider status. These are explicit unavailable states in the interface. Initial v5 evidence is in [live-final-qualification.json](../.superpowers/sdd/2026-11-04-m3100-stock-touchscreen/reports/live-final-qualification.json).

Latest redeployment (2026-11-05): the unchanged R15 RAM image and accepted v5 touchscreen are running on boot `e864c61f-71f1-4270-8874-ec6a75ca0981`. Fresh checks passed for all 17 installed hashes/modes, four original backups, input/LCD/lock descriptors, frames, three services, the same admin login and CSRF rejection. Modem and GNSS remain unavailable; no new IPA attempt was made. The new boot guard and independent evidence are preserved in [redeploy-result.json](../analysis/full206-integrated/redeploy-20261105T115523Z/redeploy-result.json) and its sibling `touchscreen/` directory. Initial rollback/full-sweep/test evidence above remains historical and was not presented as a new physical test.

## Runtime contract

The service executes:

```sh
PYTHONPATH=/opt/m3100-touchscreen PYTHONDONTWRITEBYTECODE=1 \
  python3 -m m3100_touch --base-url http://127.0.0.1:8088 \
  --device /dev/input/event0 --lcd /dev/m3100-lcd \
  --status /run/m3100-touchscreen.json --lock /run/m3100-display.lock
```

The touchscreen owns an exclusive `flock` on `/run/m3100-display.lock` for its lifetime. Its status file must be replaced atomically, contain no credentials/session cookies and publish `boot_id`, `pid`, `timestamp`, `monotonic`, `render_ok`, `last_frame_written`, `last_frame_monotonic`, `screen_id`, and `locked`. `locked` describes authentication state, not the advisory lock. A heartbeat is expected every second and a successful frame at least every five seconds, including static pages. Publish a failed write as `render_ok=false`; never advance the last successful frame for an incomplete write.

The producer in `runtime/hardware.py` is derived from the captured original. It preserves battery-voltage observations, touch-event counting, modem state and interface observations. It attempts the same advisory lock around each complete static frame. While the UI owns the lock, the producer reports UI observations only for a live PID, matching boot and finite monotonic status/frame ages of 0–15 seconds. Wall-clock changes do not determine freshness. Failed, missing, future, oversized, malformed or stale UI metadata reports unknown/failed rendering, without carrying forward an earlier successful frame. Process death releases the kernel lock and the producer can render its static fallback on its next five-second cycle. Neither a successful write nor telemetry means physical confirmation.

The procd service allows three crash respawns within its configured 60-second threshold, five seconds apart. It starts only userspace; installation does not enable it for a future reboot or alter radio/modem/network state.

## Host verification

Run the runtime and package tests on a Linux host for real flock/process-death checks:

```sh
python3 -m unittest touchscreen.tests.test_runtime touchscreen.tests.test_package -v
```

Windows can run metadata/archive/filesystem tests; Linux-specific tests are explicitly skipped there. The installation tests use a temporary simulated root and a service boundary that never invokes real services. They inject launch and mid-replacement failures, verify restoration, and reject mount/identity/hash drift, symlinks, unsafe archives, collisions, corrupted backups and concurrent transactions. Run the entire touchscreen test suite once the other tasks have frozen their contracts.

## Reproducible package

After frontend review and all host tests pass:

```sh
python3 touchscreen/tools/package.py --output analysis/full206-touch-ui/m3100-touchscreen.tar.gz
python3 touchscreen/tools/install.py validate --bundle analysis/full206-touch-ui/m3100-touchscreen.tar.gz --sha256 EXPECTED_SHA256
```

The builder refuses missing required frontend files. It includes only the explicit module/asset list, the modified producer, the new service, installer helpers, and these two backend files:

- `backend/m3100ui/touchscreen_api.py`
- `backend/m3100ui/services/__init__.py`

It excludes the original backend snapshot, bootstrap password, caches, tests and all other files. The `font9x16.bin` asset is distributed with the full DejaVu/Bitstream/Arev license in `font.py` (`FONT_LICENSE`). Members have fixed ownership, timestamps and modes; gzip has a fixed timestamp and no source filename. `manifest.json` records exact source, installed destination, mode, size and SHA256. Archives are read through a closed whitelist without filesystem extraction. Traversal, links, device nodes, duplicate/unexpected members, incorrect destinations/modes/hashes and expanded-size violations are refused.

## Guarded target installation

The session owner performs every device command. First independently recheck exact device identity over the pinned SSH transport and retain current resource/backend/SSH observations. Upload the reviewed archive, standalone `tools/install.py`, and captured guard JSON to private RAM paths. Verify their SHA256 values after transfer. Invoke the uploaded helper directly; do not extract an unvalidated archive on the router. The optional shell wrappers require their sibling `install.py` in the same staging directory.

The guard for this implementation session is `.superpowers/sdd/2026-11-04-m3100-stock-touchscreen/baseline-runtime/guard.json`. It pins the exact boot ID, `inseego,m3100`, full RAM model string, captured root mount, header-only `/proc/mtd`, and SHA256 of the original producer, original hardware service, WebUI service and backend registration. Guarded hashes refer to captured runtime files. No partition is read to validate this transaction.

The target invocation, after independent review and upload verification, is:

```sh
python3 /tmp/PRIVATE_STAGE/install.py install --bundle /tmp/PRIVATE_STAGE/m3100-touchscreen.tar.gz \
  --sha256 EXPECTED_SHA256 --guard /tmp/PRIVATE_STAGE/guard.json
```

The installer checks the closed archive, exact board/model/boot, captured root tmpfs mount, tmpfs coverage of every destination and backup, header-only MTD, parent symlinks, original runtime hashes and absent new destinations. It permits the target's exact runtime-state alias chain: `/run` links to `/var/run`, and `/var` links to the relative target `tmp`. Runtime receipts/locks are written through the verified canonical `/tmp/run` path; the symlinks are preserved, every canonical parent remains checked, and `/tmp/run` must be covered by tmpfs. This exception applies only to installer state; product destinations under `/opt`, `/usr` and `/etc` retain strict symlink refusal. It backs up every replaced original plus all four guarded originals under `/run/m3100-touchscreen-backup/BOOT_ID/`, with private modes and SHA256 receipts. Source/runtime files use explicit 0644/0755 modes. All replacements use a temporary file on the destination filesystem and atomic rename. A separate kernel flock prevents simultaneous install/rollback transactions.

Only `m3100-touchscreen`, `m3100-hardware` and `m3100-webui` are stopped/started. Original service files are preserved. Launch qualification requires a fresh, same-boot successful UI frame, live reported PID, held display lock and running status for all three services. Failure restores original bytes/modes, removes new files and restores the captured service running states. A same-package repeat verifies installed hashes and returns the existing receipt without restarting services. A different package requires rollback first. An interrupted receipt requires explicit rollback before another installation.

Every local service-status subprocess has a three-second timeout; each start/stop call has a twenty-second timeout, including rollback calls. Health qualification has a twenty-second monotonic budget, and every status probe uses the smaller remaining budget. Exhausted budgets and stalled probes raise explicit errors. Unknown initial service state fails before service changes or product replacement; a post-start status timeout enters rollback and releases the installer lock when the transaction exits.

## Rollback and evidence

```sh
python3 /tmp/PRIVATE_STAGE/install.py rollback --guard /tmp/PRIVATE_STAGE/guard.json
```

Rollback rechecks identity/tmpfs/MTD, original service sentinels, every affected target and every required backup hash before service/file changes. It refuses unexpected edits rather than overwriting them. It restores original files and modes, removes new files and starts only services that were originally running. A rolled-back receipt is retained. Backups and staging are RAM-only and disappear on reboot; retain their receipts off-device as evidence.

After installation the session owner must independently inspect the current boot, backend reads/authentication, UI/frame freshness, hardware observations, process memory, management access and rollback. Physical corner/orientation/navigation/typing/Cancel/confirmation feedback remains a separate observation. Do not test disruptive controls merely to exercise buttons. This package does not flash, reboot, reload network/radio, retry IPA or change hardware protection state.

Blank-screen retry (2026-11-05 14:12 UTC): after USB returned in stock, the same R15 image/v5 package was restored on boot `a8c72d49-9129-4587-aa9e-3b0d28c0f3aa`. The panel accepted a reinitialization/color-bar diagnostic, and fresh Home frames/services/input handles/authentication/17 target hashes passed. Physical visibility remains pending user feedback. Current evidence: [latest retry](../analysis/full206-integrated/redeploy-20261105T140234Z/redeploy-result.json).
