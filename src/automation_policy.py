"""Choose the next action. A pure function of the state, configuration, and time: no I/O, no transport calls."""
from datetime import datetime

from .automation_models import EXPECTED_REPLY, ActionIntent, AutomationConfig
from .automation_state import AutomationState
from .startup_fields import startup_problems


def _intent(state: AutomationState, kind: str, now_utc: datetime, *preconditions) -> ActionIntent:
    return ActionIntent(action_id=f'{state.run_id}-{state.sequence + 1:05d}-{kind}', kind=kind,
                        context_id=state.context_id, created_at=now_utc.isoformat(),
                        expected_reply_kind=EXPECTED_REPLY[kind], preconditions=preconditions)


def next_action(state: AutomationState, config: AutomationConfig, now_utc: datetime) -> ActionIntent | None:
    """First establish the state fishing needs (a verified profile, then the cooldown from /buffs), then fish.

    Nothing is chosen while the channel is not verified, an action is unanswered, an earlier outcome is
    uncertain, or a bounded run has reached its trip limit. Waiting out the cooldown is the controller's job.
    """
    if not state.context_ok or state.pending is not None or state.uncertain:
        return None
    if config.max_trips is not None and state.trips >= config.max_trips:
        return None
    if startup_problems(state, 'profile') and 'refresh_profile' not in state.deferred:
        choice = _intent(state, 'refresh_profile', now_utc, 'no verified profile yet')
    elif state.cooldown_seconds is None:
        choice = _intent(state, 'refresh_buffs', now_utc, 'fishing cooldown not observed yet')
    elif state.refresh_due:
        choice = _intent(state, state.refresh_due[0], now_utc, 'scheduled resync')
    else:
        choice = _intent(state, 'fish', now_utc, f'verified profile from {state.profile_observed_at}',
                         f'cooldown {state.cooldown_seconds:g} s from {state.buffs_observed_at}')
    return choice if choice.kind in config.capabilities else None
