"""Transports: how actions reach the game and how replies come back.

`Transport` is the contract. `SimulationTransport` is a deterministic stand-in for Virtual Fisher (cooldowns,
catches, reply latency, and scripted faults) used to test the controller. The experimental
native transport in automation_discord implements this same contract.
"""
from dataclasses import replace
from random import Random
from typing import Protocol

from . import account_samples as samples
from .account_reads import ALL_READS, reply_of
from .automation_models import (ActionIntent, CapabilityError, ContextStatus, DispatchReceipt, Interrupted,
                                NOT_FOREGROUND, NotDispatched, TransportError)
from .observation import Identity, Observation, check_identity, recipients

# Outcomes a test can script per send (by send index):
#   ok, unreadable (a catch count, or profile/buffs digits, misread; the misread differs per attempt, as a reply
#   caught mid-render would), noisy (a fishing reply with one extra garbled line), unreadable_fixed (the same misread every time, as the same pixels give), missing (the game acts but no reply is seen), duplicate (the reply is
#   delivered twice), other_player_first (another player's reply arrives first), uncorrelated (the reply carries
#   no evidence of which action it answers), send_error (the hand-over fails), partial (one field lost to OCR),
#   captcha, xp_unread (the profile's XP numbers lost), no_button (page 2's arrow not found; nothing sent),
#   interrupted (Discord lost focus while typing; nothing sent; out of focus until focus()), level_bonus (the trip
#   levels up with a bonus to the multipliers, as at milestone levels).
OUTCOMES = ('ok', 'unreadable', 'unreadable_fixed', 'noisy', 'missing', 'duplicate', 'other_player_first', 'uncorrelated', 'send_error')
FISH = ('Salmon', 'Cod')
# A real /charms reply (11 charms found).
SIM_CHARMS = ('Charms are found in high tier crates. Try to get them all!\n'
              '0/14 - :charm_yellow: Marketing Charm - Sell fish for more. (1 to next tier).\n'
              '1/14 - :charm_pink: Endurance Charm - Catch more fish. (2 to next tier).\n'
              '1/14 - :charm_red: Haste Charm - Fish more often. (2 to next tier).\n'
              '1/14 - :charm_white: Quantity Charm - Increase amount of items found in chests. (1 to next tier).\n'
              '1/14 - :charm_cyan: Worker Charm - Workers catch more fish. (2 to next tier).\n'
              '0/14 - :charm_lblue: Treasure Charm - Catch slightly more treasure. (1 to next tier).\n'
              '2/14 - :charm_green: Quality Charm - Increase quality of treasure. (2 to next tier).\n'
              '1/14 - :charm_blue: Experience Charm - Increase experience from all fish. (1 to next tier).\n'
              '11/840 - Charms found.')
# Account replies, both pages, as seen on 2026-10-09.
SIM_ACCOUNT = {'refresh_upgrades': samples.UPGRADES_PAGE_1, 'refresh_upgrades_page2': samples.UPGRADES_PAGE_2,
               'refresh_rod': samples.ROD, 'refresh_bait': samples.BAIT, 'refresh_biome': samples.BIOME,
               'refresh_boats': samples.BOATS, 'refresh_pets': samples.PETS,
               'refresh_prestige_shop': samples.PRESTIGE_SHOP, 'refresh_league': samples.LEAGUE_PAGE_1,
               'refresh_special': samples.SPECIAL,
               'refresh_league_page2': samples.LEAGUE_PAGE_2, 'refresh_boosts': samples.BOOSTS}
VISIBLE_REPLIES = 12


class Transport(Protocol):
    capabilities: tuple

    def inspect_context(self) -> ContextStatus: ...

    def send(self, intent: ActionIntent) -> DispatchReceipt: ...

    def poll(self) -> list[Observation]: ...


class SimulationTransport:
    """A simulated player in a Virtual Fisher DM. `poll()` returns the replies currently "on screen" (the most
    recent ones, oldest first), so a restarted controller sees what the previous one may have missed."""
    capabilities = ('refresh_profile', 'refresh_buffs', 'refresh_charms', *ALL_READS, 'fish')

    def __init__(self, monotonic, utc, display_name='sim', username='sim0001', context_id='sim:dm', cooldown=3.5,
                 latency=0.4, seed=1, script=None):
        self.monotonic, self.utc = monotonic, utc
        self.identity = Identity(display_name, username)
        self.context = ContextStatus(True, context_id)
        self.cooldown, self.latency = cooldown, latency
        self.random = Random(seed)
        self.script = dict(script or {})
        self.sent = []                  # (monotonic time, intent) per hand-over attempt
        self.queue = []                 # (deliver at, observation)
        self.history = []               # delivered observations, oldest first
        self.next_allowed = float('-inf')
        self.totals = {'trips': 0, 'catches': {}, 'xp': 0}

    def inspect_context(self) -> ContextStatus:
        return self.context

    def solve_captcha(self):
        """The person answers the captcha; the next check sees a newer reply below it."""
        self.captcha_solved = True

    def captcha_cleared(self) -> bool:
        solved, self.captcha_solved = getattr(self, 'captcha_solved', False), False
        return solved

    def send(self, intent: ActionIntent) -> DispatchReceipt:
        if intent.kind not in self.capabilities:
            raise CapabilityError(f'The simulation cannot {intent.kind}.')
        now, index = self.monotonic(), len(self.sent)
        self.sent.append((now, intent))
        outcome = self.script.get(index, 'ok')
        if outcome == 'send_error':
            raise TransportError('Simulated hand-over failure.')
        if outcome == 'no_button':
            raise NotDispatched('Next-page button not found.')
        if outcome == 'interrupted':
            # Discord lost focus while the command was typed: nothing was sent, and it stays out of focus until
            # the test gives it back (focus()).
            self.context = ContextStatus(False, self.context.context_id, NOT_FOREGROUND)
            raise Interrupted(f'Typing {intent.kind} was interrupted when Discord stopped being the active window.')
        text, kind = self._play(intent, now)
        if outcome == 'level_bonus' and kind == 'fishing':
            text += ('\n:fish: LEVEL UP! :fish: You are now level 75.\n$120,000 received for leveling up.\n'
                     '10% INCREASED SELLING PRICE FOR LEVELING UP!')
        if outcome == 'noisy' and kind == 'fishing':
            text += '\n$6 , 6O0 recieved for levelling up'  # an extra line OCR garbled beyond reading
        if outcome in ('unreadable', 'unreadable_fixed') and kind == 'fishing':
            lines = text.split('\n')
            lines[2] = lines[2].split(' ', 1)[1]  # OCR lost the count: "Salmon"
            text = '\n'.join(lines)
        elif outcome in ('unreadable', 'unreadable_fixed') and kind in ('profile', 'buffs'):
            # OCR misread the digits: "$1,0O0", "Level 1O", "3.5?s".
            glyph = '?' if outcome == 'unreadable_fixed' else '?' * (index + 1)
            text = text.replace('$1,000', '$1,0O0').replace('Level 10,', 'Level 1O,').replace(
                f'{self.cooldown:g}s', f'{self.cooldown:g}{glyph}s').replace('Balance: $1,0O0.', f'Balance: ${glyph}1,0O0.')
        if outcome == 'partial':
            # One field lost to OCR, the rest readable.
            text = text.replace('Sell price: 1.00x\n', '').replace('Currently using Steel Rod.\n', '').replace(
                'Catch more fish. (2 to next tier).', 'Catch more fish. (2 to n~xt t1er').replace(
                'Fishing Frenzy (0/5)', 'Fishing Frenzv (0/S)')
        if outcome == 'captcha':
            text, kind = 'Please complete this captcha to continue fishing.\nUse /captcha with the code shown.', 'captcha'
        if outcome == 'xp_unread' and kind == 'profile':
            text = text.replace('0/1,000 XP', 'xp')  # observed: "P4 Level 31, xp to next level."
        deliver = now + self.latency
        if outcome == 'other_player_first':
            self._queue(deliver, f'sim-{index}-other', 'fishing', 'someoneelse77\nYou caught:\n1 Cod\n+5 XP', None,
                        Identity('someoneelse', 'someoneelse77'), invoked_by='someoneelse')
        if outcome != 'missing':
            correlation = None if outcome == 'uncorrelated' else intent.action_id
            for _ in range(2 if outcome == 'duplicate' else 1):
                self._queue(deliver, f'sim-{index}', kind, text, correlation)
        return DispatchReceipt(intent.action_id, self.utc().isoformat(), f'simulated send {index}')

    def unfocus(self):
        self.context = ContextStatus(False, self.context.context_id, NOT_FOREGROUND)

    def focus(self):
        self.context = ContextStatus(True, self.context.context_id)

    def poll(self) -> list[Observation]:
        now = self.monotonic()
        if not self.context.ok:
            return []  # nothing can be read while Discord is not active
        due = [entry for entry in self.queue if entry[0] <= now]
        self.queue = [entry for entry in self.queue if entry[0] > now]
        # Stamped when they appear, as a capture would observe them.
        self.history += [replace(observation, observed_at=self.utc().isoformat()) for _, observation in due]
        del self.history[:-VISIBLE_REPLIES]
        return list(self.history)

    def _play(self, intent, now):
        """What the game does with the command, and its reply text."""
        name, username = self.identity.display_name, self.identity.username
        if intent.kind == 'refresh_profile':
            return (f'Inventory of {name}\nBalance: $1,000.\nLevel 10, 0/1,000 XP to next level.\n'
                    'Currently using Steel Rod.\nCurrent biome: River\nPet: Dolphin (Level 5)\n'
                    'Fish Value: $500', 'profile')
        if intent.kind == 'refresh_charms':
            return SIM_CHARMS, 'charms'
        if intent.kind in SIM_ACCOUNT:
            return SIM_ACCOUNT[intent.kind], reply_of(intent.kind)
        if intent.kind == 'refresh_buffs':
            return (f"{username}'s current multipliers (including bait):\nSell price: 1.00x\nFish catch: 1.00x\n"
                    'Fish quality: 1.00x\nTreasure chance: 1.00x\nTreasure quality: 1.00x\nXP Multiplier: 1.00x\n'
                    f'Fishing cooldown: {self.cooldown:g}s.', 'buffs')
        if now < self.next_allowed:
            return (f'You must wait {self.next_allowed - now:.1f}s before fishing again.\n'
                    f'Your cooldown: {self.cooldown:g}s'), None
        self.next_allowed = now + self.cooldown
        catches = {fish: self.random.randint(1, 9) for fish in FISH}
        xp = sum(catches.values()) * 10
        self.totals['trips'] += 1
        self.totals['xp'] += xp
        for fish, count in catches.items():
            self.totals['catches'][fish] = self.totals['catches'].get(fish, 0) + count
        return (f'{username}\nYou caught:\n' + '\n'.join(f'{count} {fish}' for fish, count in catches.items())
                + f'\n+{xp} XP', 'fishing')

    def _queue(self, deliver, observation_id, kind, text, correlation, identity=None, invoked_by=None):
        identity = identity or self.identity
        lines = text.split('\n')
        status, recipient, reason = check_identity(self.identity, recipients(
            kind, lines, invoked_by or identity.display_name))
        self.queue.append((deliver, Observation(
            observation_id=observation_id, capture_id=observation_id, observed_at='',
            context_id=self.context.context_id, kind=kind, recipient=recipient, text=text, identity_status=status,
            reason=reason, correlation=correlation)))
