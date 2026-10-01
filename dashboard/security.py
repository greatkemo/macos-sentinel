"""Loopback-only host validation and per-run bearer sessions; no persistent secrets."""
import secrets
from starlette.responses import JSONResponse

class LocalSession:
    def __init__(self, app, token, port=8000):
        self.app, self.token = app, token
        self.hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        self.origins = {f'http://{host}' for host in self.hosts}

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        host = headers.get(b'host', b'').decode("latin-1").lower()
        origin = headers.get(b'origin', b'').decode("latin-1")
        websocket = scope['type'] == 'websocket'
        valid = host in self.hosts and (not origin or origin in self.origins)
        protected = websocket or scope['path'].startswith('/api/')
        if protected:
            if websocket:
                protocols = [p.strip() for p in headers.get(b'sec-websocket-protocol', b'').decode("latin-1").split(',')]
                supplied = protocols[1] if len(protocols) == 2 and protocols[0] == 'sentinel' else ''
                valid = valid and origin in self.origins
            else:
                supplied = headers.get(b'x-sentinel-token', b'').decode("latin-1")
                if scope['method'] not in ('GET', 'HEAD'):
                    valid = valid and origin in self.origins
            valid = valid and secrets.compare_digest(supplied.encode("latin-1"), self.token.encode())
        if not valid:
            if websocket:
                return await send({'type':'websocket.close', 'code':1008})
            return await JSONResponse({'detail':'Local session required. Reload this dashboard.'}, status_code=403)(scope, receive, send)
        async def secured_send(message):
            if message['type'] == 'http.response.start':
                message.setdefault('headers', []).extend([
                    (b'cache-control', b'no-store'),
                    (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
                ])
            await send(message)
        await self.app(scope, receive, secured_send)
