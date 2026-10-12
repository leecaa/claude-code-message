"""Tests for CCM Agent Bridge (ccm agent serve, wait, status, stop)."""
import argparse
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_ccm import load, FakeSession


class AgentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = load(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_agent_serve_registers_session_and_key(self):
        c = self.c
        name = "test-antigravity"
        a = argparse.Namespace(
            action="serve", name=name, kind="antigravity",
            task="Audit Google Play", fifo=None, inbox=None, exec=None,
            daemon=False, once=True, timeout=0
        )

        server_thread = threading.Thread(target=c.cmd_agent, args=(a,), daemon=True)
        server_thread.start()

        # Wait for registration
        pid = os.getpid()
        reg_file = os.path.join(c.SESSIONS_DIR, f"{pid}.json")
        for _ in range(30):
            if os.path.exists(reg_file):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(reg_file), "Session registration must exist")

        reg = c.read_json(reg_file)
        self.assertEqual(reg.get("name"), name)
        self.assertEqual(reg.get("agent"), "ccm-agent-antigravity")
        self.assertEqual(reg.get("ccmTask"), "Audit Google Play")

        # Verify roster
        rows = c.roster_rows()
        agent_row = next((r for r in rows if r.get("name") == name), None)
        self.assertIsNotNone(agent_row, "Agent must appear in roster")
        self.assertEqual(agent_row.get("task"), "Audit Google Play")

    def test_agent_receives_message_via_fifo(self):
        c = self.c
        name = "test-codex"
        fifo_path = os.path.join(self.temp.name, "inbox.fifo")

        a = argparse.Namespace(
            action="serve", name=name, kind="codex",
            task="Background testing", fifo=fifo_path, inbox=None, exec=None,
            daemon=False, once=False, timeout=5
        )

        server_thread = threading.Thread(target=c.cmd_agent, args=(a,), daemon=True)
        server_thread.start()

        pid = os.getpid()
        reg_file = os.path.join(c.SESSIONS_DIR, f"{pid}.json")
        for _ in range(30):
            if os.path.exists(reg_file):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(reg_file))

        reg = c.read_json(reg_file)
        sock_path = reg["messagingSocketPath"]
        kpath = c.key_path(pid, sock_path)
        token = c.read_json(kpath)["peerToken"]

        # Send test frame via socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
        s.sendall(json.dumps({"type": "auth", "token": token}).encode() + b"\n")
        msg_frame = {
            "msgV": 1,
            "msg_id": "test-msg-1234",
            "type": "user",
            "priority": "next",
            "from": "uds:/tmp/sender.sock",
            "message": {
                "role": "user",
                "content": '<cross-session-message from="uds:/tmp/sender.sock" from-name="claude-main" from-mode="bypass">\nHello from Claude!\n</cross-session-message>'
            }
        }
        s.sendall(json.dumps(msg_frame).encode() + b"\n")
        s.close()

        # Wait for fifo delivery
        for _ in range(30):
            if os.path.exists(fifo_path) and os.path.getsize(fifo_path) > 0:
                break
            time.sleep(0.05)

        self.assertTrue(os.path.exists(fifo_path))
        with open(fifo_path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["agent"], name)
        self.assertEqual(lines[0]["from_name"], "claude-main")
        self.assertIn("Hello from Claude!", lines[0]["text"])

    def test_agent_receives_message_via_inbox(self):
        c = self.c
        name = "test-inbox-agent"
        inbox_dir = os.path.join(self.temp.name, "inbox_dir")

        a = argparse.Namespace(
            action="serve", name=name, kind="generic",
            task="Inbox testing", fifo=None, inbox=inbox_dir, exec=None,
            daemon=False, once=False, timeout=5
        )

        server_thread = threading.Thread(target=c.cmd_agent, args=(a,), daemon=True)
        server_thread.start()

        pid = os.getpid()
        reg_file = os.path.join(c.SESSIONS_DIR, f"{pid}.json")
        for _ in range(30):
            if os.path.exists(reg_file):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(reg_file))

        reg = c.read_json(reg_file)
        sock_path = reg["messagingSocketPath"]
        token = c.read_json(c.key_path(pid, sock_path))["peerToken"]

        # Send test frame
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
        s.sendall(json.dumps({"type": "auth", "token": token}).encode() + b"\n")
        msg_frame = {
            "msgV": 1,
            "msg_id": "custom-uuid-5678",
            "type": "user",
            "priority": "next",
            "from": "uds:/tmp/sender2.sock",
            "message": {
                "role": "user",
                "content": '<cross-session-message from-name="reviewer">\nDeliverable ready\n</cross-session-message>'
            }
        }
        s.sendall(json.dumps(msg_frame).encode() + b"\n")
        s.close()

        # Check inbox dir
        target_file = os.path.join(inbox_dir, "custom-uuid-5678.json")
        for _ in range(30):
            if os.path.exists(target_file):
                break
            time.sleep(0.05)

        self.assertTrue(os.path.exists(target_file))
        saved = c.read_json(target_file)
        self.assertEqual(saved["msg_id"], "custom-uuid-5678")
        self.assertEqual(saved["from_name"], "reviewer")
        self.assertIn("Deliverable ready", saved["text"])


if __name__ == "__main__":
    unittest.main()
