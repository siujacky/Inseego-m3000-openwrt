// SPDX-License-Identifier: GPL-2.0-only
/* RAM-only M3100 LCD bring-up. Probe does not enable power or reset.
 * echo bars > /dev/m3100-lcd initializes the panel and draws bars.
 * echo off > /dev/m3100-lcd disables backlight and owned resources.
 */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/clk.h>
#include <linux/gpio/consumer.h>
#include <linux/regulator/consumer.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include "lcd-qpic-pio.h"

struct m3100_lcd {
	void __iomem *base;
	struct clk *clk;
	struct regulator *vdd;
	struct gpio_desc *reset, *bl;
	struct miscdevice misc;
	struct mutex lock;
	bool powered, clocked;
	void *uploader;
};
#define LCD_FRAME_BYTES (320 * 240 * 3)
struct lcd_file {
	struct m3100_lcd *lcd;
	u8 *frame;
	size_t received;
};
struct panel_cmd { u8 cmd, n, data[14]; u16 delay; };
#include "lcd-qpic-init.h"

static void lcd_off(struct m3100_lcd *l)
{
	if (l->powered) {
		gpiod_direction_output(l->bl, 0);
		gpiod_direction_output(l->reset, 1);
	}
	if (l->clocked) {
		clk_disable_unprepare(l->clk);
		l->clocked = false;
	}
	if (l->powered) {
		regulator_disable(l->vdd);
		regulator_set_load(l->vdd, 0);
		l->powered = false;
	}
}

static int lcd_bars(struct m3100_lcd *l)
{
	static const u8 colors[][3] = {
		{255,0,0}, {0,255,0}, {0,0,255}, {255,255,255},
		{0,0,0}, {255,255,0}, {0,255,255}, {255,0,255},
	};
	u8 *rgb;
	unsigned int i, x, y;
	int ret;
	const u8 columns[] = {0, 0, 1, 63}, rows[] = {0, 0, 0, 239};
	lcd_off(l);
	rgb = kvzalloc(320 * 240 * 3, GFP_KERNEL);
	if (!rgb)
		return -ENOMEM;
	ret = regulator_set_load(l->vdd, 10000);
	if (ret < 0)
		goto free;
	ret = regulator_set_voltage(l->vdd, 2800000, 2928000);
	if (ret)
		goto free;
	ret = regulator_enable(l->vdd);
	if (ret)
		goto free;
	l->powered = true;
	ret = gpiod_direction_output(l->bl, 0);
	if (ret)
		goto fail;
	ret = clk_prepare_enable(l->clk);
	if (ret)
		goto fail;
	l->clocked = true;
	ret = m3100_qpic_setup(l->base);
	if (ret)
		goto fail;
	/* Active-low descriptor: logical 0 is physical high. */
	ret = gpiod_direction_output(l->reset, 0);
	if (ret)
		goto fail;
	msleep(20);
	gpiod_set_value_cansleep(l->reset, 1);
	msleep(50);
	gpiod_set_value_cansleep(l->reset, 0);
	msleep(50);
	for (i = 0; i < ARRAY_SIZE(panel_init); i++) {
		ret = m3100_qpic_command(l->base, panel_init[i].cmd,
				panel_init[i].data, panel_init[i].n);
		if (ret)
			goto fail;
		if (panel_init[i].delay)
			msleep(panel_init[i].delay);
	}
	ret = m3100_qpic_command(l->base, 0x2a, columns, 4);
	if (!ret)
		ret = m3100_qpic_command(l->base, 0x2b, rows, 4);
	if (ret)
		goto fail;
	for (y = 0; y < 240; y++)
		for (x = 0; x < 320; x++)
			memcpy(rgb + (y * 320 + x) * 3, colors[x / 40], 3);
	ret = m3100_qpic_rgb888_frame(l->base, rgb, 320 * 240);
	if (ret)
		goto fail;
	gpiod_set_value_cansleep(l->bl, 1);
	kvfree(rgb);
	return 0;
fail:
	lcd_off(l);
free:
	if (!l->powered)
		regulator_set_load(l->vdd, 0);
	kvfree(rgb);
	return ret;
}

static int lcd_open(struct inode *inode, struct file *file)
{
	struct miscdevice *misc = file->private_data;
	struct lcd_file *f = kzalloc(sizeof(*f), GFP_KERNEL);
	if (!f)
		return -ENOMEM;
	f->lcd = container_of(misc, struct m3100_lcd, misc);
	file->private_data = f;
	return nonseekable_open(inode, file);
}
static int lcd_release(struct inode *inode, struct file *file)
{
	struct lcd_file *f = file->private_data;
	mutex_lock(&f->lcd->lock);
	if (f->lcd->uploader == f)
		f->lcd->uploader = NULL;
	kvfree(f->frame);
	mutex_unlock(&f->lcd->lock);
	kfree(f);
	return 0;
}
static ssize_t lcd_write(struct file *file, const char __user *buf,
			 size_t count, loff_t *offset)
{
	struct lcd_file *f = file->private_data;
	struct m3100_lcd *l = f->lcd;
	char command[16];
	int ret = 0;
	if (!count)
		return 0;
	mutex_lock(&l->lock);
	if (l->uploader && l->uploader != f) {
		ret = -EBUSY;
		goto out;
	}
	if (f->frame) {
		if (!l->powered || !l->clocked) {
			kvfree(f->frame);
			f->frame = NULL;
			f->received = 0;
			l->uploader = NULL;
			ret = -EPIPE;
			goto out;
		}
		if (count > LCD_FRAME_BYTES - f->received) {
			ret = -EMSGSIZE;
			goto out;
		}
		if (copy_from_user(f->frame + f->received, buf, count)) {
			ret = -EFAULT;
			goto out;
		}
		f->received += count;
		if (f->received == LCD_FRAME_BYTES) {
			const u8 columns[] = {0,0,1,63}, rows[] = {0,0,0,239};
			ret = m3100_qpic_command(l->base, 0x2a, columns, 4);
			if (!ret)
				ret = m3100_qpic_command(l->base, 0x2b, rows, 4);
			if (!ret)
				ret = m3100_qpic_rgb888_frame(l->base, f->frame, 320 * 240);
			kvfree(f->frame);
			f->frame = NULL;
			f->received = 0;
			l->uploader = NULL;
		}
		goto out;
	}
	if (count >= sizeof(command)) {
		ret = -EINVAL;
		goto out;
	}
	if (copy_from_user(command, buf, count)) {
		ret = -EFAULT;
		goto out;
	}
	command[count] = 0;
	if (sysfs_streq(command, "bars"))
		ret = lcd_bars(l);
	else if (sysfs_streq(command, "off"))
		lcd_off(l);
	else if (count == 6 && !memcmp(command, "frame\n", 6)) {
		if (!l->powered || !l->clocked) {
			ret = -EPIPE;
			goto out;
		}
		f->frame = kvzalloc(LCD_FRAME_BYTES, GFP_KERNEL);
		if (!f->frame) {
			ret = -ENOMEM;
			goto out;
		}
		f->received = 0;
		l->uploader = f;
	}
	else
		ret = -EINVAL;
out:
	mutex_unlock(&l->lock);
	return ret ? ret : count;
}
static const struct file_operations lcd_fops = {
	.owner = THIS_MODULE, .open = lcd_open, .release = lcd_release,
	.write = lcd_write,
};
static int lcd_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct m3100_lcd *l = devm_kzalloc(dev, sizeof(*l), GFP_KERNEL);
	if (!l)
		return -ENOMEM;
	l->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(l->base))
		return PTR_ERR(l->base);
	l->clk = devm_clk_get(dev, "core_clk");
	if (IS_ERR(l->clk))
		return dev_err_probe(dev, PTR_ERR(l->clk), "clock\n");
	l->vdd = devm_regulator_get(dev, "vdd");
	if (IS_ERR(l->vdd))
		return dev_err_probe(dev, PTR_ERR(l->vdd), "vdd\n");
	l->reset = devm_gpiod_get(dev, "reset", GPIOD_ASIS);
	if (IS_ERR(l->reset))
		return dev_err_probe(dev, PTR_ERR(l->reset), "reset\n");
	l->bl = devm_gpiod_get(dev, "backlight", GPIOD_ASIS);
	if (IS_ERR(l->bl))
		return dev_err_probe(dev, PTR_ERR(l->bl), "backlight\n");
	mutex_init(&l->lock);
	l->misc = (struct miscdevice) {
		.minor = MISC_DYNAMIC_MINOR, .name = "m3100-lcd",
		.fops = &lcd_fops, .parent = dev, .mode = 0600,
	};
	platform_set_drvdata(pdev, l);
	return misc_register(&l->misc);
}
static void lcd_remove(struct platform_device *pdev)
{
	struct m3100_lcd *l = platform_get_drvdata(pdev);
	misc_deregister(&l->misc);
	mutex_lock(&l->lock);
	lcd_off(l);
	mutex_unlock(&l->lock);
}
static void lcd_shutdown(struct platform_device *pdev)
{
	struct m3100_lcd *l = platform_get_drvdata(pdev);
	mutex_lock(&l->lock);
	lcd_off(l);
	mutex_unlock(&l->lock);
}
static const struct of_device_id lcd_match[] = {
	{ .compatible = "inseego,m3100-qpic-lcd" }, {},
};
MODULE_DEVICE_TABLE(of, lcd_match);
static struct platform_driver lcd_driver = {
	.probe = lcd_probe, .remove = lcd_remove, .shutdown = lcd_shutdown,
	.driver = { .name = "m3100-qpic-lcd", .of_match_table = lcd_match,
		.suppress_bind_attrs = true },
};
module_platform_driver(lcd_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("M3100 opt-in RAM QPIC LCD color bars");

