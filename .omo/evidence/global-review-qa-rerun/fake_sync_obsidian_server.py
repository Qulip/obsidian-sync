from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, status, payload):
        raw = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.command, self.path))

    def do_GET(self):
        if self.path == '/':
            self._send(200, {'authenticated': False})
            return
        if self.path.startswith('/vaults/qa-rerun/sync/changes'):
            self._send(200, {'success': True, 'data': {'vault_id': 'qa-rerun', 'from_cursor': 0, 'to_cursor': 0, 'changes': []}, 'error': None})
            return
        self._send(404, {'success': False, 'data': None, 'error': {'code': 'not_found', 'message': 'not found', 'details': {}}})

    def do_POST(self):
        length = int(self.headers.get('content-length', '0'))
        if length:
            self.rfile.read(length)
        if self.path == '/vaults/qa-rerun/sync/devices':
            self._send(200, {'success': True, 'data': {'vault_id': 'qa-rerun', 'device_id': 'qa-device', 'registered': True}, 'error': None})
            return
        self._send(404, {'success': False, 'data': None, 'error': {'code': 'not_found', 'message': 'not found', 'details': {}}})

    def do_PUT(self):
        length = int(self.headers.get('content-length', '0'))
        body = json.loads(self.rfile.read(length) or b'{}')
        path = self.path.split('/files/', 1)[-1]
        self._send(200, {'success': True, 'data': {'vault_id': 'qa-rerun', 'path': path, 'revision': 1, 'content_hash': body.get('content_hash', '')}, 'error': None})

    def do_DELETE(self):
        length = int(self.headers.get('content-length', '0'))
        if length:
            self.rfile.read(length)
        path = self.path.split('/files/', 1)[-1]
        self._send(200, {'success': True, 'data': {'vault_id': 'qa-rerun', 'path': path, 'revision': 1, 'deleted': True}, 'error': None})

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
