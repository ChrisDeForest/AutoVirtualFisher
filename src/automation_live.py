"""Live runner with optional limits. Without --live this only inspects the configured target."""
import argparse
from pathlib import Path
import time

from .automation_controller import AutomationController
from .automation_discord import DiscordTransport
from .automation_journal import AutomationJournal
from .automation_models import AutomationConfig
from .discord_input import WindowsDiscordDriver
from .player_snapshots import account_key
from .player_snapshots import write_json_atomically
from .window_capture import find_discord_window
from .run_output import RunOutput


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Inspect Discord or fish until stopped, with optional limits')
    parser.add_argument('--channel', required=True, help='Exact VF DM label (server channels are not supported yet)')
    parser.add_argument('--account', required=True, help='VF profile display name')
    parser.add_argument('--username', required=True, help='Discord username shown in fishing replies')
    parser.add_argument('--window', type=int, help='Window handle; defaults to the detected Discord window')
    parser.add_argument('--live', action='store_true', help='Enable native input; default is inspection only')
    parser.add_argument('--trips', type=int, help='Optional limit of 1–25 confirmed trips')
    parser.add_argument('--seconds', type=int, help='Optional time limit of 1–900 seconds')
    parser.add_argument('--startup-delay', type=float, default=5, help='Time to foreground Discord after starting')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent / 'data' / 'automation_live')
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
    detected = find_discord_window() if args.window is None else None
    window = args.window or (detected[0] if detected else None)
    if window is None:
        output.say('Discord window not found. Open Discord and try again.')
        return
    driver = WindowsDiscordDriver(window, args.channel)
    transport = DiscordTransport(driver, window, args.channel, args.account, args.username)
    output.say(f'{args.account} — {args.channel}. Switch to Discord within {args.startup_delay:g} seconds.')
    time.sleep(args.startup_delay)
    if not args.live and not args.resolve:
        status = transport.inspect_context()
        output.say('Discord is ready. Inspection only; nothing sent.' if status.ok else 'Discord is not ready: ' + status.reason)
        return
    root = args.data_dir / account_key(args.account)
    config = AutomationConfig(args.account, args.username, transport.context_id,
                              max_trips=args.trips, max_seconds=args.seconds, mode='live')
    controller = AutomationController(transport, config, AutomationJournal(root / 'journal'),
                                      root / 'fishing', root / 'review', interval=0.5)
    try:
        if args.live and not args.resolve:
            target = transport.inspect_context()
            if not target.ok:
                output.say('Live run not started: ' + target.reason)
                return
            driver.title = transport.title
        controller.start()
        if args.resolve:
            resolved = controller.resolve(args.resolve, args.outcome)
            output.say('Action resolved.' if resolved else 'That action was not awaiting resolution.')
            return
        transport.enable()
        output.say('Starting. Keep Discord foreground. Ctrl+C stops the run.')
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
        output.update(controller.status())
        try:
            write_json_atomically(root / 'transport_diagnostics', controller.state.run_id,
                                  {'baseline': transport.baseline, 'last_frame': transport.last_frame,
                                   'last_lines': getattr(transport, 'last_lines', None),
                                   'blocked': transport.blocked, 'last_issue': transport.last_issue})
        except OSError as error:
            output.say(f'Could not save transport diagnostics: {error}')


if __name__ == '__main__':
    main()
