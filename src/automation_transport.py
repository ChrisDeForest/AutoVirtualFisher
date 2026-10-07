"""Transports: how actions reach the game and how replies come back.

`Transport` is the contract. `SimulationTransport` is a deterministic stand-in for Virtual Fisher (cooldowns,
catches, reply latency, and scripted faults) used to test the controller. The experimental
native transport in automation_discord implements this same contract.
"""
from dataclasses import replace
from random import Random
from typing import Protocol

from .automation_models import ActionIntent, CapabilityError, ContextStatus, DispatchReceipt, TransportError
from .observation import Identity, Observation, check_identity, recipients

# Outcomes a test can script per send (by send index):
#   ok, unreadable (a catch count, or profile/buffs digits, misread), missing (the game acts but no reply is seen), duplicate (the reply is
#   delivered twice), other_player_first (another player's reply arrives first), uncorrelated (the reply carries
#   no evidence of which action it answers), send_error (the hand-over fails).
OUTCOMES = ('ok', 'unreadable', 'missing', 'duplicate', 'other_player_first', 'uncorrelated', 'send_error')
FISH = ('Salmon', 'Cod')
VISIBLE_REPLIES = 12


class Transport(Protocol):
    capabilities: tuple

    def inspect_context(self) -> ContextStatus: ...

    def send(self, intent: ActionIntent) -> DispatchReceipt: ...

    def poll(self) -> list[Observation]: ...


class SimulationTransport:
    """A simulated player in a Virtual Fisher DM. `poll()` returns the replies currently "on screen" (the most
    recent ones, oldest first), so a restarted controller sees what the previous one may have missed."""
    capabilities = ('refresh_profile', 'refresh_buffs', 'fish')

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

    def send(self, intent: ActionIntent) -> DispatchReceipt:
        if intent.kind not in self.capabilities:
            raise CapabilityError(f'The simulation cannot {intent.kind}.')
        now, index = self.monotonic(), len(self.sent)
        self.sent.append((now, intent))
        outcome = self.script.get(index, 'ok')
        if outcome == 'send_error':
            raise TransportError('Simulated hand-over failure.')
        text, kind = self._play(intent, now)
        if outcome == 'unreadable' and kind == 'fishing':
            lines = text.split('\n')
            lines[2] = lines[2].split(' ', 1)[1]  # OCR lost the count: "Salmon"
            text = '\n'.join(lines)
        elif outcome == 'unreadable' and kind in ('profile', 'buffs'):
            # OCR misread the digits: "$1,0O0", "Level 1O", "3.5?s".
            text = text.replace('$1,000', '$1,0O0').replace('Level 10,', 'Level 1O,').replace(
                f'{self.cooldown:g}s', f'{self.cooldown:g}?s')
        deliver = now + self.latency
        if outcome == 'other_player_first':
            self._queue(deliver, f'sim-{index}-other', 'fishing', 'someoneelse77\nYou caught:\n1 Cod\n+5 XP', None,
                        Identity('someoneelse', 'someoneelse77'), invoked_by='someoneelse')
        if outcome != 'missing':
            correlation = None if outcome == 'uncorrelated' else intent.action_id
            for _ in range(2 if outcome == 'duplicate' else 1):
                self._queue(deliver, f'sim-{index}', kind, text, correlation)
        return DispatchReceipt(intent.action_id, self.utc().isoformat(), f'simulated send {index}')

    def poll(self) -> list[Observation]:
        now = self.monotonic()
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
                    'Current biome: River', 'profile')
        if intent.kind == 'refresh_buffs':
            return (f"{username}'s current multipliers (including bait):\nSell price: 1.00x\n"
                    f'Fishing cooldown: {self.cooldown:g}s.', 'buffs')
        if now < self.next_allowed:
            return f'You must wait {self.next_allowed - now:.1f} seconds before fishing again.', None
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
