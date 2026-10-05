"""Terminal menu and file-based entry point for the shared player parser."""
import argparse
import json
from pathlib import Path

from .player_state import display_value, parse_responses


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


def main():
    parser = argparse.ArgumentParser(description='Inspect pasted Virtual Fisher responses')
    parser.add_argument('--profile', type=Path, help='UTF-8 text file containing /profile output')
    parser.add_argument('--buffs', type=Path, help='UTF-8 text file containing /buffs output')
    parser.add_argument('--prestige-shop', type=Path, help='UTF-8 text file containing /prestige shop output')
    parser.add_argument('--pet', type=Path, help='UTF-8 text file containing /pet output')
    parser.add_argument('--json', action='store_true', help='Print machine-readable JSON')
    args = parser.parse_args()
    try:
        profile = args.profile.read_text(encoding='utf-8-sig') if args.profile else ''
        buffs = args.buffs.read_text(encoding='utf-8-sig') if args.buffs else ''
        prestige_shop = args.prestige_shop.read_text(encoding='utf-8-sig') if args.prestige_shop else ''
        pet = args.pet.read_text(encoding='utf-8-sig') if args.pet else ''
    except OSError as error:
        parser.error(str(error))
    if args.profile or args.buffs or args.prestige_shop or args.pet or args.json:
        state = parse_responses(profile, buffs, prestige_shop, pet)
        print(json.dumps(state, indent=2, ensure_ascii=False)) if args.json else show(state)
        return
    print('Player logbook — pasted responses only; no Discord connection.')
    try:
        while True:
            print('\n[1] Paste profile  [2] Paste buffs  [3] Paste prestige shop  [4] Paste pet  [5] View state  [6] View JSON  [q] Quit')
            choice = input('Choose: ').strip().lower()
            if choice == 'q':
                return
            if choice == '1':
                profile = paste()
            elif choice == '2':
                buffs = paste()
            elif choice == '3':
                prestige_shop = paste()
            elif choice == '4':
                pet = paste()
            elif choice == '5':
                show(parse_responses(profile, buffs, prestige_shop, pet))
                continue
            elif choice == '6':
                print(json.dumps(parse_responses(profile, buffs, prestige_shop, pet), indent=2, ensure_ascii=False))
                continue
            else:
                print('Choose 1, 2, 3, 4, 5, 6, or q.')
                continue
    except (EOFError, KeyboardInterrupt):
        print('\nClosed logbook.')


if __name__ == '__main__':
    main()
