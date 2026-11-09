#!/bin/sh
# Explicit 15-second LE discovery only. Requires a configured, powered HCI
# controller with validated identity; no pairing, connecting or advertising.
set -u
umask 077
index=${1:-0}
out=${2:-/tmp/m3100-bt-le-discovery}
case "$index" in ''|*[!0-9]*) echo 'numeric HCI index required' >&2; exit 2;; esac
for tool in btmgmt btmon timeout; do
	command -v "$tool" >/dev/null || { echo "$tool required" >&2; exit 2; }
done
test -d "/sys/class/bluetooth/hci$index" || exit 2
mkdir -p "$out" || exit 1
monitor=
cleanup()
{
	timeout 5 btmgmt -i "$index" stop-find -l >> "$out/mgmt.txt" 2>&1 || true
	if [ -n "$monitor" ]; then
		kill "$monitor" 2>/dev/null || true
		wait "$monitor" 2>/dev/null || true
		monitor=
	fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
timeout 5 btmgmt -i "$index" info > "$out/controller-private.txt" 2>&1 || exit 1
timeout 25 btmon -i "$index" -w "$out/scan-private.btsnoop" > "$out/monitor-private.txt" 2>&1 &
monitor=$!
timeout 15 btmgmt -i "$index" find -l > "$out/mgmt.txt" 2>&1
result=$?
# timeout is expected if discovery remained active for the whole window.
case "$result" in 0|124) ;; *) echo "discovery command failed: $result" >&2; exit "$result";; esac
cleanup
trap - EXIT
echo "Private scan evidence saved under $out; inspect actual RX advertising reports."
