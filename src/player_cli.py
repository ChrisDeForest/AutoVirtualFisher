"""Terminal menu and file-based entry point for the shared player parser."""
import argparse
import json
from pathlib import Path
import sys

from .player_snapshots import (DEFAULT_SNAPSHOT_ROOT, compare_latest, describe_changes,
                               list_accounts, load_history, save_snapshot, validate_full_state)
from .player_state import display_value, parse_responses

HISTORY_LIMIT = 10


def show(state):
    print('\nAutoVirtualFisher | Player logbook')
    for section in ('profile', 'buffs', 'prestige', 'pets', 'inventory'):
        print('\n' + section.title())
        for field, value in state[section].items():
            print(f'  {field.replace("_", " ").capitalize():22} {display_value(value)}')
    for warning in state['warnings']:
        print('  ! ' + warning)


def paste():
    print('Paste the response. Enter a single period on its own line to finish.')
    lines = []
    while (line := input()) != '.':
        lines.append(line)
    return '\n'.join(lines)


def format_history(account, history, warnings):
    lines = [f'Recent snapshots for {account}']
    lines += [f'  {item["captured_at"]}  {item.get("save_mode", "unknown"):9}  {item.get("validity", "unknown")}'
              for item in history] or ['  No snapshots saved for this account yet.']
    lines += ['  ! ' + warning for warning in warnings]
    return '\n'.join(lines)


def format_comparison(account, comparison):
    lines = [f'Progress for {account}']
    if comparison['status'] != 'ok':
        lines.append('  Another full snapshot is needed before progress can be compared.')
    else:
        lines.append(f'  From {comparison["older"]["captured_at"]} to {comparison["newer"]["captured_at"]}')
        lines += [f'  {label:22} {text or "Unknown"}' for label, text in describe_changes(comparison['changes'])]
    lines += ['  ! ' + warning for warning in comparison['warnings']]
    return '\n'.join(lines)


def save_message(result):
    """Return (ok, message) describing a save_snapshot() result."""
    snapshot = result.get('snapshot')
    if result['status'] == 'saved':
        kind = 'Snapshot' if snapshot['validity'] == 'full' else 'Partial snapshot'
        return True, f'{kind} saved for {snapshot["account_name"]}.'
    if result['status'] == 'unchanged':
        return True, f'Latest snapshot for {snapshot["account_name"]} is unchanged; nothing new was saved.'
    return False, f'Not saved: {result["reason"]}'


def save_state(state, root, account=None):
    """Save a full state with duplicate suppression, or a partial state manually."""
    full, _ = validate_full_state(state)
    try:
        result = save_snapshot(state, root, mode='automatic' if full else 'manual', account_name=account)
    except OSError as error:
        return False, f'Snapshot could not be saved: {error}'
    return save_message(result)


def run_files(parser, args):
    try:
        profile = args.profile.read_text(encoding='utf-8-sig') if args.profile else ''
        buffs = args.buffs.read_text(encoding='utf-8-sig') if args.buffs else ''
        prestige_shop = args.prestige_shop.read_text(encoding='utf-8-sig') if args.prestige_shop else ''
        pet = args.pet.read_text(encoding='utf-8-sig') if args.pet else ''
    except OSError as error:
        parser.error(str(error))
    state = parse_responses(profile, buffs, prestige_shop, pet)
    if args.save and not validate_full_state(state)[0] and not args.account:
        parser.error('this is a partial snapshot; pass --account NAME to save it')
    print(json.dumps(state, indent=2, ensure_ascii=False)) if args.json else show(state)
    if not args.save:
        return 0
    ok, message = save_state(state, args.snapshot_dir, args.account)
    print(message, file=sys.stderr if args.json or not ok else sys.stdout)
    return 0 if ok else 1


def run_query(args):
    if args.history:
        history, warnings = load_history(args.account, args.snapshot_dir, limit=HISTORY_LIMIT)
        if args.json:
            print(json.dumps({'account': args.account, 'snapshots': history, 'warnings': warnings},
                             indent=2, ensure_ascii=False))
        else:
            print(format_history(args.account, history, warnings))
    else:
        comparison = compare_latest(args.account, args.snapshot_dir)
        if args.json:
            print(json.dumps(comparison, indent=2, ensure_ascii=False))
        else:
            print(format_comparison(args.account, comparison))
    return 0


def choose_account(root, current):
    accounts, warnings = list_accounts(root)
    for warning in warnings:
        print('  ! ' + warning)
    for number, name in enumerate(accounts, 1):
        print(f'  [{number}] {name}')
    answer = input('Account number or new name (blank keeps current): ').strip()
    if not answer:
        return current
    if answer.isdigit() and 1 <= int(answer) <= len(accounts):
        return accounts[int(answer) - 1]
    return answer


def interactive(root):
    profile = buffs = prestige_shop = pet = ''
    account = None
    print('Player logbook — pasted responses only; no Discord connection.')
    print(f'Snapshots are saved in {root}; pasted text itself is never stored.')
    try:
        while True:
            print(f'\nAccount: {account or "from /profile"}')
            print('[1] Paste profile  [2] Paste buffs  [3] Paste prestige shop  [4] Paste pet  [5] View state  [6] View JSON')
            print('[a] Choose account  [s] Save snapshot  [h] History  [c] Compare latest  [q] Quit')
            choice = input('Choose: ').strip().lower()
            state = lambda: parse_responses(profile, buffs, prestige_shop, pet)
            if choice == 'q':
                return 0
            if choice == '1':
                profile = paste()
            elif choice == '2':
                buffs = paste()
            elif choice == '3':
                prestige_shop = paste()
            elif choice == '4':
                pet = paste()
            elif choice == '5':
                show(state())
            elif choice == '6':
                print(json.dumps(state(), indent=2, ensure_ascii=False))
            elif choice == 'a':
                account = choose_account(root, account)
            elif choice == 's':
                if not (profile or buffs or prestige_shop or pet).strip():
                    print('Paste a response before saving.')
                    continue
                try:
                    result = save_snapshot(state(), root, mode='manual', account_name=account)
                    print(save_message(result)[1])
                except OSError as error:
                    print(f'Snapshot could not be saved: {error}')
            elif choice in ('h', 'c'):
                name = account or state()['profile']['account_name']
                if not name:
                    print('Choose an account first.')
                elif choice == 'h':
                    print(format_history(name, *load_history(name, root, limit=HISTORY_LIMIT)))
                else:
                    print(format_comparison(name, compare_latest(name, root)))
            else:
                print('Choose 1-6, a, s, h, c, or q.')
    except (EOFError, KeyboardInterrupt):
        print('\nClosed logbook.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect pasted Virtual Fisher responses')
    parser.add_argument('--profile', type=Path, help='UTF-8 text file containing /profile output')
    parser.add_argument('--buffs', type=Path, help='UTF-8 text file containing /buffs output')
    parser.add_argument('--prestige-shop', type=Path, help='UTF-8 text file containing /prestige shop output')
    parser.add_argument('--pet', type=Path, help='UTF-8 text file containing /pet output')
    parser.add_argument('--json', action='store_true', help='Print machine-readable JSON')
    parser.add_argument('--save', action='store_true', help='Save the parsed result as a local snapshot')
    parser.add_argument('--account', help='Account for partial saves, --history, and --compare')
    query = parser.add_mutually_exclusive_group()
    query.add_argument('--history', action='store_true', help='List recent snapshots for --account')
    query.add_argument('--compare', action='store_true', help='Compare the latest two full snapshots for --account')
    parser.add_argument('--snapshot-dir', type=Path, default=DEFAULT_SNAPSHOT_ROOT,
                        help='Snapshot folder (default: %(default)s)')
    args = parser.parse_args(argv)
    has_files = bool(args.profile or args.buffs or args.prestige_shop or args.pet)
    if args.history or args.compare:
        if not args.account:
            parser.error('--history and --compare require --account NAME')
        if has_files or args.save:
            parser.error('--history and --compare cannot be combined with response files or --save')
        return run_query(args)
    if args.save and not has_files:
        parser.error('--save needs at least one response file')
    if has_files or args.json:
        return run_files(parser, args)
    return interactive(args.snapshot_dir)


if __name__ == '__main__':
    sys.exit(main())
