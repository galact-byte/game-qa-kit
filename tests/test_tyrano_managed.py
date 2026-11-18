import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image

from gameqakit import admission
from gameqakit.sessions import read_json
from gameqakit.win32 import _pid_path
from tests.native_input_mcp import mcp, until
from tests.test_tyrano_bridge import BASE, COMPONENTS


@unittest.skipUnless(os.name == "nt" and COMPONENTS, "Requires registered Tyrano components")
class TyranoManagedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gameqa-tyrano-managed-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "sample"
        self.source.mkdir()
        self.script = self.source / "gameqa-tyrano-fixture.ks"
        shutil.copyfile(BASE / "plugins/tyrano/samples" / self.script.name, self.script)
        self.destination = self.root / "session"
        self.bridge = self.destination / "bridge"
        if evidence := os.environ.get("GAMEQAKIT_TEST_EVIDENCE"):
            def ignore(directory, names):
                return [name for name in ("runtime", "data") if name in names] if Path(directory) == self.destination else []
            self.addCleanup(shutil.copytree, self.root, Path(evidence) / self._testMethodName,
                            dirs_exist_ok=True, ignore=ignore)

    def test_registered_sample_can_be_prepared_but_changed_sample_is_rejected(self):
        plan = admission.make_plan(self.source, self.destination, artifacts=COMPONENTS)
        self.assertEqual(plan["backend"], "tyrano_authored")
        self.assertEqual(admission.revalidate_plan(plan), plan)
        self.script.write_bytes(self.script.read_bytes() + b"\n; changed\n")
        self.assertEqual(admission.inspect(self.source, artifacts=COMPONENTS)["launch_readiness"], "blocked")
        self.assertFalse(self.destination.exists())

    def connect(self):
        return mcp(self.root, Path(COMPONENTS).resolve() / "engine-runtime.exe")

    def rpc(self, client, name, *, expect_error=False, **arguments):
        client.send("tools/call", {"name": name, "arguments": arguments})
        response = client.responses.get(timeout=10)
        client.records[-1]["response"] = response
        self.assertEqual(response["id"], client.number)
        if expect_error:
            self.assertIn("error", response)
            return response["error"]
        self.assertNotIn("error", response)
        return json.loads(response["result"]["content"][0]["text"])

    def wait_state(self, client, expected):
        def poll():
            state = self.rpc(client, "game_status", directory=str(self.destination))
            terminal = state["state"] in {"failed", "cleanup_failed", "stopped"}
            if terminal:
                self.assertEqual(state["state"], expected, state)
            return state if state["state"] == expected and (not terminal or not state["worker_alive"]) else None
        return until(poll, seconds=30)

    def prepare(self, client):
        result = self.rpc(client, "game_inspect", path=str(self.source), artifacts=COMPONENTS, destination=str(self.destination))
        self.assertEqual(result["launch_readiness"], "ready", result)
        plan = result["plan"]
        self.rpc(client, "game_prepare", plan=plan, request_id="prepare", confirm=True)
        self.wait_state(client, "prepared")
        return plan

    def start(self, client, plan):
        self.rpc(client, "game_start", directory=str(self.destination), fingerprint=plan["fingerprint"],
                    request_id="start", confirm=True)

    def ready(self, client):
        self.start(client, self.prepare(client))
        state = self.wait_state(client, "ready_paused")
        self.assertEqual((state["paused"], state["held"]), (True, []))
        self.assertFalse((self.bridge / "request.json").exists())
        self.assertEqual(admission.file_digest(state["screenshot"]), state["screenshot_sha256"])
        self.rpc(client, "game_bind", directory=str(self.bridge), session_id=state["session_id"], confirm=True)
        self.rpc(client, "control_resume", directory=str(self.bridge), command_id="resume", confirm=True)
        return state

    def test_changed_prepared_entry_is_rejected_before_launch(self):
        with self.connect() as client:
            plan = self.prepare(client)
            (self.destination / "runtime/resources/app/main.js").write_text("// changed", encoding="utf-8")
            self.start(client, plan)
            failed = self.wait_state(client, "failed")
            self.assertIn("runtime_changed", failed["error"])
            self.assertFalse(any(self.bridge.iterdir()))

    def test_confirm_advances_pixels_wait_does_not_and_stop_releases(self):
        with self.connect() as client:
            ready = self.ready(client)
            result = self.rpc(client, "control_action", directory=str(self.bridge), action="confirm", duration_ms=70,
                                 command_id="enter", confirm=True)
            self.assertTrue(result["press_consumed"] and result["release_consumed"])
            def color_left(path, color):
                with Image.open(path) as image:
                    return min(i % image.width for i, pixel in enumerate(image.convert("RGB").getdata())
                               if pixel == color)

            self.assertGreater(color_left(result["screenshot"], (64, 192, 128)),
                               color_left(ready["screenshot"], (255, 128, 32)))
            waited = self.rpc(client, "control_action", directory=str(self.bridge), action="wait", duration_ms=50,
                                 command_id="wait", confirm=True)
            self.assertFalse(waited["press_consumed"] or waited["release_consumed"])
            self.assertEqual(waited["screenshot_sha256"], result["screenshot_sha256"])
            self.rpc(client, "game_stop", directory=str(self.destination), request_id="stop", confirm=True)
            stopped = self.wait_state(client, "stopped")
            self.assertTrue(stopped["release_confirmed"] and stopped["target_exited"])
            self.assertTrue(stopped["source_unchanged"] and stopped["runtime_source_unchanged"])
            self.assertEqual(stopped["termination_reason"], "stop_requested")
        self.assertFalse(any(_pid_path(pid) for pid in (ready["pid"], ready["worker_pid"], client.child.pid)))

    def test_black_frame_preserves_release_receipt_and_reclaims_process(self):
        with self.connect() as client:
            ready = self.ready(client)
            (self.destination / "blank-after-confirm").touch()
            error = self.rpc(client, "control_action", directory=str(self.bridge), action="confirm", duration_ms=70,
                                command_id="black", confirm=True, expect_error=True)
            self.assertIn("blank", error["message"])
            receipt = read_json(self.bridge / "results/black.json")
            self.assertEqual((receipt["ok"], receipt["press_consumed"], receipt["release_consumed"]), (False, True, True))
            self.assertFalse((self.bridge / "screenshots/black.png").exists())
            failed = self.wait_state(client, "failed")
            self.assertIn("observation_failed", failed["error"])
            self.assertTrue(failed["target_exited"] and failed["release_confirmed"])
            self.rpc(client, "control_resume", directory=str(self.bridge), command_id="blocked", confirm=True, expect_error=True)
            self.assertEqual(read_json(self.bridge / "request.json")["id"], "black")
        self.assertFalse(any(_pid_path(pid) for pid in (ready["pid"], ready["worker_pid"], client.child.pid)))
