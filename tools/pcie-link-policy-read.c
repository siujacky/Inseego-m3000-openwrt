/* Fixed M3100 DBI link-policy snapshot. Never writes MMIO or selectors.
 * Provenance: actual stock msm_pcie_enable b04a0d8c, b04a119c..1218;
 * PCIe capability at DBI+0x70. EQ data is ONLY the currently selected bank.
 */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
struct snapshot { unsigned offset; const char *name; };
int main(int argc, char **argv)
{
    (void)argv;
    if (argc != 1 || geteuid() != 0) return 2;
    char board[64] = {0};
    int ident = open("/proc/device-tree/compatible", O_RDONLY);
    if (ident < 0) return 2;
    ssize_t n = read(ident, board, sizeof(board)-1);
    close(ident);
    if (n <= 0 || (strcmp(board,"inseego,m3100") && strcmp(board,"qcom,sdxlemur-mtp"))) return 2;
    unsigned char id[8], expected[8] = {2,1,0,8,0,0,3,2};
    ident = open("/proc/device-tree/qcom,board-id", O_RDONLY);
    if (ident < 0) return 2;
    n = read(ident,id,8);
    close(ident);
    if (n != 8 || memcmp(id,expected,8) || sysconf(_SC_PAGESIZE) != 4096) return 2;
    int fd = open("/dev/mem", O_RDONLY|O_SYNC);
    if (fd < 0) { perror("open devmem"); return 1; }
    void *map = mmap(NULL,4096,PROT_READ,MAP_SHARED,fd,0x40000000);
    if (map == MAP_FAILED) { perror("map DBI"); close(fd); return 1; }
    const volatile uint32_t *dbi = map;
    uint32_t pciid = dbi[0], cap = dbi[0x70/4];
    if (pciid != 0x030817cb || (cap & 0xff) != 0x10) {
        fprintf(stderr,"unexpected root/capability: id=%08x cap70=%08x; stop\n",pciid,cap);
        munmap(map,4096); close(fd); return 2;
    }
    static const struct snapshot registers[] = {
        {0x7c,"PCIE_LINK_CAPABILITY"}, {0x80,"PCIE_LINK_CONTROL_STATUS"},
        {0xb40,"AUX_CLK_FREQUENCY"}, {0x890,"GEN3_RELATED_ACTIVE_SHADOW_SELECTOR"},
        {0x8a8,"GEN3_EQ_CONTROL_ACTIVE_BANK"}, {0x8ac,"GEN3_EQ_FB_MODE_DIR_CHANGE_ACTIVE_BANK"}
    };
    printf("root_id=%08x exp_cap70=%08x; READ_ONLY; no bank selector writes\n",pciid,cap);
    for (unsigned i=0;i<sizeof(registers)/sizeof(registers[0]);i++) {
        uint32_t value=dbi[registers[i].offset/4];
        printf("0x%08x %-44s = 0x%08x\n",0x40000000U+registers[i].offset,registers[i].name,value);
        if (registers[i].offset==0x890)
            printf("  active_shadow_selector_bits25_24=%u zrxdc_noncompl_bit0=%u\n",(value>>24)&3,value&1);
        if (registers[i].offset==0x80)
            printf("  DLLLA=%u training=%u width=%u speed_code=%u\n",!!(value&(1U<<29)),!!(value&(1U<<27)),(value>>20)&0x3f,(value>>16)&0xf);
    }
    puts("EQ values describe the currently selected bank only; no proof of other banks or pre-link values.");
    munmap(map,4096); close(fd); return 0;
}
