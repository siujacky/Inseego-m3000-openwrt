/* Sample only M3100 switch bus GPIO82/83 as inputs after QUP is unbound. */
#include <fcntl.h>
#include <linux/gpio.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
int main(void)
{
    struct gpiochip_info chip = {0};
    struct gpio_v2_line_request request = {
        .offsets = {82, 83}, .config.flags = GPIO_V2_LINE_FLAG_INPUT, .num_lines = 2,
        .consumer = "m3100-switch-input-check"
    };
    struct gpio_v2_line_values data = {.mask = 3};
    int fd = open("/dev/gpiochip0", O_RDONLY);
    if (fd < 0 || ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, &chip)) { perror("gpiochip"); return 1; }
    if (!strstr(chip.label, "f100000") || chip.lines < 84) {
        fprintf(stderr, "not the expected M3100 TLMM controller\n"); return 2;
    }
    if (ioctl(fd, GPIO_V2_GET_LINE_IOCTL, &request)) { perror("request inputs"); return 1; }
    if (ioctl(request.fd, GPIO_V2_LINE_GET_VALUES_IOCTL, &data)) { perror("read inputs"); return 1; }
    printf("GPIO82=%u GPIO83=%u (inputs only)\n", (unsigned)(data.bits & 1), (unsigned)((data.bits >> 1) & 1));
    close(request.fd); close(fd);
    return 0;
}
