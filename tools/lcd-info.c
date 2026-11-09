#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/fb.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>
#include <sys/syscall.h>
int main(int argc, char **argv) {
    struct fb_var_screeninfo v = {0};
    struct fb_fix_screeninfo f = {0};
    int fd = open("/dev/fb0", argc == 2 ? O_RDWR : O_RDONLY);
    if (fd < 0) { perror("open"); return 1; }
    if (ioctl(fd, FBIOGET_VSCREENINFO, &v) || ioctl(fd, FBIOGET_FSCREENINFO, &f)) {
        perror("fb info"); close(fd); return 1;
    }
    printf("visible=%ux%u virtual=%ux%u bpp=%u stride=%u smem=%u type=%u visual=%u\n",
           v.xres,v.yres,v.xres_virtual,v.yres_virtual,v.bits_per_pixel,
           f.line_length,f.smem_len,f.type,f.visual);
    printf("red=%u:%u green=%u:%u blue=%u:%u alpha=%u:%u offset=%u,%u\n",
           v.red.offset,v.red.length,v.green.offset,v.green.length,
           v.blue.offset,v.blue.length,v.transp.offset,v.transp.length,v.xoffset,v.yoffset);
    printf("smem_start=%lx page_offset=%lu\n",f.smem_start,f.smem_start % (unsigned long)sysconf(_SC_PAGESIZE));
    if (argc == 2) {
        size_t size = (size_t)f.line_length * v.yres;
        FILE *in = fopen(argv[1], "rb");
        unsigned char *buf = malloc(size);
        if (!in || !buf) { perror("input"); return 1; }
        if (fread(buf, 1, size, in) != size) { fprintf(stderr,"short input\n"); return 1; }
        fclose(in);
        void *fb = mmap(NULL, f.smem_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (fb == MAP_FAILED) { perror("mmap"); return 1; }
        memcpy(fb, buf, size);
        if (msync(fb, f.smem_len, MS_SYNC)) perror("msync");
        __builtin___clear_cache((char *)fb, (char *)fb + size);
        unsigned char check[3] = {0};
        if (pread(fd, check, sizeof(check), 0) != sizeof(check)) perror("readback");
        printf("kernel_readback=%02x%02x%02x\n",check[0],check[1],check[2]);
        printf("mapped_pixels=%02x%02x%02x input=%02x%02x%02x\n",
               ((unsigned char *)fb)[0],((unsigned char *)fb)[1],((unsigned char *)fb)[2],buf[0],buf[1],buf[2]);
        free(buf);
        /* Stock Qt linuxfb plugin maps pixels, then requests a rectangle
         * update with ioctl 0x9999 and four 32-bit fields: x, y, width, height.
         * Standard FBIOPAN_DISPLAY is not this driver's update protocol.
         */
        uint32_t rect[4] = {0, 0, v.xres, v.yres};
        if (ioctl(fd, 0x9999, rect)) { perror("vendor rectangle update"); return 1; }
        pread(fd, check, sizeof(check), 0);
        printf("after_ioctl kernel=%02x%02x%02x mapped=%02x%02x%02x\n",check[0],check[1],check[2],((unsigned char*)fb)[0],((unsigned char*)fb)[1],((unsigned char*)fb)[2]);
        printf("mapped=%zu vendor_rectangle_update=OK\n",size);
        fflush(stdout);
        sleep(2);
        pread(fd, check, sizeof(check), 0);
        printf("after_wait kernel=%02x%02x%02x mapped=%02x%02x%02x\n",check[0],check[1],check[2],((unsigned char*)fb)[0],((unsigned char*)fb)[1],((unsigned char*)fb)[2]);
        munmap(fb, f.smem_len);
    }
    close(fd); return 0;
}
