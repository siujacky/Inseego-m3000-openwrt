/* Standard RESTART2 command, guarded to this board's NAND-free RAM session. */
#include <fcntl.h>
#include <linux/reboot.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char board[64] = {0}, line[256];
    FILE *f = fopen("/tmp/sysinfo/board_name", "r");
    if (!f || !fgets(board, sizeof(board), f)) return 2;
    fclose(f);
    if (strcmp(board, "inseego,m3100\n") || getuid() != 0 ||
        argc != 2 || strcmp(argv[1], "bootloader")) return 2;
    f = fopen("/proc/mtd", "r");
    if (!f) return 2;
    while (fgets(line, sizeof(line), f)) {
        if (!strncmp(line, "mtd", 3)) { fclose(f); return 2; }
    }
    fclose(f);
    sync();
    syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
            LINUX_REBOOT_CMD_RESTART2, "bootloader");
    perror("RESTART2 bootloader");
    return 1;
}
