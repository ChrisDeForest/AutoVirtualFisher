import unittest
from unittest.mock import patch
import io

from src.automation_live import parse_args


class LiveArgumentsTests(unittest.TestCase):
    base = ['--channel', 'Virtual Fisher', '--account', 'sim', '--username', 'sim0001']

    def test_default_has_no_run_limits(self):
        args = parse_args(self.base + ['--live'])
        self.assertIsNone(args.trips)
        self.assertIsNone(args.seconds)

    def test_optional_limits_are_independent(self):
        args = parse_args(self.base + ['--trips', '1'])
        self.assertEqual(args.trips, 1)
        self.assertIsNone(args.seconds)
        args = parse_args(self.base + ['--seconds', '120'])
        self.assertIsNone(args.trips)
        self.assertEqual(args.seconds, 120)

    def test_invalid_limits_rejected(self):
        for options in (['--trips', '0'], ['--seconds', '-1'], ['--startup-delay', '61']):
            with self.subTest(options=options), patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                parse_args(self.base + options)


if __name__ == '__main__':
    unittest.main()
