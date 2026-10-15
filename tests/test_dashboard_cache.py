"""Dashboard projection reuse without stale clocks or mutable cache exposure."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.automation_controller import AutomationController
from src.automation_journal import AutomationJournal
from src.automation_transport import SimulationTransport
from src.live_stats import project, summarize_finds
from tests.test_automation_controller import Clock, CONFIG
from tests.test_live_stats import PROFILE, BUFFS, trip


class DashboardCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.clock = Clock()
        self.controller = AutomationController(
            SimulationTransport(self.clock.monotonic, self.clock.utc), CONFIG,
            AutomationJournal(root / 'journal'), root / 'fishing', root / 'review',
            monotonic=self.clock.monotonic, utc=self.clock.utc, plan_spawn=lambda work: None)
        self.controller.synced.update(profile=PROFILE, buffs=BUFFS)
        self.controller.started_at = 0
        self.clock.now = 10
        tally = trip('5 Salmon\n+100 XP')
        self.controller.run_tallies = [tally]
        self.controller.sync_tallies = [tally]
        self.controller.charm_tallies = [tally]

    def test_status_reuses_expensive_work_but_updates_rates_and_isolates_results(self):
        with patch('src.automation_controller.project', wraps=project) as projection, patch(
                'src.automation_controller.summarize_finds', wraps=summarize_finds) as finds:
            first = self.controller.status()['dashboard']
            calls = (projection.call_count, finds.call_count)
            first['run']['sources']['chests']['money'] = 999
            first['finds']['run']['other']['fake'] = 9
            first['inventory']['fish']['Salmon'] = -1
            self.clock.now = 20
            second = self.controller.status()['dashboard']
            self.assertEqual((projection.call_count, finds.call_count), calls)
            self.assertEqual(second['per_hour']['xp'], 18000)
            self.assertEqual(second['inventory']['fish']['Salmon'], 3948)
            self.assertEqual(second['run']['sources']['chests']['money'], 0)
            self.assertNotIn('fake', second['finds']['run']['other'])
            self.assertGreater(second['next_level_in_seconds'], 0)

    def test_replacements_nested_mutations_reads_and_references_invalidate(self):
        controller = self.controller
        with patch('src.automation_controller.project', wraps=project) as projection:
            controller._dashboard()
            for change in (
                lambda: setattr(controller, 'run_tallies', [trip('7 Salmon\n+200 XP')]),
                lambda: controller.sync_tallies[0].update(fishing_xp=300),
                lambda: setattr(controller, 'charm_tallies', [trip('1 Fish\nYou found a Haste Charm\n+1 XP')]),
                lambda: controller.synced.update(profile=PROFILE.replace('P4', 'P5')),
                lambda: controller.synced.update(buffs=BUFFS.replace('2.20x', '3.00x')),
                lambda: controller.synced.update(charms='1/15 - Haste Charm (2 to next tier).'),
                lambda: controller.earlier_buffs.update(extra={'value': 4, 'unit': 'multiplier'}),
            ):
                before = projection.call_count
                change()
                result = controller._dashboard()
                self.assertGreater(projection.call_count, before)
            self.assertEqual(result['run']['xp'], 200)
            self.assertEqual(result['profile']['prestige'], 5)
            self.assertEqual(result['profile']['xp_current'], 1977)
            before = projection.call_count
            with patch('src.automation_controller._references', return_value=([100] * 100, {})):
                controller._dashboard()
            self.assertGreater(projection.call_count, before)

    def test_reset_drops_cached_projection(self):
        with patch('src.automation_controller.project', wraps=project) as projection:
            self.controller._dashboard()
            before = projection.call_count
            self.controller._reset_stats()
            result = self.controller._dashboard()
            self.assertGreater(projection.call_count, before)
            self.assertEqual(result['run']['trips'], 0)

    def test_account_model_and_countdowns_stay_live_on_projection_hit(self):
        controller = self.controller
        controller.running = True
        controller.ready_at = 40
        controller.model_data = None
        first = controller.status()
        controller.account['sections']['new_read'] = {'values': {'level': 7}}
        controller.model_problem = 'Updated model data unavailable'
        controller.unread = {'refresh_profile': ['xp_current']}
        controller.unread_trips = {'refresh_profile': 2}
        controller.uncounted = 3
        controller.synced_at['profile'] = 'new read time'
        self.clock.now = 20
        with patch('src.automation_controller.project', wraps=project) as projection:
            second = controller.status()
            self.assertEqual(projection.call_count, 0)
        self.assertEqual(second['account']['sections']['new_read']['values']['level'], 7)
        self.assertEqual(second['model']['error'], 'Updated model data unavailable')
        self.assertEqual((first['next_eligible_in'], second['next_eligible_in']), (30, 20))
        self.assertEqual(second['dashboard']['uncounted_since_sync'], 3)
        self.assertEqual(second['dashboard']['synced_at']['profile'], 'new read time')
        self.assertEqual(second['dashboard']['unread']['profile']['retry_in'],
                         controller.config.retry_unread_trips - 2)
        self.assertEqual(second['dashboard']['next_level_in_seconds'],
                         first['dashboard']['next_level_in_seconds'] * 2)
