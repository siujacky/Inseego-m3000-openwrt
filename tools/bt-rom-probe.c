/* SPDX-License-Identifier: GPL-2.0-only */
/* Query an explicitly powered M3100 BT ROM over UART830000, no firmware. */
#define _DEFAULT_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static long long now_ms(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static int exact(int fd, unsigned char *p, size_t n, long long end)
{
	while (n) {
		int left = (int)(end - now_ms());
		struct pollfd pollfd = {fd, POLLIN, 0};
		if (left <= 0 || poll(&pollfd, 1, left) <= 0)
			return -1;
		ssize_t got = read(fd, p, n);
		if (got < 0 && (errno == EINTR || errno == EAGAIN))
			continue;
		if (got <= 0)
			return -1;
		p += got;
		n -= got;
	}
	return 0;
}
static unsigned le16(const unsigned char *p) { return p[0] | p[1] << 8; }
static uint32_t le32(const unsigned char *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
/* 1 valid identified reply, 0 unrelated, -1 matching error/malformed. */
static int reply(uint16_t opcode, const unsigned char *p, size_t size, int quiet)
{
	const unsigned char *v = NULL;
	if (size < 3 || p[0] != 4 || size != (size_t)p[2] + 3)
		return -1;
	if (p[1] == 0x0e) {
		if (p[2] < 3)
			return -1;
		if (le16(p + 4) != opcode)
			return 0;
		if (p[2] < 4)
			return -1;
		if (opcode == 0x1001) {
			if (!quiet)
				printf("HCI status=0x%02x\n", p[6]);
			if (p[6] || p[2] != 12)
				return -1;
			if (!quiet)
				printf("HCI version=%u revision=0x%04x LMPversion=%u "
				       "manufacturer=0x%04x subversion=0x%04x\n",
				       p[7], le16(p + 8), p[10], le16(p + 11), le16(p + 13));
			return 1;
		}
		if (opcode == 0xfc00) {
			/* Newer QCA response: EDL header00/19, version length, version12. */
			if (p[2] != 18 || p[6] != 0 || p[7] != 0x19 || p[8] != 12)
				return -1;
			v = p + 9;
		}
	} else if (opcode == 0xfc00 && p[1] == 0xff) {
		/* Older ROM vendor event: EDL header00/02 followed by version12. */
		if (p[2] != 14 || p[3] != 0 || p[4] != 2)
			return -1;
		v = p + 5;
	} else
		return 0;
	if (!v)
		return 0;
	if (!quiet)
		printf("QCA product=0x%08x patch=0x%04x ROM=0x%04x SOC=0x%08x\n", le32(v),
		       le16(v + 4), le16(v + 6), le32(v + 8));
	return le16(v + 6) && le32(v + 8) ? 1 : -1;
}
static int selftest(void)
{
	const unsigned char good[] = {4, 14, 12, 1, 1, 16, 0, 9, 52, 18, 9, 29, 0, 120, 86};
	const unsigned char error[] = {4, 14, 4, 1, 1, 16, 1};
	const unsigned char shortpacket[] = {4, 14, 12, 1, 1, 16, 0};
	const unsigned char vendor[] = {4, 255, 14, 0, 2, 1, 0, 0, 0, 2, 0, 3, 0, 4, 0, 0, 0};
	const unsigned char newer[] = {4, 14, 18, 1, 0, 252, 0, 25, 12, 1, 0,
				       0, 0,  2,  0, 3, 0,   4, 0,  0,	0};
	const unsigned char live[] = {4, 14, 18,  1,  0, 252, 0,  25, 12, 19, 0,
				      0, 0,  230, 56, 1, 2,   17, 18, 12, 64};
	const unsigned char unrelated[] = {4, 14, 4, 1, 3, 12, 0};
	const unsigned char wrongtype[] = {4, 255, 14, 0, 3, 1, 0, 0, 0, 2, 0, 3, 0, 4, 0, 0, 0};
	if (reply(0x1001, good, sizeof(good), 1) != 1 ||
	    reply(0x1001, error, sizeof(error), 1) != -1 ||
	    reply(0x1001, shortpacket, sizeof(shortpacket), 1) != -1 ||
	    reply(0xfc00, vendor, sizeof(vendor), 1) != 1 ||
	    reply(0xfc00, newer, sizeof(newer), 1) != 1 ||
	    reply(0xfc00, live, sizeof(live), 1) != 1 ||
	    reply(0x1001, unrelated, sizeof(unrelated), 1) != 0 ||
	    reply(0xfc00, wrongtype, sizeof(wrongtype), 1) != -1)
		return 1;
	puts("PASS8HCI fixtures:standard,controller "
	     "error,truncated,vendor,newerQCA,liveM3100,unrelated,wrongtype");
	return 0;
}
static int board_ok(void)
{
	char data[256];
	int fd = open("/proc/device-tree/compatible", O_RDONLY);
	ssize_t n;
	if (fd < 0)
		return 0;
	n = read(fd, data, sizeof(data));
	close(fd);
	if (n <= 0)
		return 0;
	for (ssize_t i = 0; i < n;) {
		size_t len = strnlen(data + i, n - i);
		if (len == 13 && !memcmp(data + i, "inseego,m3100", 13))
			return 1;
		i += len + 1;
	}
	return 0;
}
static int query(int fd, uint16_t opcode, const unsigned char *data, size_t n)
{
	unsigned char cmd[260] = {1, opcode & 255, opcode >> 8, (unsigned char)n};
	unsigned char p[258];
	long long end = now_ms() + 2500;
	memcpy(cmd + 4, data, n);
	printf("TX:");
	for (size_t i = 0; i < n + 4; i++)
		printf(" %02x", cmd[i]);
	puts("");
	fflush(stdout);
	if (write(fd, cmd, n + 4) != (ssize_t)(n + 4))
		return -1;
	while (now_ms() < end) {
		if (exact(fd, p, 1, end))
			break;
		if (p[0] != 4) {
			printf("non-H4-event byte %02x\n", p[0]);
			continue;
		}
		if (exact(fd, p + 1, 2, end) || exact(fd, p + 3, p[2], end))
			break;
		printf("RX:");
		for (size_t i = 0; i < (size_t)p[2] + 3; i++)
			printf(" %02x", p[i]);
		puts("");
		int result = reply(opcode, p, (size_t)p[2] + 3, 0);
		if (result)
			return result == 1 ? 0 : -1;
	}
	fprintf(stderr, "opcode %04x: no matching event within2500ms\n", opcode);
	return -1;
}
int main(int argc, char **argv)
{
	char sys[PATH_MAX], resolved[PATH_MAX];
	struct termios saved, t;
	int fd, rc = 1;
	const unsigned char version = 0x19;
	if (argc == 2 && !strcmp(argv[1], "--selftest"))
		return selftest();
	if (geteuid() != 0 || !board_ok()) {
		fprintf(stderr, "requires root on inseego,m3100\n");
		return 2;
	}
	if (argc < 2 || argc > 3 || (argc == 3 && strcmp(argv[2], "--no-flow"))) {
		fprintf(stderr,
			"usage: %s /dev/ttyMSM1 [--no-flow]\nRequires UART830000 and pre-enabled "
			"BT power. Sends only HCI/version queries.\n",
			argv[0]);
		return 2;
	}
	const char *tty = strrchr(argv[1], '/');
	tty = tty ? tty + 1 : argv[1];
	if (strchr(tty, '/') || !tty[0])
		return 2;
	snprintf(sys, sizeof(sys), "/sys/class/tty/%s/device", tty);
	if (!realpath(sys, resolved) || !strstr(resolved, "830000")) {
		fprintf(stderr, "refusing UART not verified as830000: %s\n", sys);
		return 2;
	}
	printf("verified UART %s -> %s\n", argv[1], resolved);
	fd = open(argv[1], O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0) {
		perror("open");
		return 1;
	}
	if (ioctl(fd, TIOCEXCL) || tcgetattr(fd, &saved)) {
		perror("exclusive/termios");
		close(fd);
		return 1;
	}
	t = saved;
	cfmakeraw(&t);
	cfsetispeed(&t, B115200);
	cfsetospeed(&t, B115200);
	t.c_cflag |= CLOCAL | CREAD;
	if (argc == 2)
		t.c_cflag |= CRTSCTS;
	else
		t.c_cflag &= ~CRTSCTS;
	if (tcsetattr(fd, TCSANOW, &t)) {
		perror("settermios");
		goto done;
	}
	tcflush(fd, TCIOFLUSH);
	int standard = query(fd, 0x1001, (const unsigned char *)"", 0);
	int vendor = query(fd, 0xfc00, &version, 1);
	printf("standard_version_result=%d qca_version_result=%d\n", standard, vendor);
	rc = vendor ? 1 : 0;
done:
	tcsetattr(fd, TCSANOW, &saved);
	ioctl(fd, TIOCNXCL);
	close(fd);
	return rc;
}
