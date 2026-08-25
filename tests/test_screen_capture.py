import subprocess
import unittest
from pathlib import Path

import screen_capture


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class ScreenCaptureTests(unittest.TestCase):
    def test_choose_target_requires_requested_target(self):
        original = screen_capture.list_targets
        screen_capture.list_targets = lambda _: ["phone-a", "phone-b"]
        try:
            self.assertEqual(screen_capture.choose_target("hdc", "phone-b"), "phone-b")
            with self.assertRaisesRegex(screen_capture.CaptureError, "not connected"):
                screen_capture.choose_target("hdc", "missing")
        finally:
            screen_capture.list_targets = original


    def test_capture_once_transfers_and_cleans_remote_file(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[4] == "snapshot_display":
                return completed("success")
            if command[3] == "file":
                Path(command[-1]).write_bytes(b"jpeg")
                return completed("FileTransfer finish")
            return completed("removed")

        original = screen_capture._run
        screen_capture._run = fake_run
        try:
            output_dir = Path(__import__("tempfile").mkdtemp())
            saved = screen_capture.capture_once("hdc", "phone", output_dir)
            self.assertEqual(saved.suffix, ".jpeg")
            self.assertEqual(saved.read_bytes(), b"jpeg")
            self.assertEqual(list(calls[0][0:4]), ["hdc", "-t", "phone", "shell"])
            self.assertEqual(list(calls[-1][3:5]), ["shell", "rm"])
        finally:
            screen_capture._run = original


    def test_main_captures_once_with_default_count(self):
        original = (screen_capture.find_hdc, screen_capture.choose_target, screen_capture.capture_loop)
        screen_capture.find_hdc = lambda _: "hdc"
        screen_capture.choose_target = lambda *_: "phone"
        screen_capture.capture_loop = lambda *args: [Path("screen.jpeg")]
        try:
            self.assertEqual(screen_capture.main(["--output-dir", "captures-test"]), 0)
        finally:
            screen_capture.find_hdc, screen_capture.choose_target, screen_capture.capture_loop = original


if __name__ == "__main__":
    unittest.main()
