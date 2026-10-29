import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server


class ClientTests(unittest.TestCase):
    def test_marketplace_manifest_points_to_a_working_source_entry(self):
        manifest = json.loads((ROOT / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
        mcp = json.loads((ROOT / manifest['mcpServers']).read_text(encoding='utf-8'))
        entry = mcp['mcpServers'][manifest['name']]
        self.assertEqual(entry['command'], 'python')
        script = entry['args'][0].replace('${CLAUDE_PLUGIN_ROOT}', str(ROOT))
        self.assertTrue(Path(script).is_file())
        catalog = json.loads((ROOT.parents[1] / '.agents/plugins/marketplace.json').read_text(encoding='utf-8'))
        registered = [item for item in catalog['plugins'] if item['name'] == manifest['name']]
        self.assertEqual(len(registered), 1)
        self.assertEqual((ROOT.parents[1] / registered[0]['source']['path']).resolve(), ROOT)

    def test_source_stdio_runs_from_unrelated_directory_without_cardbush_or_credentials(self):
        with tempfile.TemporaryDirectory(prefix='gallery-source-client-') as temporary:
            environment = {**os.environ, 'CARDBUSH_PLUGIN_CONNECTIONS': temporary}
            environment.pop('PYTHONPATH', None)
            requests = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                    'name': 'lookup_product_images', 'arguments': {'product_codes': ['TEST123']}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'sync', 'arguments': {}}},
            ]
            process = subprocess.run([sys.executable, '-I', str(ROOT / 'server.py')], cwd=temporary,
                env=environment, input=''.join(json.dumps(request) + '\n' for request in requests),
                text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([item['id'] for item in responses], [1, 2, 3, 4])
        self.assertEqual(responses[0]['result']['serverInfo']['name'], 'zjfs-product-image-gallery')
        tools = responses[1]['result']['tools']
        self.assertEqual({tool['name'] for tool in tools}, {'lookup_product_images', 'get_product_image'})
        self.assertTrue(all(tool['annotations']['readOnlyHint'] for tool in tools))
        self.assertTrue(responses[2]['result']['isError'])
        self.assertEqual(responses[3]['error']['code'], -32602)

    def test_unknown_tool_never_reads_configuration_or_uses_network(self):
        descriptor = json.loads((ROOT / 'lan-tools.json').read_text(encoding='utf-8'))
        with patch('server.connection', side_effect=AssertionError('must not load configuration')):
            response = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                      'params': {'name': 'delete_image', 'arguments': {}}}, descriptor)
        self.assertEqual(response['error']['code'], -32602)

    def test_private_configuration_keeps_plain_http_and_redirect_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'zjfs-product-image-gallery.json'
            with patch.dict(os.environ, {'CARDBUSH_PLUGIN_CONNECTIONS': temporary}):
                for url in ['http://8.8.8.8/mcp', 'http://0.0.0.0/mcp', 'file:///secret',
                            'http://user:password@127.0.0.1/mcp']:
                    config.write_text(json.dumps({'url': url, 'token': 'test-token-' * 4}), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        server.connection('zjfs-product-image-gallery')
        with self.assertRaises(ValueError):
            server.NoRedirect().redirect_request(None, None, None, None, None, None)


if __name__ == '__main__':
    unittest.main()
