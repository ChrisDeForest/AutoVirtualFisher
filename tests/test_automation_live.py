import unittest
from unittest.mock import patch
import io

from src.automation_live import parse_args


class LiveArgumentsTests(unittest.TestCase):
    base = ['--channel', 'Virtual Fisher', '--account', 'sim', '--username', 'sim0001']

    def test_default_has_no_run_limits(self):
        args = parse_args(self.base + ['--live'])
        self.assertIsNone(args.trips)
        self.assertIsNone(args.seconds)

    def test_optional_limits_are_independent(self):
        args = parse_args(self.base + ['--trips', '1'])
        self.assertEqual(args.trips, 1)
        self.assertIsNone(args.seconds)
        args = parse_args(self.base + ['--seconds', '120'])
        self.assertIsNone(args.trips)
        self.assertEqual(args.seconds, 120)

    def test_invalid_limits_rejected(self):
        for options in (['--trips', '0'], ['--seconds', '-1'], ['--startup-delay', '61']):
            with self.subTest(options=options), patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                parse_args(self.base + options)


if __name__ == '__main__':
    unittest.main()


class AhkLiveIntegrationTests(unittest.TestCase):
    def test_cli_accepts_ahk_override(self):
        from pathlib import Path
        args=parse_args(LiveArgumentsTests.base+['--ahk-exe','C:/Program Files/AutoHotkey/v2/AutoHotkey64.exe'])
        self.assertEqual(args.ahk_exe,Path('C:/Program Files/AutoHotkey/v2/AutoHotkey64.exe'))

    def test_live_factory_defaults_to_ahk_without_starting_input(self):
        from pathlib import Path
        from unittest.mock import Mock
        from src.ahk_runtime import AhkRuntime
        from src import automation_live as live
        runtime=AhkRuntime(Path('C:/AHK/AutoHotkey64.exe'),'2.0.30',64)
        with patch.object(live,'resolve_ahk',return_value=runtime), patch.object(live,'AhkDiscordDriver') as driver, patch.object(live,'AutomationController'), patch.object(live,'AutomationJournal'):
            live.build_live('Virtual Fisher','epicmouse','epicmouse9925',root=Path('.uv-cache/factory-test'),window=99)
        driver.assert_called_once_with(99,'Virtual Fisher',runtime)
        driver.return_value.enable_input.assert_not_called()

    def test_inspection_disposes_without_enabling(self):
        from unittest.mock import Mock
        from src import automation_live as live
        args=parse_args(LiveArgumentsTests.base+['--startup-delay','0'])
        controller=Mock(); transport=Mock(); transport.inspect_context.return_value.ok=True
        with patch.object(live,'build_live',return_value=(controller,transport)), patch.object(live,'save_diagnostics',return_value=''):
            live.run(args,Mock())
        transport.enable.assert_not_called(); transport.close.assert_called_once()
        controller.start.assert_not_called()

    def test_input_enabled_before_controller_start_and_closed_on_failure(self):
        from unittest.mock import Mock
        from src import automation_live as live
        args=parse_args(LiveArgumentsTests.base+['--live','--startup-delay','0'])
        order=[]; controller=Mock(); transport=Mock()
        transport.enable.side_effect=lambda:order.append('enable')
        def start(): order.append('start'); raise RuntimeError('startup failed')
        controller.start.side_effect=start
        with patch.object(live,'build_live',return_value=(controller,transport)), patch.object(live,'save_diagnostics',return_value=''):
            with self.assertRaises(RuntimeError): live.run(args,Mock())
        self.assertEqual(order,['enable','start']); transport.close.assert_called_once()

    def test_missing_runtime_reports_setup_problem(self):
        from src import automation_live as live
        from src.ahk_runtime import AhkSetupError
        with patch('sys.platform','win32'), patch('src.window_capture.capture_supported',return_value=True), patch('src.screenshot_ocr.ocr_available',return_value=True), patch.object(live,'ready_ahk',side_effect=AhkSetupError('Install global AHK')):
            self.assertIn('Install global AHK',live.live_problem())
