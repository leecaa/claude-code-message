"""Durable transport regressions, using only isolated state and synthetic peers."""
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import closing, contextmanager

from test_ccm import load


class BrokenLink:
    def write(self, data):
        raise BrokenPipeError("synthetic link disconnected")

    def flush(self):
        pass


class RootCauseRegressions(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.ccm = load(home.name)
        self.frame = {"msgV": 1, "msg_id": "20000000-0000-4000-8000-000000000001",
                      "type": "user", "priority": "next",
                      "message": {"role": "user", "content": "synthetic message"}}

    def test_link_drop_keeps_original_message_in_durable_outbox(self):
        c = self.ccm
        node = c.Node(None, BrokenLink(), "laptop", "server")
        node.mirrors[900] = {"name": "server-target", "rec": {"pid": 900, "sessionId": "target-sid"}}
        proc = type("P", (), {"stdout": io.BytesIO((json.dumps({"frame": self.frame}) + "\n").encode())})()
        with patch.object(c, "read_sessions", return_value=[]):
            try:
                node.mirror_reader(900, proc)
            except BrokenPipeError:
                pass
        database = os.path.join(c.STATE_DIR, "reliability.sqlite3")
        self.assertTrue(os.path.isfile(database), "a socket/link success path must not be the only message copy")
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute("SELECT native_id, target_sid FROM outbox").fetchall()
        self.assertEqual(rows, [(self.frame["msg_id"], "target-sid")])

    def test_concurrent_duplicate_deliver_injects_native_once(self):
        c = self.ccm
        node = c.Node(None, io.BytesIO(), "server", "laptop")
        target = {"pid": 900, "sessionId": "target-sid", "name": "target", "messagingSocketPath": "/synthetic/900.sock",
                  "procStart": "synthetic-generation", "pidDomain": "synthetic-domain"}
        message = {"op": "deliver", "to_pid": 900, "from_pid": None, "frame": self.frame}
        native_writes = []
        lock = threading.Lock()

        def native_sink(sock, pid, frame, **kwargs):
            with lock:
                native_writes.append(frame)
            return None

        barrier = threading.Barrier(2)
        def receive():
            barrier.wait()
            node.on_deliver(message)

        with patch.object(c, "read_sessions", return_value=[target]), patch.object(c, "deliver_local", side_effect=native_sink):
            workers = [threading.Thread(target=receive) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
                self.assertFalse(worker.is_alive())
        self.assertEqual(len(native_writes), 1, "retrying a deliver must not inject duplicate user messages")
        self.assertEqual(native_writes[0], self.frame)


class FakeClock:
    def __init__(self):
        self.now = 1700000000.0

    def __call__(self):
        return self.now


class StoreContractTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = home.name
        self.ccm = load(self.home)
        self.clock = FakeClock()
        self.source = self.ccm.ReliabilityStore(clock=self.clock)
        self.receiver = self.ccm.ReliabilityStore(directory=os.path.join(self.home,"receiver"),clock=self.clock)
        self.origin = (self.source.node_id,self.source.incarnation)
        self.peer = (self.receiver.node_id,self.receiver.incarnation)
        self.frame = {"msgV":1,"msg_id":"20000000-0000-4000-8000-000000000001","type":"user","priority":"next",
                      "message":{"role":"user","content":"synthetic body; never a real session"}}
        self.target = {"pid":os.getpid(),"procStart":self.ccm.proc_start(os.getpid()),"pidDomain":self.ccm.pid_domain(),
                       "sessionId":"target-sid","messagingSocketPath":"/synthetic/socket","name":"target"}

    def enqueue(self, **fields):
        frame = dict(self.frame,**fields)
        return self.source.enqueue([{"peer":"receiver","sid":"target-sid"}],frame)[0]

    def accepted(self):
        row = self.enqueue()
        item,created = self.receiver.accept(self.source.outgoing_envelope(row),"sender","synthetic-link",self.origin)
        self.assertTrue(created)
        return row,item

    def key(self,item):
        return (item["origin_node"],item["origin_inc"],item["id"])

    def configure(self, **values):
        cfg=self.ccm.load_config();cfg.update(values);self.ccm.save_config(cfg)

    def test_defaults_are_independent_booleans_enabled(self):
        cfg=self.ccm.load_config()
        self.assertIs(cfg["read_receipts"],True)
        self.assertIs(cfg["retry_enabled"],True)

    def test_private_database_and_identity_modes(self):
        self.assertEqual(os.stat(self.source.path).st_mode & 0o777,0o600)
        self.assertEqual(os.stat(self.source.identity_path).st_mode & 0o777,0o600)
        self.assertEqual(os.stat(self.source.directory).st_mode & 0o777,0o700)
        with self.source.connection() as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0],"delete")
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0],2)

    def test_outbox_restart_preserves_id_frame_and_budget(self):
        row=self.enqueue()
        attempt=self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
        restored=self.ccm.ReliabilityStore(clock=self.clock)
        saved=restored.outbox_rows()[0]
        self.assertEqual((saved["id"],saved["frame"],saved["attempts"]),(row["id"],row["frame"],1))
        self.assertEqual(saved["next_at"],attempt["next_at"])

    def test_broadcast_acceptance_is_single_transaction(self):
        second=dict(self.frame,msg_id="20000000-0000-4000-8000-000000000002")
        with patch.object(self.source,"_budget",side_effect=[None,self.ccm.ReliabilityError("synthetic full")]):
            with self.assertRaises(self.ccm.ReliabilityError):
                self.source.enqueue_batch([({"peer":"receiver","sid":"target-sid"},self.frame),({"peer":"receiver","sid":"other-sid"},second)],group_id="synthetic-group")
        self.assertEqual(self.source.outbox_rows(),[])

    def test_duplicate_id_is_one_inbox_and_one_durable_ack(self):
        row,item=self.accepted()
        repeat,created=self.receiver.accept(self.source.outgoing_envelope(row),"sender","synthetic-link",self.origin)
        self.assertFalse(created)
        self.assertEqual(repeat["id"],item["id"])
        self.assertEqual(len(self.receiver.inbox_rows()),1)
        self.assertEqual(len(self.receiver.receipt_rows()),1)

    def test_same_delivery_id_changed_target_is_conflict(self):
        row,item=self.accepted();message=self.source.outgoing_envelope(row);message["target_sid"]="other-sid"
        value={"source_sid":message["source_sid"],"source_generation":self.ccm.canonical_json(message["source_generation"]),"target_sid":message["target_sid"],
               "frame":message["frame"],"created":message["created"],"expires":message["expires"],"read_requested":message["read_requested"]}
        message["fingerprint"]=self.source._fingerprint(value)
        with self.assertRaises(self.ccm.ReliabilityError):self.receiver.accept(message,"sender","synthetic-link",self.origin)
        self.assertEqual(len(self.receiver.inbox_rows()),1)

    def test_same_id_changed_body_source_or_expiry_is_conflict(self):
        row,item=self.accepted()
        for field,value in (("source_sid","different-source"),("expires",row["expires"]-1),("read_requested",False),("frame",dict(self.frame,message={"content":"different"}))):
            with self.subTest(field=field):
                message=self.source.outgoing_envelope(row);message[field]=value
                fp={"source_sid":message["source_sid"],"source_generation":self.ccm.canonical_json(message["source_generation"]),"target_sid":message["target_sid"],
                    "frame":message["frame"],"created":message["created"],"expires":message["expires"],"read_requested":message["read_requested"]}
                message["fingerprint"]=self.source._fingerprint(fp)
                with self.assertRaises(self.ccm.ReliabilityError):self.receiver.accept(message,"sender","synthetic-link",self.origin)
        self.assertEqual(len(self.receiver.inbox_rows()),1)

    def test_native_id_mapping_cannot_create_second_delivery(self):
        row,item=self.accepted();message=self.source.outgoing_envelope(row);message["delivery_id"]="different-delivery"
        with self.assertRaises(self.ccm.ReliabilityError):self.receiver.accept(message,"sender","synthetic-link",self.origin)
        self.assertEqual(len(self.receiver.inbox_rows()),1)

    def test_authenticated_origin_mismatch_rejected(self):
        row=self.enqueue()
        with self.assertRaises(self.ccm.ReliabilityError):self.receiver.accept(self.source.outgoing_envelope(row),"sender","synthetic-link",("wrong-origin","wrong-inc"))
        self.assertEqual(self.receiver.inbox_rows(),[])

    def test_nonfinite_bool_and_excessive_lifetimes_rejected(self):
        row=self.enqueue()
        for field,value in (("created",float("nan")),("expires",float("inf")),("created",True),("expires",row["created"]+86401)):
            message=self.source.outgoing_envelope(row);message[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(self.ccm.ReliabilityError):
                self.receiver.accept(message,"sender","synthetic-link",self.origin)
        self.assertEqual(self.receiver.inbox_rows(),[])

    def test_received_ack_stops_body_retries(self):
        row=self.enqueue();self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
        self.assertTrue(self.source.peer_received(row["id"],self.peer,"target-sid"))
        self.clock.now+=1000
        self.assertIsNone(self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True))

    def test_ack_wrong_incarnation_or_target_cannot_update(self):
        row=self.enqueue();self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
        self.assertFalse(self.source.peer_received(row["id"],(self.peer[0],"wrong-inc"),"target-sid"))
        self.assertFalse(self.source.peer_received(row["id"],self.peer,"different-target"))
        self.assertEqual(self.source.outbox_rows()[0]["transport"],"queued")

    def test_only_one_native_dispatch_owner(self):
        row,item=self.accepted();first=self.receiver.claim_native(self.key(item),self.target,explicit=True)
        self.assertIsNotNone(first)
        self.assertIsNone(self.receiver.claim_native(self.key(item),self.target,explicit=True))
        self.assertTrue(self.receiver.prewrite_allowed(self.key(item),first["dispatch_owner"]))
        self.assertFalse(self.receiver.prewrite_allowed(self.key(item),"wrong-owner"))

    def test_live_dispatch_is_not_released_by_time(self):
        row,item=self.accepted();claim=self.receiver.claim_native(self.key(item),self.target,explicit=True)
        self.clock.now+=1000;self.receiver.recover_dispatches()
        self.assertEqual(self.receiver.inbox_rows()[0]["native_state"],"native_dispatching")
        self.assertIsNone(self.receiver.claim_native(self.key(item),self.target,explicit=True))

    def test_dead_dispatch_recovers_unknown_not_pending(self):
        row,item=self.accepted();claim=self.receiver.claim_native(self.key(item),self.target,explicit=True)
        with self.receiver.transaction() as db:db.execute("UPDATE inbox SET dispatch_start='synthetic-stale'")
        self.receiver.recover_dispatches()
        self.assertEqual(self.receiver.inbox_rows()[0]["native_state"],"handoff_unknown")
        self.assertIsNone(self.receiver.claim_native(self.key(item),self.target,explicit=True))

    def test_prewrite_owner_may_retry_but_partial_write_may_not(self):
        row,item=self.accepted();key=self.key(item);claim=self.receiver.claim_native(key,self.target,explicit=True)
        self.assertTrue(self.receiver.native_finished(key,claim["dispatch_owner"],{"state":"prewrite_failed"}))
        self.clock.now+=1000;again=self.receiver.claim_native(key,self.target,explicit=True);self.assertIsNotNone(again)
        self.assertTrue(self.receiver.native_finished(key,again["dispatch_owner"],{"state":"handoff_unknown"}))
        self.clock.now+=1000;self.assertIsNone(self.receiver.claim_native(key,self.target,explicit=True))

    def test_post_write_commit_error_keeps_irreclaimable_state(self):
        row,item=self.accepted();key=self.key(item);claim=self.receiver.claim_native(key,self.target,explicit=True)
        with patch.object(self.receiver,"transaction",side_effect=sqlite3.OperationalError("synthetic commit failure")):
            with self.assertRaises(sqlite3.Error):self.receiver.native_finished(key,claim["dispatch_owner"],{"state":"socket_written_unconfirmed"})
        self.assertIsNone(self.receiver.claim_native(key,self.target,explicit=True))

    def test_read_ack_needs_owned_target_generation_and_handoff(self):
        row,item=self.accepted();key=self.key(item)
        with self.assertRaises(self.ccm.ReliabilityError):self.receiver.acknowledge(item["id"],self.target)
        claim=self.receiver.claim_native(key,self.target,explicit=True)
        self.receiver.native_finished(key,claim["dispatch_owner"],{"state":"socket_written_unconfirmed"})
        for target in (dict(self.target,sessionId="wrong-sid"),dict(self.target,procStart="wrong-generation")):
            with self.assertRaises(self.ccm.ReliabilityError):self.receiver.acknowledge(item["id"],target)
        self.assertTrue(self.receiver.acknowledge(item["id"],self.target))
        self.assertFalse(self.receiver.acknowledge(item["id"],self.target))
        self.assertEqual(self.receiver.inbox_rows()[0]["acknowledged"],1)

    def test_status_and_transport_never_auto_read_ack(self):
        row,item=self.accepted();claim=self.receiver.claim_native(self.key(item),self.target,explicit=True)
        self.receiver.native_finished(self.key(item),claim["dispatch_owner"],{"state":"socket_written_unconfirmed"})
        for _ in range(3):self.receiver.status("target-sid")
        self.assertEqual(self.receiver.inbox_rows()[0]["acknowledged"],0)
        self.assertFalse(any(r["kind"]=="receiver_acknowledged" for r in self.receiver.receipt_rows()))

    def test_receiver_disabled_suppresses_declared_ack_permanently(self):
        row,item=self.accepted();claim=self.receiver.claim_native(self.key(item),self.target,explicit=True)
        self.receiver.native_finished(self.key(item),claim["dispatch_owner"],{"state":"socket_written_unconfirmed"})
        cfg=self.ccm.load_config();cfg["read_receipts"]=False;self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg)
        self.receiver.acknowledge(item["id"],self.target)
        self.assertEqual(self.receiver.inbox_rows()[0]["disclosure"],"suppressed")
        self.assertFalse(any(r["kind"]=="receiver_acknowledged" for r in self.receiver.receipt_rows()))
        cfg["read_receipts"]=True;self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg)
        self.assertFalse(self.receiver.acknowledge(item["id"],self.target))
        self.assertFalse(any(r["kind"]=="receiver_acknowledged" for r in self.receiver.receipt_rows()))

    def test_privacy_off_cancels_reserved_not_started_receipt(self):
        row,item=self.accepted();claim=self.receiver.claim_native(self.key(item),self.target,explicit=True)
        self.receiver.native_finished(self.key(item),claim["dispatch_owner"],{"state":"socket_written_unconfirmed"})
        self.receiver.acknowledge(item["id"],self.target)
        receipt=next(r for r in self.receiver.receipt_rows() if r["kind"]=="receiver_acknowledged")
        reserved=self.receiver.reserve_receipt(receipt["id"],explicit=True)
        cfg=self.ccm.load_config();cfg["read_receipts"]=False;self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg)
        self.assertFalse(self.receiver.receipt_send_allowed(reserved))
        self.assertEqual(next(r for r in self.receiver.receipt_rows() if r["id"]==receipt["id"])["state"],"suppressed")

    def test_retry_disabled_blocks_timer_and_duplicate_attempts(self):
        cfg=self.ccm.load_config();cfg["retry_enabled"]=False;self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg)
        row=self.enqueue()
        self.assertIsNone(self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable"))
        first=self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
        self.assertIsNotNone(first)
        self.source.outgoing_finished(row["id"],first["attempt_owner"],"uncertain")
        self.clock.now+=1000
        self.assertIsNone(self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable"))
        self.assertEqual(self.source.outbox_rows()[0]["attempts"],1)

    def test_explicit_notice_cannot_be_consumed_twice(self):
        row=self.enqueue()
        self.assertTrue(self.source.claim_explicit_notice("outbox",row["id"]))
        self.assertFalse(self.source.claim_explicit_notice("outbox",row["id"]))

    def test_legacy_write_never_becomes_retryable_after_upgrade(self):
        row=self.enqueue();attempt=self.source.reserve_outgoing(row["id"],"synthetic-link",None,"legacy",explicit=True)
        self.assertTrue(self.source.outgoing_send_allowed(attempt,explicit=True))
        self.source.outgoing_finished(row["id"],attempt["attempt_owner"],"uncertain")
        self.clock.now+=1000
        self.assertIsNone(self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True))
        self.assertEqual(self.source.outbox_rows()[0]["transport"],"legacy_socket_written_unconfirmed")

    def test_peer_identity_or_database_incarnation_change_pauses(self):
        self.assertTrue(self.source.bind_peer("synthetic-link","receiver",*self.peer))
        self.assertFalse(self.source.bind_peer("synthetic-link","receiver",self.peer[0],"different-inc"))
        self.assertEqual(self.source.peer("synthetic-link")["disposition"],"needs_reconcile")
        self.assertFalse(self.source.bind_peer("synthetic-link","receiver",*self.peer))

    def test_database_rebuild_changes_incarnation_and_old_object_stops(self):
        old=self.receiver.incarnation;os.unlink(self.receiver.path)
        with self.assertRaises((OSError,self.ccm.ReliabilityError)):self.receiver.status()
        replacement=self.ccm.ReliabilityStore(directory=self.receiver.directory,clock=self.clock)
        self.assertEqual(replacement.node_id,self.receiver.node_id)
        self.assertNotEqual(replacement.incarnation,old)

    def test_clock_rollback_pauses_without_extending_expiry(self):
        row=self.enqueue();self.clock.now-=1
        with self.assertRaises(self.ccm.ReliabilityError):self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
        self.assertEqual(self.source.outbox_rows()[0]["expires"],row["expires"])

    def test_expired_replay_rejected_before_and_after_tombstone_gc(self):
        row,item=self.accepted();message=self.source.outgoing_envelope(row)
        self.clock.now=item["expires"]+1
        with self.assertRaises(self.ccm.ReliabilityError):self.receiver.accept(message,"sender","synthetic-link",self.origin)
        self.receiver.gc();self.assertEqual(len(self.receiver.inbox_rows()),1)
        self.clock.now=item["retain_until"]+1;self.receiver.gc();self.assertEqual(self.receiver.inbox_rows(),[])
        with self.assertRaises(self.ccm.ReliabilityError):self.receiver.accept(message,"sender","synthetic-link",self.origin)

    def test_attempt_budget_and_backoff_are_persistent(self):
        cfg=self.ccm.load_config();cfg["max_attempts"]=2;self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg)
        row=self.enqueue()
        for n in (1,2):
            attempt=self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
            self.assertEqual(attempt["attempts"],n)
            self.assertGreaterEqual(attempt["next_at"]-self.clock.now,2**n *0.8)
            self.assertLessEqual(attempt["next_at"]-self.clock.now,2**n *1.2)
            self.source.outgoing_finished(row["id"],attempt["attempt_owner"],"uncertain")
            self.clock.now+=1000
        self.assertIsNone(self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True))

    def test_outgoing_policy_revision_rechecked_before_link_io(self):
        row=self.enqueue();attempt=self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
        revision=self.source.begin_policy_update();cfg=self.ccm.load_config();cfg.update(policy_revision=revision,retry_enabled=False)
        self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg);self.source.finish_policy_update(cfg)
        self.assertFalse(self.source.outgoing_send_allowed(attempt,explicit=True))
        self.assertEqual(self.source.outbox_rows()[0]["attempts"],1)

    def test_only_one_outgoing_lease(self):
        row=self.enqueue();first=self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
        self.assertIsNotNone(first)
        self.assertIsNone(self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True))

    def test_active_budget_rejects_new_acceptance_not_old_dedup(self):
        cfg=self.ccm.load_config();cfg["max_active_messages"]=1;self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg)
        row=self.enqueue()
        with self.assertRaises(self.ccm.ReliabilityError):self.enqueue(msg_id="different-native")
        self.assertEqual(self.enqueue()["id"],row["id"])
        self.assertEqual(len(self.source.outbox_rows()),1)

    def test_invalid_config_types_fail_closed(self):
        for key,value in (("read_receipts","false"),("retry_enabled",0),("max_attempts",True),("message_ttl",float("inf")),("retry_jitter",2)):
            with self.subTest(key=key),self.assertRaises(self.ccm.ReliabilityError):self.ccm.validate_config({key:value})
        with self.assertRaises(self.ccm.ReliabilityError):self.ccm.validate_config({"retry_unknown":1})

    def test_corrupt_unreadable_and_deleted_config_do_not_reenable(self):
        os.makedirs(os.path.dirname(self.ccm.CONFIG_PATH),exist_ok=True)
        with open(self.ccm.CONFIG_PATH,"w") as f:f.write("{broken")
        with self.assertRaises(self.ccm.ReliabilityError):self.ccm.load_config()
        with patch("builtins.open",side_effect=PermissionError("synthetic denied")):
            with self.assertRaises(self.ccm.ReliabilityError):self.ccm.load_config()

    def test_policy_failure_window_remains_closed(self):
        revision=self.source.begin_policy_update()
        cfg=self.ccm.load_config();cfg["policy_revision"]=revision;self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg)
        with self.assertRaises(self.ccm.ReliabilityError):self.enqueue()
        self.source.finish_policy_update(cfg)
        self.assertIsNotNone(self.enqueue())

    def test_status_contains_no_body_or_secret_values(self):
        row=self.enqueue();text=json.dumps(self.source.status())
        self.assertNotIn("synthetic body",text)
        self.assertNotIn("frame",text)
        self.assertNotIn("peerToken",text)

    def test_native_status_correlates_original_id_and_both_generations(self):
        source={"sessionId":"source-sid","pid":123,"procStart":"source-generation","pidDomain":"synthetic-domain"}
        row=self.source.enqueue([{"peer":"receiver","sid":"target-sid"}],self.frame,source)[0]
        self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
        binding=self.ccm.generation(self.target)
        with self.source.transaction() as db:db.execute("UPDATE outbox SET target_generation=?",(self.ccm.canonical_json(binding),))
        status={"type":"control","action":"peer_message_status","msg_id":"10000000-0000-4000-8000-000000000001","orig_msg_id":self.frame["msg_id"],"status":"held","from":"uds:/synthetic/222.sock"}
        args=(self.peer,"source-sid","target-sid",binding,status)
        self.assertEqual(self.source.native_status(*args,source_generation=self.ccm.generation(source),ingress_channel="synthetic-link"),1)
        self.assertEqual(self.source.outbox_rows()[0]["admission"],"held")
        self.assertEqual(self.source.native_status(*args,source_generation=self.ccm.generation(source)),0)
        self.assertEqual(self.source.native_status(*args,source_generation={"pid":123,"procStart":"wrong","pidDomain":"synthetic-domain"},ingress_channel="synthetic-link"),0)
        wrong=dict(status,orig_msg_id=status["msg_id"])
        self.assertEqual(self.source.native_status(self.peer,"source-sid","target-sid",binding,wrong,source_generation=self.ccm.generation(source),ingress_channel="synthetic-link"),0)
        self.assertIsNone(self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True))
        status["status"]="delivered"
        self.assertEqual(self.source.native_status(*args,source_generation=self.ccm.generation(source),ingress_channel="synthetic-link"),1)
        self.assertEqual(self.source.outbox_rows()[0]["acknowledged"],0)

    def test_native_refused_encodings_and_dropped_list_remain_terminal(self):
        source={"sessionId":"source-sid","pid":123,"procStart":"source-generation","pidDomain":"synthetic-domain"}
        for number,encoding in enumerate(("direct","expired-detail","dropped-list")):
            frame=dict(self.frame,msg_id="synthetic-native-"+str(number))
            row=self.source.enqueue([{"peer":"receiver","sid":"target-sid"}],frame,source)[0]
            self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
            binding=self.ccm.generation(self.target)
            with self.source.transaction() as db:db.execute("UPDATE outbox SET target_generation=? WHERE id=?",(self.ccm.canonical_json(binding),row["id"]))
            status={"type":"control","action":"peer_message_status","msg_id":"synthetic-control","orig_msg_id":frame["msg_id"],"status":"refused"}
            if encoding=="expired-detail":status.update(status="expired",status_detail="refused")
            if encoding=="dropped-list":status.update(status="dropped",orig_msg_id="not-matching",dropped_msg_ids=[frame["msg_id"]],drop_reason="synthetic-drop")
            self.assertEqual(self.source.native_status(self.peer,"source-sid","target-sid",binding,status,source_generation=self.ccm.generation(source),ingress_channel="synthetic-link"),1)
            actual=next(x for x in self.source.outbox_rows() if x["id"]==row["id"])
            self.assertEqual(actual["admission"],"dropped" if encoding=="dropped-list" else "refused")
            status["status"]="delivered"
            self.assertEqual(self.source.native_status(self.peer,"source-sid","target-sid",binding,status,source_generation=self.ccm.generation(source),ingress_channel="synthetic-link"),0)

    def test_unknown_native_control_literal_is_not_invented_read(self):
        row=self.enqueue();self.source.reserve_outgoing(row["id"],"synthetic-link",self.peer,"durable",explicit=True)
        for action,value in (("unknown-action","held"),("peer_message_status","consumed"),("peer_message_status","read")):
            status={"type":"control","action":action,"orig_msg_id":row["native_id"],"status":value}
            self.assertEqual(self.source.native_status(self.peer,"","target-sid",self.ccm.generation(self.target),status,source_generation=self.ccm.generation({}),ingress_channel="synthetic-link"),0)
        self.assertEqual(self.source.outbox_rows()[0]["acknowledged"],0)

    def test_control_receipt_ack_terminates_without_ack_of_ack(self):
        row,item=self.accepted();receipt=self.receiver.receipt_rows()[0]
        reserved=self.receiver.reserve_receipt(receipt["id"],explicit=True);self.assertTrue(self.receiver.receipt_send_allowed(reserved))
        self.receiver.receipt_finished(receipt["id"],reserved["owner"])
        self.assertTrue(self.receiver.receipt_ack(receipt["id"],self.origin))
        self.assertIsNone(self.receiver.reserve_receipt(receipt["id"],explicit=True))
        self.assertEqual(len(self.receiver.receipt_rows()),1)

    def test_turning_privacy_off_during_io_cannot_replay_after_reenable(self):
        row,item=self.accepted();claim=self.receiver.claim_native(self.key(item),self.target,explicit=True)
        self.receiver.native_finished(self.key(item),claim["dispatch_owner"],{"state":"socket_written_unconfirmed"});self.receiver.acknowledge(item["id"],self.target)
        receipt=next(r for r in self.receiver.receipt_rows() if r["kind"]=="receiver_acknowledged")
        reservation=self.receiver.reserve_receipt(receipt["id"],explicit=True);self.assertTrue(self.receiver.receipt_send_allowed(reservation))
        cfg=self.ccm.load_config();cfg["read_receipts"]=False;self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg)
        with self.receiver.transaction() as db:self.receiver._policy(db)
        cfg["read_receipts"]=True;self.ccm.write_json_private(self.ccm.CONFIG_PATH,cfg)
        self.clock.now+=1000
        self.assertIsNone(self.receiver.reserve_receipt(receipt["id"],explicit=True))
        saved=next(r for r in self.receiver.receipt_rows() if r["id"]==receipt["id"])
        self.assertEqual(saved["privacy_suppressed"],1)

    def test_same_native_id_changed_sender_is_conflict(self):
        row,item=self.accepted();message=self.source.outgoing_envelope(row);message["source_sid"]="other-source";message["delivery_id"]="other-delivery"
        values={"source_sid":message["source_sid"],"source_generation":self.ccm.canonical_json(message["source_generation"]),"target_sid":message["target_sid"],"frame":message["frame"],"created":message["created"],"expires":message["expires"],"read_requested":message["read_requested"]}
        message["fingerprint"]=self.source._fingerprint(values)
        with self.assertRaises(self.ccm.ReliabilityError):self.receiver.accept(message,"sender","synthetic-link",self.origin)
        self.assertEqual(len(self.receiver.inbox_rows()),1)

    def test_wrong_key_generation_is_proved_prewrite_failure(self):
        from test_ccm import FakeSession
        target=FakeSession(self.ccm,"synthetic","target-sid");self.addCleanup(target.close)
        rec=self.ccm.read_sessions()[0]
        key=self.ccm.key_path(rec["pid"],rec["messagingSocketPath"])
        self.ccm.write_json_private(key,{"peerToken":"cd"*16,"procStart":"wrong-generation","pidDomain":rec["pidDomain"]})
        result=self.ccm.deliver_local(rec["messagingSocketPath"],rec["pid"],self.frame,expected_sid="target-sid",structured=True)
        self.assertEqual((result["state"],result["error"]),("prewrite_failed","key_generation_mismatch"))
        self.assertEqual(target.got,[])

    def test_native_post_send_exception_is_unknown_not_connect_failure(self):
        from test_ccm import FakeSession
        target=FakeSession(self.ccm,"synthetic","target-sid");self.addCleanup(target.close);rec=self.ccm.read_sessions()[0]
        class PartialSocket:
            def settimeout(self,value):pass
            def connect(self,path):pass
            def sendall(self,data):raise TimeoutError("synthetic partial write")
            def close(self):pass
        with patch.object(self.ccm.socket,"socket",return_value=PartialSocket()):
            result=self.ccm.deliver_local(rec["messagingSocketPath"],rec["pid"],self.frame,expected_sid="target-sid",structured=True)
        self.assertEqual(result["state"],"handoff_unknown")
        self.assertFalse(result["safe_to_retry"])

    def test_no_ancestor_or_forged_env_cannot_declare_read(self):
        row,item=self.accepted()
        with patch.object(self.ccm,"whoami",return_value=None),self.assertRaises(SystemExit):
            self.ccm.cmd_read_ack(type("A",(),{"id":item["id"]})())
        self.assertEqual(self.receiver.inbox_rows()[0]["acknowledged"],0)

    def test_two_nodes_lost_ack_replay_dedup_then_explicit_receipt(self):
        from test_ccm import FakeSession
        c=self.ccm;target=FakeSession(c,"synthetic-target","target-sid");self.addCleanup(target.close)
        rec=c.read_sessions()[0]
        sender=c.Node(None,io.BytesIO(),"sender","receiver",trusted_link="synthetic-outgoing")
        receiver=c.Node(None,io.BytesIO(),"receiver","sender",trusted_link="synthetic-incoming")
        sender._store=self.source;receiver._store=self.receiver
        sender.peer_identity=self.peer;sender.durable_peer=True;sender.peer_advertised=True
        receiver.peer_identity=self.origin;receiver.durable_peer=True;receiver.peer_advertised=True
        row=self.enqueue();attempt=self.source.reserve_outgoing(row["id"],"synthetic-outgoing",self.peer,"durable",explicit=True)
        message=self.source.outgoing_envelope(attempt)
        receiver.on_reliable_deliver(message)
        # The replay is a later network retry, not an unbudgeted immediate ACK bypass.
        self.clock.now += 10
        receiver.on_reliable_deliver(message)
        import time
        deadline=time.monotonic()+2
        while not target.got and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(len(target.got),1)
        self.assertEqual(target.got[0][1],self.frame)
        # First received ACK may be lost; the replay returned an equivalent durable fact.
        replies=[json.loads(x) for x in receiver.wfile.getvalue().splitlines()]
        received=[x for x in replies if x["kind"]=="peer_received"]
        self.assertEqual(len(received),2)
        sender.on_receipt(received[-1])
        self.assertEqual(self.source.outbox_rows()[0]["transport"],"peer_received")
        self.assertIsNone(self.source.reserve_outgoing(row["id"],"synthetic-outgoing",self.peer,"durable",explicit=True))
        for receipt in self.receiver.receipt_rows("sender"):
            if receipt["kind"]=="native_handoff":receiver.send_receipt(receipt,explicit=True)
        replies=[json.loads(x) for x in receiver.wfile.getvalue().splitlines()]
        for reply in replies:
            if reply["kind"]=="native_handoff":sender.on_receipt(reply)
        self.receiver.acknowledge(row["id"],rec)
        receipt=next(x for x in self.receiver.receipt_rows("sender") if x["kind"]=="receiver_acknowledged")
        receiver.send_receipt(receipt,explicit=True)
        reply=json.loads(receiver.wfile.getvalue().splitlines()[-1]);sender.on_receipt(reply)
        self.assertEqual(self.source.outbox_rows()[0]["acknowledged"],1)
        self.assertEqual(len(target.got),1)
        for ack in [json.loads(x) for x in sender.wfile.getvalue().splitlines()]:
            self.assertEqual(ack["op"],"receipt_ack")
            self.receiver.receipt_ack(ack["id"],self.origin)
        self.assertEqual(next(x for x in self.receiver.receipt_rows() if x["id"]==receipt["id"])["state"],"received")

    def test_local_recovery_worker_after_pending_acceptance(self):
        from test_ccm import FakeSession
        c=self.ccm;target=FakeSession(c,"synthetic-target","target-sid");self.addCleanup(target.close)
        row=self.source.enqueue([{"peer":"@local","sid":"target-sid"}],self.frame)[0]
        c.local_recovery(self.source,row["id"],explicit=True)
        c.local_recovery(self.source)
        import time
        deadline=time.monotonic()+2
        while not target.got and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(len(target.got),1)
        self.assertEqual(self.source.outbox_rows()[0]["transport"],"peer_received")
        self.assertEqual(self.source.inbox_rows()[0]["native_state"],"socket_written_unconfirmed")
        self.assertEqual(self.source.outbox_rows()[0]["acknowledged"],0)

    def test_exact_name_duplicates_refused(self):
        with self.assertRaises(SystemExit):self.ccm.resolve("same",[{"name":"same","sessionId":"a"},{"name":"same","sessionId":"b"}])


class VerifiedBoundaryRegressions(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ);env.start();self.addCleanup(env.stop)
        home=tempfile.TemporaryDirectory();self.addCleanup(home.cleanup);self.home=home.name
        self.c=load(self.home);self.source=self.c.ReliabilityStore();self.receiver=self.c.ReliabilityStore(directory=os.path.join(self.home,"receiver"))
        self.frame={"msgV":1,"msg_id":"20000000-0000-4000-8000-000000000009","type":"user","priority":"next","message":{"role":"user","content":"synthetic verified boundary"}}
        self.target={"pid":os.getpid(),"procStart":self.c.proc_start(os.getpid()),"pidDomain":self.c.pid_domain(),"sessionId":"target-sid","messagingSocketPath":"/synthetic/target","name":"target"}
        self.owner=dict(self.target,sessionId="source-sid",messagingSocketPath="/synthetic/source",name="source")

    def pair(self, owner=False):
        row=self.source.enqueue([{"peer":"receiver","sid":"target-sid"}],self.frame,self.owner if owner else None)[0]
        identity=(self.source.node_id,self.source.incarnation)
        item,created=self.receiver.accept(self.source.outgoing_envelope(row),"sender","synthetic-incoming",identity)
        self.assertTrue(created)
        return row,item

    def node_pair(self):
        a=self.c.Node(None,io.BytesIO(),"sender","receiver",trusted_link="synthetic-outgoing");b=self.c.Node(None,io.BytesIO(),"receiver","sender",trusted_link="synthetic-incoming")
        a._store=self.source;b._store=self.receiver
        a.peer_identity=(self.receiver.node_id,self.receiver.incarnation);b.peer_identity=(self.source.node_id,self.source.incarnation)
        for n in (a,b):n.durable_peer=True;n.peer_advertised=True
        return a,b

    def test_native_finished_commits_handoff_control_atomically(self):
        row,item=self.pair();key=(item['origin_node'],item['origin_inc'],item['id'])
        claim=self.receiver.claim_native(key,self.target,explicit=True)
        self.receiver.native_finished(key,claim['dispatch_owner'],{'state':'socket_written_unconfirmed'})
        restored=self.c.ReliabilityStore(directory=self.receiver.directory)
        receipt=next((r for r in restored.receipt_rows() if r['kind']=='native_handoff'),None)
        self.assertIsNotNone(receipt,'a committed handoff state must have its generation control fact in the same transaction')
        self.assertEqual(json.loads(receipt['payload'])['target_generation'],self.c.generation(self.target))

    def test_dead_dispatch_recovery_commits_unknown_control_without_io(self):
        row,item=self.pair();key=(item['origin_node'],item['origin_inc'],item['id'])
        self.receiver.claim_native(key,self.target,explicit=True)
        with self.receiver.transaction() as db:db.execute("UPDATE inbox SET dispatch_start='synthetic-dead'")
        self.receiver.recover_dispatches()
        receipt=next((r for r in self.receiver.receipt_rows() if r['kind']=='native_handoff'),None)
        self.assertIsNotNone(receipt,'unknown recovery must preserve generation correlation for explicit acknowledgement')
        self.assertEqual(json.loads(receipt['payload'])['native_state'],'handoff_unknown')
        self.assertEqual(self.receiver.inbox_rows()[0]['native_state'],'handoff_unknown')

    def test_real_sqlite_grow_gc_restart_accepts_small_frame(self):
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],dict(self.frame,message={'content':'x'*470000}))[0]
        with self.source.connection() as db:
            grown=db.execute('PRAGMA page_count').fetchone()[0];self.assertGreaterEqual(grown,126)
        cfg=self.c.load_config();cfg.update(max_store_bytes=1048576,ack_reserve_bytes=4096);self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        self.source.clock=lambda:row['retain_until']+1
        self.source.gc()
        restored=self.c.ReliabilityStore(clock=self.source.clock)
        self.assertEqual(restored.outbox_rows(),[])
        new=restored.enqueue([{'peer':'receiver','sid':'target-sid'}],dict(self.frame,msg_id='small-after-gc'))[0]
        self.assertEqual(new['native_id'],'small-after-gc')

    def test_real_route_churn_preserves_existing_read_ack_capacity(self):
        row,item=self.pair();key=(item['origin_node'],item['origin_inc'],item['id'])
        claim=self.receiver.claim_native(key,self.target,explicit=True);self.receiver.native_finished(key,claim['dispatch_owner'],{'state':'socket_written_unconfirmed'})
        cfg=self.c.load_config();cfg.update(max_store_bytes=1048576,ack_reserve_bytes=4096);self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        routes=[{'sessionId':'route-'+str(i),'pid':10000+i,'procStart':'synthetic','pidDomain':'synthetic','name':'r'*300} for i in range(4000)]
        self.receiver.remember_routes('sender',routes)
        self.assertTrue(self.receiver.acknowledge(item['id'],self.target))
        self.assertEqual(self.receiver.inbox_rows()[0]['acknowledged'],1)

    def test_real_node_native_status_before_handoff_is_reconciled(self):
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame,self.owner)[0]
        a,b=self.node_pair();self.source.reserve_outgoing(row['id'],'synthetic-outgoing',a.peer_identity,'durable',explicit=True)
        a.mirrors[901]={'rec':dict(self.target,pid=901),'sock':'/synthetic/mirror','name':'receiver-target'}
        control={'type':'control','action':'peer_message_status','msg_id':'10000000-0000-4000-8000-000000000009','orig_msg_id':row['native_id'],'status':'refused','from':'uds:/synthetic/901'}
        with patch.object(self.c,'read_sessions',return_value=[self.owner]),patch.object(self.c,'deliver_local',return_value=None):
            a.on_deliver({'frame':control,'to_pid':self.owner['pid'],'from_pid':901})
        binding=self.c.generation(a.mirrors[901]['rec'])
        a.on_receipt({'op':'receipt','id':'a'*64,'delivery_id':row['id'],'kind':'native_handoff','node_id':a.peer_identity[0],'incarnation':a.peer_identity[1],
                      'payload':{'target_sid':'target-sid','target_generation':binding,'native_state':'socket_written_unconfirmed'}})
        self.assertEqual(self.source.outbox_rows()[0]['admission'],'refused')

    def test_retry_off_real_dispatch_and_read_wakeup_send_dependency(self):
        cfg=self.c.load_config();cfg['retry_enabled']=False;self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame)[0]
        a,b=self.node_pair();self.source.reserve_outgoing(row['id'],'synthetic-outgoing',a.peer_identity,'durable',explicit=True)
        with patch.object(self.c,'read_sessions',return_value=[self.target]),patch.object(self.c,'deliver_local',return_value=None):
            b.on_reliable_deliver(self.source.outgoing_envelope(row))
        # Only production dispatch creates/sends the dependency; no manual send_handoff here.
        for packet in [json.loads(x) for x in b.wfile.getvalue().splitlines()]:a.on_receipt(packet)
        self.assertIsNotNone(self.source.outbox_rows()[0]['target_generation'])
        self.receiver.acknowledge(row['id'],self.target)
        receipt=next(r for r in self.receiver.receipt_rows() if r['kind']=='receiver_acknowledged')
        b.local={self.target['pid']:self.target}
        proc=type('P',(),{'stdout':io.BytesIO((json.dumps({'frame':{'type':'ccm_local','action':'receipt','id':receipt['id'],'from':self.c.peer_address(self.target['messagingSocketPath'])}})+'\n').encode())})()
        b.mirror_reader(0,proc)
        for packet in [json.loads(x) for x in b.wfile.getvalue().splitlines()]:a.on_receipt(packet)
        self.assertEqual(self.source.outbox_rows()[0]['acknowledged'],1)

    def test_cli_retry_off_delivers_a_separate_one_shot_wakeup(self):
        os.makedirs(self.c.SESSIONS_DIR,exist_ok=True)
        self.c.write_json_private(os.path.join(self.c.SESSIONS_DIR,str(self.owner['pid'])+'.json'),self.owner)
        cfg=self.c.load_config();cfg['retry_enabled']=False;self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame,self.owner)[0]
        a,b=self.node_pair();a.local={self.owner['pid']:self.owner}
        attempt=self.source.reserve_outgoing(row['id'],'synthetic-outgoing',a.peer_identity,'durable',explicit=True)
        self.source.outgoing_finished(row['id'],attempt['attempt_owner'],'uncertain');self.source.claim_explicit_notice('outbox',row['id'])
        mirror={'pid':901,'sessionId':'target-sid','name':'receiver-target','messagingSocketPath':'/synthetic/mirror','agent':'claude-code-message','ccmHost':'receiver'}
        a.mirrors[901]={'rec':dict(self.target,pid=901),'name':mirror['name']};wakes=[]
        with patch.object(self.c,'whoami',return_value=self.owner),patch.object(self.c,'ReliabilityStore',return_value=self.source),patch.object(self.c,'read_sessions',return_value=[mirror,self.owner]),patch.object(self.c,'deliver_local',side_effect=lambda sock,pid,frame,**kw:wakes.append(frame)):
            self.c.cmd_retry(type('A',(),{'id':row['id'],'inbox':False})())
        proc=type('P',(),{'stdout':io.BytesIO((json.dumps({'frame':wakes[0]})+'\n').encode())})()
        a.mirror_reader(901,proc)
        packets=[json.loads(x) for x in a.wfile.getvalue().splitlines()]
        self.assertEqual(sum(p.get('op')=='reliable_deliver' for p in packets),1)
        self.assertEqual(self.source.outbox_rows()[0]['attempts'],2)
        a.mirror_reader(901,type('P',(),{'stdout':io.BytesIO((json.dumps({'frame':wakes[0]})+'\n').encode())})())
        self.assertEqual(self.source.outbox_rows()[0]['attempts'],2)

    def test_duplicate_received_response_respects_persistent_budget_and_lease(self):
        cfg=self.c.load_config();cfg.update(retry_enabled=False,max_attempts=1);self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame)[0]
        a,b=self.node_pair()
        with patch.object(self.c,'read_sessions',return_value=[]):
            b.on_reliable_deliver(self.source.outgoing_envelope(row));b.on_reliable_deliver(self.source.outgoing_envelope(row))
        packets=[json.loads(x) for x in b.wfile.getvalue().splitlines()]
        self.assertEqual(len(packets),1,'a duplicate must not create an unbudgeted unlimited control write')
        self.assertEqual(self.receiver.receipt_rows()[0]['attempts'],1)

    def test_duplicate_response_live_lease_and_backoff_block_parallel_io(self):
        cfg=self.c.load_config();cfg['retry_enabled']=False;self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame)[0];a,b=self.node_pair()
        with patch.object(self.c,'read_sessions',return_value=[]):b.on_reliable_deliver(self.source.outgoing_envelope(row))
        now=self.receiver.clock();self.receiver.clock=lambda:now+10
        receipt=self.receiver.receipt_rows()[0];reservation=self.receiver.reserve_receipt(receipt['id'],reactive=True)
        self.assertIsNotNone(reservation)
        with patch.object(self.c,'read_sessions',return_value=[]):b.on_reliable_deliver(self.source.outgoing_envelope(row))
        self.assertEqual(len(b.wfile.getvalue().splitlines()),1)
        self.assertEqual(self.receiver.receipt_rows()[0]['attempts'],2)
        self.receiver.receipt_finished(receipt['id'],reservation['owner'])
        with patch.object(self.c,'read_sessions',return_value=[]):b.on_reliable_deliver(self.source.outgoing_envelope(row))
        self.assertEqual(len(b.wfile.getvalue().splitlines()),1)

    def test_duplicate_after_receipt_ack_is_bounded_reactive_not_background(self):
        cfg=self.c.load_config();cfg['retry_enabled']=False;self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame)[0];a,b=self.node_pair()
        with patch.object(self.c,'read_sessions',return_value=[]):b.on_reliable_deliver(self.source.outgoing_envelope(row))
        receipt=self.receiver.receipt_rows()[0];self.receiver.receipt_ack(receipt['id'],b.peer_identity)
        now=self.receiver.clock();self.receiver.clock=lambda:now+10
        with patch.object(self.c,'read_sessions',return_value=[]):b.on_reliable_deliver(self.source.outgoing_envelope(row))
        saved=self.receiver.receipt_rows()[0]
        self.assertEqual(saved['state'],'received')
        self.assertEqual(saved['attempts'],2)
        self.assertEqual(len(b.wfile.getvalue().splitlines()),2)
        self.assertIsNone(self.receiver.reserve_receipt(receipt['id']))
        self.assertEqual(self.receiver.inbox_rows()[0]['attempts'],0)

    def test_pending_native_status_wrong_generation_never_applies(self):
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame,self.owner)[0]
        a,b=self.node_pair();self.source.reserve_outgoing(row['id'],'synthetic-outgoing',a.peer_identity,'durable',explicit=True)
        binding=self.c.generation(self.target)
        status={'type':'control','action':'peer_message_status','orig_msg_id':row['native_id'],'status':'denied'}
        self.source.native_status(a.peer_identity,'source-sid','target-sid',dict(binding,procStart='wrong'),status,source_generation=self.c.generation(self.owner),ingress_channel=a.trusted_link)
        a.on_receipt({'op':'receipt','id':'a'*64,'delivery_id':row['id'],'kind':'native_handoff','node_id':a.peer_identity[0],'incarnation':a.peer_identity[1],
                      'payload':{'target_sid':'target-sid','target_generation':binding,'native_state':'socket_written_unconfirmed'}})
        self.assertEqual(self.source.outbox_rows()[0]['admission'],'unknown')
        self.assertEqual(self.source.outbox_rows()[0]['target_generation'],self.c.canonical_json(binding))

    def test_durable_send_accept_and_handoff_have_each_side_audit(self):
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame)[0]
        a,b=self.node_pair()
        a.send_outgoing(row['id'],explicit=True)
        packet=json.loads(a.wfile.getvalue().splitlines()[-1])
        with patch.object(self.c,'read_sessions',return_value=[self.target]),patch.object(self.c,'deliver_local',return_value=None):
            b.on_reliable_deliver(packet);b.on_reliable_deliver(packet)
        with open(self.c.AUDITFILE) as stream:events=[json.loads(x) for x in stream]
        self.assertTrue(any(e['ev']=='relay_out' and e.get('delivery_id')==row['id'] for e in events))
        self.assertTrue(any(e['ev']=='relay_in' and e.get('delivery_id')==row['id'] for e in events))
        self.assertEqual(b.session['relay_in'],1)
        self.assertEqual(sum(e['ev']=='native_handoff' and e.get('delivery_id')==row['id'] for e in events),1)


class ResidualBoundaryRegressions(unittest.TestCase):
    def setUp(self):
        StoreContractTests.setUp(self)
        self.c=self.ccm
        self.owner=dict(self.target,sessionId='source-sid',name='source',messagingSocketPath='/synthetic/source')

    node_pair = VerifiedBoundaryRegressions.node_pair

    def linked_status_nodes(self):
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame,self.owner)[0]
        a,_=self.node_pair()
        self.source.reserve_outgoing(row['id'],a.trusted_link,a.peer_identity,'durable',explicit=True)
        other=self.c.Node(None,io.BytesIO(),'sender','receiver',trusted_link='synthetic-other-link')
        other._store=self.source;other.peer_identity=a.peer_identity;other.durable_peer=True;other.peer_advertised=True
        self.assertTrue(self.source.bind_peer(other.trusted_link,'receiver',*other.peer_identity))
        for node in (a,other):
            node.mirrors[901]={'rec':dict(self.target,pid=901),'sock':'/synthetic/mirror','name':'receiver-target'}
        status={'type':'control','action':'peer_message_status','msg_id':'synthetic-control-id',
                'orig_msg_id':row['native_id'],'status':'held','from':'uds:/synthetic/901'}
        return row,a,other,{'frame':status,'to_pid':self.owner['pid'],'from_pid':901}

    def handoff(self, row, node):
        node.on_receipt({'op':'receipt','id':'a'*64,'delivery_id':row['id'],'kind':'native_handoff',
                         'node_id':node.peer_identity[0],'incarnation':node.peer_identity[1],
                         'payload':{'target_sid':'target-sid','target_generation':self.c.generation(node.mirrors[901]['rec']),
                                    'native_state':'socket_written_unconfirmed'}})

    def test_actual_ingress_channel_rejects_pending_status_on_other_link(self):
        row,a,other,message=self.linked_status_nodes()
        with patch.object(self.c,'read_sessions',return_value=[self.owner]),patch.object(self.c,'deliver_local',return_value=None):
            other.on_deliver(message)
            with self.source.connection() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM native_status_pending').fetchone()[0],0,
                                 'actual channel B must not be recorded as original outbox channel A')
            self.handoff(row,a)
            self.assertEqual(self.source.outbox_rows()[0]['admission'],'unknown')
            # Positive early control on A must survive until its own handoff fact.
            second=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],dict(self.frame,msg_id='second-status'),self.owner)[0]
            self.source.reserve_outgoing(second['id'],a.trusted_link,a.peer_identity,'durable',explicit=True)
            a.on_deliver(dict(message,frame=dict(message['frame'],orig_msg_id=second['native_id'])))
            with self.source.connection() as db:
                saved=db.execute('SELECT channel FROM native_status_pending').fetchall()
                self.assertEqual([r['channel'] for r in saved],[a.trusted_link])
            self.handoff(second,a)
            self.assertEqual(next(r for r in self.source.outbox_rows() if r['id']==second['id'])['admission'],'held')

    def test_actual_ingress_channel_rejects_direct_status_on_other_link(self):
        row,a,other,message=self.linked_status_nodes();self.handoff(row,a)
        with patch.object(self.c,'read_sessions',return_value=[self.owner]),patch.object(self.c,'deliver_local',return_value=None):
            other.on_deliver(message)
            self.assertEqual(self.source.outbox_rows()[0]['admission'],'unknown')
            a.on_deliver(message)
        self.assertEqual(self.source.outbox_rows()[0]['admission'],'held')
        self.assertEqual(self.source.outbox_rows()[0]['acknowledged'],0)

    def test_old_ack_during_reactive_node_io_cannot_release_current_lease(self):
        cfg=self.c.load_config();cfg.update(retry_enabled=False,retry_initial=2,retry_jitter=0,ack_wait=10)
        self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame)[0];_,b=self.node_pair()
        with patch.object(self.c,'read_sessions',return_value=[]):b.on_reliable_deliver(self.source.outgoing_envelope(row))
        receipt=self.receiver.receipt_rows()[0];self.receiver.receipt_ack(receipt['id'],b.peer_identity)
        other=self.c.Node(None,io.BytesIO(),'receiver','sender',trusted_link=b.trusted_link)
        other._store=self.receiver;other.peer_identity=b.peer_identity;other.durable_peer=True
        self.clock.now+=10;owners=[]
        def stalled_send(packet):
            saved=self.receiver.receipt_rows()[0];owners.append(saved['owner']);deadline=saved['owner_until']
            self.clock.now+=1
            self.assertTrue(self.receiver.receipt_ack(receipt['id'],b.peer_identity))
            self.clock.now+=4
            current=self.receiver.receipt_rows()[0]
            self.assertEqual(current['owner_until'],deadline,'an ACK for the durable fact is not IO owner completion')
            self.assertLessEqual(current['reactive_next_at'],self.clock.now)
            with patch.object(self.c,'read_sessions',return_value=[]):other.on_reliable_deliver(self.source.outgoing_envelope(row))
            self.assertEqual(other.wfile.getvalue(),b'')
            self.assertEqual(self.receiver.receipt_rows()[0]['attempts'],2)
            self.receiver.receipt_finished(receipt['id'],'wrong-owner')
            self.assertEqual(self.receiver.receipt_rows()[0]['owner_until'],deadline)
        with patch.object(b,'send',side_effect=stalled_send):self.assertTrue(b.send_receipt(receipt,reactive=True))
        current=self.receiver.receipt_rows()[0]
        self.assertEqual(current['owner'],owners[0]);self.assertIsNone(current['owner_until'])
        self.assertEqual(current['state'],'received')
        with patch.object(self.c,'read_sessions',return_value=[]):other.on_reliable_deliver(self.source.outgoing_envelope(row))
        self.assertEqual(len(other.wfile.getvalue().splitlines()),1)
        self.assertEqual(self.receiver.receipt_rows()[0]['attempts'],3)

    def test_regular_receipt_ack_is_fact_and_matching_finish_releases_io(self):
        row=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],self.frame)[0]
        self.receiver.accept(self.source.outgoing_envelope(row),'sender','synthetic-incoming',self.origin)
        receipt=self.receiver.receipt_rows()[0];claim=self.receiver.reserve_receipt(receipt['id'],explicit=True)
        self.assertTrue(self.receiver.receipt_send_allowed(claim))
        self.assertTrue(self.receiver.receipt_ack(receipt['id'],self.origin))
        self.assertEqual(self.receiver.receipt_rows()[0]['owner_until'],claim['owner_until'])
        self.receiver.receipt_finished(receipt['id'],'wrong-owner')
        self.assertEqual(self.receiver.receipt_rows()[0]['owner_until'],claim['owner_until'])
        self.receiver.receipt_finished(receipt['id'],claim['owner'])
        self.assertIsNone(self.receiver.receipt_rows()[0]['owner_until'])
        self.assertEqual(self.receiver.receipt_rows()[0]['state'],'received')

    def partial_capacity_state(self):
        cfg=self.c.load_config();cfg.update(clock_skew=0,tombstone_margin=3600);self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        live=self.source.enqueue([{'peer':'receiver','sid':'target-sid'}],dict(self.frame,message={'content':'L'*380000}),self.owner)[0]
        cfg['message_ttl']=1;self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        self.source.enqueue([{'peer':'receiver','sid':'dead-sid'}],dict(self.frame,msg_id='expired-frame',message={'content':'D'*32000}))
        cfg.update(message_ttl=86400,max_store_bytes=1048576,ack_reserve_bytes=4096);self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        self.clock.now+=3602
        return live

    def assert_live_preserved(self, store, live):
        rows=store.outbox_rows();self.assertEqual(len(rows),1)
        for key in ('id','native_id','target_sid','source_sid','source_generation','frame','fingerprint','expires','retain_until'):
            self.assertEqual(rows[0][key],live[key])

    def test_real_partial_gc_shared_reserve_restart_accepts_without_losing_live_message(self):
        live=self.partial_capacity_state();self.source.gc()
        restored=self.c.ReliabilityStore(clock=self.clock);self.assert_live_preserved(restored,live)
        added=restored.enqueue([{'peer':'receiver','sid':'small-sid'}],dict(self.frame,msg_id='small-after-partial'))[0]
        self.assertEqual(added['native_id'],'small-after-partial')
        saved=next(r for r in restored.outbox_rows() if r['id']==live['id'])
        self.assertEqual((saved['fingerprint'],saved['target_sid'],saved['frame']),(live['fingerprint'],'target-sid',live['frame']))

    def test_real_partial_compaction_failure_preserves_records_and_recovery(self):
        live=self.partial_capacity_state();original=self.source.connection;denied=[]
        @contextmanager
        def failed_vacuum():
            with original() as db:
                def authorize(action,one,two,database,trigger):
                    if action==sqlite3.SQLITE_ATTACH:
                        denied.append(one);return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                yield db
        with patch.object(self.source,'connection',failed_vacuum):
            with self.assertRaises(sqlite3.DatabaseError):self.source.gc()
        self.assertTrue(denied,'inject failure at real SQLite VACUUM, not at budget evaluation')
        self.assert_live_preserved(self.source,live)
        with self.assertRaises(self.c.ReliabilityError):
            self.source.enqueue([{'peer':'receiver','sid':'small-sid'}],dict(self.frame,msg_id='small-after-failed-compact'))
        self.source.gc();restored=self.c.ReliabilityStore(clock=self.clock);self.assert_live_preserved(restored,live)
        self.assertIsNotNone(restored.enqueue([{'peer':'receiver','sid':'small-sid'}],dict(self.frame,msg_id='small-after-recovered-compact')))

    def test_route_ttl_regular_gc_without_roster_recovers_real_capacity(self):
        cfg=self.c.load_config();cfg.update(max_store_bytes=1048576,ack_reserve_bytes=4096);self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        routes=[{'sessionId':'route-'+str(i),'pid':10000+i,'procStart':'synthetic','pidDomain':'synthetic','name':'r'*300} for i in range(4000)]
        outcome=self.source.remember_routes('receiver',routes)
        self.assertGreater(outcome['accepted'],0);self.assertGreater(outcome['rejected'],0)
        with self.assertRaises(self.c.ReliabilityError):
            self.source.enqueue([{'peer':'receiver','sid':'small-sid'}],dict(self.frame,msg_id='full-route-cache'))
        self.clock.now+=86401;self.source.gc()
        restored=self.c.ReliabilityStore(clock=self.clock)
        self.assertEqual(restored.cached_routes(),[],'offline GC must not need a new roster to retire TTL cache')
        self.assertIsNotNone(restored.enqueue([{'peer':'receiver','sid':'small-sid'}],dict(self.frame,msg_id='after-route-ttl')))

    def test_route_ttl_preserves_fixed_sid_dependency_and_replay_tombstones(self):
        initial=[dict(self.target,sessionId='fixed-old',name='shared-name'),dict(self.target,sessionId='unreferenced',name='old-cache')]
        self.source.remember_routes('receiver',initial)
        cfg=self.c.load_config();cfg['message_ttl']=1;self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        expired=self.source.enqueue([{'peer':'receiver','sid':'expired-target'}],dict(self.frame,msg_id='tombstone'))[0]
        envelope=self.source.outgoing_envelope(expired);item,created=self.receiver.accept(envelope,'sender','synthetic-incoming',self.origin)
        self.assertTrue(created)
        cfg['message_ttl']=86400;self.c.write_json_private(self.c.CONFIG_PATH,cfg)
        self.clock.now+=23*3600
        live=self.source.enqueue([{'peer':'receiver','sid':'fixed-old'}],dict(self.frame,msg_id='still-live'),self.owner)[0]
        self.clock.now+=3602;self.source.gc();self.receiver.gc()
        restored=self.c.ReliabilityStore(clock=self.clock)
        self.assertEqual([r['sid'] for r in restored.cached_routes()],['fixed-old'])
        for store,rows in ((restored,restored.outbox_rows()),(self.receiver,self.receiver.inbox_rows())):
            tomb=next(r for r in rows if r['native_id']=='tombstone')
            self.assertIsNone(tomb['frame']);self.assertGreater(tomb['retain_until'],self.clock.now)
        restored.remember_routes('receiver',[dict(self.target,sessionId='new-sid',name='shared-name')])
        queued=next(r for r in restored.outbox_rows() if r['id']==live['id'])
        self.assertEqual((queued['target_sid'],queued['fingerprint'],queued['frame']),('fixed-old',live['fingerprint'],live['frame']))
        self.assertEqual({r['sid'] for r in restored.cached_routes()},{'fixed-old','new-sid'})
        with self.assertRaises(self.c.ReliabilityError):self.receiver.accept(envelope,'sender','synthetic-incoming',self.origin)
        self.assertEqual(len(self.receiver.inbox_rows()),1)


if __name__ == "__main__":
    unittest.main()
