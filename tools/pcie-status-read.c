/* Bounded, read-only M3100 PCIe link snapshot. No MMIO stores.
 * PARF/ELBI fields: Qualcomm downstream pci-msm.c.
 * DBI debug/link fields: Linux6.12 pcie-designware.h, pcie-qcom.c, pci_regs.h.
 */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static uint32_t read32(const void *page, unsigned offset)
{ return *(const volatile uint32_t *)((const unsigned char *)page + offset); }

static const char *state_name(unsigned state)
{
    static const char *const names[] = {
        "DETECT_QUIET", "DETECT_ACTIVE", "POLL_ACTIVE", "POLL_COMPLIANCE",
        "POLL_CONFIG", "PRE_DETECT_QUIET", "DETECT_WAIT", "CFG_WIDTH_START",
        "CFG_WIDTH_ACCEPT", "CFG_LANENUM_WAIT", "CFG_LANENUM_ACCEPT",
        "CFG_COMPLETE", "CFG_IDLE", "RECOVERY_LOCK", "RECOVERY_SPEED",
        "RECOVERY_RCVRCFG", "RECOVERY_IDLE", "L0", "L0S", "L123_SEND_EIDLE",
        "L1_IDLE", "L2_IDLE", "L2_WAKE", "DISABLED_ENTRY", "DISABLED_IDLE",
        "DISABLED", "LOOPBACK_ENTRY", "LOOPBACK_ACTIVE", "LOOPBACK_EXIT",
        "LOOPBACK_EXIT_TIMEOUT", "HOT_RESET_ENTRY", "HOT_RESET"
    };
    return state < sizeof(names)/sizeof(names[0]) ? names[state] : "OTHER";
}

int main(int argc, char **argv)
{
    (void)argv;
    if (argc != 1 || geteuid() != 0) return 2;
    char board[64] = {0};
    int ident = open("/proc/device-tree/compatible", O_RDONLY);
    if (ident < 0 || read(ident, board, sizeof(board) - 1) <= 0) return 2;
    close(ident);
    if (strcmp(board, "inseego,m3100") && strcmp(board, "qcom,sdxlemur-mtp")) return 2;
    unsigned char id[8], expected[8] = {2,1,0,8,0,0,3,2};
    ident = open("/proc/device-tree/qcom,board-id", O_RDONLY);
    if (ident < 0 || read(ident, id, sizeof(id)) != sizeof(id) || memcmp(id, expected, sizeof(id))) return 2;
    close(ident);
    if (sysconf(_SC_PAGESIZE) != 4096) return 2;
    int fd = open("/dev/mem", O_RDONLY | O_SYNC);
    if (fd < 0) { perror("open devmem"); return 1; }
    void *parf = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0x01c00000);
    void *dbi = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0x40000000);
    if (parf == MAP_FAILED || dbi == MAP_FAILED) { perror("map registers"); close(fd); return 1; }
    uint32_t pciid = read32(dbi, 0), cap = read32(dbi, 0x70);
    if (pciid != 0x030817cb || (cap & 0xff) != 0x10) {
        fprintf(stderr, "unexpected root/capability: id=%08x cap70=%08x; stop\n", pciid, cap);
        munmap(dbi, 4096); munmap(parf, 4096); close(fd); return 2;
    }
    uint32_t linkcap = read32(dbi, 0x7c);
    printf("root_id=%08x exp_cap70=%08x LNKCAP@4000007c=%08x DLLLARC=%u\n",
           pciid, cap, linkcap, !!(linkcap & (1U << 20)));
    uint32_t ctlsta2 = read32(dbi, 0xa0); /* cap70 +LNKCTL2 offset30 */
    printf("LNKCTL2STA2@400000a0=%08x enter_compliance=%u target_speed_code=%u autonomous_speed_disable=%u\n",
           ctlsta2, !!(ctlsta2 & 0x10), ctlsta2 & 0xf, !!(ctlsta2 & 0x20));
    for (unsigned i = 0; i < 10; i++) {
        uint32_t p = read32(parf, 0x1b0);       /* PARF_LTSSM */
        uint32_t e = read32(dbi, 0xf28);        /* ELBI basef20 +SYS_STTS8 */
        uint32_t d0 = read32(dbi, 0x728);       /* DWC PORT_DEBUG0 */
        uint32_t d1 = read32(dbi, 0x72c);       /* DWC PORT_DEBUG1 */
        uint32_t lc = read32(dbi, 0x80);        /* PCIe cap70 +LNKCTL/STA10 */
        unsigned ls = lc >> 16;
        printf("sample=%u PARF@01c001b0=%08x enable=%u state=%02x(%s) "
               "ELBI@40000f28=%08x xmlh_up=%u state=%02x(%s) "
               "DBI728=%08x state=%02x(%s) DBI72c=%08x debug_up=%u debug_training=%u "
               "LNKCTLSTA@40000080=%08x DLLLA=%u training=%u width=%u speed_code=%u\n",
               i, p, !!(p & (1U << 8)), p & 0x3f, state_name(p & 0x3f),
               e, !!(e & 0x400), (e >> 12) & 0x3f, state_name((e >> 12) & 0x3f),
               d0, d0 & 0x3f, state_name(d0 & 0x3f), d1,
               !!(d1 & (1U << 4)), !!(d1 & (1U << 29)), lc,
               !!(ls & 0x2000), !!(ls & 0x0800), (ls >> 4) & 0x3f, ls & 0xf);
        fflush(stdout);
        if (i != 9) {
            struct timespec delay = {.tv_sec=0, .tv_nsec=100000000};
            nanosleep(&delay, NULL);
        }
    }
    munmap(dbi, 4096); munmap(parf, 4096); close(fd);
    return 0;
}
