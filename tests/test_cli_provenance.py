"""Local subprocess/socket integration tests; no Claude account or SSH needed."""
import json
import os
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_ccm import CCM, FakeSession, load


class CliProvenanceTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.ccm = load(home.name)
        self.sender = FakeSession(self.ccm, "test-sender", "sender-session")
        self.addCleanup(self.sender.close)
        self.mirror = subprocess.Popen([sys.executable, CCM, "mirror", "--name", "test-recipient",
                                      "--peer", "test-node", "--remote", '{"sessionId":"recipient-session"}'],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.stop_mirror)
        self.assertTrue(self.receive()["ready"])

    def stop_mirror(self):
        self.mirror.terminate()
        try:
            self.mirror.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.mirror.kill()
            self.mirror.communicate()

    def receive(self):
        ready, _, _ = select.select([self.mirror.stdout], [], [], 5)
        self.assertTrue(ready, "mirror did not respond within five seconds")
        return json.loads(self.mirror.stdout.readline())

    def cli(self, *args, payload=None):
        result = subprocess.run([sys.executable, CCM, *args], input=payload,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("failed", result.stderr)
        return result

    def attest(self, mode):
        self.cli("hook", "permission", payload=json.dumps({"session_id": "sender-session", "permission_mode": mode}))

    def test_first_send_and_broadcast_follow_runtime_mode_changes(self):
        for mode, wire_mode, command in (("bypassPermissions", "bypass", ("send", "test-recipient")),
                                         ("default", "prompting", ("broadcast",)),
                                         ("bypassPermissions", "bypass", ("send", "test-recipient"))):
            with self.subTest(mode=mode, command=command):
                self.attest(mode)
                self.cli(*command, "hello from the test")
                frame = self.receive()["frame"]
                self.assertIn(f'from-mode="{wire_mode}"', frame["message"]["content"])
                self.assertIn('from-session="sender-session"', frame["message"]["content"])
                self.assertIn('from-name="test-sender"', frame["message"]["content"])
                self.assertEqual(frame["from"], self.ccm.peer_address(self.sender.sock))

    def test_first_send_without_hook_does_not_invent_mode(self):
        self.cli("send", "test-recipient", "no hook metadata")
        frame = self.receive()["frame"]
        self.assertNotIn("from-mode=", frame["message"]["content"])
        self.assertEqual(frame["from"], self.ccm.peer_address(self.sender.sock))

    def test_mode_state_is_private_and_unrelated_session_cannot_attest(self):
        self.attest("bypassPermissions")
        path = self.ccm.roster_path("sender-session")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.cli("hook", "permission", payload=json.dumps({"session_id": "recipient-session",
                                                            "permission_mode": "bypassPermissions"}))
        self.assertIsNone(self.ccm.roster_get("recipient-session"))


if __name__ == "__main__":
    unittest.main()
