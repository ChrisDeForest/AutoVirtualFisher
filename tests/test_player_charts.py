import re
import unittest

from src.player_charts import line_chart_svg

money = lambda value: f'${value:,}'


def attribute(html, name):
    return [float(value) for value in re.findall(rf'<circle[^>]* {name}="([0-9.]+)"', html)]


class LineChartTests(unittest.TestCase):
    def test_draws_one_dot_per_point_with_hover_titles(self):
        html = line_chart_svg('Balance', [('2026-10-01T10:00:00Z', 100), ('2026-10-02T10:00:00Z', 250),
                                          ('2026-10-03T10:00:00Z', 175)], money)
        self.assertEqual(html.count('<circle'), 3)
        self.assertIn('<polyline', html)
        self.assertIn('<title>2026-10-02 10:00 UTC: $250</title>', html)
        self.assertIn('role="img"', html)

    def test_horizontal_spacing_follows_real_time(self):
        html = line_chart_svg('Balance', [('2026-10-01T00:00:00Z', 1), ('2026-10-02T00:00:00Z', 2),
                                          ('2026-10-04T00:00:00Z', 3)], money)
        first, second, third = attribute(html, 'cx')
        self.assertAlmostEqual((third - second) / (second - first), 2.0, places=2)

    def test_higher_values_are_drawn_higher(self):
        html = line_chart_svg('Balance', [('2026-10-01T00:00:00Z', 10), ('2026-10-02T00:00:00Z', 90)], money)
        low, high = attribute(html, 'cy')
        self.assertLess(high, low)

    def test_summary_states_first_latest_and_change(self):
        html = line_chart_svg('Balance', [('2026-10-01T00:00:00Z', 2343033), ('2026-10-02T00:00:00Z', 2400000)], money)
        self.assertIn('Balance: $2,343,033 → $2,400,000 (+$56,967)', html)
        html = line_chart_svg('Balance', [('2026-10-01T00:00:00Z', 500), ('2026-10-02T00:00:00Z', 200)], money)
        self.assertIn('(−$300)', html)
        self.assertIn('aria-label="Balance: $500 → $200 (−$300)', html)

    def test_flat_series_draws_a_level_line(self):
        html = line_chart_svg('Fish value', [('2026-10-01T00:00:00Z', 7), ('2026-10-02T00:00:00Z', 7)], money)
        heights = attribute(html, 'cy')
        self.assertEqual(len(set(heights)), 1)
        self.assertIn('(no change)', html)

    def test_identical_timestamps_are_centered(self):
        html = line_chart_svg('Balance', [('2026-10-01T00:00:00Z', 1), ('2026-10-01T00:00:00Z', 2)], money)
        self.assertEqual(len(set(attribute(html, 'cx'))), 1)

    def test_fewer_than_two_points_shows_message_instead_of_chart(self):
        for points in ([], [('2026-10-01T00:00:00Z', 1)]):
            with self.subTest(points=points):
                html = line_chart_svg('Balance', points, money)
                self.assertNotIn('<svg', html)
                self.assertIn('Needs two full snapshots with this value.', html)

    def test_text_is_escaped(self):
        html = line_chart_svg('<b>Balance</b>', [('2026-10-01T00:00:00Z', 1), ('2026-10-02T00:00:00Z', 2)],
                              lambda value: f'<{value}>')
        self.assertNotIn('<b>', html)
        self.assertIn('&lt;b&gt;Balance&lt;/b&gt;', html)
        self.assertIn('&lt;2&gt;', html)


if __name__ == '__main__':
    unittest.main()
