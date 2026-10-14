import os
from pathlib import Path
import unittest
from src.ahk_runtime import resolve_ahk

@unittest.skipUnless(os.environ.get('AVF_TEST_AHK_EXE'), 'Opt-in real AHK worker tests')
class WorkerTests(unittest.TestCase):
    def setUp(self):
        from src.ahk_bridge import AhkBridge
        self.bridge=AhkBridge(resolve_ahk(Path(os.environ['AVF_TEST_AHK_EXE'])))
        self.addCleanup(self.bridge.close)
        self.bridge.start()

    def test_real_worker_probe_and_utf8_errors(self):
        reply=self.bridge.request('probe',None,None,'',lambda:False)
        self.assertEqual(reply.message,'2.0.30\t64')
        self.assertEqual(reply.input_state,'not_started')

    def test_real_worker_rejects_unknown_operation_and_invalid_target(self):
        from src.ahk_bridge import AhkBridgeError, AhkTarget
        with self.assertRaises(ValueError): self.bridge.request('eval',None,None,'',lambda:False)
        with self.assertRaises(AhkBridgeError) as error:
            self.bridge.request('text',AhkTarget(0,0),None,'literal é',lambda:False)
        self.assertEqual(error.exception.input_state,'not_started')

    def test_real_worker_exits_on_eof(self):
        process=self.bridge._process
        process.stdin.close()
        self.assertEqual(process.wait(timeout=2),0)

    def test_real_worker_empty_success_reply_and_shutdown(self):
        # Successful input acknowledgements use this same empty message payload.
        # Shutdown exercises it without targeting a window or sending any input.
        process = self.bridge._process
        reply = self.bridge.request('shutdown',None,None,'',lambda:False)
        self.assertEqual((reply.status,reply.code,reply.input_state,reply.message),
                         ('ok','shutdown','not_started',''))
        self.assertEqual(process.wait(timeout=2),0)
