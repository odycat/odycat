# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""Device profiles: a slave entry naming a class with "device" is filled in
from the class PROFILE before the engine sees it (odycat._profiles).

The profiles of the bundled classes must give the same shm layout as the
bench configs that were run on the hardware, and the layout each class
expects (its fixed struct formats).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import load_config  # noqa: E402

from odycat import _profiles  # noqa: E402
from odycat._config import (slave_entry_layout,  # noqa: E402
                            slave_pdo_sizes)



def layout(slave):
    return [[(e["index"], e["subindex"], e["bits"], e["bit_offset"], e["label"])
             for e in d] for d in slave_entry_layout(slave)]


class ExpandTests(unittest.TestCase):
    def test_entry_without_device_is_untouched(self):
        sl = {"label": "x", "position": 0, "vendor_id": "0x1"}
        self.assertIs(_profiles.expand_slave(sl), sl)

    def test_profile_fills_identity_pdos_and_name(self):
        sl = _profiles.expand_slave({"device": "leadshine.ECT60", "alias": 60, "label": "ax"})
        self.assertEqual((sl["vendor_id"], sl["product_code"]),
                         ("0x00000A88", "0x0A880002"))
        self.assertEqual((sl["name"], sl["alias"], sl["label"]), ("ECT60", 60, "ax"))
        self.assertNotIn("dc_sync", sl)                 # free-run unless the entry asks
        self.assertEqual(sl["device"], "leadshine.ECT60")

    def test_names_ignore_case_and_separators(self):
        for name in ("amsamotion.EC3A_IO1632", "AmsaMotion.EC3A-IO1632", "amsamotion.ec3a-io1632"):
            self.assertEqual(_profiles.find_device(name).__name__, "EC3A_IO1632")

    def test_unknown_device_lists_the_known_ones(self):
        with self.assertRaises(KeyError) as cm:
            _profiles.find_device("nobody.XYZ")
        self.assertIn("leadshine.ECT60", str(cm.exception))

    def test_names_are_qualified_by_vendor(self):
        with self.assertRaises(KeyError) as cm:
            _profiles.find_device("ECT60")
        self.assertIn("leadshine.ECT60", str(cm.exception))      # suggested
        with self.assertRaises(KeyError):
            _profiles.expand_slave({"device": "ECT60", "label": "a"})

    def test_entry_keys_override_the_profile(self):
        sl = _profiles.expand_slave({"device": "leadshine.ECT60", "label": "a", "name": "X axis"})
        self.assertEqual(sl["name"], "X axis")

    def test_sdos_are_appended_and_dc_sync_merged(self):
        sdo = {"index": "0x6083", "subindex": 0, "type": "u32", "value": 1000}
        sl = _profiles.expand_slave({"device": "leadshine.ECT60", "label": "a", "sdos": [sdo],
                                     "dc_sync": {"sync0_shift_ns": 5000}})
        self.assertEqual(sl["sdos"], [sdo])
        self.assertEqual(sl["dc_sync"], {"assign_activate": "0x0300",
                                         "sync0_shift_ns": 5000})

    def test_dc_sync_number_is_the_sync0_period(self):
        sl = _profiles.expand_slave({"device": "leadshine.ECT60", "label": "a", "dc_sync": 10000000})
        self.assertEqual(sl["dc_sync"], {"assign_activate": "0x0300",
                                         "sync0_cycle_ns": 10000000})
        sl = _profiles.expand_slave({"device": "leadshine.ECT60", "label": "a", "dc_sync": 0})
        self.assertEqual(sl["dc_sync"]["sync0_cycle_ns"], 0)          # 0 = bus cycle
        sl = _profiles.expand_slave({"device": "amsamotion.EC3A_IO1632", "label": "a", "dc_sync": 0})
        self.assertEqual(sl["dc_sync"], {"sync0_cycle_ns": 0})         # no class template
        for bad in (-1, 1.5, "10ms", True):
            with self.assertRaises(ValueError):
                _profiles.expand_slave({"device": "leadshine.ECT60", "label": "a", "dc_sync": bad})

    def test_null_removes_a_profile_key(self):
        sl = _profiles.expand_slave({"device": "leadshine.ECT60", "label": "a", "dc_sync": None})
        self.assertNotIn("dc_sync", sl)

    def test_profile_is_not_shared_between_entries(self):
        a = _profiles.expand_slave({"device": "leadshine.ECT60", "label": "a"})
        a["rxpdo"]["entries"].clear()
        b = _profiles.expand_slave({"device": "leadshine.ECT60", "label": "b"})
        self.assertTrue(b["rxpdo"]["entries"])


class PdoRangeTests(unittest.TestCase):
    def cfg(self, key, index):
        other = "txpdo" if key == "rxpdo" else "rxpdo"
        return {"slaves": [{"label": "s", key: {"index": index, "entries": [
            {"index": "0x7000", "subindex": 1, "type": "u16"}]},
            other: {"entries": []}}]}

    def test_rxpdo_must_be_an_rxpdo(self):
        _profiles.expand_config(self.cfg("rxpdo", "0x1600"))
        with self.assertRaisesRegex(ValueError, "rxpdo index 0x1A00"):
            _profiles.expand_config(self.cfg("rxpdo", "0x1A00"))

    def test_txpdo_must_be_a_txpdo(self):
        _profiles.expand_config(self.cfg("txpdo", "0x1A03"))
        with self.assertRaisesRegex(ValueError, "txpdo index 0x1600"):
            _profiles.expand_config(self.cfg("txpdo", "0x1600"))

    def test_repo_configs_are_in_range(self):
        for rel in ("examples/config.json", "examples/demo_box.json"):
            _profiles.expand_config(load_config(rel))


class BundledProfileTests(unittest.TestCase):
    def test_every_bundled_class_with_a_fixed_layout_has_a_profile(self):
        self.assertEqual(set(_profiles.device_classes()),
                         {"leadshine.ECT60", "beckhoff.EL2809",
                          "beckhoff.EL1809", "beckhoff.EL1014", "beckhoff.EL2004",
                          "beckhoff.ES1014", "beckhoff.ES2004",
                          "amsamotion.EC3A_IO1632", "delta.AX516AM", "delta.AX516AN"})

    def test_fixed_format_classes_fit_their_profile(self):
        fixed = {  # class: (input bytes, output bytes) the class unpacks
            "amsamotion.EC3A_IO1632": (2, 2),
            "beckhoff.EL2809": (0, 2), "beckhoff.EL1809": (2, 0),
            "beckhoff.EL2004": (0, 1), "beckhoff.EL1014": (1, 0),
            "beckhoff.ES2004": (0, 1), "beckhoff.ES1014": (1, 0),
            "delta.AX516AM": (2, 0), "delta.AX516AN": (0, 2),
        }
        for name, sizes in fixed.items():
            with self.subTest(device=name):
                sl = _profiles.expand_slave({"device": name, "label": "x"})
                self.assertEqual(slave_pdo_sizes(sl), sizes)



def _exported_classes() -> dict:
    """{"vendor.Class": class} of every Device subclass a vendor package
    exports, with or without a PROFILE (generic classes included)."""
    import importlib
    import odycat.devices as catalogue
    from odycat.device import Device
    out = {}
    for vendor, module in catalogue.vendors().items():
        for name in importlib.import_module(module).__all__:
            obj = getattr(importlib.import_module(module), name)
            if isinstance(obj, type) and issubclass(obj, Device):
                out[f"{vendor}.{name}"] = obj
    return out


class CatalogueTests(unittest.TestCase):
    """odycat.devices finds the vendors among its own subpackages only."""

    def test_bundled_vendors(self):
        import odycat.devices as catalogue
        self.assertTrue({"beckhoff", "cia402", "delta", "leadshine",
                         "amsamotion"} <= set(catalogue.vendors()))
        self.assertTrue(all(m.startswith("odycat.devices.")
                            for m in catalogue.vendors().values()))

    def test_no_entry_points(self):
        import odycat.devices as catalogue
        self.assertFalse(hasattr(catalogue, "ENTRY_POINT_GROUP"))


class SupportTierTests(unittest.TestCase):
    """Every device class states its tier; a tested one says what it was
    tested with (#38)."""

    def test_every_class_has_a_tier(self):
        from odycat.devices import TIERS, support
        classes = _exported_classes()
        self.assertIn("cia402.Servo", classes)
        for name, cls in classes.items():
            with self.subTest(device=name):
                self.assertIn(support(cls), TIERS)

    def test_tested_classes_say_what_with(self):
        from odycat.devices import device_classes, support
        for name, cls in _exported_classes().items():
            if support(cls) != "tested":
                continue
            with self.subTest(device=name):
                if "PROFILE" in vars(cls):
                    revisions = vars(cls).get("TESTED_REVISIONS")
                    self.assertTrue(revisions)
                    self.assertTrue(all(isinstance(r, int) for r in revisions))
                else:
                    on = vars(cls).get("TESTED_ON")
                    self.assertTrue(on)
                    for dev in on:      # tested on devices whose classes are tested
                        self.assertEqual(support(device_classes()[dev]), "tested")

    def test_tier_is_not_inherited(self):
        from odycat.devices import support, support_text
        from odycat.devices.leadshine import ECT60

        class MyDrive(ECT60):
            pass
        self.assertIsNone(support(MyDrive))
        self.assertEqual(support_text(MyDrive), "none")
        self.assertEqual(support_text(ECT60), "tested (revision 0x00000202)")

    def test_devices_page_shows_each_tier(self):
        """The class table at the top of docs/devices.md has a Support column
        that matches the classes."""
        from odycat.devices import support
        page = (Path(__file__).resolve().parents[1] / "docs" / "devices.md").read_text()
        rows = {}
        for line in page.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if line.startswith("| `") and len(cells) >= 6:
                rows[f"{cells[1].strip('`')}.{cells[0].strip('`')}"] = cells[5]
        classes = _exported_classes()
        self.assertEqual(set(rows), set(classes))
        for name, tier in rows.items():
            with self.subTest(device=name):
                self.assertEqual(tier, support(classes[name]))


if __name__ == "__main__":
    unittest.main()
