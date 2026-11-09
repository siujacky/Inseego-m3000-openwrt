/* SPDX-License-Identifier: GPL-2.0-only */
/* M3100 stock-derived QPIC PIO transport for a Linux 6.12 platform driver.
 * Caller owns the 0x01b22000/0x2000 resource, enables QPIC clock and panel
 * regulator, selects pinctrl, resets the panel, and serializes ALL calls.
 * This is source-level recovery, not yet compiled or tested on hardware.
 */
#ifndef M3100_LCD_QPIC_PIO_H
#define M3100_LCD_QPIC_PIO_H
#include <linux/io.h>
#include <linux/bits.h>
#include <linux/iopoll.h>
#include <linux/delay.h>
#include <linux/errno.h>

static inline int m3100_qpic_setup(void __iomem *b)
{
	u32 v;
	int ret;
	writel(1, b + 0x2c);
	usleep_range(100, 200);
	ret = readl_poll_timeout(b + 0x14, v, v & BIT(8), 100, 500000);
	if (ret)
		return ret;
	writel((readl(b) & 0xffffc004) | 0x308, b);
	writel(0xff, b + 0x10);
	writel(0, b + 0x08);
	writel(0x024105ef, b + 0x20);
	writel(0x005e122c, b + 0x28);
	return 0;
}

static inline int m3100_qpic_command(void __iomem *b, u8 command,
				      const u8 *data, size_t n)
{
	u32 value = 0, cfg, status;
	size_t i;
	int ret;
	if (n <= 4) {
		for (i = 0; i < n; i++)
			value |= (u32)data[i] << (8 * i);
		writel(n, b + 0x18);
		writel(value, b + ((0x400 | command) << 2));
		return 0;
	}
	writel(0xff, b + 0x10);
	writel(0, b + 0x18);
	cfg = readl(b + 0x28);
	writel((cfg & ~0xff) | command | BIT(24), b + 0x28);
	writel(0, b + 0x100);
	for (i = 0; i < n; i++) {
		usleep_range(100, 200); /* exact stock long-command pacing */
		writel(data[i], b + 0x140);
	}
	writel(0, b + 0x180);
	ret = readl_poll_timeout(b + 0x14, status, !(status & BIT(5)),
				10, 500000);
	writel(cfg, b + 0x28);
	return ret;
}

/* The stock framebuffer is packed 18-bit RGB in three bytes, not RGB888.
 * This helper accepts ordinary R,G,B bytes and reproduces the stock FIFO
 * word after converting to the stock packed representation.
 */
static inline u32 m3100_qpic_rgb888(u8 r, u8 g, u8 bl)
{
	u32 packed = ((u32)(r >> 2) << 12) |
		     ((u32)(g >> 2) << 6) | (bl >> 2);
	u8 a = packed >> 16, c = packed >> 8, d = packed;
	u32 middle = ((d >> 6) | ((c & 15) << 2));
	u32 high = ((c >> 4) | ((a & 3) << 4));
	return (high << 18) | ((d & 63) << 2) | (middle << 10);
}

/* Set window with 2a/2b first. The configured stock command is 2c.
 * Stock PIO writes every pixel directly, then terminates via +0x180.
 * Frame pacing/FIFO saturation still requires hardware validation.
 */
static inline int m3100_qpic_rgb888_frame(void __iomem *b,
					const u8 *rgb, size_t pixels)
{
	u32 status;
	size_t i;
	if (pixels != 320 * 240)
		return -EINVAL;
	writel(0xff, b + 0x10);
	writel(0, b + 0x18);
	writel(0, b + 0x100);
	for (i = 0; i < pixels; i++, rgb += 3)
		writel(m3100_qpic_rgb888(rgb[0], rgb[1], rgb[2]), b + 0x140);
	writel(0, b + 0x180);
	return readl_poll_timeout(b + 0x14, status, !(status & BIT(5)),
				 10, 500000);
}
#endif
