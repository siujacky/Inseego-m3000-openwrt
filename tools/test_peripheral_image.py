import copy
import struct
import unittest
from check_ram_image import check_pcie_configuration


def cells(*values):
    return struct.pack('>' + 'I' * len(values), *values)


def fixture():
    mappings = (0, 0x200, 0x100, 0x201, 0x208, 0x202, 0x210, 0x203,
                0x218, 0x204, 0x300, 0x207, 0x400, 0x208, 0x401, 0x20a,
                0x500, 0x20c, 0x501, 0x20e)
    entries = [(mappings[i], 5, mappings[i + 1], 1)
               for i in range(0, len(mappings), 2)]
    iommu = cells(*(v for e in entries for v in e))
    root = {'compatible': b'qcom,pcie-sdx65\0', 'status': b'okay\0', 'num-lanes': cells(2),
            'perst-gpios': cells(7, 57, 1), 'iommu-map': iommu}
    draft = {'/soc/pcie@1c00000': root,
             '/soc/pinctrl': {'phandle': cells(7)},
             '/soc/iommu': {'phandle': cells(5), 'status': b'okay\0'}}
    stock = {'/soc/qcom,pcie@1c00000': {'perst-gpio': cells(9, 57, 0),
                                      'iommu-map': iommu}}
    return draft, stock


class PeripheralChecks(unittest.TestCase):
    def test_exact_stock_switch_sid_map_passes(self):
        check_pcie_configuration(*fixture())

    def test_endpoint_resources_are_not_mistaken_for_controllers(self):
        draft, stock = fixture()
        draft['/soc/pcie@1c00000/pcie-root@0,0/ethernet@0,1'] = {
            'reg': cells(0x100, 0, 0, 0, 0), 'phy-mode': b'rgmii\0'}
        check_pcie_configuration(draft, stock)

    def test_h5_reset_pin_or_polarity_is_rejected(self):
        draft, stock = fixture()
        draft['/soc/pcie@1c00000']['perst-gpios'] = cells(7, 102, 1)
        with self.assertRaisesRegex(ValueError, 'reset'):
            check_pcie_configuration(draft, stock)

    def test_missing_ethernet_sid_is_rejected(self):
        draft, stock = fixture()
        draft['/soc/pcie@1c00000']['iommu-map'] = draft['/soc/pcie@1c00000']['iommu-map'][:32]
        with self.assertRaisesRegex(ValueError, 'SID'):
            check_pcie_configuration(draft, stock)

    def test_single_lane_h5_link_is_rejected(self):
        draft, stock = fixture()
        draft['/soc/pcie@1c00000']['num-lanes'] = cells(1)
        with self.assertRaisesRegex(ValueError, 'two-lane'):
            check_pcie_configuration(draft, stock)

    def test_unexpected_second_controller_is_rejected(self):
        draft, stock = fixture()
        draft['/soc/pcie@dead000'] = copy.deepcopy(draft['/soc/pcie@1c00000'])
        with self.assertRaisesRegex(ValueError, 'controller'):
            check_pcie_configuration(draft, stock)

    def test_disabled_smmu_is_rejected(self):
        draft, stock = fixture()
        draft['/soc/iommu']['status'] = b'disabled\0'
        with self.assertRaisesRegex(ValueError, 'SMMU'):
            check_pcie_configuration(draft, stock)
