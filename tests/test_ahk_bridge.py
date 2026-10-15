import importlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from src.ahk_runtime import AhkRuntime

FAKE = r"""
import base64, sys, time
for line in sys.stdin:
    f=line.rstrip('\n').split('\t'); op=f[2]
    message=f[7]
    if op=='probe': message=base64.b64encode(b'2.0.30\t64').decode()
    if op=='shutdown': break
    if op=='key': time.sleep(5)
    rid=str(int(f[1])+1) if op=='text' and base64.b64decode(f[7])==b'wrong-id' else f[1]
    print('1\t'+rid+'\tok\t'+op+'\t'+('not_started' if op=='probe' else 'posted')+'\t'+message, flush=True)
"""

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.m=importlib.import_module('src.ahk_bridge')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.script=Path(self.tmp.name)/'fake worker.py'; self.script.write_text(FAKE)
        self.bridge=self.m.AhkBridge(AhkRuntime(Path(sys.executable),'2.0.30',64))
        self.override=patch.object(self.bridge,'_command',return_value=[sys.executable,'-u',str(self.script)])
        self.override.start(); self.addCleanup(self.override.stop); self.addCleanup(self.bridge.close)
        self.target=self.m.AhkTarget(99,101)

    def test_lazy_start_and_utf8_literal_round_trip(self):
        self.assertIsNone(self.bridge._process)
        self.bridge.start()
        reply=self.bridge.request('text',self.target,None,'Ã© {Enter} ^!+# river fish',lambda:False)
        self.assertEqual(reply.message,'Ã© {Enter} ^!+# river fish')
        self.assertEqual(reply.input_state,'posted')
        self.assertEqual(reply.request_id,2)

    def test_text_rejects_linebreak_and_control_characters(self):
        self.bridge.start()
        for text in ('one\ntwo','one\rtwo','nul\0','tab\t'):
            with self.assertRaises(ValueError): self.bridge.request('text',self.target,None,text,lambda:False)

    def test_protocol_rejects_malformed_stale_or_wrong_id(self):
        self.bridge.start()
        with self.assertRaises(self.m.AhkBridgeError): self.bridge.request('text',self.target,None,'wrong-id',lambda:False)
        with self.assertRaises(self.m.AhkBridgeError): self.bridge.request('text',self.target,None,'no replay',lambda:False)

    def test_timeout_never_replays(self):
        self.bridge.start(); process=self.bridge._process
        with patch.object(self.m,'PRIMITIVE_TIMEOUT',0.12):
            with self.assertRaises(self.m.AhkBridgeError) as error:
                self.bridge.request('key',self.target,None,'Enter',lambda:False)
        self.assertEqual(error.exception.input_state,'unknown')
        self.assertIsNone(self.bridge._process)
        self.assertIsNotNone(process.poll())

    def test_cancel_does_not_wait_on_request_lock(self):
        self.bridge.start(); process=self.bridge._process; errors=[]
        def request():
            try: self.bridge.request('key',self.target,None,'Enter',lambda:False)
            except self.m.AhkBridgeError as error: errors.append(error)
        thread=threading.Thread(target=request); thread.start(); time.sleep(.05)
        self.bridge.cancel_input(); thread.join(.5)
        self.assertFalse(thread.is_alive()); self.assertIsNotNone(process.poll())
        self.assertEqual(errors[0].input_state,'unknown')
        with self.assertRaises(self.m.AhkBridgeError): self.bridge.request('text',self.target,None,'late',lambda:False)

    def test_cancelled_request_does_not_post(self):
        self.bridge.start()
        with self.assertRaises(self.m.AhkBridgeError) as error:
            self.bridge.request('text',self.target,None,'late',lambda:True)
        self.assertEqual(error.exception.input_state,'not_started')

    def test_close_is_idempotent_and_restart_is_explicit(self):
        self.bridge.start(); first=self.bridge._process
        self.bridge.close(); self.bridge.close()
        self.assertIsNotNone(first.poll())
        self.bridge.start(); self.assertIsNot(self.bridge._process,first)
        self.assertEqual(self.bridge.request('text',self.target,None,'fresh',lambda:False).request_id,2)

    def test_protocol_parser_rejects_bad_fields(self):
        for line in ('nonsense','2\t1\tok\tx\tposted\t','1\t1\tmaybe\tx\tposted\t','1\t1\tok\tx\tbad\t','1\t1\tok\tx\tposted\t%%%'):
            with self.assertRaises(self.m.AhkBridgeError): self.m.parse_reply(line,1)

    def test_one_outstanding_request(self):
        self.bridge.start(); ids=[]; errors=[]
        def send(value):
            try: ids.append(self.bridge.request('text',self.target,None,value,lambda:False).request_id)
            except Exception as error: errors.append(error)
        threads=[threading.Thread(target=send,args=(str(i),)) for i in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2)
        self.assertEqual(errors,[])
        self.assertEqual(sorted(ids),list(range(2,10)))


    def test_failed_keyboard_cleanup_blocks_restart(self):
        self.bridge._cleanup_error = 'Could not reset Discord keyboard state; restart Discord before resuming.'
        with self.assertRaises(self.m.AhkBridgeError) as caught:
            self.bridge.start()
        self.assertEqual(caught.exception.code,'cleanup')
        self.assertIsNone(self.bridge._process)

    def test_cleanup_in_progress_blocks_restart(self):
        self.bridge._cleanup_pending = True
        with self.assertRaises(self.m.AhkBridgeError):
            self.bridge.start()
        self.assertIsNone(self.bridge._process)
