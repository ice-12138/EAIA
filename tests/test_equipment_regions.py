import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from equipment_regions import (
    FIELD_NAMES,
    SHARED_FIELDS,
    FineEquipmentRecognizer,
    extract_numeric_value,
    find_red_bbox,
    load_fine_regions,
)
from equipment_workflow import OcrResult


class FakeOcr:
    def recognize(self, path):
        return OcrResult("专属" if "probe" in path.name else "攻击 +16")


def write_annotation(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (200, 300), "black")
    ImageDraw.Draw(image).rectangle((20, 30, 160, 80), fill="red")
    image.save(path)


class EquipmentRegionTests(unittest.TestCase):
    def test_find_red_bbox(self):
        image = Image.new("RGB", (100, 100), "black")
        ImageDraw.Draw(image).rectangle((10, 20, 80, 40), fill="red")
        self.assertEqual(find_red_bbox(image), (10, 20, 80, 40))

    def test_numeric_value_and_unlock_marker(self):
        self.assertEqual(extract_numeric_value("攻击加成 +16%"), 16)
        self.assertEqual(extract_numeric_value("暴击伤害 +16解锁"), -1)
        self.assertIsNone(extract_numeric_value("没有数值"))

    def test_load_regions_and_recognize_four_subattribute_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "annotations"
            for field_name in FIELD_NAMES:
                write_annotation(root / "exclusive" / f"{field_name}.png")
            for field_name in set(FIELD_NAMES) - {"专属标识"} - SHARED_FIELDS:
                write_annotation(root / "general" / f"{field_name}.png")

            regions = load_fine_regions(root)
            self.assertIn("专属标识", regions["exclusive"])
            self.assertIn("主词条与数值", regions["general"])

            temp = Path(directory) / "runtime"
            temp.mkdir()
            screenshot = temp / "screen.jpeg"
            Image.new("RGB", (2720, 1260), "black").save(screenshot)
            result = FineEquipmentRecognizer(FakeOcr(), regions, temp / "out").recognize(screenshot, "r1c1")
            self.assertEqual(result["profile"], "exclusive")
            self.assertEqual(result["enhancement_level"]["value"], 16)
            self.assertEqual(len(result["sub_attributes"]), 4)


if __name__ == "__main__":
    unittest.main()
