#!/bin/sh
# Read-only stock snapshot. No rfkill, GPIO, regulator or UART writes.
echo '=== Regulator summary (cached driver votes, not physical measurement) ==='
cat /sys/kernel/debug/regulator/regulator_summary 2>/dev/null
echo '=== PMX65 S7 / L6 cached regulator attributes ==='
for r in /sys/class/regulator/regulator.*; do
	name=$(cat "$r/name" 2>/dev/null)
	case "$name" in
	pmx65_s7|pmx65_l6)
		echo "$r name=$name"
		for field in microvolts state status num_users min_microvolts max_microvolts; do
			printf '%s=' "$field"
			cat "$r/$field" 2>/dev/null || echo unavailable
		done
		;;
	esac
done
echo '=== BT / rfkill bindings ==='
ls -ld /sys/class/rfkill /sys/class/bluetooth /sys/bus/platform/devices/*bt* /sys/bus/platform/drivers/*bt* 2>/dev/null
echo '=== GPIO debug state (BT_EN PMX65 GPIO15, SW_CTRL PMX65 GPIO2) ==='
cat /sys/kernel/debug/gpio 2>/dev/null
echo '=== UART device paths ==='
for tty in /sys/class/tty/ttyHS* /sys/class/tty/ttyMSM*; do
	echo "$tty"
	readlink -f "$tty/device" 2>/dev/null
done
