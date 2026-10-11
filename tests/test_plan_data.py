import json
import tempfile
import unittest
from pathlib import Path

from src.plan_data import PlanDataError, load_plan_data


class PlanDataTests(unittest.TestCase):
    def test_loads_prices_and_route(self):
        data = load_plan_data()
        self.assertEqual(data['prices']['upgrades']['Salesman'][7], 25000)  # Salesman 8
        self.assertEqual(sum(data['prices']['special']['Fish Ovens']['prices']), 3360)
        self.assertEqual(sum(data['prices']['special']['Statistician']['prices']), 4000)
        self.assertEqual(data['prices']['boats'][6], ['Luxury Yacht', 100000000, 50])
        self.assertEqual(data['route']['salesman_before_rod']['Alloy Rod'], 6)
        self.assertEqual(data['route']['charm_grind']['exits']['4'], 640)

    def test_missing_table_is_refused(self):
        route = json.loads(Path('src/reference/route.json').read_text(encoding='utf-8'))
        del route['charm_grind']
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'route.json'
            path.write_text(json.dumps(route), encoding='utf-8')
            with self.assertRaises(PlanDataError) as raised:
                load_plan_data(route_path=path)
        self.assertIn('charm_grind', str(raised.exception))

    def test_unsorted_prices_are_refused(self):
        prices = json.loads(Path('src/reference/prices.json').read_text(encoding='utf-8'))
        prices['upgrades']['Salesman'][3] = 1
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prices.json'
            path.write_text(json.dumps(prices), encoding='utf-8')
            with self.assertRaises(PlanDataError):
                load_plan_data(prices_path=path)


if __name__ == '__main__':
    unittest.main()
