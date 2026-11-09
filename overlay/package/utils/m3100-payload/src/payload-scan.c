/* Read only the explicitly reserved staging pool, never MMIO or NAND.
 * The caller verifies the copied archive against its build-time SHA256.
 */
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define POOL_START UINT64_C(0xa1000000)
#define POOL_END UINT64_C(0xaa000000)
#define WINDOW (1024 * 1024)
static const unsigned char magic[32] = "M3100_FW_RAM_TRAILER_V1";

static uint64_t le64(const unsigned char *p)
{
    uint64_t n = 0;
    for (unsigned i = 0; i < 8; i++) n |= (uint64_t)p[i] << (8 * i);
    return n;
}

static int copy_payload(int mem, uint64_t start, uint64_t size, const char *path)
{
    int out = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (out < 0) { perror("payload output"); return 1; }
    while (size) {
        size_t count = size > 65536 ? 65536 : (size_t)size;
        size_t mapped = (count + 4095) & ~(size_t)4095;
        const unsigned char *buf = mmap(NULL, mapped, PROT_READ, MAP_SHARED, mem, (off_t)start);
        if (buf == MAP_FAILED) { perror("payload map"); close(out); return 1; }
        size_t done = 0;
        while (done < count) {
            ssize_t n = write(out, buf + done, count - done);
            if (n <= 0) { perror("payload write"); munmap((void *)buf, mapped); close(out); return 1; }
            done += (size_t)n;
        }
        munmap((void *)buf, mapped);
        start += count;
        size -= count;
    }
    return close(out) ? 1 : 0;
}

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: %s /dev/mem output.xz\n", argv[0]);
        return 2;
    }
    int mem = open(argv[1], O_RDONLY | O_SYNC);
    if (mem < 0) { perror("staging memory"); return 1; }
    for (uint64_t base = POOL_START; base < POOL_END; base += WINDOW) {
        const unsigned char *p = mmap(NULL, WINDOW, PROT_READ, MAP_SHARED, mem, (off_t)base);
        if (p == MAP_FAILED) { perror("staging map"); close(mem); return 1; }
        for (unsigned off = 0; off < WINDOW; off += 4096) {
            if (memcmp(p + off, magic, sizeof(magic))) continue;
            const unsigned char *h = p + off;
            uint64_t size = le64(h + 40), start = base + off + 4096;
            if (h[32] != 1 || h[33] || h[34] || h[35] ||
                h[36] || h[37] != 0x10 || h[38] || h[39] ||
                !size || size > UINT64_C(96) * 1024 * 1024 || start + size > POOL_END)
                continue;
            printf("payload_start=0x%llx payload_bytes=%llu\n",
                   (unsigned long long)start, (unsigned long long)size);
            munmap((void *)p, WINDOW);
            int result = copy_payload(mem, start, size, argv[2]);
            close(mem);
            return result;
        }
        munmap((void *)p, WINDOW);
    }
    close(mem);
    fprintf(stderr, "No firmware trailer in reserved staging pool\n");
    return 3;
}
