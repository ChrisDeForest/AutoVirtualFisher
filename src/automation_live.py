"""Bounded live runner. Without --live this only inspects the configured target."""
import argparse
import json
from pathlib import Path
import time

from .automation_controller import AutomationController
from .automation_discord import DiscordTransport
from .automation_journal import AutomationJournal
from .automation_models import AutomationConfig
from .discord_input import WindowsDiscordDriver
from .player_snapshots import account_key
from .window_capture import find_discord_window


def main():
    parser = argparse.ArgumentParser(description='Inspect Discord or run a bounded live VF fishing cycle')
    parser.add_argument('--channel', required=True, help='Exact VF DM label (server channels are not supported yet)')
    parser.add_argument('--account', required=True, help='VF profile display name')
    parser.add_argument('--username', required=True, help='Discord username shown in fishing replies')
    parser.add_argument('--window', type=int, help='Window handle; defaults to the detected Discord window')
    parser.add_argument('--live', action='store_true', help='Enable native input; default is inspection only')
    parser.add_argument('--trips', type=int, default=1)
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--startup-delay', type=float, default=5, help='Time to foreground Discord after starting')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent / 'data' / 'automation_live')
    parser.add_argument('--resolve', help='Resolve a previously uncertain action without starting a run')
    parser.add_argument('--outcome', choices=('happened', 'not_sent'))
    args = parser.parse_args()
    if not 1 <= args.trips <= 25 or not 1 <= args.seconds <= 900 or not 0 <= args.startup_delay <= 60:
        parser.error('Trips must be 1–25; seconds 1–900; startup delay 0–60.')
    if bool(args.resolve) != bool(args.outcome):
        parser.error('--resolve and --outcome must be provided together.')
    detected = find_discord_window() if args.window is None else None
    window = args.window or (detected[0] if detected else None)
    if window is None:
        parser.error('Discord window not found.')
    driver = WindowsDiscordDriver(window, args.channel)
    transport = DiscordTransport(driver, window, args.channel, args.account, args.username)
    print(f'Target window {window}, channel {args.channel}. Foreground Discord now.', flush=True)
    time.sleep(args.startup_delay)
    if not args.live and not args.resolve:
        status = transport.inspect_context()
        print(json.dumps(dict(ok=status.ok, context_id=status.context_id, reason=status.reason), indent=2))
        return
    root = args.data_dir / account_key(args.account)
    config = AutomationConfig(args.account, args.username, transport.context_id,
                              max_trips=args.trips, max_seconds=args.seconds, mode='live')
    controller = AutomationController(transport, config, AutomationJournal(root / 'journal'),
                                      root / 'fishing', root / 'review', interval=0.5)
    previous = None
    try:
        if args.live and not args.resolve:
            target = transport.inspect_context()
            if not target.ok:
                print('Live run not started: ' + target.reason, flush=True)
                return
            driver.title = transport.title
        controller.start()
        if args.resolve:
            print('Resolved:', controller.resolve(args.resolve, args.outcome))
            return
        transport.enable()
        print('Live input enabled. Ctrl+C stops this runner. Do not manually invoke VF commands.', flush=True)
        while controller.running:
            controller.tick()
            status = controller.status()
            key = (status['phase'], status['pending'], status['trips'], tuple(status['unresolved']))
            if key != previous:
                print(json.dumps(status, indent=2), flush=True)
                previous = key
            if status['phase'] in ('NEEDS_REVIEW', 'FAILED'):
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print('Stopping.', flush=True)
    finally:
        transport.stop()
        controller.stop()
        print(json.dumps(controller.status(), indent=2), flush=True)


if __name__ == '__main__':
    main()
