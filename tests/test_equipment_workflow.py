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

    def test_grid_calibration_has_eight_columns_and_four_rows(self):
        grid = GridConfig()
        self.assertEqual(len(grid.x_centers), 8)
        self.assertEqual(grid.x_centers[0], 453)
        self.assertEqual(grid.x_centers[-1], 1843)
        self.assertEqual(grid.y_centers, (363, 594, 825, 1056))
        self.assertEqual(grid.overlap_rows, 3)
        self.assertEqual(grid.swipe_end, (1200, 754))
        self.assertEqual(grid.swipe_velocity, 200)

    def test_equipment_count_parser_uses_m_from_m_over_n(self):
        from equipment_workflow import EquipmentScanner

        self.assertEqual(EquipmentScanner._parse_equipment_count("137/200"), 137)
        self.assertEqual(EquipmentScanner._parse_equipment_count("137 / 200"), 137)

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

    def test_grid_occupancy_keeps_red_tiles_when_previous_tiles_are_yellow(self):
        from equipment_workflow import EquipmentScanner

        image = Image.new("RGB", (2720, 1260), (25, 25, 30))
        scanner = EquipmentScanner.__new__(EquipmentScanner)
        scanner.grid = GridConfig()
        draw = ImageDraw.Draw(image)
        for x in scanner.grid.x_centers:
            draw.rectangle((x - 82, 260, x + 82, 465), fill=(220, 190, 40))
        for x in scanner.grid.x_centers[:4]:
            draw.rectangle((x - 82, 491, x + 82, 696), fill=(220, 190, 40))
        draw.rectangle((scanner.grid.x_centers[4] - 82, 491, scanner.grid.x_centers[4] + 82, 696), fill=(190, 35, 35))

        calibration = {}
        scanner._row_occupied_columns(image, scanner.grid.y_centers[0], calibration)

        self.assertEqual(
            scanner._row_occupied_columns(image, scanner.grid.y_centers[1], calibration),
            list(range(1, 6)),
        )

    def test_later_occupied_row_fills_previous_row_to_eight_columns(self):
        from equipment_workflow import EquipmentScanner

        scanner = EquipmentScanner.__new__(EquipmentScanner)
        scanner.grid = GridConfig()
        occupied = {0: [1, 2, 3, 4, 5, 6], 1: [1], 2: []}

        normalized = scanner._normalize_occupied_rows(occupied)

        self.assertEqual(normalized[0], list(range(1, 9)))
        self.assertEqual(normalized[1], [1])
        self.assertEqual(normalized[2], [])

    def test_occupied_row_with_dimmed_leading_tiles_is_filled_to_detected_prefix(self):
        from equipment_workflow import EquipmentScanner

        scanner = EquipmentScanner.__new__(EquipmentScanner)
        scanner.grid = GridConfig()

        normalized = scanner._normalize_occupied_rows({0: list(range(1, 9)), 1: [6], 2: []})

        self.assertEqual(normalized[1], list(range(1, 7)))

    def test_scan_end_position_is_converted_to_an_inclusive_item_count(self):
        from equipment_workflow import EquipmentScanner

        scanner = EquipmentScanner.__new__(EquipmentScanner)
        scanner.grid = GridConfig()
        scanner.workflow = object()

        with self.assertRaises(ValueError):
            scanner.scan_until_bottom(end_row=7, end_column=0)

    def test_selected_slot_is_detected_by_gold_highlight(self):
        from equipment_workflow import EquipmentScanner

        image = Image.new("RGB", (2720, 1260), (50, 50, 55))
        scanner = EquipmentScanner.__new__(EquipmentScanner)
        scanner.grid = GridConfig()
        from PIL import ImageDraw
        ImageDraw.Draw(image).rectangle((371, 260, 535, 465), fill=(130, 90, 90))
        ImageDraw.Draw(image).rectangle((371, 260, 535, 267), fill=(220, 180, 50))
        self.assertTrue(scanner._slot_selected(image, 453, 363))

    def test_grid_snapshot_excludes_inventory_background(self):
        from equipment_workflow import EquipmentScanner

        scanner = EquipmentScanner.__new__(EquipmentScanner)
        scanner.grid = GridConfig()
        first = Image.new("RGB", (2720, 1260), (50, 50, 55))
        second = Image.new("RGB", (2720, 1260), (90, 90, 95))
        for image in (first, second):
            draw = ImageDraw.Draw(image)
            for y in scanner.grid.y_centers:
                for x in scanner.grid.x_centers:
                    draw.rectangle(
                        (
                            x - scanner.grid.slot_width // 2,
                            y - scanner.grid.slot_height // 2,
                            x + scanner.grid.slot_width // 2,
                            y + scanner.grid.slot_height // 2,
                        ),
                        fill=(130, 90, 90),
                    )

        self.assertTrue(stable(scanner._grid_snapshot(first), scanner._grid_snapshot(second)))

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

    def test_fast_scan_uses_equipment_count_without_skipping_second_row(self):
        from equipment_fast_scan import FastEquipmentScanner

        image_path = Path(tempfile.mkdtemp()) / "grid.jpg"
        image = Image.new("RGB", (2720, 1260), (50, 50, 55))
        draw = ImageDraw.Draw(image)
        for x in GridConfig().x_centers:
            draw.rectangle((x - 82, 260, x + 82, 465), fill=(130, 90, 90))
            draw.rectangle((x - 82, 491, x + 82, 696), fill=(130, 90, 90))
        image.save(image_path)

        class Workflow:
            poll_interval = 0
            timeout = 0.1

            def capture(self):
                return image_path

            def capture_item(self, row, column, x, y, **kwargs):
                return {"row": row, "column": column, "final_path": image_path}

            def recognize_captured_item(self, captured):
                return {"row": captured["row"], "column": captured["column"]}

            def persist_record(self, record):
                pass

        scanner = FastEquipmentScanner(Workflow(), lambda *args: None)
        scanner.equipment_count = 14
        records, _ = scanner._process_rows(1, range(0, 2))

        self.assertEqual(
            [(record["row"], record["column"]) for record in records],
            [(1, column) for column in range(1, 9)]
            + [(2, column) for column in range(1, 7)],
        )

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
