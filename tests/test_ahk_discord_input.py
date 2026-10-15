import importlib
import unittest
from unittest.mock import Mock, patch
from src.ahk_bridge import AhkTarget, AhkBridgeError
from src.automation_models import TransportError, NotDispatched

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.m=importlib.import_module('src.ahk_discord_input')
        self.d=self.m.AhkDiscordDriver.__new__(self.m.AhkDiscordDriver)
        self.d.window=99; self.d.channel='Virtual Fisher'; self.d.title=None
        self.d.bridge=Mock(); self.d.api=Mock()
        self.d.api.GetAsyncKeyState.return_value=0
        self.d.api.IsIconic.return_value=False
        self.d.api.GetForegroundWindow.return_value=123
        self.d._target_window=lambda:(99,'Virtual Fisher - Discord')
        self.d._window_rect=lambda:(-1920,100,-920,1100)
        self.d._client_bounds=lambda:(984,970)
        self.d._screen_to_client=lambda x,y:(x+1912,y-130)
        self.d._renderers=lambda:[101]
        self.d._pid=lambda hwnd:8
        self.d._frame_geometry=self.d._geometry()
        for name in ('SetCursorPos','GetCursorPos','SendInput'):
            getattr(self.d.api,name).side_effect=AssertionError('Global input used')

    def test_inactive_target_does_not_lie_about_foreground(self):
        self.assertFalse(self.d.is_foreground())
        self.d._guard(lambda:False)
        self.assertFalse(self.d.requires_foreground)
        self.d.api.GetForegroundWindow.return_value=456
        self.d._text('one',lambda:False)
        self.d.api.GetForegroundWindow.return_value=99
        self.d._text('two',lambda:False)

    def test_all_injection_paths_use_bridge(self):
        self.d._click((660,810),lambda:False)
        self.d._text('multiword {Enter}',lambda:False)
        self.d._key(13,lambda:False)
        self.d._clear(lambda:False)
        self.d.click((660,810),lambda:False)
        calls=self.d.bridge.request.call_args_list
        self.assertEqual([c.args[0] for c in calls],['target','text','key','key','select_all','key','click'])
        self.assertEqual(calls[0].args[2],(652,780))

    def test_renderer_reselected_after_restore(self):
        self.d._text('one',lambda:False)
        self.d._renderers=lambda:[202]
        self.d._frame_geometry=self.d._geometry()
        self.d._text('two',lambda:False)
        self.assertEqual(self.d.bridge.request.call_args.args[1],AhkTarget(99,202))
        for renderers in ([],[202,303]):
            self.d._renderers=lambda:renderers
            with self.assertRaises(TransportError): self.d._guard(lambda:False)

    def test_window_point_converts_through_screen_to_client(self):
        self.assertEqual(self.d._client_point((660,810)),(652,780))
        with self.assertRaises(TransportError): self.d._client_point((2,1))

    def test_move_resize_and_renderer_change_refuse_stale_geometry(self):
        for change in ('move','resize','renderer'):
            with self.subTest(change=change):
                original=self.d._window_rect; renderers=self.d._renderers
                self.d._frame_geometry=self.d._geometry()
                if change=='move': self.d._window_rect=lambda:(-1800,100,-800,1100)
                if change=='resize': self.d._window_rect=lambda:(-1920,100,-900,1100)
                if change=='renderer': self.d._renderers=lambda:[404]
                self.d.bridge.reset_mock()
                with self.assertRaises(TransportError): self.d._click((660,810),lambda:False)
                self.d.bridge.request.assert_not_called()
                self.d._frame_geometry=self.d._geometry()
                self.d._click((660,810),lambda:False)
                self.d._window_rect=original; self.d._renderers=renderers

    def test_minimized_foreign_process_and_stop_refuse(self):
        self.d.api.IsIconic.return_value=True
        with self.assertRaises(TransportError): self.d._guard(lambda:False)
        self.d.api.IsIconic.return_value=False
        with self.assertRaises(TransportError): self.d._guard(lambda:True)
        self.d._target_window=lambda:None
        with self.assertRaises(TransportError): self.d._guard(lambda:False)

    def test_literal_failure_maps_to_transport_error(self):
        self.d.bridge.request.side_effect=AhkBridgeError('lost ack')
        with self.assertRaises(TransportError): self.d._text('draft',lambda:False)

    def test_primitive_lifecycle_hooks(self):
        self.d.enable_input(); self.d.cancel_input(); self.d.close()
        self.d.bridge.start.assert_called_once(); self.d.bridge.cancel_input.assert_called_once(); self.d.bridge.close.assert_called_once()

    def test_title_changed_between_primitives_refuses_input(self):
        self.d.title='Virtual Fisher - Discord'
        self.d._target_window=lambda:(99,'Other DM - Discord')
        with self.assertRaises(TransportError): self.d._text('draft',lambda:False)
        self.d.bridge.request.assert_not_called()


class ComposerRecoveryTests(unittest.TestCase):
    def driver(self, frames):
        from test_discord_input import NativeDispatchTests
        from src.ahk_discord_input import AhkDiscordDriver
        driver = NativeDispatchTests().driver(frames)
        driver.__class__ = AhkDiscordDriver
        driver._geometry = lambda: ('same window/client/renderer',)
        driver._composer_anchor = (driver._geometry(), (660,810))
        return driver

    @patch('src.discord_input.time.sleep')
    def test_unowned_leftover_is_cleared_then_read_command_is_typed(self, sleep):
        from test_discord_input import box
        from test_discord_input import NativeDispatchTests as Fixture
        driver = self.driver([{'boxes':[box('fish',800)]}, Fixture.EMPTY, {'boxes':[box('/profile',600),box('Virtual Fisher',620)]}, Fixture.EMPTY])
        driver.dispatch('/profile',lambda:False)
        self.assertEqual(driver.events[:2], [('click',(660,810)),('clear',None)])
        self.assertIn(('text','/profile'),driver.events)

    @patch('src.discord_input.time.sleep')
    def test_unowned_leftover_is_cleared_before_action_options(self, sleep):
        from test_discord_input import box
        from test_discord_input import NativeDispatchTests as Fixture
        driver = self.driver([{'boxes':[box('leftover',800)]}, Fixture.EMPTY,
                              {'boxes':[box('/buy luxury yacht',800)]}, Fixture.EMPTY])
        driver.dispatch_action('/buy luxury yacht',None,lambda:False)
        self.assertEqual(driver.events[:2], [('click',(660,810)),('clear',None)])
        self.assertIn(('text','/buy luxury yacht'),driver.events)

    def test_failed_clear_never_types_or_submits(self):
        from test_discord_input import box
        driver = self.driver([{'boxes':[box('leftover',800)]}])
        with self.assertRaises(NotDispatched):
            driver.dispatch('/profile',lambda:False)
        self.assertEqual(driver.events,[('click',(660,810)),('clear',None)])

    def test_stale_or_unknown_composer_location_is_never_used(self):
        from test_discord_input import box
        for anchor in (None, (('old geometry',),(660,810))):
            driver = self.driver([{'boxes':[box('leftover',800)]}])
            driver._composer_anchor = anchor
            with self.assertRaises(NotDispatched):
                driver.dispatch('/profile',lambda:False)
            self.assertEqual(driver.events,[])

    def test_verified_empty_frame_remembers_composer_and_geometry(self):
        from test_discord_input import box
        from src.ahk_discord_input import AhkDiscordDriver
        from unittest.mock import patch
        driver = AhkDiscordDriver.__new__(AhkDiscordDriver)
        driver.channel = 'Virtual Fisher'
        driver._geometry = lambda: ('fresh geometry',)
        frame = {'boxes':[box('Message @Virtual Fisher',800)], '_ahk_geometry':driver._geometry()}
        with patch('src.discord_input.WindowsDiscordDriver._frame',return_value=frame):
            driver._frame(lambda:False)
        self.assertEqual(driver._composer_anchor,(driver._geometry(),(660,810)))


class HeldModifierTests(unittest.TestCase):
    setUp = AdapterTests.setUp
    @patch('src.ahk_discord_input.time.sleep')
    def test_shift_held_in_another_app_defers_enter_without_sending(self, sleep):
        down = [True]
        self.d.api.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if down[0] and vk == 0xA0 else 0
        self.d._frame = Mock()
        def release(_):
            self.d.bridge.request.assert_not_called()
            down[0] = False
        sleep.side_effect = release
        self.d._key(13,lambda:False)
        self.d._frame.assert_called_once()
        self.d.bridge.request.assert_called_once()
        self.assertEqual(self.d.bridge.request.call_args.args[3],'Enter')

    @patch('src.ahk_discord_input.time.sleep')
    def test_all_sided_modifiers_defer_text(self, sleep):
        for vk in (0xA0,0xA1,0xA2,0xA3,0xA4,0xA5,0x5B,0x5C):
            with self.subTest(vk=vk):
                down = [True]
                self.d.bridge.reset_mock()
                self.d.api.GetAsyncKeyState.side_effect = lambda key: 0x8000 if down[0] and key == vk else 0
                self.d._frame = Mock()
                def release(_):
                    self.d.bridge.request.assert_not_called()
                    down[0] = False
                sleep.side_effect = release
                self.d._text('/fish',lambda:False)
                self.d.bridge.request.assert_called_once()

    @patch('src.ahk_discord_input.time.sleep')
    def test_stop_while_shift_held_does_not_send(self, sleep):
        stopped = [False]
        self.d.api.GetAsyncKeyState.return_value=0x8000
        sleep.side_effect=lambda _:stopped.__setitem__(0,True)
        with self.assertRaises(TransportError):
            self.d._key(13,lambda:stopped[0])
        self.d.bridge.request.assert_not_called()

    @patch('src.ahk_discord_input.time.sleep')
    def test_worker_modifier_race_is_retried_only_when_definitely_not_started(self, sleep):
        self.d._frame=Mock()
        self.d.bridge.request.side_effect=[AhkBridgeError('Held Shift','not_started','modifiers'),None]
        self.d._key(13,lambda:False)
        self.assertEqual(self.d.bridge.request.call_count,2)
        self.d._frame.assert_called_once()


    @patch('src.ahk_discord_input.time.sleep')
    def test_geometry_change_during_modifier_wait_refuses_stale_click(self, sleep):
        down = [True]
        self.d.api.GetAsyncKeyState.side_effect=lambda vk:0x8000 if down[0] and vk==0xA1 else 0
        def release(_):
            down[0]=False
            self.d._window_rect=lambda:(-1800,100,-800,1100)
        sleep.side_effect=release
        with self.assertRaises(TransportError):
            self.d._click((660,810),lambda:False)
        self.d.bridge.request.assert_not_called()

    @patch('src.ahk_discord_input.time.sleep')
    def test_recheck_after_wait_refuses_changed_channel_or_verification(self, sleep):
        down=[True]
        self.d.api.GetAsyncKeyState.side_effect=lambda vk:0x8000 if down[0] and vk==0xA0 else 0
        sleep.side_effect=lambda _:down.__setitem__(0,False)
        self.d._frame=Mock(side_effect=TransportError('Target changed or verification prompt'))
        with self.assertRaises(TransportError):
            self.d._key(13,lambda:False)
        self.d.bridge.request.assert_not_called()

    def test_uncertain_modifier_error_is_never_replayed(self):
        self.d.bridge.request.side_effect=AhkBridgeError('uncertain','unknown','modifiers')
        with self.assertRaises(TransportError):
            self.d._key(13,lambda:False)
        self.d.bridge.request.assert_called_once()


    @patch('src.ahk_discord_input.time.sleep')
    def test_resize_during_successful_refresh_never_reuses_old_click(self, sleep):
        down=[True]
        self.d.api.GetAsyncKeyState.side_effect=lambda vk:0x8000 if down[0] and vk==0xA0 else 0
        sleep.side_effect=lambda _:down.__setitem__(0,False)
        def resize(*args):
            self.d._client_bounds=lambda:(1200,970)
            self.d._frame_geometry=self.d._geometry()
        self.d._frame=Mock(side_effect=resize)
        with self.assertRaises(TransportError):
            self.d._click((660,810),lambda:False)
        self.d.bridge.request.assert_not_called()
