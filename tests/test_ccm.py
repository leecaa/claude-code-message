"""Unit tests. Everything runs against a throwaway HOME; no real sessions are touched."""
import importlib.machinery, importlib.util, io, json, os, socket, subprocess, sys, tempfile, threading, unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
CCM = os.path.join(HERE, "..", "bin", "ccm")


def load(home):
    os.environ.update(HOME=home, CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"),
                      XDG_STATE_HOME=os.path.join(home, "state"), XDG_CONFIG_HOME=os.path.join(home, "cfg"))
    for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET"):
        os.environ.pop(k, None)
    loader = importlib.machinery.SourceFileLoader("ccm", CCM)
    spec = importlib.util.spec_from_loader("ccm", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class FakeSession:
    """A registry record + key + inbox socket owned by this test process."""

    def __init__(self, ccm, name, sid, mirror_of=None):
        self.pid = os.getpid()
        d = tempfile.mkdtemp(dir="/tmp", prefix="ccmt")
        self.sock = os.path.join(d, "s.sock")
        self.got = []
        self.srv = socket.socket(socket.AF_UNIX); self.srv.bind(self.sock); self.srv.listen(8)
        threading.Thread(target=self.loop, daemon=True).start()
        os.makedirs(ccm.SESSIONS_DIR, exist_ok=True)
        json.dump({"peerToken": "cd" * 16}, open(ccm.key_path(self.pid, self.sock), "w"))
        rec = {"pid": self.pid, "sessionId": sid, "name": name, "cwd": "/w", "messagingSocketPath": self.sock, "status": "idle"}
        if mirror_of:
            rec.update(agent="claude-code-message", ccmHost=mirror_of, ccmTask="remote task")
        # one record per pid is a registry rule; tests use distinct fake pid files
        self.reg = os.path.join(ccm.SESSIONS_DIR, f"{self.pid}.json")
        json.dump(rec, open(self.reg, "w"))

    def loop(self):
        while True:
            c, _ = self.srv.accept(); buf = b""
            while True:
                x = c.recv(65536)
                if not x: break
                buf += x
            if buf.strip():
                self.got.append([json.loads(l) for l in buf.decode().strip().split("\n")])


class T(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.ccm = load(self.home)

    def test_rewrite_only_touches_header(self):
        body = 'quoted from="uds:/tmp/cc-socks/1.sock" from-name="x"'
        frame = {"from": "uds:/tmp/cc-socks/1.sock", "message": {"content":
                 f'<cross-session-message from="uds:/tmp/cc-socks/1.sock" from-name="web-ui" from-mode="bypass">\n{body}\n</cross-session-message>'}}
        out = self.ccm.rewrite_from(frame, "/run/user/1000/cc-socks/9.sock", "laptop-web-ui")
        self.assertEqual(out["from"], "uds:/run/user/1000/cc-socks/9.sock")
        c = out["message"]["content"]
        self.assertTrue(c.startswith('<cross-session-message from="uds:/run/user/1000/cc-socks/9.sock" from-name="laptop-web-ui" from-mode="bypass">'))
        self.assertIn(body, c)
        self.assertEqual(frame["from"], "uds:/tmp/cc-socks/1.sock")

    def test_task_from_prompt(self):
        f = self.ccm.task_from_prompt
        self.assertEqual(f("  修复登录超时\n细节..."), "修复登录超时")
        self.assertIsNone(f("/tm search x"))
        self.assertIsNone(f("<cross-session-message>hi"))
        self.assertIsNone(f("ok"))
        self.assertTrue(f("word " * 80).endswith("…"))
        self.assertIsNone(f("Another Claude session sent a message: hi"))
        self.assertIsNone(f("Base directory for this skill: /x"))

    def test_infer_task_uses_human_prompts_only(self):
        c = self.ccm
        proj = os.path.join(c.CLAUDE_DIR, "projects", "-w"); os.makedirs(proj)
        rows = [{"type": "user", "origin": {"kind": "human"}, "message": {"content": "真正的任务：修复登录"}},
                {"type": "user", "isMeta": True, "message": {"content": [{"type": "text", "text": "Skill body text here"}]}},
                {"type": "user", "origin": {"kind": "peer"}, "message": {"content": "peer text that is long"}}]
        open(os.path.join(proj, "sid.jsonl"), "w").write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        self.assertEqual(c.infer_task({"sessionId": "sid", "cwd": "/w"}), "真正的任务：修复登录")

    def test_mirror_name_keeps_unicode(self):
        n = self.ccm.Node(None, None, "laptop", "server")
        self.assertEqual(n.mirror_name({"pid": 1, "name": "移动端开发"}), "server-移动端开发")
        self.assertEqual(n.mirror_name({"pid": 1, "name": "a b/c"}), "server-a-b-c")

    def test_roster_lifecycle_and_audit(self):
        c = self.ccm
        c.roster_put("s1", name="a", task="t1")
        self.assertEqual(c.roster_get("s1")["task"], "t1")
        c.hook_session_end({"session_id": "s1", "reason": "exit"})
        self.assertIsNone(c.roster_get("s1"))
        evs = [json.loads(l)["ev"] for l in open(c.AUDITFILE)]
        self.assertEqual(evs, ["leave"])

    def test_sweep_keeps_fresh_drops_stale(self):
        c = self.ccm
        c.roster_put("fresh", name="f")
        c.roster_put("stale", name="s")
        p = c.roster_path("stale"); r = json.load(open(p)); r["updatedAt"] = 0; json.dump(r, open(p, "w"))
        c.roster_sweep(set())
        self.assertIsNotNone(c.roster_get("fresh"))
        self.assertIsNone(c.roster_get("stale"))

    def test_broadcast_delivers_and_audits(self):
        c = self.ccm
        target = FakeSession(c, "server-api", "remote-sid", mirror_of="server")
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit) as ex:
            c.cmd_broadcast(type("A", (), {"text": ["hello", "all"], "local": False, "node": None, "match": None})())
        self.assertEqual(ex.exception.code, 0)
        import time; time.sleep(0.5)
        auth, frame = target.got[0]
        self.assertEqual(auth, {"type": "auth", "token": "cd" * 16})
        self.assertIn("[broadcast to 1 members] hello all", frame["message"]["content"])
        self.assertIn('from-mode="default"', frame["message"]["content"])
        a = [json.loads(l) for l in open(c.AUDITFILE)][-1]
        self.assertEqual((a["ev"], a["to"], a["results"]), ("broadcast", ["server-api"], {"server-api": "ok"}))
        self.assertIn("hello all", a["preview"])
        rows = c.roster_rows()
        self.assertEqual((rows[0]["node"], rows[0]["task"]), ("server", "remote task"))

    def test_redacts_credentials(self):
        r = self.ccm.redact
        out = r("use ctx7sk-00000000-fake-0000-test-000000000000 and sk-FAKEtestKEY0000000000000000xx then password=hunter2 https://u:p@host/x")
        for leak in ("fake-0000-test", "FAKEtestKEY", "hunter2", "u:p@"):
            self.assertNotIn(leak, out)
        self.assertIn("password=[redacted]", out)
        self.assertEqual(r("修复登录超时 in 2 files"), "修复登录超时 in 2 files")
        self.assertNotIn("fake-0000-test", self.ccm.task_from_prompt("接手，context7 用ctx7sk-00000000-fake-0000-test-000000000000 先过渡"))
        # JSON keys, prefixed env vars, and Basic auth
        json_out = r('{"password": "hunter2secret"}')
        self.assertNotIn("hunter2secret", json_out)
        self.assertIn('"password": "[redacted]"', json_out)
        pg_out = r("PGPASSWORD=abc123xyz")
        self.assertNotIn("abc123xyz", pg_out)
        self.assertIn("PGPASSWORD=[redacted]", pg_out)
        basic_out = r("Authorization: Basic dXNlcjpwYXNzd29yZA==")
        self.assertNotIn("dXNlcjpwYXNzd29yZA==", basic_out)
        self.assertIn("Authorization: Basic [redacted]", basic_out)

    def test_hook_sent_redacts_summary(self):
        c = self.ccm
        c.hook_sent({
            "session_id": "s1",
            "tool_input": {"summary": "secret=mysecrettoken123", "message": "hello"},
            "tool_response": {"success": True}
        })
        a = [json.loads(l) for l in open(c.AUDITFILE)][-1]
        self.assertEqual(a["ev"], "native_send")
        self.assertNotIn("mysecrettoken123", a["summary"])
        self.assertIn("secret=[redacted]", a["summary"])

    def test_resolve(self):
        m = [{"name": "server-api"}, {"name": "server-docs"}, {"name": "web-ui"}]
        self.assertEqual(self.ccm.resolve("server-a", m)["name"], "server-api")
        with self.assertRaises(SystemExit):
            self.ccm.resolve("server-", m)

    def test_session_start_hook_output(self):
        c = self.ccm
        FakeSession(c, "server-uat", "r1", mirror_of="server")
        buf = io.StringIO()
        with redirect_stdout(buf):
            c.hook_session_start({"session_id": "me", "cwd": "/w", "source": "startup"})
        out = json.loads(buf.getvalue())["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertIn("server-uat [server, idle]: remote task", out["additionalContext"])
        self.assertIsNotNone(c.roster_get("me"))


if __name__ == "__main__":
    unittest.main()
