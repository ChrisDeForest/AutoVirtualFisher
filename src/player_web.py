"""Local, stateless web interface. Run with python -m src.player_web."""
import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from string import Template
from urllib.parse import parse_qs

from .player_state import display_value, parse_responses

TEMPLATE = Path(__file__).with_name('player_web.html')


def render(profile='', buffs='', prestige_shop='', pet='', submitted=False):
    state = parse_responses(profile, buffs, prestige_shop, pet)
    sections = []
    for name in ('profile', 'buffs', 'prestige', 'pets', 'inventory'):
        values = state[name].copy()
        if name == 'pets' and values['owned'] is not None:
            values['owned'] = ', '.join(values['owned'])
        rows = ''.join(f'<div class="stat"><dt>{escape(field.replace("_", " ").capitalize())}</dt>'
                       f'<dd class="{"unknown" if value is None else "known"}">{escape(display_value(value))}</dd></div>'
                       for field, value in values.items())
        sections.append(f'<section><h3>{name.title()}</h3><dl>{rows}</dl></section>')
    warnings = ''
    if submitted and state['warnings']:
        warnings = '<aside class="warnings"><h3>Needs a look</h3><ul>' + ''.join(
            '<li>' + escape(warning) + '</li>' for warning in state['warnings']) + '</ul></aside>'
    return Template(TEMPLATE.read_text(encoding='utf-8')).substitute(
        profile=escape(profile), buffs=escape(buffs), prestige_shop=escape(prestige_shop), pet=escape(pet),
        sections=''.join(sections), warnings=warnings,
        status='Parsed from your pasted responses' if submitted else 'Waiting for your first response')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Pasted account data should not appear in request logs.
        pass

    def send_content(self, content, content_type='text/html; charset=utf-8', download=False):
        body = content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="player-state.json"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != '/':
            self.send_error(404)
            return
        self.send_content(render())

    def do_POST(self):
        if self.path != '/':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 65536:
                self.send_error(413, 'Response text is too large')
                return
            if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                self.send_error(415, 'Expected form input')
                return
            fields = parse_qs(self.rfile.read(length).decode('utf-8'), max_num_fields=10)
        except (ValueError, UnicodeDecodeError):
            self.send_error(400, 'Invalid form input')
            return
        profile = fields.get('profile', [''])[0]
        buffs = fields.get('buffs', [''])[0]
        prestige_shop = fields.get('prestige_shop', [''])[0]
        pet = fields.get('pet', [''])[0]
        if fields.get('format', [''])[0] == 'json':
            self.send_content(json.dumps(parse_responses(profile, buffs, prestige_shop, pet), indent=2, ensure_ascii=False),
                              'application/json; charset=utf-8', download=True)
        else:
            self.send_content(render(profile, buffs, prestige_shop, pet, submitted=True))


def main():
    parser = argparse.ArgumentParser(description='Open the local player logbook')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        print(f'Player logbook: http://127.0.0.1:{server.server_port}', flush=True)
        print('Press Ctrl+C to stop. Pasted responses are not stored.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
