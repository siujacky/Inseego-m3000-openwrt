/* Bounded read-only M3100 PCIe comparison. Never writes mapped registers. */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
int main(void)
{
    static const uint32_t addresses[] = {0x01c07420, 0x40000710, 0x4000080c};
    char board[64] = {0};
    int ident = open("/proc/device-tree/compatible", O_RDONLY);
    if (ident < 0 || read(ident, board, sizeof(board) - 1) <= 0) return 2;
    close(ident);
    /* Stock uses its downstream model compatible; independently check the
     * SoC and the same serial-targeted USB link before executing there.
     */
    if (geteuid() != 0) return 2;
    if (strcmp(board, "inseego,m3100") && strcmp(board, "qcom,sdxlemur-mtp")) {
        fprintf(stderr, "unsupported first board compatible: %s\n", board); return 2;
    }
    unsigned char board_id[8], expected[8] = {2, 1, 0, 8, 0, 0, 3, 2};
    ident = open("/proc/device-tree/qcom,board-id", O_RDONLY);
    if (ident < 0 || read(ident, board_id, 8) != 8 || memcmp(board_id, expected, 8)) return 2;
    close(ident);
    int fd = open("/dev/mem", O_RDONLY | O_SYNC);
    if (fd < 0) { perror("open devmem"); return 1; }
    long page = sysconf(_SC_PAGESIZE);
    if (page != 4096) return 2;
    for (unsigned i = 0; i < sizeof(addresses) / sizeof(addresses[0]); i++) {
        uint32_t base = addresses[i] & ~4095U;
        void *map = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, base);
        if (map == MAP_FAILED) { perror("map register"); return 1; }
        volatile uint32_t *reg = (volatile uint32_t *)((char *)map + addresses[i] - base);
        uint32_t value = *reg;
        printf("0x%08x = 0x%08x\n", addresses[i], value);
        munmap(map, 4096);
    }
    close(fd);
    return 0;
}
