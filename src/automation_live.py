"""Live runner with optional limits. Without --live this only inspects the configured target."""
import argparse
import threading
from pathlib import Path
import time

from .automation_controller import AutomationController
from .automation_discord import DiscordTransport
from .automation_journal import AutomationJournal
from .automation_models import AutomationConfig
from .ahk_discord_input import AhkDiscordDriver
from .ahk_runtime import AhkSetupError, ready_ahk, resolve_ahk
from .player_snapshots import account_key, write_json_atomically
from .window_capture import find_discord_window
from .run_output import RunOutput
from .ocr_journal import OcrJournal

DEFAULT_LIVE_ROOT = Path(__file__).parent / 'data' / 'automation_live'


def live_problem(ahk_exe: Path | None = None) -> str:
    """Why live mode cannot run on this machine, or '' when it can."""
    import sys
    from .screenshot_ocr import ocr_available
    from .window_capture import capture_supported
    if sys.platform != 'win32' or not capture_supported():
        return 'Live mode needs Windows.'
    if not ocr_available():
        return ('Live mode needs the Windows OCR packages. Start the server with the project\'s .venv Python '
                '(see README).')
    try:
        ready_ahk(ahk_exe)
    except AhkSetupError as error:
        return str(error)
    return ''


def build_live(channel, account, username, root=DEFAULT_LIVE_ROOT, max_trips=None, max_seconds=None,
               interval=0.5, window=None, ahk_exe: Path | None = None):
    """(controller, transport) for a live run against the Discord window. Raises RuntimeError when no Discord
    window is found. Nothing is sent until the transport is enabled and the controller started."""
    if window is None:
        detected = find_discord_window()
        if detected is None:
            raise RuntimeError('Discord window not found. Open Discord and try again.')
        window = detected[0]
    runtime = resolve_ahk(ahk_exe)
    driver = AhkDiscordDriver(window, channel, runtime)
    transport = DiscordTransport(driver, window, channel, account, username)
    folder = Path(root) / account_key(account)
    config = AutomationConfig(account, username, transport.context_id,
                              max_trips=max_trips, max_seconds=max_seconds, mode='live')
    controller = AutomationController(transport, config, AutomationJournal(folder / 'journal'),
                                      folder / 'fishing', folder / 'review', interval=interval, alert=show_alert,
                                      state_path=folder / 'account_state.json')
    def ocr_context():
        pending = controller.state.pending
        return {'run_id': controller.state.run_id, 'action_id': pending.action_id if pending else None}
    driver.ocr_journal = OcrJournal(folder / 'journal', ocr_context)
    return controller, transport


def show_alert(message, title='AutoVirtualFisher: captcha'):
    """A chime and topmost Windows popup, each on its own thread so neither holds up the run."""
    def chime():
        try:
            import winsound
            for frequency, duration in ((880, 180), (1175, 220), (880, 180)):
                winsound.Beep(frequency, duration)
        except (ImportError, OSError, RuntimeError):
            # Audio being unavailable must not interfere with the captcha popup or pause.
            pass

    def show():
        import ctypes
        # MB_OK | MB_ICONWARNING | MB_SYSTEMMODAL | MB_SETFOREGROUND | MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x0 | 0x30 | 0x1000 | 0x10000 | 0x40000)
    threading.Thread(target=chime, daemon=True).start()
    threading.Thread(target=show, daemon=True).start()


def save_diagnostics(controller, transport, root=DEFAULT_LIVE_ROOT) -> str:
    """Write what the transport last saw; returns a problem, or '' when saved."""
    from . import storage_limits as sl
    from .retention import RECORD_NAME, prune_files
    import re
    try:
        write_json_atomically(Path(root) / account_key(controller.config.account) / 'transport_diagnostics',
                              controller.state.run_id or 'unstarted',
                              {'baseline': transport.baseline, 'last_frame': transport.last_frame,
                               'last_lines': getattr(transport, 'last_lines', None),
                               'blocked': transport.blocked, 'last_issue': transport.last_issue})
        pattern = re.compile(r'(?:' + RECORD_NAME.pattern + r'|unstarted\.json)')
        prune_files(Path(root) / account_key(controller.config.account) / 'transport_diagnostics',
                    pattern, sl.DIAGNOSTIC_MAX_FILES, sl.DIAGNOSTIC_MAX_BYTES)
        return ''
    except OSError as error:
        return f'Could not save transport diagnostics: {error}'


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Inspect Discord or fish until stopped, with optional limits')
    parser.add_argument('--channel', required=True, help='Exact VF DM label (server channels are not supported yet)')
    parser.add_argument('--account', required=True, help='VF profile display name')
    parser.add_argument('--username', required=True, help='Discord username shown in fishing replies')
    parser.add_argument('--ahk-exe', type=Path, help='Ordinary AutoHotkey v2 x64 executable override')
    parser.add_argument('--window', type=int, help='Window handle; defaults to the detected Discord window')
    parser.add_argument('--live', action='store_true', help='Enable targeted AHK input; default is inspection only')
    parser.add_argument('--trips', type=int, help='Optional limit of 1–25 confirmed trips')
    parser.add_argument('--seconds', type=int, help='Optional time limit of 1–900 seconds')
    parser.add_argument('--startup-delay', type=float, default=5, help='Preparation grace period before starting')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_LIVE_ROOT)
    parser.add_argument('--resolve', help='Resolve a previously uncertain action without starting a run')
    parser.add_argument('--outcome', choices=('happened', 'not_sent'))
    args = parser.parse_args(argv)
    if ((args.trips is not None and not 1 <= args.trips <= 25)
            or (args.seconds is not None and not 1 <= args.seconds <= 900)
            or not 0 <= args.startup_delay <= 60):
        parser.error('Trips must be 1–25; seconds 1–900; startup delay 0–60.')
    if bool(args.resolve) != bool(args.outcome):
        parser.error('--resolve and --outcome must be provided together.')
    return args


def main():
    args = parse_args()
    with RunOutput() as output:
        output.say(f'Run log: {output.path}')
        try:
            run(args, output)
        except Exception as error:
            output.say(f'Run failed: {error}')
            raise SystemExit(1) from None


def run(args, output):
    try:
        controller, transport = build_live(args.channel, args.account, args.username, args.data_dir,
                                           args.trips, args.seconds, window=args.window, ahk_exe=args.ahk_exe)
    except RuntimeError as error:
        output.say(str(error))
        return
    try:
        runtime = getattr(transport.driver, 'bridge', None)
        if runtime is not None:
            runtime = runtime.runtime
            output.say(f'AutoHotkey {runtime.version} x{runtime.bitness}: {runtime.executable}')
        output.say(f'{args.account} — {args.channel}. Starting after {args.startup_delay:g} seconds.')
        time.sleep(args.startup_delay)
        if not args.live and not args.resolve:
            status = transport.inspect_context()
            output.say('Discord is ready. Inspection only; nothing sent.' if status.ok else 'Discord is not ready: ' + status.reason)
            return
        if args.resolve:
            controller.start()
            resolved = controller.resolve(args.resolve, args.outcome)
            output.say('Action resolved.' if resolved else 'That action was not awaiting resolution.')
            return
        transport.enable()
        controller.start()
        output.say('Keep Discord open on the Virtual Fisher DM and not minimized. '
                   'You can use other applications while the run continues. Ctrl+C stops it.')
        while controller.running:
            controller.tick()
            status = controller.status()
            output.update(status)
            if status['phase'] in ('NEEDS_REVIEW', 'FAILED'):
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        output.say('Stopping...')
    finally:
        transport.stop()
        controller.stop()
        try:
            output.update(controller.status())
            problem = save_diagnostics(controller, transport, args.data_dir)
            if problem:
                output.say(problem)
        finally:
            transport.close()


if __name__ == '__main__':
    main()
