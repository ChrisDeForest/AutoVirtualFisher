"""Regression checks for fatal worker failures without sending live input."""
import unittest
from unittest.mock import patch
from src.ahk_bridge import AhkBridgeError
from src.automation_models import CapabilityError, NotDispatched, TransportError
from tests import test_ahk_discord_input as adapter_tests
from tests.test_discord_input import box
from tests import test_automation_controller as controller_tests

class WorkerFailureTests(unittest.TestCase):
    def driver(self):
        fixture = adapter_tests.AdapterTests()
        fixture.setUp()
        driver = fixture.d
        frames = iter([{'boxes':[box('Message @Virtual Fisher',800)]}, {'boxes':[box('/profile',600),box('Virtual Fisher',620)]}, {'boxes':[box('Message @Virtual Fisher',800)]}])
        driver._frame = lambda cancelled, frame=None, deep=False: next(frames)
        return driver

    @patch('src.discord_input.time.sleep')
    def test_dead_worker_before_enter_is_terminal_and_unsent(self, sleep):
        for code in ('ipc', 'protocol'):
            driver = self.driver()
            driver.bridge.request.side_effect = AhkBridgeError('worker died', code=code)
            with self.assertRaises(CapabilityError) as caught:
                driver.dispatch('/profile', lambda: False)
            self.assertNotIsInstance(caught.exception, NotDispatched)
            self.assertIn('restart', str(caught.exception).lower())

    @patch('src.discord_input.time.sleep')
    def test_dead_worker_at_enter_remains_uncertain(self, sleep):
        driver = self.driver()
        def request(operation, *args):
            if operation == 'key': raise AhkBridgeError('lost Enter acknowledgement')
        driver.bridge.request.side_effect = request
        with self.assertRaises(TransportError): driver.dispatch('/profile', lambda: False)

    def test_terminal_unsent_failure_stops_without_retries_or_uncertainty(self):
        fixture = controller_tests.ControllerTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        controller = fixture.make()
        attempts = []
        def send(intent):
            attempts.append(intent)
            raise CapabilityError('AHK worker unavailable; restart the run.')
        fixture.sim.send = send
        controller.start()
        for second in range(60):
            fixture.clock.now = second
            controller.tick()
        self.assertEqual(len(attempts), 1)
        self.assertFalse(controller.running)
        self.assertEqual(controller.phase, 'FAILED')
        self.assertIn('restart', controller.reason)
        self.assertFalse(controller.unresolved)
        self.assertIsNone(controller.state.pending)

    @patch('src.discord_input.time.sleep')
    def test_transport_preserves_terminal_refusal_without_pending_outcome(self, sleep):
        from datetime import datetime, timezone
        from src.automation_discord import DiscordTransport
        from src.automation_models import ActionIntent
        from tests.test_automation_discord import Driver
        driver = self.driver()
        driver.bridge.request.side_effect = AhkBridgeError('worker died')
        capture_driver = Driver()
        capture_driver.dispatch = driver.dispatch
        transport = DiscordTransport(capture_driver,42,'Virtual Fisher','epicmouse','epicmouse9925')
        transport.enable()
        intent = ActionIntent('worker-failed','refresh_profile',transport.context_id,
                              datetime.now(timezone.utc).isoformat(),'profile')
        with self.assertRaises(CapabilityError) as caught: transport.send(intent)
        self.assertNotIsInstance(caught.exception, NotDispatched)
        self.assertIsNone(transport.pending)
        self.assertIsNone(transport.baseline)
        self.assertIsNone(transport.blocked)

    @patch('src.discord_input.time.sleep')
    def test_action_dead_worker_before_enter_is_terminal(self, sleep):
        driver = self.driver()
        driver.bridge.request.side_effect = AhkBridgeError('worker died')
        with self.assertRaises(CapabilityError) as caught:
            driver.dispatch_action('/buy magic bait',500,lambda:False)
        self.assertNotIsInstance(caught.exception, NotDispatched)

    @patch('src.discord_input.time.sleep')
    def test_worker_target_refusal_remains_retryable(self, sleep):
        driver = self.driver()
        driver.bridge.request.side_effect = AhkBridgeError('target minimized','not_started','worker')
        with self.assertRaises(NotDispatched): driver.dispatch('/profile',lambda:False)
