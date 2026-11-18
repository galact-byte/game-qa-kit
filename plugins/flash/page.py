"""Serve the admitted movie and Ruffle assets from a session-local origin."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import uuid
from zipfile import ZipFile

from gameqakit.admission import file_digest
from .prepare import ARCHIVE, ARCHIVE_SHA, SCRIPT

ASSETS = {
    'ruffle.js': 'text/javascript',
    'core.ruffle.16d61072ac7fdc4d5013.js': 'text/javascript',
    'core.ruffle.994eeef0a471a88cd0e3.js': 'text/javascript',
    '172de42e6619eac8f371.wasm': 'application/wasm',
    '878d548199cddef07035.wasm': 'application/wasm',
}
PAGE = b'''<!doctype html><meta charset="utf-8"><body style="margin:0">
<script>window.RufflePlayer = {config: {polyfills:false, allowNetworking:'none', allowScriptAccess:false}};</script>
<script src="ruffle.js"></script>
<script>
(async () => {
  const p = window.RufflePlayer.newest().createPlayer();
  p.style.cssText = 'display:block;width:320px;height:240px';
  document.body.appendChild(p);
  await p.ruffle().load({url:'gameqa-flash-fixture.swf', autoplay:'on', unmuteOverlay:'hidden'});
})();
</script>'''


def unpack(runtime):
    archive = runtime / ARCHIVE.name
    if file_digest(archive) != ARCHIVE_SHA:
        raise ValueError('Ruffle 资源副本摘要不符')
    asset_dir = runtime / 'ruffle'
    asset_dir.mkdir()
    with ZipFile(archive) as bundle:
        for name in (*ASSETS, 'LICENSE_APACHE', 'LICENSE_MIT'):
            (asset_dir / name).write_bytes(bundle.read(name))


@contextmanager
def local_page(runtime):
    prefix = '/' + uuid.uuid4().hex + '/'
    contents = {'': ('text/html; charset=utf-8', PAGE),
                SCRIPT: ('application/x-shockwave-flash', (runtime / SCRIPT).read_bytes()),
                **{name: (mime, (runtime / 'ruffle' / name).read_bytes()) for name, mime in ASSETS.items()}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Host') != server_address or not self.path.startswith(prefix):
                self.send_error(404)
                return
            resource = contents.get(self.path[len(prefix):])
            if resource is None:
                self.send_error(404)
                return
            mime, body = resource
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self' 'unsafe-inline' "
                             "'wasm-unsafe-eval'; connect-src 'self'; style-src 'unsafe-inline'; "
                             "worker-src 'self' blob:; img-src 'self' data: blob:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        server_address = f'127.0.0.1:{server.server_port}'
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        try:
            yield 'http://' + server_address + prefix
        finally:
            server.shutdown()
            thread.join()
