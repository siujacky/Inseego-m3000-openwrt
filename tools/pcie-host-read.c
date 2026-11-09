/* Fixed M3100 host-controller/clock snapshot. O_RDONLY/PROT_READ only.
 * PARF offsets: Qualcomm pci-msm.c and Linux6.12 pcie-qcom.c.
 * GCC offsets: Linux6.12 gcc-sdx65.c (GCC physical base0x100000).
 * No clock enable, GPIO, reset, test-bus selector, or register writes.
 */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

struct snapshot { uint32_t address; const char *name; };
int main(int argc, char **argv)
{
    (void)argv;
    static const struct snapshot registers[] = {
        {0x01c00000, "PARF_SYS_CTRL"},
        {0x01c00020, "PARF_PM_CTRL"},
        {0x01c00024, "PARF_PM_STTS"},
        {0x01c00028, "PARF_PM_STTS_1"},
        {0x01c00040, "PARF_PHY_CTRL"},
        {0x01c00174, "PARF_MHI_CLOCK_RESET_CTRL"},
        {0x01c001a8, "PARF_AXI_MSTR_WR_ADDR_HALT"},
        {0x01c001b0, "PARF_LTSSM"},
        {0x01c00230, "PARF_STATUS"},
        {0x01c002b0, "PARF_CLKREQ_OVERRIDE"},
        {0x01c00350, "PARF_DBI_BASE_ADDR"},
        {0x01c00358, "PARF_SLV_ADDR_SPACE_SIZE"},
        {0x01c01000, "PARF_DEVICE_TYPE"},
        {0x00188004, "GCC_PCIE_0_CLKREF_EN"},
        {0x00143030, "GCC_PCIE_RCHNG_PHY_CBCR"},
        {0x00143034, "GCC_PCIE_AUX_CBCR"},
        {0x0014303c, "GCC_PCIE_PIPE_CBCR"},
        {0x00143044, "GCC_PCIE_PIPE_CLK_SRC_MUX"},
        {0x00143048, "GCC_PCIE_AUX_PHY_CMD_RCGR"},
        {0x0014304c, "GCC_PCIE_AUX_PHY_CFG_RCGR"},
        {0x0016d010, "GCC_PCIE_VOTE_ENABLE"},
        {0x40000710, "DBI_PORT_LINK_CONTROL"},
        {0x4000080c, "DBI_LINK_WIDTH_SPEED_CONTROL"}
    };
    if (argc != 1 || geteuid() != 0) return 2;
    char board[64] = {0};
    int ident = open("/proc/device-tree/compatible", O_RDONLY);
    if (ident < 0 || read(ident, board, sizeof(board)-1) <= 0) return 2;
    close(ident);
    if (strcmp(board, "inseego,m3100") && strcmp(board, "qcom,sdxlemur-mtp")) return 2;
    unsigned char id[8], expected[8] = {2,1,0,8,0,0,3,2};
    ident = open("/proc/device-tree/qcom,board-id", O_RDONLY);
    if (ident < 0 || read(ident, id, 8) != 8 || memcmp(id, expected, 8)) return 2;
    close(ident);
    if (sysconf(_SC_PAGESIZE) != 4096) return 2;
    int fd = open("/dev/mem", O_RDONLY | O_SYNC);
    if (fd < 0) { perror("open devmem"); return 1; }
    for (unsigned i = 0; i < sizeof(registers)/sizeof(registers[0]); i++) {
        uint32_t base = registers[i].address & ~4095U;
        void *map = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, base);
        if (map == MAP_FAILED) { perror("map register"); close(fd); return 1; }
        const volatile uint32_t *reg = (const volatile uint32_t *)
            ((const char *)map + registers[i].address - base);
        uint32_t value = *reg;
        printf("0x%08x %-33s = 0x%08x\n", registers[i].address, registers[i].name, value);
        if (registers[i].address == 0x01c002b0)
            printf("  CLKREQ override_enable_bit1=%u override_value_bit3=%u\n",
                   !!(value & 2), !!(value & 8));
        if (registers[i].address == 0x00188004)
            printf("  CLKREF software_enable_bit0=%u (not a physical clock measurement)\n", !!(value & 1));
        if (registers[i].address == 0x0016d010)
            printf("  AUX_vote_bit3=%u PIPE_vote_bit4=%u RCHNG_vote_bit7=%u\n",
                   !!(value & 8), !!(value & 16), !!(value & 128));
        munmap(map, 4096);
    }
    close(fd);
    return 0;
}
