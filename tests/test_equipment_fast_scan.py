import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from equipment_fast_scan import FastEquipmentWorkflow, PaddleTextRecognitionV5Mobile
from equipment_workflow import OcrResult, Region


class DummyOcr:
    def recognize(self, image_path):
        return OcrResult("unused", 1.0)


class DummyFineRecognizer:
    def __init__(self, thread_ids):
        self.thread_ids = thread_ids

    def recognize(self, screenshot, item_id):
        self.thread_ids.append(threading.get_ident())
        return {
            "item_id": item_id,
            "profile": "general",
            "fully_unlocked": True,
        }


class FastEquipmentScanTests(unittest.TestCase):
    def test_text_recognition_result_parser_accepts_nested_result(self):
        data = PaddleTextRecognitionV5Mobile._result_data(
            {"res": {"rec_text": "攻击 +16%", "rec_score": 0.97}}
        )
        self.assertEqual(data["rec_text"], "攻击 +16%")
        self.assertEqual(data["rec_score"], 0.97)

    def test_selected_item_reuses_baseline_without_click_or_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = root / "before.jpg"
            Image.new("RGB", (40, 40), "black").save(before)
            captures = []
            clicks = []
            workflow = FastEquipmentWorkflow(
                capture=lambda: captures.append(True) or before,
                click=lambda x, y: clicks.append((x, y)),
                ocr=DummyOcr(),
                detail_region=Region(0, 0, 39, 39),
                output_dir=root / "out",
                poll_interval=0,
                settle_delay=0,
                recovery_delay=0,
                verify_baseline_ocr=False,
                enable_coarse_ocr=False,
            )
            baseline = Image.open(before).convert("RGB")
            captured = workflow.capture_item(
                1,
                1,
                20,
                20,
                allow_unchanged=True,
                before_path=before,
                before_image=baseline,
            )
            self.assertEqual(captured["final_path"], before)
            self.assertEqual(captured["fast_path"], "reuse_selected")
            self.assertEqual(captures, [])
            self.assertEqual(clicks, [])

    def test_changed_item_uses_reused_baseline_and_one_post_click_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = root / "before.jpg"
            after = root / "after.jpg"
            Image.new("RGB", (40, 40), "black").save(before)
            Image.new("RGB", (40, 40), "white").save(after)
            captures = []
            clicks = []

            def capture():
                captures.append(True)
                return after

            workflow = FastEquipmentWorkflow(
                capture=capture,
                click=lambda x, y: clicks.append((x, y)),
                ocr=DummyOcr(),
                detail_region=Region(0, 0, 39, 39),
                output_dir=root / "out",
                poll_interval=0,
                settle_delay=0,
                recovery_delay=0,
                verify_baseline_ocr=False,
                enable_coarse_ocr=False,
            )
            baseline = Image.open(before).convert("RGB")
            captured = workflow.capture_item(
                1,
                2,
                20,
                20,
                before_path=before,
                before_image=baseline,
            )
            self.assertEqual(captured["final_path"], after)
            self.assertEqual(captured["fast_path"], "single_post_click")
            self.assertEqual(len(captures), 1)
            self.assertEqual(clicks, [(20, 20)])

    def test_ocr_runs_in_worker_but_persistence_runs_on_caller_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            screenshot = root / "screen.jpg"
            output_dir = root / "out"
            output_dir.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (40, 40), "black").save(screenshot)
            main_thread_id = threading.get_ident()
            ocr_thread_ids = []
            persistence_thread_ids = []

            workflow = FastEquipmentWorkflow(
                capture=lambda: screenshot,
                click=lambda *_: None,
                ocr=DummyOcr(),
                detail_region=Region(0, 0, 39, 39),
                output_dir=output_dir,
                fine_recognizer=DummyFineRecognizer(ocr_thread_ids),
                persistence=lambda record, path: persistence_thread_ids.append(
                    threading.get_ident()
                ),
                poll_interval=0,
                settle_delay=0,
                recovery_delay=0,
                verify_baseline_ocr=False,
                enable_coarse_ocr=False,
            )
            captured = {
                "row": 1,
                "column": 1,
                "click": {"x": 20, "y": 20},
                "final_path": screenshot,
                "baseline_result": None,
                "stem": "thread_boundary",
            }

            with ThreadPoolExecutor(max_workers=1) as executor:
                record = executor.submit(
                    workflow.recognize_captured_item, captured
                ).result()

            self.assertEqual(persistence_thread_ids, [])
            self.assertEqual(len(ocr_thread_ids), 1)
            self.assertNotEqual(ocr_thread_ids[0], main_thread_id)

            workflow.persist_record(record)
            self.assertEqual(persistence_thread_ids, [main_thread_id])


if __name__ == "__main__":
    unittest.main()
