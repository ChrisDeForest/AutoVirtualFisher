from dataclasses import replace
import unittest
from src.automation_state import AutomationState
from src.observation import Observation


class StartupFieldsTests(unittest.TestCase):
    def seen(self, text, kind='profile', ident='one'):
        return Observation(ident, ident, '2026-10-07T15:00:00+00:00', 'dm', kind, 'sim', text, 'verified')

    def test_complementary_reads_retain_confirmed_fields(self):
        from src.startup_fields import apply_startup, startup_problems
        state = AutomationState('run', 'sim', 'sim0001', 'dm')
        state = apply_startup(state, self.seen('Inventory of sim\nBalance: $50.\nLevel: 1O'))
        self.assertEqual(state.profile['balance'], 50)
        self.assertEqual(startup_problems(state, 'profile'), ['Level has not been read.'])
        state = apply_startup(state, self.seen('Inventory of sim\nBalance: $5O.\nP4 Level 30, xp to next leveL\nFish Invent«y', ident='two'))
        self.assertEqual(state.profile['balance'], 50)
        self.assertEqual(state.profile['level'], 30)
        self.assertEqual(startup_problems(state, 'profile'), [])

    def test_optional_buffs_text_does_not_hide_valid_cooldown(self):
        from src.startup_fields import apply_startup, startup_problems
        state = AutomationState('run', 'sim', 'sim0001', 'dm')
        state = apply_startup(state, self.seen("sim0001's current multipliers (including bait):\nFish quality: ???\nFishing cooldown: 3.5s.", 'buffs'))
        self.assertEqual(state.cooldown_seconds, 3.5)
        self.assertEqual(startup_problems(state, 'buffs'), [])

    def test_wrong_identity_and_conflicting_required_lines_do_not_establish_values(self):
        from src.startup_fields import apply_startup
        state = AutomationState('run', 'sim', 'sim0001', 'dm')
        self.assertEqual(apply_startup(state, self.seen('Inventory of other\nBalance: $50.\nLevel: 30')), state)
        partial = apply_startup(state, self.seen('Inventory of sim\nBalance: $50.\nBalance: $60.\nLevel: 30'))
        self.assertIsNone(partial.profile.get('balance'))

    def test_unverified_and_other_channel_are_ignored(self):
        from src.startup_fields import apply_startup
        state = AutomationState('run', 'sim', 'sim0001', 'dm')
        observation = self.seen('Inventory of sim\nBalance: $50.\nLevel: 30')
        for bad in (replace(observation, identity_status='unknown'), replace(observation, context_id='other')):
            self.assertEqual(apply_startup(state, bad), state)


class ObservedGlyphTests(unittest.TestCase):
    def test_buffs_bullet_apostrophe_and_underscore_period(self):
        # Native OCR, 2026-10-08: "'s" read as "•s" and the closing period as "_".
        from src.startup_fields import reply_fields
        text = 'epicmouse9925•s current multipliers (including bait):\nFishing cooldown: 3.5s_'
        self.assertEqual(reply_fields('buffs', text), {'username': 'epicmouse9925', 'cooldown_seconds': 3.5})
        self.assertNotIn('cooldown_seconds', reply_fields('buffs', text.replace('3.5s_', '3_5s')))
