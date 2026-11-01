"""Small in-process stand-ins for ComfyUI and the local TTS service, for the mars/ tests (standard library + numpy/soundfile)."""
from __future__ import annotations

import io
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeComfy:
    """Accepts /prompt, completes it at once, and 'renders' a PNG whose bytes record the submitted workflow."""

    def __init__(self, fail_node: str | None = None):
        self.uploads: dict[str, bytes] = {}
        self.prompts: dict[str, dict] = {}
        self.fail_node = fail_node
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype='application/json'):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == '/system_stats':
                    return self._send(200, {'system': {'comfyui_version': '0.42'},
                                            'devices': [{'name': 'fake', 'vram_free': 2 ** 33, 'vram_total': 2 ** 34}]})
                m = re.fullmatch(r'/history/(\w+)', u.path)
                if m:
                    pid = m.group(1)
                    wf = outer.prompts.get(pid)
                    if wf is None:
                        return self._send(200, {})
                    if outer.fail_node:
                        return self._send(200, {pid: {'outputs': {}, 'status': {'status_str': 'error', 'completed': False, 'messages': [
                            ['execution_error', {'node_id': outer.fail_node, 'node_type': 'KSampler', 'exception_message': 'CUDA out of memory'}]]}}})
                    return self._send(200, {pid: {'outputs': {'9': {'images': [{'filename': f'{pid}.png', 'subfolder': '', 'type': 'output'}]},
                                                              '10': {'images': [{'filename': 'preview.png', 'subfolder': '', 'type': 'temp'}]}},
                                                  'status': {'status_str': 'success', 'completed': True}}})
                if u.path == '/view':
                    q = urllib.parse.parse_qs(u.query)
                    pid = q['filename'][0].split('.')[0]
                    return self._send(200, json.dumps(outer.prompts[pid], ensure_ascii=False).encode(), 'image/png')
                self._send(404, {'error': 'nope'})

            def do_POST(self):
                n = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(n)
                if self.path == '/prompt':
                    wf = json.loads(body)['prompt']
                    pid = f'p{len(outer.prompts)}'
                    outer.prompts[pid] = wf
                    return self._send(200, {'prompt_id': pid, 'number': 0, 'node_errors': {}})
                if self.path == '/upload/image':
                    m = re.search(rb'filename="([^"]+)"\r\nContent-Type: [^\r]+\r\n\r\n(.*?)\r\n--', body, re.S)
                    name = m.group(1).decode()
                    outer.uploads[name] = m.group(2)
                    return self._send(200, {'name': name, 'subfolder': 'lemo', 'type': 'input'})
                self._send(404, {'error': 'nope'})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()


class FakeTTS:
    """Same API as OpenMontage services/local_tts: /health, /voices, /tts (a tone padded with silence, 22.05 kHz)."""

    def __init__(self, voices=('narrator',), loaded=True):
        self.calls: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype='application/json'):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == '/health':
                    return self._send(200, {'status': 'ok' if loaded else 'loading', 'backend': 'indextts25', 'model': 'checkpoints_25',
                                            'loaded': loaded, 'error': None, 'needs_voice': True, 'default_voice': None,
                                            'voices': list(voices)})
                self._send(404, {'detail': 'nope'})

            def do_POST(self):
                import numpy as np
                import soundfile as sf
                req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.calls.append(req)
                if req.get('voice') not in voices:
                    return self._send(404, {'detail': f"Unknown voice {req.get('voice')!r}"})
                sr = 22050
                n = int(sr * (0.3 + 0.1 * len(req['text'])) / req.get('speed', 1.0))
                y = np.zeros(n + sr, 'float32')
                y[sr // 2: sr // 2 + n] = 0.3 * np.sin(np.arange(n) / sr * 2 * np.pi * 300)
                buf = io.BytesIO()
                sf.write(buf, y, sr, format='WAV')
                self._send(200, buf.getvalue(), 'audio/wav')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
