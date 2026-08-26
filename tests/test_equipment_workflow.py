import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from equipment_workflow import EquipmentWorkflow, GridConfig, OcrResult, PaddleOcrV5Mobile, Region, WorkflowError, changed, stable


class FakeOcr:
    def recognize(self, image_path):
        self.path = image_path
        if not hasattr(self, "calls"):
            self.calls = 0
        self.calls += 1
        text = "旧装备" if self.calls == 1 else "新装备"
        return OcrResult(text, 0.98)


class EquipmentWorkflowTests(unittest.TestCase):
    def test_paddle_result_parser_preserves_all_raw_lines(self):
        result = PaddleOcrV5Mobile._result_data({"rec_texts": ["名称", "攻击 +60"]})
        self.assertEqual(result["rec_texts"], ["名称", "攻击 +60"])

    def test_grid_calibration_has_eight_columns_and_three_rows(self):
        grid = GridConfig()
        self.assertEqual(len(grid.x_centers), 8)
        self.assertEqual(grid.x_centers[0], 453)
        self.assertEqual(grid.x_centers[-1], 1843)
        self.assertEqual(grid.y_centers, (363, 594, 825))

    def test_grid_occupancy_uses_brightness_difference_from_current_image(self):
        from equipment_workflow import EquipmentScanner

        image = Image.new("RGB", (2720, 1260), (50, 50, 55))
        scanner = EquipmentScanner.__new__(EquipmentScanner)
        scanner.grid = GridConfig()
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        for x in scanner.grid.x_centers:
            draw.rectangle((x - 82, 260, x + 82, 465), fill=(130, 90, 90))
        calibration = {}
        self.assertEqual(scanner._row_occupied_columns(image, 363, calibration), list(range(1, 9)))
        image2 = Image.new("RGB", (2720, 1260), (50, 50, 55))
        ImageDraw.Draw(image2).rectangle((371, 491, 535, 696), fill=(130, 90, 90))
        self.assertEqual(scanner._row_occupied_columns(image2, 594, calibration), [1])

    def test_grid_occupancy_detects_seven_items_and_empty_following_rows(self):
        from equipment_workflow import EquipmentScanner

        image = Image.new("RGB", (2720, 1260), (50, 50, 55))
        scanner = EquipmentScanner.__new__(EquipmentScanner)
        scanner.grid = GridConfig()
        draw = ImageDraw.Draw(image)
        for x in scanner.grid.x_centers[:7]:
            draw.rectangle((x - 82, 260, x + 82, 465), fill=(130, 90, 90))
        calibration = {}

        self.assertEqual(
            scanner._row_occupied_columns(image, 363, calibration),
            list(range(1, 8)),
        )
        self.assertEqual(scanner._row_occupied_columns(image, 594, calibration), [])
        self.assertEqual(scanner._row_occupied_columns(image, 825, calibration), [])

    def test_selected_slot_is_detected_by_gold_highlight(self):
        from equipment_workflow import EquipmentScanner

        image = Image.new("RGB", (2720, 1260), (50, 50, 55))
        scanner = EquipmentScanner.__new__(EquipmentScanner)
        scanner.grid = GridConfig()
        from PIL import ImageDraw
        ImageDraw.Draw(image).rectangle((371, 260, 535, 465), fill=(130, 90, 90))
        ImageDraw.Draw(image).rectangle((371, 260, 535, 267), fill=(220, 180, 50))
        self.assertTrue(scanner._slot_selected(image, 453, 363))

    def test_scan_stops_without_swiping_when_visible_rows_end(self):
        from equipment_workflow import EquipmentScanner

        temp = Path(tempfile.mkdtemp())
        image_path = temp / "two_rows.jpg"
        image = Image.new("RGB", (2720, 1260), (50, 50, 55))
        draw = ImageDraw.Draw(image)
        for x in GridConfig().x_centers:
            draw.rectangle((x - 82, 260, x + 82, 465), fill=(130, 90, 90))
        for x in (453, 652, 850, 1049):
            draw.rectangle((x - 82, 491, x + 82, 696), fill=(130, 90, 90))
        image.save(image_path)

        class Workflow:
            poll_interval = 0
            timeout = 0.1

            def capture(self):
                return image_path

            def process_item(self, row, column, x, y, allow_unchanged=False):
                return {"row": row, "column": column}

        swipes = []
        scanner = EquipmentScanner(Workflow(), lambda *args: swipes.append(args))
        records = scanner.scan_until_bottom()

        self.assertEqual(len(records), 12)
        self.assertEqual(swipes, [])

    def test_detail_change_and_stability(self):
        first = Image.new("RGB", (100, 100), "black")
        second = Image.new("RGB", (100, 100), "white")
        self.assertTrue(changed(first, second))
        self.assertTrue(stable(first, first.copy()))

    def test_process_item_waits_for_change_and_stability_then_ocr(self):
        temp = Path(tempfile.mkdtemp())
        before = temp / "before.jpg"
        moving = temp / "moving.jpg"
        final = temp / "final.jpg"
        for path, color in ((before, "black"), (moving, "gray"), (final, "white")):
            image = Image.new("RGB", (200, 200), color)
            ImageDraw.Draw(image).rectangle((20, 20, 180, 180), outline="white", width=2)
            image.save(path)
        paths = iter((before, moving, final, final, final))
        clicks = []
        ocr = FakeOcr()
        workflow = EquipmentWorkflow(
            capture=lambda: next(paths),
            click=lambda x, y: clicks.append((x, y)),
            ocr=ocr,
            detail_region=Region(0, 0, 199, 199),
            output_dir=temp / "out",
            poll_interval=0,
        )
        result = workflow.process_item(1, 1, 100, 100)
        self.assertEqual(clicks, [(100, 100)])
        self.assertEqual(result["ocr_text"], "新装备")
        self.assertTrue(Path(result["ocr_crop"]).is_file())
        self.assertTrue((temp / "out" / "ocr_results.jsonl").is_file())

    def test_process_item_fails_when_panel_never_changes(self):
        temp = Path(tempfile.mkdtemp())
        image_path = temp / "same.jpg"
        Image.new("RGB", (20, 20), "black").save(image_path)
        workflow = EquipmentWorkflow(
            capture=lambda: image_path,
            click=lambda *_: None,
            ocr=FakeOcr(),
            detail_region=Region(0, 0, 19, 19),
            output_dir=temp / "out",
            poll_interval=0,
            timeout=0.01,
        )
        with self.assertRaises(WorkflowError):
            workflow.process_item(1, 1, 10, 10)


if __name__ == "__main__":
    unittest.main()
