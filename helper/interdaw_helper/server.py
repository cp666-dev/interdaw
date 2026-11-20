"""Local service for the InterDAW panel: reads project files, which an Add On page can't do.

Only the InterDAW page may call it. Any website can reach 127.0.0.1, so without the origin
check a page you visited could read your sets' contents through this service.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .als import AlsError, read_set
from .ret import build, prepare

VERSION = '1.0.0'
# Bumped when a route or its body changes; the panel asks for an update when it differs.
API = 1
DEFAULT_PORT = 8792
ALLOWED_ORIGINS = {'ua-content://com.cp.interdaw'}
# `ua-builder serve`, for development only: INTERDAW_DEV=1 python3 -m interdaw_helper
DEV_ORIGINS = {'http://localhost:8790', 'http://127.0.0.1:8790'}
MAX_BODY = 1024 * 1024
PREPARED = set()


class Handler(BaseHTTPRequestHandler):
    server_version = f'InterDAWHelper/{VERSION}'

    def log_message(self, *args):
        pass

    def _origin_allowed(self):
        origin = self.headers.get('Origin')
        return origin in ALLOWED_ORIGINS or (os.environ.get('INTERDAW_DEV') == '1' and origin in DEV_ORIGINS)

    def _answer(self, status, body=None):
        data = b'' if body is None else json.dumps(body).encode()
        self.send_response(status)
        if self._origin_allowed():
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Headers', 'content-type')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        if body is not None:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._answer(204 if self._origin_allowed() else 403)

    def do_GET(self):
        if not self._origin_allowed():
            self._answer(403, {'error': 'origin not allowed'})
        elif self.path == '/health':
            self._answer(200, {'ok': True, 'version': VERSION, 'api': API})
        else:
            self._answer(404, {'error': 'not found'})

    def do_POST(self):
        if not self._origin_allowed():
            self._answer(403, {'error': 'origin not allowed'})
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
            self._answer(422, {'error': f'Could not write that: {error.strerror or error}'})

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
