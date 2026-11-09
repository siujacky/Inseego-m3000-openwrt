/* Single-register, volatile M3100 RAM PHY experiment. No flash writes.
 * Stock readback and DT sequence: PHY+0x1420 =0x40; OpenWrt reads0.
 * Default reads only. Explicit apply/restore require exact prior value.
 */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/utsname.h>
#include <unistd.h>

static int guard(void)
{
    char text[64] = {0}, line[256];
    struct utsname name;
    FILE *f = fopen("/tmp/sysinfo/board_name", "r");
    if (!f || !fgets(text, sizeof(text), f)) return 0;
    fclose(f);
    if (geteuid() || strcmp(text, "inseego,m3100\n") || uname(&name) ||
        strcmp(name.release, "6.12.112")) return 0;
    unsigned vendor = 0, device = 0;
    f = fopen("/sys/bus/pci/devices/0000:00:00.0/vendor", "r");
    if (!f) return 0;
    int present = fscanf(f, "%x", &vendor) == 1;
    fclose(f);
    f = fopen("/sys/bus/pci/devices/0000:00:00.0/device", "r");
    if (!f) return 0;
    present = present && fscanf(f, "%x", &device) == 1;
    fclose(f);
    if (!present || vendor != 0x17cb || device != 0x0308) return 0;
    f = fopen("/proc/mtd", "r");
    if (!f) return 0;
    while (fgets(line, sizeof(line), f)) {
        if (!strncmp(line, "mtd", 3)) { fclose(f); return 0; }
    }
    fclose(f);
    const unsigned char expected[4] = {0, 0, 0, 2};
    unsigned char lanes[4];
    f = fopen("/proc/device-tree/soc/pcie@1c00000/num-lanes", "rb");
    if (!f) return 0;
    int match = fread(lanes, 1, 4, f) == 4 && !memcmp(lanes, expected, 4);
    fclose(f);
    return match;
}
int main(int argc, char **argv)
{
    int apply = argc == 2 && !strcmp(argv[1], "--apply-stock-value");
    int restore = argc == 2 && !strcmp(argv[1], "--restore-zero");
    if (argc != 1 && !apply && !restore) return 2;
    if (!guard()) { fputs("requires exact M3100 x2 Linux6.12.112 NAND-free RAM session\n", stderr); return 2; }
    int fd = open("/dev/mem", (apply || restore ? O_RDWR : O_RDONLY) | O_SYNC);
    if (fd < 0) { perror("devmem"); return 1; }
    void *map = mmap(NULL, 4096, PROT_READ | (apply || restore ? PROT_WRITE : 0),
                     MAP_SHARED, fd, 0x01c07000);
    if (map == MAP_FAILED) { perror("mmap"); close(fd); return 1; }
    volatile uint32_t *reg = (volatile uint32_t *)((char *)map + 0x420);
    uint32_t before = *reg;
    printf("PHY 0x01c07420 before=0x%08x\n", before);
    int result = 0;
    if (apply || restore) {
        uint32_t expected = apply ? 0 : 0x40, target = apply ? 0x40 : 0;
        if (before != expected) { fputs("prior-value guard failed; no write\n", stderr); result = 2; }
        else {
            __sync_synchronize();
            *reg = target;
            __sync_synchronize();
            uint32_t after = *reg;
            printf("PHY after=0x%08x\n", after);
            if (after != target) result = 1;
        }
    }
    munmap(map, 4096); close(fd);
    return result;
}
