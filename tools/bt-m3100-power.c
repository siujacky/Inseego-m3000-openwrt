// SPDX-License-Identifier: GPL-2.0-only
/* Board-specific opt-in BT power, independent of HCI/firmware selection.
 * Resources and voltages from M3100 stock live.dts. Sequence follows
 * downstream btpower: rails, ref clock, BT_EN low100ms, high50ms.
 * SW_CTRL is an INPUT observed from the BT chip, never driven.
 */
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regulator/consumer.h>

struct bt_power {
	struct device *dev;
	struct regulator *io, *aon;
	struct clk *ref;
	struct gpio_desc *enable, *swctrl;
	struct mutex lock;
	bool io_on, aon_on, clock_on, enabled;
};
static int bt_off(struct bt_power *b)
{
	int ret, first_error = 0;

	if (b->enabled || b->clock_on) {
		gpiod_set_value_cansleep(b->enable, 0);
		msleep(50);
	}
	b->enabled = false;
	if (b->clock_on) {
		clk_disable_unprepare(b->ref);
		b->clock_on = false;
	}
	if (b->aon_on) {
		ret = regulator_disable(b->aon);
		if (ret) {
			dev_err(b->dev, "AON disable failed: %d; vote remains owned\n", ret);
			first_error = ret;
		} else {
			b->aon_on = false;
		}
	}
	/* Keep the IO vote while an AON vote could not be removed, preserving
	 * the reverse-of-startup rail ordering for a later off retry.
	 */
	if (first_error)
		return first_error;
	if (b->io_on) {
		ret = regulator_disable(b->io);
		if (ret) {
			dev_err(b->dev, "IO disable failed: %d; vote remains owned\n", ret);
			if (!first_error)
				first_error = ret;
		} else {
			b->io_on = false;
		}
	}
	return first_error;
}
static int bt_on(struct bt_power *b)
{
	int ret;
	if (b->enabled)
		return 0;
	ret = regulator_set_voltage(b->io, 1800000, 1800000);
	if (ret)
		return ret;
	ret = regulator_set_voltage(b->aon, 1012000, 1012000);
	if (ret)
		return ret;
	/* A failed disable leaves its owned vote active. Never add another
	 * enable reference when recovering from that partial-off state.
	 */
	if (!b->io_on) {
		ret = regulator_enable(b->io);
		if (ret)
			goto fail;
		b->io_on = true;
	}
	if (!b->aon_on) {
		ret = regulator_enable(b->aon);
		if (ret)
			goto fail;
		b->aon_on = true;
	}
	ret = clk_prepare_enable(b->ref);
	if (ret)
		goto fail;
	b->clock_on = true;
	ret = gpiod_direction_output(b->enable, 0);
	if (ret)
		goto fail;
	msleep(100);
	gpiod_set_value_cansleep(b->enable, 1);
	msleep(50);
	b->enabled = true;
	return 0;
fail:
	if (bt_off(b))
		dev_err(b->dev, "power-on rollback incomplete; retry off before removing driver\n");
	return ret;
}
static ssize_t enabled_show(struct device *dev, struct device_attribute *a, char *buf)
{
	struct bt_power *b = dev_get_drvdata(dev);
	bool value;
	mutex_lock(&b->lock);
	value = b->enabled;
	mutex_unlock(&b->lock);
	return sysfs_emit(buf, "%u\n", value);
}
static ssize_t enabled_store(struct device *dev, struct device_attribute *a, const char *buf,
			     size_t count)
{
	struct bt_power *b = dev_get_drvdata(dev);
	bool on;
	int ret;
	ret = kstrtobool(buf, &on);
	if (ret)
		return ret;
	mutex_lock(&b->lock);
	if (on)
		ret = bt_on(b);
	else
		ret = bt_off(b);
	mutex_unlock(&b->lock);
	return ret ? ret : count;
}
static ssize_t swctrl_show(struct device *dev, struct device_attribute *a, char *buf)
{
	struct bt_power *b = dev_get_drvdata(dev);
	int value;
	mutex_lock(&b->lock);
	value = gpiod_get_value_cansleep(b->swctrl);
	mutex_unlock(&b->lock);
	return value < 0 ? value : sysfs_emit(buf, "%d\n", value);
}
static DEVICE_ATTR_RW(enabled);
static DEVICE_ATTR_RO(swctrl);
static struct attribute *bt_attrs[] = {&dev_attr_enabled.attr, &dev_attr_swctrl.attr, NULL};
ATTRIBUTE_GROUPS(bt);
static int bt_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct bt_power *b = devm_kzalloc(dev, sizeof(*b), GFP_KERNEL);
	if (!of_machine_is_compatible("inseego,m3100"))
		return -ENODEV;
	if (!b)
		return -ENOMEM;
	b->dev = dev;
	b->io = devm_regulator_get(dev, "vdd-io");
	if (IS_ERR(b->io))
		return dev_err_probe(dev, PTR_ERR(b->io), "vdd-io\n");
	b->aon = devm_regulator_get(dev, "vdd-aon");
	if (IS_ERR(b->aon))
		return dev_err_probe(dev, PTR_ERR(b->aon), "vdd-aon\n");
	b->ref = devm_clk_get(dev, "ref");
	if (IS_ERR(b->ref))
		return dev_err_probe(dev, PTR_ERR(b->ref), "refclock\n");
	b->enable = devm_gpiod_get(dev, "enable", GPIOD_ASIS);
	if (IS_ERR(b->enable))
		return dev_err_probe(dev, PTR_ERR(b->enable), "BT_EN\n");
	b->swctrl = devm_gpiod_get(dev, "swctrl", GPIOD_IN);
	if (IS_ERR(b->swctrl))
		return dev_err_probe(dev, PTR_ERR(b->swctrl), "SW_CTRL\n");
	mutex_init(&b->lock);
	platform_set_drvdata(pdev, b);
	return 0;
}
static void bt_stop(struct platform_device *pdev)
{
	struct bt_power *b = platform_get_drvdata(pdev);
	int ret;

	mutex_lock(&b->lock);
	ret = bt_off(b);
	mutex_unlock(&b->lock);
	if (ret)
		dev_err(&pdev->dev, "stop left regulator vote(s) active: %d\n", ret);
}
static const struct of_device_id bt_match[] = {
    {.compatible = "inseego,m3100-bt-power"},
    {},
};
MODULE_DEVICE_TABLE(of, bt_match);
static struct platform_driver bt_driver = {
    .probe = bt_probe,
    .remove = bt_stop,
    .shutdown = bt_stop,
    .driver = {.name = "m3100-bt-power", .of_match_table = bt_match, .dev_groups = bt_groups},
};
module_platform_driver(bt_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("M3100 opt-in two-rail BT ROM probe power");
