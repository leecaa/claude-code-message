"""Crash/concurrent-process coverage. Local synthetic state and sockets only."""
import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_ccm import FakeSession, load, HERE


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ);env.start();self.addCleanup(env.stop)
        home=tempfile.TemporaryDirectory();self.addCleanup(home.cleanup)
        self.home=home.name;self.ccm=load(self.home);self.store=self.ccm.ReliabilityStore()
        self.frame={"msgV":1,"msg_id":"20000000-0000-4000-8000-000000000001","type":"user","priority":"next",
                    "message":{"role":"user","content":"synthetic crash recovery"}}
        row=self.store.enqueue([{"peer":"receiver","sid":"target-sid"}],self.frame)[0]
        self.message=self.store.outgoing_envelope(row)
        self.children=[];self.addCleanup(self.drain)

    def drain(self):
        for child in self.children:
            if child.poll() is None:child.terminate()
            try:child.communicate(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.communicate()

    def child(self, operation):
        script="""
import json,os,sys
sys.path.insert(0,sys.argv[1])
from test_ccm import load
c=load(sys.argv[2]);store=c.ReliabilityStore(directory=os.path.join(sys.argv[2],'receiver'))
message=json.loads(sys.argv[3]);identity=(message['origin_node_id'],message['store_incarnation'])
operation=sys.argv[4]
if operation=='accept-concurrent':
    sys.stdin.readline()
item,created=store.accept(message,'sender','synthetic-link',identity)
key=(item['origin_node'],item['origin_inc'],item['id'])
if operation.startswith('dispatch'):
    target=json.loads(sys.argv[5])
    claim=store.claim_native(key,target,explicit=True)
    if operation in ('dispatch-write','dispatch-write-commit'):
        outcome=c.deliver_local(target['messagingSocketPath'],target['pid'],message['frame'],expected_sid=target['sessionId'],expected_generation=c.generation(target),structured=True,prewrite_check=lambda:store.prewrite_allowed(key,claim['dispatch_owner']))
        assert outcome['state']=='socket_written_unconfirmed'
    if operation=='dispatch-write-commit':
        store.native_finished(key,claim['dispatch_owner'],{'state':'socket_written_unconfirmed'})
    print(json.dumps({'created':created,'state':'socket_written_unconfirmed' if operation=='dispatch-write-commit' else 'native_dispatching','pid':os.getpid()}),flush=True)
    sys.stdin.readline()
else:
    print(json.dumps({'created':created,'id':item['id']}),flush=True)
    if operation=='accept-crash':sys.stdin.readline()
"""
        target=getattr(self,'target',None)
        record=self.ccm.read_sessions()[0] if target else {"pid":os.getpid(),"sessionId":"target-sid","procStart":self.ccm.proc_start(os.getpid()),"pidDomain":self.ccm.pid_domain(),"messagingSocketPath":"/synthetic/socket"}
        child=subprocess.Popen([sys.executable,'-B','-c',script,HERE,self.home,json.dumps(self.message),operation,json.dumps(record)],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.children.append(child)
        return child

    def line(self,child):
        ready,_,_=select.select([child.stdout],[],[],5)
        self.assertTrue(ready,'synthetic child did not reach durable boundary')
        line=child.stdout.readline()
        if not line:
            self.fail(child.stderr.read())
        return json.loads(line)

    def kill_owned(self,child):
        self.assertIsNone(child.poll())
        child.kill();child.communicate(timeout=5)
        self.assertEqual(child.returncode,-9)

    def receiver(self):
        return self.ccm.ReliabilityStore(directory=os.path.join(self.home,'receiver'))

    def test_two_processes_accept_one_inbox_and_one_receipt(self):
        children=[self.child('accept-concurrent') for _ in range(2)]
        for child in children:child.stdin.write('go\n');child.stdin.flush()
        rows=[self.line(child) for child in children]
        for child in children:
            child.communicate(timeout=5);self.assertEqual(child.returncode,0)
        self.assertEqual(sorted(row['created'] for row in rows),[False,True])
        receiver=self.receiver()
        self.assertEqual(len(receiver.inbox_rows()),1)
        self.assertEqual(len(receiver.receipt_rows()),1)

    def test_crash_after_inbox_commit_before_ack_keeps_dedup(self):
        child=self.child('accept-crash');self.assertTrue(self.line(child)['created']);self.kill_owned(child)
        receiver=self.receiver();item,created=receiver.accept(self.message,'sender','synthetic-link',(self.message['origin_node_id'],self.message['store_incarnation']))
        self.assertFalse(created)
        self.assertEqual(len(receiver.inbox_rows()),1)
        self.assertEqual(len(receiver.receipt_rows()),1)

    def test_crash_after_dispatch_cas_before_io_is_unknown(self):
        child=self.child('dispatch-before');self.assertEqual(self.line(child)['state'],'native_dispatching');self.kill_owned(child)
        receiver=self.receiver();receiver.recover_dispatches();item=receiver.inbox_rows()[0]
        self.assertEqual(item['native_state'],'handoff_unknown')
        target={"pid":os.getpid(),"sessionId":"target-sid","procStart":self.ccm.proc_start(os.getpid()),"pidDomain":self.ccm.pid_domain()}
        self.assertIsNone(receiver.claim_native((item['origin_node'],item['origin_inc'],item['id']),target,explicit=True))

    def correlated_read_fact(self,receiver,item):
        peer=(receiver.node_id,receiver.incarnation)
        self.store.reserve_outgoing(item['id'],'synthetic-link',peer,'durable',explicit=True)
        handoff=next(r for r in receiver.receipt_rows() if r['kind']=='native_handoff')
        self.assertTrue(self.store.received_receipt({'id':handoff['id'],'delivery_id':item['id'],'kind':'native_handoff','payload':json.loads(handoff['payload'])},peer))
        target=self.ccm.read_sessions()[0]
        self.assertTrue(receiver.acknowledge(item['id'],target))
        read=next(r for r in receiver.receipt_rows() if r['kind']=='receiver_acknowledged')
        self.assertTrue(self.store.received_receipt({'id':read['id'],'delivery_id':item['id'],'kind':'receiver_acknowledged','payload':json.loads(read['payload'])},peer))
        self.assertEqual(self.store.outbox_rows()[0]['acknowledged'],1)

    def test_crash_after_native_state_commit_before_control_send_recovers_read(self):
        self.target=FakeSession(self.ccm,'synthetic-target','target-sid');self.addCleanup(self.target.close)
        child=self.child('dispatch-write-commit');self.assertEqual(self.line(child)['state'],'socket_written_unconfirmed');self.kill_owned(child)
        receiver=self.receiver();item=receiver.inbox_rows()[0]
        self.assertEqual(item['native_state'],'socket_written_unconfirmed')
        self.assertEqual(sum(r['kind']=='native_handoff' for r in receiver.receipt_rows()),1)
        self.correlated_read_fact(receiver,item)
        self.assertEqual(len(self.target.got),1)

    def test_crash_after_actual_synthetic_uds_write_never_reinjects(self):
        self.target=FakeSession(self.ccm,'synthetic-target','target-sid');self.addCleanup(self.target.close)
        child=self.child('dispatch-write');self.assertEqual(self.line(child)['state'],'native_dispatching');self.kill_owned(child)
        deadline=time.monotonic()+2
        while not self.target.got and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(len(self.target.got),1)
        receiver=self.receiver();receiver.recover_dispatches();item=receiver.inbox_rows()[0]
        target=self.ccm.read_sessions()[0]
        self.assertEqual(item['native_state'],'handoff_unknown')
        self.assertIsNone(receiver.claim_native((item['origin_node'],item['origin_inc'],item['id']),target,explicit=True))
        self.assertEqual(sum(r['kind']=='native_handoff' for r in receiver.receipt_rows()),1)
        self.correlated_read_fact(receiver,item)
        self.assertEqual(len(self.target.got),1)


if __name__=='__main__':unittest.main()
