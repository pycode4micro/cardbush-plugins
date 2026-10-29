"""Explicit cross-repository contract test; never requires a sibling checkout."""
import argparse
from contextlib import contextmanager
import base64
import http.client
import ipaddress
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--cardbush-root', type=Path, required=True)
options, unittest_args = parser.parse_known_args()
distribution = options.cardbush_root.resolve() / 'scripts/plugin-distribution'
if not (distribution / 'lan_gateway.py').is_file():
    parser.error('Expected a CardBush checkout containing the generic LAN gateway')
sys.path.insert(0, str(distribution))
from lan_gateway import BoundedServer, make_handler, load_routes
import test_gallery as fixtures
from test_gallery import rpc, payload
from product_gallery import TOOLS
import server as lan_client

class GatewayIntegrationTests(unittest.TestCase):
    setUp = fixtures.GalleryTests.setUp
    tearDown = fixtures.GalleryTests.tearDown

    @contextmanager
    def gateway(self):
        route = {'token': 'test-token-' * 4, 'allowed_tools': {t['name'] for t in TOOLS}, 'handler': self.gallery.handle}
        server = BoundedServer(('127.0.0.1', 0), make_handler({'/mcp/gallery': route}, [ipaddress.ip_network('127.0.0.0/8')]))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, route
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_http_gateway_and_existing_client_deliver_images_with_route_auth(self):
        with self.gateway() as (server, route):
            (self.base / 'fixture.json').write_text(json.dumps({
                'url': f'http://127.0.0.1:{server.server_port}/mcp/gallery', 'token': route['token']}))
            with patch.dict(os.environ, {'CARDBUSH_PLUGIN_CONNECTIONS': str(self.base)}):
                descriptor = {'id': 'fixture', 'version': '1', 'tools': TOOLS}
                response = lan_client.handle(rpc('get_product_image', {'product_code': 'ABC123'}), descriptor)
            self.assertEqual(base64.b64decode(response['result']['content'][1]['data']), self.data)
            for token, name, status in [('wrong', 'get_product_image', 401),
                                        (route['token'], 'sync', 403)]:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                try:
                    connection.request('POST', '/mcp/gallery', json.dumps(rpc(name, {'product_code': 'ABC123'})),
                                       {'Authorization': 'Bearer ' + token})
                    result = connection.getresponse()
                    self.assertEqual(result.status, status)
                    self.assertNotIn(base64.b64encode(self.data), result.read())
                finally:
                    connection.close()

    def test_route_factory_receives_only_administrator_settings(self):
        import types
        calls = []
        module = types.SimpleNamespace(TOOLS=TOOLS, create_handler=lambda settings: calls.append(settings) or self.gallery.handle)
        with patch('lan_gateway.importlib.import_module', return_value=module):
            routes = load_routes({'routes': [{'path': '/mcp/gallery', 'module': 'fixture',
                'token': 'x' * 40, 'allowed_tools': ['get_product_image'], 'settings': {'image_root': str(self.root)}}]})
        self.assertEqual(calls, [{'image_root': str(self.root)}])
        self.assertEqual(payload(routes['/mcp/gallery']['handler'](rpc('get_product_image', {'product_code': 'ABC123'})))['status'], 'ready')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *unittest_args])
