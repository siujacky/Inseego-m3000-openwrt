/* M3100 RAM-only, full stock TC956x I2C workaround reproduction.
 * Default: print plan, open no device. Actual writes require explicit flag.
 * Source evidence: analysis/network-switch-stock-wa-sequence.md.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

struct reg_pair { uint32_t reg, value; };
#include "tc956x-switch-sequence.h"

static void encode_address(uint8_t out[3], uint32_t reg)
{ out[0] = reg >> 16; out[1] = reg >> 8; out[2] = reg; }
static void encode_value(uint8_t out[4], uint32_t value)
{ for (unsigned i = 0; i < 4; i++) out[i] = value >> (8 * i); }
static uint32_t decode_value(const uint8_t in[4])
{ return (uint32_t)in[0] | (uint32_t)in[1] << 8 | (uint32_t)in[2] << 16 | (uint32_t)in[3] << 24; }

static int read_register(int fd, uint32_t reg, uint32_t *value)
{
    uint8_t address[3], data[4] = {0};
    encode_address(address, reg);
    struct i2c_msg messages[2] = {
        {.addr = 0x77, .flags = 0, .len = 3, .buf = address},
        {.addr = 0x77, .flags = I2C_M_RD, .len = 4, .buf = data}
    };
    struct i2c_rdwr_ioctl_data request = {.msgs = messages, .nmsgs = 2};
    int result = ioctl(fd, I2C_RDWR, &request);
    if (result != 2) {
        fprintf(stderr, "read 0x%06x failed result=%d errno=%d\n", reg, result, errno);
        return -1;
    }
    *value = decode_value(data);
    return 0;
}

static int write_register(int fd, uint32_t reg, uint32_t value)
{
    uint8_t data[7];
    encode_address(data, reg);
    encode_value(data + 3, value);
    struct i2c_msg message = {.addr = 0x77, .flags = 0, .len = 7, .buf = data};
    struct i2c_rdwr_ioctl_data request = {.msgs = &message, .nmsgs = 1};
    int result = ioctl(fd, I2C_RDWR, &request);
    if (result != 1) {
        fprintf(stderr, "write 0x%06x failed result=%d errno=%d\n", reg, result, errno);
        return -1;
    }
    printf("W 0x%06x 0x%08x\n", reg, value);
    fflush(stdout);
    return 0;
}

static int pairs(int fd, const struct reg_pair *sequence, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        uint32_t actual;
        if (fd < 0) {
            printf("W+verify 0x%06x 0x%08x\n", sequence[i].reg, sequence[i].value);
            continue;
        }
        if (write_register(fd, sequence[i].reg, sequence[i].value) ||
            read_register(fd, sequence[i].reg, &actual)) return -1;
        if (actual != sequence[i].value) {
            fprintf(stderr, "readback mismatch 0x%06x expected=%08x actual=%08x; STOP\n",
                    sequence[i].reg, sequence[i].value, actual);
            return -1;
        }
    }
    return 0;
}

static int write_only(int fd, uint32_t reg, uint32_t value)
{
    if (fd < 0) { printf("W 0x%06x 0x%08x\n", reg, value); return 0; }
    return write_register(fd, reg, value);
}
static int rmw(int fd, uint32_t reg, uint32_t clear, uint32_t set)
{
    if (fd < 0) {
        printf("RMW 0x%06x clear=0x%08x set=0x%08x\n", reg, clear, set);
        return 0;
    }
    uint32_t actual;
    if (read_register(fd, reg, &actual)) return -1;
    return write_register(fd, reg, (actual & ~clear) | set);
}

/* Extra hardcoded operations in stock b04a8574..b04a8c84. */
static int hardcoded_wa(int fd)
{
    if (write_only(fd, 0x82c030, 4) || write_only(fd, 0x828240, 0) ||
        write_only(fd, 0x82c030, 2) || write_only(fd, 0x828000, 1)) return -1;
    for (unsigned lane = 1; lane <= 2; lane++) {
        if (lane == 2 && write_only(fd, 0x828000, 2)) return -1;
        if (rmw(fd, 0x8280a0, 0x80000, 0) || rmw(fd, 0x82b0a4, 1, 0) ||
            rmw(fd, 0x82bcd0, 0, 1)) return -1;
    }
    return write_only(fd, 0x80100c, 0x80000000) ||
           write_only(fd, 0x801010, 0xc0000000) ||
           write_only(fd, 0x84d080, 0) || write_only(fd, 0x84f0e8, 3) ||
           write_only(fd, 0x80100c, 0);
}

static int read_small(const char *path, char *buf, size_t size)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t n = read(fd, buf, size - 1);
    close(fd);
    if (n <= 0 || (size_t)n == size - 1) return -1;
    buf[n] = 0;
    return 0;
}

static int environment_guard(void)
{
    char buf[8192], path[PATH_MAX];
    if (geteuid() != 0) { fputs("requires root\n", stderr); return -1; }
    if (read_small("/proc/device-tree/compatible", buf, sizeof(buf)) ||
        strcmp(buf, "inseego,m3100")) {
        fputs("requires exact M3100 board\n", stderr); return -1;
    }
    if (read_small("/proc/cmdline", buf, sizeof(buf))) return -1;
    unsigned roots = 0;
    char *save, *token = strtok_r(buf, " \t\n", &save);
    while (token) {
        if (!strncmp(token, "root=", 5)) {
            if (strcmp(token, "root=/dev/ram0")) return -1;
            roots++;
        }
        if (!strncmp(token, "ubi.", 4) || !strncmp(token, "mtdparts=", 9) ||
            !strncmp(token, "rootfstype=", 11)) return -1;
        token = strtok_r(NULL, " \t\n", &save);
    }
    if (roots != 1) { fputs("requires RAM root\n", stderr); return -1; }
    DIR *directory = opendir("/sys/class/mtd");
    if (directory) {
        struct dirent *item;
        while ((item = readdir(directory))) {
            if (!strncmp(item->d_name, "mtd", 3)) {
                closedir(directory); fputs("MTD devices must be disabled\n", stderr); return -1;
            }
        }
        closedir(directory);
    } else if (errno != ENOENT) return -1;
    if (!realpath("/sys/bus/i2c/devices/i2c-1/of_node", path) ||
        strlen(path) < strlen("/soc/i2c@835000") ||
        strcmp(path + strlen(path) - strlen("/soc/i2c@835000"), "/soc/i2c@835000")) {
        fputs("i2c-1 must map to QUP1 at835000\n", stderr); return -1;
    }
    return 0;
}

static int selftest(void)
{
    uint8_t data[7];
    const uint8_t expected[7] = {0x80, 0x12, 0x08, 0x78, 0x56, 0x34, 0x12};
    encode_address(data, 0x801208); encode_value(data + 3, 0x12345678);
    if (memcmp(data, expected, sizeof(data)) || decode_value(data + 3) != 0x12345678) return 1;
    puts("wire encoding selftest passed");
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--selftest")) return selftest();
    int fd = -1;
    if (argc != 1) {
        if (argc != 4 || strcmp(argv[1], "--apply-stock-wa") || strcmp(argv[2], "--expect-version")) {
            fprintf(stderr, "usage: %s [--selftest | --apply-stock-wa --expect-version 0xVALUE]\n", argv[0]);
            return 2;
        }
        char *end;
        errno = 0;
        unsigned long expected = strtoul(argv[3], &end, 0);
        if (errno || end == argv[3] || *end || !expected || expected >= UINT32_MAX) {
            fputs("expected version must be established nonzero/non-FFFFFFFF value\n", stderr); return 2;
        }
        if (environment_guard()) return 1;
        fd = open("/dev/i2c-1", O_RDWR | O_CLOEXEC);
        if (fd < 0) { perror("open i2c-1"); return 1; }
        uint32_t version;
        if (read_register(fd, 0x800000, &version) || version != expected) {
            fputs("version guard failed; no register value writes performed\n", stderr);
            close(fd); return 1;
        }
    } else puts("DRY RUN; no device opened. M3100 stock force-i2c-setting path.");
    int failed = pairs(fd, stock_reg_update, sizeof(stock_reg_update)/sizeof(stock_reg_update[0])) ||
                 hardcoded_wa(fd) ||
                 pairs(fd, stock_switch_reg_update, sizeof(stock_switch_reg_update)/sizeof(stock_switch_reg_update[0]));
    if (fd >= 0) close(fd);
    return failed ? 1 : 0;
}
