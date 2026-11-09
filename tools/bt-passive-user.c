/* SPDX-License-Identifier: GPL-2.0-only */
/* Exclusive HCI user-channel passive LE receive diagnostic for M3100.
 * No address writes, active scan, advertising, pairing or connection.
 * Requires firmware setup completed and the HCI transport quiescent/down.
 */
#define _DEFAULT_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Linux Bluetooth socket ABI, avoiding a userspace libbluetooth dependency. */
#ifndef AF_BLUETOOTH
#define AF_BLUETOOTH 31
#endif
#define BTPROTO_HCI 1
#define HCI_CHANNEL_USER 1
struct sockaddr_hci {
	sa_family_t family;
	unsigned short device;
	unsigned short channel;
};
static volatile sig_atomic_t interrupted;
static void interrupt_scan(int sig)
{
	(void)sig;
	interrupted = 1;
}
static int64_t millis(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}
struct evidence {
	unsigned reports, nonempty, events;
	bool quiet;
};
static unsigned le16(const unsigned char *p) { return p[0] | (unsigned)p[1] << 8; }

static int controller_error(const unsigned char *p, size_t n, bool quiet)
{
	if (n < 2 || p[0] != 4 || p[1] != 0x10)
		return 0;
	if (n != 4 || p[2] != 1)
		return -1;
	if (!quiet)
		fprintf(stderr, "HCI Hardware Error code=0x%02x\n", p[3]);
	return -1;
}

/* Validate the entire event before counting any report from it. */
static int advertising(const unsigned char *p, size_t n, struct evidence *ev)
{
	size_t off = 5;
	unsigned i, count;
	if (n < 3 || p[0] != 4 || n != (size_t)p[2] + 3)
		return -1;
	if (p[1] != 0x3e)
		return 0;
	if (n < 4)
		return -1;
	if (p[3] != 2)
		return 0;
	if (n < 5)
		return -1;
	count = p[4];
	if (!count)
		return -1;
	for (i = 0; i < count; i++) {
		/* event type, address type, address6, data length, data, RSSI */
		if (off + 10 > n || p[off + 8] > 31 || off + 10 + p[off + 8] > n || p[off] > 4 ||
		    p[off + 1] > 3)
			return -1;
		off += 10 + p[off + 8];
	}
	if (off != n)
		return -1;
	ev->events++;
	off = 5;
	for (i = 0; i < count; i++) {
		unsigned len = p[off + 8];
		int rssi = (int8_t)p[off + 9 + len];
		ev->reports++;
		if (len)
			ev->nonempty++;
		if (!ev->quiet) {
			/* Omit addresses, names and complete payloads from stdout. */
			printf("RX LE Advertising Report number=%u event_type=%u address_type=%u "
			       "data_bytes=%u RSSI=%d\n",
			       ev->reports, p[off], p[off + 1], len, rssi);
			fflush(stdout);
		}
		off += 10 + len;
	}
	return 1;
}
static int receive_event(int fd, unsigned char *buf, size_t cap, int64_t deadline, bool heed_signal)
{
	for (;;) {
		int64_t left = deadline - millis();
		struct pollfd pfd = {fd, POLLIN, 0};
		int ret;
		if ((heed_signal && interrupted) || left <= 0)
			return 0;
		ret = poll(&pfd, 1, (int)left);
		if (ret < 0 && errno == EINTR)
			continue;
		if (ret <= 0)
			return ret;
		ssize_t n = recv(fd, buf, cap, 0);
		if (n < 0 && (errno == EINTR || errno == EAGAIN))
			continue;
		if (n <= 0)
			return -1;
		return (int)n;
	}
}
static int command(int fd, uint16_t opcode, const unsigned char *data, size_t len,
		   struct evidence *ev, bool cleanup)
{
	unsigned char tx[32] = {1, opcode & 255, opcode >> 8, (unsigned char)len};
	unsigned char rx[4096];
	struct evidence ignored = {.quiet = true};
	int64_t deadline = millis() + 2000;
	if (len > sizeof(tx) - 4)
		return -1;
	memcpy(tx + 4, data, len);
	if (send(fd, tx, len + 4, MSG_NOSIGNAL) != (ssize_t)len + 4)
		return -1;
	for (;;) {
		int n = receive_event(fd, rx, sizeof(rx), deadline, !cleanup);
		if (n <= 0)
			return -1;
		if (rx[0] != 4)
			continue;
		if (n < 3 || n != rx[2] + 3)
			return -1;
		if (controller_error(rx, n, ev->quiet))
			return -1;
		if (rx[1] == 0x0e && rx[2] >= 4 && le16(rx + 4) == opcode) {
			if (!ev->quiet)
				printf("CommandComplete opcode=0x%04x status=0x%02x\n", opcode,
				       rx[6]);
			return rx[6] ? -1 : 0;
		}
		if (rx[1] == 0x0f && rx[2] == 4 && le16(rx + 5) == opcode && rx[3])
			return -1;
		/* Validate asynchronous events but never count startup or cleanup
		 * traffic as proof from the explicitly enabled receive window.
		 */
		if (advertising(rx, n, &ignored) < 0)
			return -1;
	}
}
static int passive_receive(int fd, unsigned duration_ms, struct evidence *ev)
{
	/* LE Meta Event plus command complete/status/hardware-error events. */
	static const unsigned char event_mask[8] = {0x00, 0xe0, 0, 0, 0, 0, 0, 0x20};
	static const unsigned char le_mask[8] = {0x02, 0, 0, 0, 0, 0, 0, 0};
	/* passive, interval96*0.625ms, window48*0.625ms, public own type
	 * (not transmitted during passive receive), accept all advertisers. */
	static const unsigned char params[7] = {0, 0x60, 0, 0x30, 0, 0, 0};
	static const unsigned char off[2] = {0, 0}, on[2] = {1, 0};
	unsigned char rx[4096];
	int result = 1, stop_result;
	int64_t deadline;
	if (command(fd, 0x200c, off, sizeof(off), ev, false) ||
	    command(fd, 0x0c01, event_mask, sizeof(event_mask), ev, false) ||
	    command(fd, 0x2001, le_mask, sizeof(le_mask), ev, false) ||
	    command(fd, 0x200b, params, sizeof(params), ev, false) ||
	    command(fd, 0x200c, on, sizeof(on), ev, false))
		goto cleanup;
	ev->reports = 0;
	ev->nonempty = 0;
	ev->events = 0;
	deadline = millis() + duration_ms;
	while (!interrupted && millis() < deadline) {
		int n = receive_event(fd, rx, sizeof(rx), deadline, true);
		if (n < 0)
			goto cleanup;
		if (!n)
			break;
		if (controller_error(rx, n, ev->quiet) ||
		    (rx[0] == 4 && advertising(rx, n, ev) < 0))
			goto cleanup;
	}
	result = interrupted ? 130 : (ev->nonempty ? 0 : 3);
cleanup:
	/* Always issue a bounded disable after acquiring the exclusive channel,
	 * including startup failure and SIGINT/SIGTERM. Ignore signal flag while
	 * waiting at most2seconds for that cleanup command's completion.
	 */
	if (interrupted)
		result = 130;
	stop_result = command(fd, 0x200c, off, sizeof(off), ev, true);
	if (stop_result)
		result = 1;
	if (!ev->quiet)
		printf(
		    "scan_disable=%s received_events=%u reports=%u nonempty_reports=%u result=%d\n",
		    stop_result ? "FAILED" : "success", ev->events, ev->reports, ev->nonempty,
		    result);
	return result;
}

static int board_ok(void)
{
	char data[256];
	int fd = open("/proc/device-tree/compatible", O_RDONLY | O_CLOEXEC);
	ssize_t n;
	if (fd < 0)
		return 0;
	n = read(fd, data, sizeof(data));
	close(fd);
	for (ssize_t i = 0; i < n;) {
		size_t len = strnlen(data + i, n - i);
		if (len == 13 && !memcmp(data + i, "inseego,m3100", 13))
			return 1;
		i += len + 1;
	}
	return 0;
}

/* Socket-pair emulator tests the real command/cleanup path without Bluetooth. */
static const unsigned char adv_fixture[] = {4, 0x3e, 15, 2, 1, 0, 1, 1, 2,
					    3, 4,    5,	 6, 3, 2, 1, 6, 0xc4};
static void emulator(int fd, int failure_step)
{
	static const uint16_t expected[] = {0x200c, 0x0c01, 0x2001, 0x200b, 0x200c};
	unsigned char p[64];
	for (unsigned i = 0; i < 6; i++) {
		int n = recv(fd, p, sizeof(p), 0);
		unsigned opcode;
		if (n < 4 || p[0] != 1 || n != p[3] + 4)
			_exit(10);
		opcode = le16(p + 1);
		if (i == 5 || (failure_step >= 0 && i == (unsigned)failure_step + 1)) {
			if (opcode != 0x200c || n != 6 || p[4] || p[5])
				_exit(11);
		} else if (opcode != expected[i])
			_exit(12);
		if (opcode == 0x200b && (n != 11 || p[4] != 0 || p[9] != 0 || p[10] != 0))
			_exit(13); /* Never allow active scan or nonzero filter policy. */
		/* Stale startup/cleanup reports must not satisfy receive evidence. */
		if ((failure_step == -3 && i < 5) || (failure_step == -4 && i == 5))
			if (send(fd, adv_fixture, sizeof(adv_fixture), MSG_NOSIGNAL) !=
			    (ssize_t)sizeof(adv_fixture))
				_exit(17);
		unsigned char reply[] = {4, 14, 4, 1, opcode & 255, opcode >> 8, 0};
		if (i == (unsigned)failure_step)
			reply[6] = 0x0c;
		if (send(fd, reply, sizeof(reply), MSG_NOSIGNAL) != (ssize_t)sizeof(reply))
			_exit(14);
		if (i == 5 || (failure_step >= 0 && i == (unsigned)failure_step + 1))
			_exit(0);
		if (i == 4 && failure_step == 6)
			kill(getppid(), SIGTERM);
		if (i == 4 && (failure_step == -1 || failure_step == 7))
			if (send(fd, adv_fixture, sizeof(adv_fixture), MSG_NOSIGNAL) !=
			    (ssize_t)sizeof(adv_fixture))
				_exit(15);
		if (i == 4 && failure_step == 7) {
			const unsigned char hardware_error[] = {4, 0x10, 1, 0x42};
			if (send(fd, hardware_error, sizeof(hardware_error), MSG_NOSIGNAL) !=
			    (ssize_t)sizeof(hardware_error))
				_exit(18);
		}
	}
	_exit(16);
}
static int selftest(void)
{
	struct evidence e = {.quiet = true};
	unsigned char bad[sizeof(adv_fixture)];
	if (advertising(adv_fixture, sizeof(adv_fixture), &e) != 1 || e.reports != 1 ||
	    e.nonempty != 1)
		return 1;
	memcpy(bad, adv_fixture, sizeof(bad));
	bad[13] = 40;
	if (advertising(bad, sizeof(bad), &e) != -1 || e.reports != 1)
		return 1;
	unsigned char multi[31] = {4, 0x3e, 28, 2, 2};
	memcpy(multi + 5, adv_fixture + 5, 13);
	memcpy(multi + 18, adv_fixture + 5, 13);
	if (advertising(multi, sizeof(multi), &e) != 1 || e.reports != 3)
		return 1;
	multi[26] = 40;
	if (advertising(multi, sizeof(multi), &e) != -1 || e.reports != 3)
		return 1;
	unsigned char oversized[47] = {4, 0x3e, 44, 2, 1, 0, 1, 1, 2, 3, 4, 5, 6, 32};
	oversized[46] = 0xc4;
	if (advertising(oversized, sizeof(oversized), &e) != -1 || e.reports != 3)
		return 1;
	signal(SIGTERM, interrupt_scan);
	for (int failure = -4; failure <= 7; failure++) {
		int sv[2], status, ret;
		if (socketpair(AF_UNIX, SOCK_SEQPACKET, 0, sv))
			return 1;
		pid_t pid = fork();
		if (pid < 0)
			return 1;
		if (!pid) {
			close(sv[0]);
			emulator(sv[1], failure);
		}
		close(sv[1]);
		e = (struct evidence){.quiet = true};
		interrupted = 0;
		ret = passive_receive(sv[0], 30, &e);
		close(sv[0]);
		waitpid(pid, &status, 0);
		if (!WIFEXITED(status) || WEXITSTATUS(status) ||
		    (failure == -1   ? ret != 0 || e.nonempty != 1
		     : failure <= -2 ? ret != 3 || e.nonempty != 0 || e.reports != 0
		     : failure == 6  ? ret != 130
		     : failure == 7  ? ret != 1 || e.nonempty != 1
				     : ret != 1))
			return 1;
	}
	puts(
	    "PASS: valid/truncated advertising parser and twelve real socket command/cleanup flows "
	    "(including stale startup/cleanup reports, no reports, startup failures, stop failure, "
	    "signal)");
	return 0;
}
int main(int argc, char **argv)
{
	unsigned long index, seconds;
	char *end, path[128], resolved[PATH_MAX];
	int fd, result;
	struct evidence ev = {0};
	if (argc == 2 && !strcmp(argv[1], "--selftest"))
		return selftest();
	if (argc != 3) {
		fprintf(stderr, "usage: %s HCI_INDEX SECONDS(1..30)\n", argv[0]);
		return 2;
	}
	errno = 0;
	index = strtoul(argv[1], &end, 10);
	if (errno || !argv[1][0] || *end || index > 65534)
		return 2;
	errno = 0;
	seconds = strtoul(argv[2], &end, 10);
	if (errno || !argv[2][0] || *end || seconds < 1 || seconds > 30)
		return 2;
	if (geteuid() || !board_ok()) {
		fprintf(stderr, "requires root on inseego,m3100\n");
		return 2;
	}
	snprintf(path, sizeof(path), "/sys/class/bluetooth/hci%lu/device", index);
	if (!realpath(path, resolved) || !strstr(resolved, "830000")) {
		fprintf(stderr, "HCI index does not resolve to UART830000\n");
		return 2;
	}
	signal(SIGINT, interrupt_scan);
	signal(SIGTERM, interrupt_scan);
	fd = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_NONBLOCK | SOCK_CLOEXEC, BTPROTO_HCI);
	if (fd < 0) {
		perror("HCI socket");
		return 1;
	}
	struct sockaddr_hci addr = {AF_BLUETOOTH, index, HCI_CHANNEL_USER};
	if (bind(fd, (struct sockaddr *)&addr, sizeof(addr))) {
		perror("exclusive user-channel bind (controller must be quiescent/down)");
		close(fd);
		return 1;
	}
	printf("M3100 hci%lu UART830000: passive receive for%lu seconds; addresses omitted\n",
	       index, seconds);
	result = passive_receive(fd, seconds * 1000, &ev);
	close(fd);
	return result;
}
