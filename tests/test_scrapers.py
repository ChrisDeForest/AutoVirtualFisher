import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/data_scraping'))
import web_scraper as scraper
import data_processing as processing


def soup(html):
    return BeautifulSoup(html, 'lxml')


def response(text, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = text.encode('utf-8')
    result.encoding = 'utf-8'
    result.url = 'https://example.test/page'
    return result


class ScraperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_cwd = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.old_cwd)

    def test_missing_index_cache_is_downloaded_independently(self):
        cache = Path('src/data/html')
        cache.mkdir(parents=True)
        (cache / 'commands.html').write_text('<p>cached commands</p>')
        with patch.object(scraper.requests, 'get', return_value=response('<p>new index</p>')):
            pages = scraper.process_urls(['https://virtualfisher.com/commands',
                'https://virtual-fisher.fandom.com/wiki/Special:AllPages'], 0)
        self.assertEqual([page.p.text for page in pages], ['cached commands', 'new index'])
        self.assertEqual((cache / 'all_pages.html').read_text(), '<p>new index</p>')

    def test_single_wiki_page_is_cached_and_reused(self):
        url = 'https://virtual-fisher.fandom.com/wiki/Fish'
        with patch.object(scraper.requests, 'get', return_value=response('<p>fish</p>')):
            self.assertEqual(len(scraper.process_urls([url], 1)), 1)
        self.assertEqual(Path('src/data/html/pages/fish.html').read_text(), '<p>fish</p>')
        with patch.object(scraper.requests, 'get', side_effect=AssertionError('Unexpected network')):
            self.assertEqual(scraper.process_urls([url], 1)[0].p.text, 'fish')

    def test_refresh_replaces_existing_page(self):
        cache = Path('src/data/html/pages/fish.html')
        cache.parent.mkdir(parents=True)
        cache.write_text('<p>old</p>')
        with patch.object(scraper.requests, 'get', return_value=response('<p>fresh</p>')):
            pages = scraper.process_urls(['https://virtual-fisher.fandom.com/wiki/Fish'], 1, refresh=True)
        self.assertEqual(pages[0].p.text, 'fresh')
        self.assertEqual(cache.read_text(), '<p>fresh</p>')

    def test_http_failure_does_not_replace_cache(self):
        cache = Path('src/data/html/pages/fish.html')
        cache.parent.mkdir(parents=True)
        cache.write_text('<p>old</p>')
        with patch.object(scraper.requests, 'get', return_value=response('Forbidden', 403)):
            with self.assertRaises(requests.HTTPError):
                scraper.process_urls(['https://virtual-fisher.fandom.com/wiki/Fish'], 1, refresh=True)
        self.assertEqual(cache.read_text(), '<p>old</p>')

    def test_wiki_pages_are_keyed_by_name(self):
        index = soup('<div class="mw-allpages-chunk"><ul><li><a>Pet</a></li>'
                     '<li><a>Fish</a></li><li><a>Fish</a></li></ul></div>')
        with patch.object(scraper.requests, 'get', return_value=response('<p>page</p>')):
            pages = scraper.process_all_pages([soup(''), index])
        self.assertIsInstance(pages, dict)
        self.assertEqual(set(pages), {'Pet', 'Fish'})

    def test_missing_command_layout_does_not_overwrite_output(self):
        output = Path('src/data/commands/all_commands.json')
        output.parent.mkdir(parents=True)
        output.write_text('{"commands": ["previous"]}')
        with self.assertRaisesRegex(ValueError, 'command'):
            scraper.process_and_parse_commands([soup('<html>Challenge page</html>')])
        self.assertEqual(json.loads(output.read_text()), {'commands': ['previous']})

    def test_current_command_layout_keeps_inline_command_references_in_description(self):
        html = '<main><h2 class="subtitle">Find a list of commands</h2><div class="block"><h3 class="subtitle">Basic Commands</h3>'
        html += '<strong>/shop</strong> - Buy using <strong>/buy</strong> command.<br>'
        html += '<strong>/fish</strong> - Catch fish!</div></main>'
        scraper.process_and_parse_commands([soup(html)])
        commands = json.loads(Path('src/data/commands/all_commands.json').read_text())['commands']
        self.assertEqual(commands, [
            {'cmd': '/shop', 'description': 'Buy using /buy command.'},
            {'cmd': '/fish', 'description': 'Catch fish!'}])

    def test_fish_parser_ignores_unrelated_tables_and_page_order(self):
        html = '<table><tr><td>Unrelated</td></tr></table><table><tr>'
        headers = ['Type', 'River', 'Volcanic', 'Ocean', 'Sky', 'Space', 'Alien', 'Base XP', 'Base Sell Price']
        html += ''.join('<th>' + h + '</th>' for h in headers) + '</tr><tr>'
        html += ''.join('<td>' + v + '</td>' for v in ['Common', '+', '', '+', '', '', '', '1,000', '25'])
        html += '</tr></table>'
        processing.parse_fish({'Pet': soup(''), 'Fish': soup(html)})
        data = json.loads(Path('src/data/json/fish.json').read_text())['fish']
        self.assertEqual(set(data), {'Common'})
        self.assertEqual(data['Common']['Base XP'], 1000)
        self.assertEqual(data['Common']['Volcanic'], 0)

    def test_fish_table_accepts_wiki_header_cells(self):
        html = '<table><tr>' + ''.join('<td>' + h + '</td>' for h in
            ['Type / biome', 'River', 'Volcanic', 'Ocean', 'Sky', 'Space', 'Alien', 'Base XP', 'Base sell price'])
        html += '</tr><tr>' + ''.join('<td>' + v + '</td>' for v in
            ['Fish', '+', '', '+', '', '', '', '1', '1']) + '</tr></table>'
        processing.parse_fish({'Fish': soup(html)})
        data = json.loads(Path('src/data/json/fish.json').read_text())['fish']
        self.assertEqual(data['Fish']['Base Sell Price'], 1)

    def test_bait_costs_follow_named_rows_instead_of_position(self):
        fixture = Path(__file__).parent / 'fixtures/bait.html'
        bait = soup(fixture.read_text(encoding='utf-8'))
        # A changed source price must flow through without editing the scraper.
        for row in bait.select('tr'):
            cells = row.select('td')
            if cells and cells[0].text.strip() == 'Worms':
                cells[1].string = '9$'
        pages = {'Bait': bait}
        for name in ['Artifact_Magnet', 'Fish_(Bait)', 'Leeches', 'Magic_Bait',
                     'Magnet', 'Support_Bait', 'Wise_Bait', 'Worms']:
            pages[name] = soup('<p>Introduction</p><p>+2 extra fish</p>')
        processing.parse_all_baits(pages)
        worms = json.loads(Path('src/data/json/bait/worms.json').read_text())['worms']
        artifact = json.loads(Path('src/data/json/bait/artifact_magnet.json').read_text())['artifact_magnet']
        self.assertEqual(worms['cost'], 9)
        self.assertEqual(artifact['cost'], 75)

    def test_pet_colspan_applies_bonus_to_each_covered_stat(self):
        fixture = Path(__file__).parent / 'fixtures/pet.html'
        processing.parse_pet({'Pet': soup(fixture.read_text(encoding='utf-8'))})
        pets = json.loads(Path('src/data/json/pet.json').read_text())['pets']
        self.assertEqual(pets['Dolphin']['fish_quality_pct'], 0)
        self.assertEqual(pets['Dolphin']['treasure_chance_pct'], 63.75)
        self.assertEqual(pets['Dolphin']['xp_pct'], 63.75)
        self.assertEqual(pets['Tuna']['xp_pct'], 25.5)


if __name__ == '__main__':
    unittest.main()
