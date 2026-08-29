import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from equipment_capture_session import (
    CaptureSession,
    OfflineEquipmentRecognizer,
    SeparatedEquipmentScanner,
)


class CaptureSessionTests(unittest.TestCase):
    def _source(self, root: Path, name: str = "source.png") -> Path:
        path = root / name
        Image.new("RGB", (32, 32), "white").save(path)
        return path

    def test_capture_session_copies_stable_frame_and_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = CaptureSession.create(root / "sessions")
            source = self._source(root)
            captured = session.append_capture(
                {
                    "row": 2,
                    "column": 3,
                    "click": {"x": 10, "y": 20},
                    "final_path": source,
                    "stem": "item_r002_c03_test",
                    "fast_path": "single_post_click",
                }
            )
            self.assertTrue(Path(captured["final_path"]).is_file())
            self.assertNotEqual(Path(captured["final_path"]), source)
            session.mark_capture_complete()
            self.assertTrue(session.manifest["device_released"])
            self.assertEqual(session.manifest["status"], "captured")
            self.assertFalse(session.working_dir.exists())

            loaded = CaptureSession.load(session.path)
            self.assertEqual(loaded.session_id, session.session_id)
            self.assertEqual(len(loaded.items), 1)
            self.assertEqual(loaded.items[0]["row"], 2)
            self.assertEqual(loaded.items[0]["column"], 3)

    def test_offline_recognizer_reads_only_session_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = CaptureSession.create(root / "sessions")
            source = self._source(root)
            session.append_capture(
                {
                    "row": 1,
                    "column": 1,
                    "click": {"x": 10, "y": 20},
                    "final_path": source,
                    "stem": "item_one",
                }
            )
            session.mark_capture_complete()
            recognized_paths = []
            persisted = []

            class DummyWorkflow:
                def recognize_captured_item(self, captured):
                    recognized_paths.append(Path(captured["final_path"]))
                    return {
                        "row": captured["row"],
                        "column": captured["column"],
                        "screenshot": str(captured["final_path"]),
                        "fine_detail": {"item_id": captured["stem"]},
                    }

                def persist_record(self, record):
                    persisted.append(record["fine_detail"]["item_id"])

            progress = []
            recognizer = OfflineEquipmentRecognizer(
                DummyWorkflow(),
                session,
                progress_callback=progress.append,
            )
            records = recognizer.recognize()
            self.assertEqual(len(records), 1)
            self.assertEqual(persisted, ["item_one"])
            self.assertEqual(recognized_paths[0].parent, session.frames_dir)
            self.assertTrue(progress[-1]["device_released"])
            self.assertEqual(session.manifest["status"], "recognized")
            self.assertTrue(session.items[0]["recognized"])

    def test_separated_scanner_constructs_recognizer_after_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = CaptureSession.create(root / "sessions")
            source = self._source(root)
            events = []

            class DummyCaptureScanner:
                def __init__(self):
                    self.session = session
                    self.grid = SimpleNamespace(columns=8)
                    self.progress_callback = None

                def scan_until_bottom(self, **kwargs):
                    events.append("capture_start")
                    session.append_capture(
                        {
                            "row": 1,
                            "column": 1,
                            "click": {"x": 1, "y": 1},
                            "final_path": source,
                            "stem": "captured_first",
                        }
                    )
                    events.append("capture_end")
                    return [session.as_captured(session.items[0])]

            class DummyRecognizer:
                progress_callback = None

                def recognize(self):
                    events.append("recognize")
                    return [{"row": 1, "column": 1}]

            def factory(_session):
                events.append("factory")
                self.assertTrue(_session.manifest["device_released"])
                return DummyRecognizer()

            scanner = SeparatedEquipmentScanner(DummyCaptureScanner(), factory)
            updates = []
            scanner.progress_callback = updates.append
            records = scanner.scan_until_bottom()
            self.assertEqual(events, ["capture_start", "capture_end", "factory", "recognize"])
            self.assertEqual(records, [{"row": 1, "column": 1}])
            self.assertTrue(updates[0]["device_released"])
            self.assertEqual(updates[0]["phase"], "recognizing")


if __name__ == "__main__":
    unittest.main()
