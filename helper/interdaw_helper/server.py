"""Local service for the InterDAW panel: reads project files, which an Add On page can't do.

Only the InterDAW page may call it. Any website can reach 127.0.0.1, so without the origin
check a page you visited could read your sets' contents through this service.
"""

import json
import os
import re
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .als import AlsError, read_set
from .ret import build, prepare

VERSION = '1.0.1'
# Bumped when a route or its body changes; the panel asks for an update when it differs.
API = 1
DEFAULT_PORT = 8792
# The InterDAW page. Since LUNA 3.1.2 a page installed in the Add Ons folder is served from
# `<id>_t<install token>` (32 hex digits, new for each install), so the origin changes with every
# install or update. LUNA writes `_` in an id as `__`, so no other Add On's id can produce this.
PANEL_ORIGIN = re.compile(r'ua-content://com\.cp\.interdaw(_t[0-9a-f]{32})?')
# `ua-builder serve`, for development only: INTERDAW_DEV=1 python3 -m interdaw_helper
DEV_ORIGINS = {'http://localhost:8790', 'http://127.0.0.1:8790'}
MAX_BODY = 1024 * 1024
PREPARED = set()


class Handler(BaseHTTPRequestHandler):
    server_version = f'InterDAWHelper/{VERSION}'

    def log_message(self, format, *args):
        # Into helper.log (stdout), with the time, so a report shows what the panel asked for.
        print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {format % args}', flush=True)

    def _origin_allowed(self):
        origin = self.headers.get('Origin')
        return bool(origin and PANEL_ORIGIN.fullmatch(origin)) or (os.environ.get('INTERDAW_DEV') == '1' and origin in DEV_ORIGINS)

    def _answer(self, status, body=None):
        data = b'' if body is None else json.dumps(body).encode()
        self.send_response(status)
        if self._origin_allowed():
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Headers', 'content-type')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        elif status == 403:
            # Let any page read the refusal, which holds no set data, so a panel whose origin
            # differs says so instead of "not running".
            self.send_header('Access-Control-Allow-Origin', '*')
            self.log_message('refused origin %r', self.headers.get('Origin'))
        if body is not None:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _refuse(self):
        self._answer(403, {'error': 'origin not allowed', 'origin': self.headers.get('Origin')})

    def do_OPTIONS(self):
        if self._origin_allowed():
            self._answer(204)
        else:
            self._refuse()

    def do_GET(self):
        if not self._origin_allowed():
            self._refuse()
        elif self.path == '/health':
            self._answer(200, {'ok': True, 'version': VERSION, 'api': API})
        else:
            self._answer(404, {'error': 'not found'})

    def do_POST(self):
        if not self._origin_allowed():
            self._refuse()
            return
        routes = {'/als/read': self._read, '/return/prepare': self._prepare, '/return/build': self._build}
        route = routes.get(self.path)
        if route is None:
            self._answer(404, {'error': 'not found'})
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > MAX_BODY:
                raise ValueError('request too large')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('expected an object')
        except (ValueError, TypeError) as error:
            self._answer(400, {'error': f'bad request: {error}'})
            return
        try:
            self._answer(200, route(body))
        except (KeyError, TypeError) as error:
            self._answer(400, {'error': f'bad request: {error}'})
        except AlsError as error:
            self._answer(422, {'error': str(error)})
        except OSError as error:
            self._answer(422, {'error': f'Could not open or write that: {error.strerror or error}'})
        except Exception as error:  # noqa: BLE001
            # Answer anything unexpected: a dropped connection reads as "helper not running" in the panel.
            print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {self.path} failed:', flush=True)
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
            self._answer(500, {'error': f'InterDAW hit something in this set it doesn\'t handle yet ({type(error).__name__}: {str(error)[:160]}). '
                                        'Please report it with helper.log from ~/Library/Logs/InterDAW.'})

    def _read(self, body):
        return read_set(_text(body['path']))

    def _prepare(self, body):
        folder = prepare(_text(body['setPath']), _text(body['parent']), body.get('format', 'both'))
        PREPARED.add(folder)
        return {'folder': folder}

    def _build(self, body):
        # Only a folder this helper made and owns can be written to.
        folder = _text(body['folder'])
        if folder not in PREPARED:
            raise AlsError('That return folder was not prepared here.')
        return build(folder, _text(body['setPath']), body['prints'], body['moves'], body['removed'], body.get('warnings', ()), body.get('format', 'both'), body.get('sessionTempo'))


def _text(value):
    if not isinstance(value, str):
        raise TypeError('expected text')
    return value


def main():
    port = int(os.environ.get('INTERDAW_PORT', DEFAULT_PORT))
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'InterDAW helper listening on http://127.0.0.1:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
