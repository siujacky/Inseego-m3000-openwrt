/* Read the CYTTSP5 HID descriptor at the stock address only.
 * No bus scan, reset, firmware update or charger access.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <linux/i2c.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static unsigned le16(const uint8_t *b) { return b[0] | ((unsigned)b[1] << 8); }

int main(int argc, char **argv)
{
    uint8_t reg[2] = {1, 0}, input_reg = 3, desc[32] = {0};
    if (argc != 2) {
        fprintf(stderr, "usage: %s /dev/i2c-N\n", argv[0]);
        return 2;
    }
    int fd = open(argv[1], O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("open I2C bus"); return 1; }
    if (ioctl(fd, I2C_SLAVE, 0x24) < 0) {
        perror("claim unbound touchscreen address"); close(fd); return 1;
    }
    if (write(fd, reg, sizeof(reg)) != sizeof(reg)) {
        perror("request HID descriptor"); close(fd); return 1;
    }
    usleep(100000);
    /* Match the upstream driver's regmap input read: register 3 followed by
     * a repeated-start read, rather than reading the current bus pointer.
     */
    struct i2c_msg msgs[] = {
        {.addr = 0x24, .len = 1, .buf = &input_reg},
        {.addr = 0x24, .flags = I2C_M_RD, .len = sizeof(desc), .buf = desc},
    };
    struct i2c_rdwr_ioctl_data rdwr = {.msgs = msgs, .nmsgs = 2};
    if (ioctl(fd, I2C_RDWR, &rdwr) != 2) {
        perror("read HID descriptor"); close(fd); return 1;
    }
    ssize_t n = sizeof(desc);
    close(fd);
    printf("{\"address\":36,\"bytes\":%zd,\"raw\":\"", n);
    for (ssize_t i = 0; i < n; i++) printf("%02x", desc[i]);
    printf("\"");
    if (n == 32) {
        printf(",\"descriptor_length\":%u,\"bcd_version\":%u,"
               "\"vendor_id\":%u,\"product_id\":%u",
               le16(desc), le16(desc + 4), le16(desc + 22), le16(desc + 24));
    }
    puts("}");
    return n == 32 && le16(desc) == 32 && le16(desc + 4) == 0x100 ? 0 : 1;
}
