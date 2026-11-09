// SPDX-License-Identifier: GPL-2.0-only
/* M3100 PM7250B QG: read-only last ADC voltage experiment.
 * Register/scale evidence: downstream qg_get_battery_voltage(), qg-reg.h,
 * qg-defs.h at kernel/msm c4689af91bc5889198657495dd6e70d4f7e689ea;
 * independently matched stock 5.4.161-perf disassembly at b07afff0.
 * No conversion trigger, latch, configuration, IRQ or charger writes.
 * Hardware may return stale data when its autonomous engine is not running.
 */
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/unaligned.h>

#define M3100_QG_BASE 0x4800
#define QG_PERPH_TYPE 0x04
#define QG_TYPE 0x0d
#define QG_LAST_ADC_V 0xc0

struct m3100_qg { struct regmap *map; };

static int voltage_raw(struct device *dev, unsigned int *raw)
{
	struct m3100_qg *qg = dev_get_drvdata(dev);
	u8 bytes[2];
	int ret = regmap_bulk_read(qg->map, M3100_QG_BASE + QG_LAST_ADC_V,
				   bytes, sizeof(bytes));
	if (ret)
		return ret;
	*raw = get_unaligned_le16(bytes);
	return 0;
}

static ssize_t voltage_raw_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	unsigned int raw;
	int ret = voltage_raw(dev, &raw);
	return ret ? ret : sysfs_emit(buf, "%u\n", raw);
}
static DEVICE_ATTR_RO(voltage_raw);

static ssize_t voltage_now_uv_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	unsigned int raw;
	int ret = voltage_raw(dev, &raw);
	return ret ? ret : sysfs_emit(buf, "%llu\n",
		(unsigned long long)div_u64(194637ULL * raw, 1000));
}
static DEVICE_ATTR_RO(voltage_now_uv);

static struct attribute *m3100_qg_attrs[] = {
	&dev_attr_voltage_raw.attr,
	&dev_attr_voltage_now_uv.attr,
	NULL
};
static const struct attribute_group m3100_qg_group = {
	.attrs = m3100_qg_attrs,
};

static int m3100_qg_probe(struct platform_device *pdev)
{
	struct m3100_qg *qg;
	unsigned int type;
	u32 base;
	int ret;

	ret = of_property_read_u32(pdev->dev.of_node, "reg", &base);
	if (ret || base != M3100_QG_BASE)
		return dev_err_probe(&pdev->dev, -EINVAL, "requires QG base0x4800\n");
	qg = devm_kzalloc(&pdev->dev, sizeof(*qg), GFP_KERNEL);
	if (!qg)
		return -ENOMEM;
	qg->map = dev_get_regmap(pdev->dev.parent, NULL);
	if (!qg->map)
		return dev_err_probe(&pdev->dev, -EPROBE_DEFER, "parent PMIC regmap unavailable\n");
	ret = regmap_read(qg->map, base + QG_PERPH_TYPE, &type);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "QG type read failed\n");
	if (type != QG_TYPE)
		return dev_err_probe(&pdev->dev, -ENODEV, "unexpected QG type0x%x\n", type);
	platform_set_drvdata(pdev, qg);
	return devm_device_add_group(&pdev->dev, &m3100_qg_group);
}

static const struct of_device_id m3100_qg_of_match[] = {
	{ .compatible = "inseego,m3100-pm7250b-qg-telemetry" },
	{ }
};
MODULE_DEVICE_TABLE(of, m3100_qg_of_match);
static struct platform_driver m3100_qg_driver = {
	.probe = m3100_qg_probe,
	.driver = {
		.name = "m3100-qg-telemetry",
		.of_match_table = m3100_qg_of_match,
	},
};
module_platform_driver(m3100_qg_driver);
MODULE_DESCRIPTION("M3100 PM7250B QG read-only last ADC voltage experiment");
MODULE_LICENSE("GPL");
