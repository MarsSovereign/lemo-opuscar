"""Tests for the mars/ tools against fake services. Run from the library root:

    .venv/bin/python -m unittest discover -s mars/tests        (Windows: .venv\\Scripts\\python.exe -m unittest discover -s mars\\tests)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARS = HERE.parent
sys.path.insert(0, str(MARS))
sys.path.insert(0, str(HERE))

import comfy  # noqa: E402
import mux  # noqa: E402
from fake_services import FakeComfy, FakeTTS  # noqa: E402

WF = {
    '3': {'class_type': 'KSampler', 'inputs': {'seed': 1, 'steps': 8, 'model': ['4', 0]}},
    '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'old', 'clip': ['4', 1]}},
    '12': {'class_type': 'LoadImage', 'inputs': {'image': 'x.png'}},
    '58': {'class_type': 'EmptySD3LatentImage', 'inputs': {'width': 1024, 'height': 1024, 'batch_size': 1}},
    '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'ComfyUI', 'images': ['8', 0]}},
}


def py(*args, env=None, **kw):
    e = dict(os.environ, PYTHONUTF8='1', **(env or {}))
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, encoding='utf-8', env=e, **kw)


class ComfyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / 'wf_api.json').write_text(json.dumps(WF), encoding='utf-8')
        (self.tmp / 'ref.png').write_bytes(b'\x89PNGfake')
        self.profile = self.tmp / 'qwen.json'
        self.profile.write_text(json.dumps({
            'workflow': 'wf_api.json',
            'slots': {'prompt': '6.text', 'seed': '3.seed', 'width': '58.width', 'height': '58.height', 'image': '12.image'},
            'uploads': ['image'], 'random': ['seed'], 'defaults': {'width': 1344, 'height': 768}, 'outputs': ['9']}), encoding='utf-8')

    def test_run_fills_slots_uploads_and_records(self):
        with FakeComfy() as fc:
            files = comfy.run(self.profile, self.tmp / 'out', 's01', {'prompt': '雨中的红伞', 'image': str(self.tmp / 'ref.png')}, url=fc.url)
            self.assertEqual([f.name for f in files], ['s01.png'])          # temp previews and other nodes skipped
            sent = json.loads(files[0].read_text(encoding='utf-8'))
            self.assertEqual(sent['6']['inputs']['text'], '雨中的红伞')
            self.assertEqual((sent['58']['inputs']['width'], sent['58']['inputs']['height']), (1344, 768))
            self.assertEqual(sent['12']['inputs']['image'], 'lemo/ref.png')
            self.assertIn('ref.png', fc.uploads)
            self.assertNotEqual(sent['3']['inputs']['seed'], 1)            # random seed drawn
            meta = json.loads((self.tmp / 'out' / 's01.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['values']['seed'], sent['3']['inputs']['seed'])

    def test_fixed_seed_and_patch(self):
        with FakeComfy() as fc:
            f, = comfy.run(self.profile, self.tmp / 'out', 's02', {'prompt': 'a', 'seed': 42}, {'3.steps': 4}, url=fc.url)
            sent = json.loads(f.read_text(encoding='utf-8'))
            self.assertEqual((sent['3']['inputs']['seed'], sent['3']['inputs']['steps']), (42, 4))

    def test_errors(self):
        with FakeComfy(fail_node='3') as fc:
            with self.assertRaisesRegex(comfy.ComfyError, 'CUDA out of memory'):
                comfy.run(self.profile, self.tmp / 'out', 'x', {'prompt': 'a'}, url=fc.url)
            with self.assertRaisesRegex(comfy.ComfyError, 'unknown slot'):
                comfy.run(self.profile, self.tmp / 'out', 'x', {'colour': 'red'}, url=fc.url)
        ui = self.tmp / 'ui.json'
        ui.write_text(json.dumps({'nodes': [], 'links': []}), encoding='utf-8')
        with self.assertRaisesRegex(comfy.ComfyError, 'Export \\(API\\)'):
            comfy.load_workflow(ui)

    def test_gen_images_batch_resume_reseed(self):
        shots = self.tmp / 'shots.json'
        shots.write_text(json.dumps([{'id': 's01', 'prompt': '一'}, {'id': 's02', 'prompt': '二', 'seed': 7}], ensure_ascii=False),
                         encoding='utf-8')
        with FakeComfy() as fc:
            env = {'LEMO_COMFY_URL': fc.url}
            r = py(MARS / 'gen_images.py', self.profile, shots, self.tmp / 'img', '--set', 'steps=8', env=env)
            self.assertNotEqual(r.returncode, 0)                            # steps is not a slot of this profile
            r = py(MARS / 'gen_images.py', self.profile, shots, self.tmp / 'img', env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(fc.prompts), 2)
            seed1 = json.loads((self.tmp / 'img' / 's01.json').read_text(encoding='utf-8'))['values']['seed']
            r = py(MARS / 'gen_images.py', self.profile, shots, self.tmp / 'img', env=env)
            self.assertEqual(len(fc.prompts), 2, r.stdout)                  # both kept
            py(MARS / 'gen_images.py', self.profile, shots, self.tmp / 'img', '--redo', 's01', env=env)
            self.assertEqual(json.loads((self.tmp / 'img' / 's01.json').read_text(encoding='utf-8'))['values']['seed'], seed1)
            py(MARS / 'gen_images.py', self.profile, shots, self.tmp / 'img', '--reseed', 's01', env=env)
            self.assertEqual(len(fc.prompts), 4)
            self.assertNotEqual(json.loads((self.tmp / 'img' / 's01.json').read_text(encoding='utf-8'))['values']['seed'], seed1)

    def test_cli_unreachable_and_slots(self):
        r = py(MARS / 'comfy.py', 'check', env={'LEMO_COMFY_URL': 'http://127.0.0.1:9'})
        self.assertEqual(r.returncode, 2, r.stderr)
        r = py(MARS / 'comfy.py', 'slots', self.tmp / 'wf_api.json')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"prompt": "6.text"', r.stdout)


class TTSTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lines = self.tmp / 'lines.json'
        self.lines.write_text(json.dumps([{'id': 'a', 'text': '我有个朋友。'}, {'id': 'b', 'text': '第一条：看合同。', 'say': '第一条，看合同。'}],
                                         ensure_ascii=False), encoding='utf-8')

    def test_synth_cache_redo(self):
        with FakeTTS() as ft:
            env = {'LEMO_TTS_URL': ft.url}
            r = py(MARS / 'tts_local.py', self.lines, self.tmp / 'vo', '--voice', 'narrator', env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            dur = json.loads((self.tmp / 'vo' / 'dur.json').read_text(encoding='utf-8'))
            self.assertEqual(set(dur), {'a', 'b'})
            self.assertLess(dur['a'], 0.3 + 0.1 * 6 + 0.2)                  # silence trimmed
            self.assertEqual(ft.calls[1]['text'], '第一条，看合同。')          # "say" is what is spoken
            import soundfile as sf
            self.assertEqual(sf.info(str(self.tmp / 'vo' / 'a.wav')).samplerate, 24000)
            py(MARS / 'tts_local.py', self.lines, self.tmp / 'vo', '--voice', 'narrator', env=env)
            self.assertEqual(len(ft.calls), 2)                              # cached
            py(MARS / 'tts_local.py', self.lines, self.tmp / 'vo', '--voice', 'narrator', '--redo', 'b', env=env)
            self.assertEqual(len(ft.calls), 3)

    def test_bad_voice_and_down(self):
        with FakeTTS() as ft:
            r = py(MARS / 'tts_local.py', self.lines, self.tmp / 'vo', '--voice', 'ghost', env={'LEMO_TTS_URL': ft.url})
            self.assertEqual(r.returncode, 1)
            self.assertIn('not on the service', r.stderr)
        r = py(MARS / 'tts_local.py', self.lines, self.tmp / 'vo', '--voice', 'narrator', env={'LEMO_TTS_URL': 'http://127.0.0.1:9'})
        self.assertEqual(r.returncode, 2)
        with FakeTTS(loaded=False) as ft:
            r = py(MARS / 'tts_local.py', self.lines, self.tmp / 'vo', '--voice', 'narrator', env={'LEMO_TTS_URL': ft.url})
            self.assertEqual(r.returncode, 2)


class MuxTests(unittest.TestCase):
    def test_wav_truncation(self):
        import numpy as np
        import soundfile as sf
        tmp = Path(tempfile.mkdtemp())
        sf.write(str(tmp / 'a.wav'), np.zeros(48000, 'float32'), 48000)
        self.assertFalse(mux.wav_truncated(str(tmp / 'a.wav')))
        (tmp / 'b.wav').write_bytes((tmp / 'a.wav').read_bytes()[:50000])
        self.assertTrue(mux.wav_truncated(str(tmp / 'b.wav')))
        (tmp / 'c.txt').write_text('not audio')
        self.assertFalse(mux.wav_truncated(str(tmp / 'c.txt')))


if __name__ == '__main__':
    unittest.main()
