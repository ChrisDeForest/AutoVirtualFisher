from pathlib import Path
import tempfile
import unittest

from src.observation import (MISMATCH, UNKNOWN, VERIFIED, Identity, check_identity, load_identity, recipients,
                             save_identity)

ME = Identity('epicmouse', 'epicmouse9925')


class RecipientTests(unittest.TestCase):
    def test_each_reply_kind_names_its_player_in_its_own_field(self):
        self.assertEqual(recipients('profile', ['Inventory of epicmouse', 'Balance: $5.']),
                         [('epicmouse', 'display_name')])
        self.assertEqual(recipients('buffs', ["epicmouse9925's current multipliers (including bait):"]),
                         [('epicmouse9925', 'username')])
        self.assertEqual(recipients('fishing', ['epicmouse9925', 'You caught:', '1 Salmon']),
                         [('epicmouse9925', 'username')])
        self.assertEqual(recipients('pet', ['Current pet: Dolphin.'], invoked_by='epicmouse'),
                         [('epicmouse', 'display_name')])
        self.assertEqual(recipients('pet', ['Current pet: Dolphin.']), [])


class IdentityTests(unittest.TestCase):
    def test_configured_names_verify(self):
        status, recipient, _ = check_identity(ME, [('epicmouse', 'display_name'), ('epicmouse9925', 'username')])
        self.assertEqual((status, recipient), (VERIFIED, 'epicmouse9925'))

    def test_names_are_not_assumed_equivalent(self):
        # The username is not accepted as the display name just because it looks alike, or the other way round.
        self.assertEqual(check_identity(ME, [('epicmouse9925', 'display_name')])[0], MISMATCH)
        self.assertEqual(check_identity(ME, [('epicmouse', 'username')])[0], MISMATCH)

    def test_any_other_name_is_a_mismatch(self):
        status, recipient, reason = check_identity(ME, [('epicmouse', 'display_name'), ('someoneelse', 'username')])
        self.assertEqual((status, recipient), (MISMATCH, 'someoneelse'))
        self.assertIn('someoneelse', reason)

    def test_without_names_or_configuration_it_is_unknown(self):
        self.assertEqual(check_identity(ME, [])[0], UNKNOWN)
        self.assertEqual(check_identity(Identity('epicmouse'), [('epicmouse9925', 'username')])[0], UNKNOWN)
        self.assertEqual(check_identity(None, [('epicmouse', 'display_name')])[0], UNKNOWN)

    def test_identity_is_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ids' / 'identities.json'
            self.assertIsNone(load_identity('epicmouse', path))
            save_identity(ME, path)
            save_identity(Identity('Alt', 'alt1'), path)
            self.assertEqual(load_identity('EPICMOUSE', path), ME)
            self.assertEqual(load_identity('alt', path), Identity('Alt', 'alt1'))


if __name__ == '__main__':
    unittest.main()
