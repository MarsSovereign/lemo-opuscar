"""Run ComfyUI workflows (images, music, video) from a film project. Standard library only.

    python mars/comfy.py check                                   is ComfyUI up? (mars/config.json comfy_url, default :8189)
    python mars/comfy.py slots <workflow_api.json>               list nodes and their settable inputs, to write a profile
    python mars/comfy.py run <profile.json | workflow_api.json> --out <dir> [--name <file stem>]
                         [--set slot=value …] [--set-json slot=<json> …] [--patch node.input=value …] [--timeout 3600]

The workflow must be ComfyUI's "Export (API)" format (a dict of node id → {class_type, inputs}), not the UI save format.
A profile names the inputs you change per call, so a film never hard-codes node ids:

    {"workflow": "qwen21_t2i_api.json",                          path, relative to the profile's folder or absolute
     "slots":   {"prompt": "6.text", "seed": "3.seed", "width": "58.width", "height": "58.height", "image": "12.image"},
     "uploads": ["image"],                                       slots whose value is a local file: uploaded to ComfyUI's input folder
     "random":  ["seed"],                                        slots that get a random value unless set
     "defaults": {"width": 1344, "height": 768},
     "outputs": ["60"]}                                          node ids to download (default: every output)

A slot may map to several inputs: "seed": ["3.seed", "31.noise_seed"]. Each run writes <name>.json next to the outputs with
the values used (seed included), so any result can be made again. Exit codes: 0 done, 1 bad input or ComfyUI error, 2 unreachable.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import config, utf8_console  # noqa: E402


class ComfyError(Exception):
    pass


class Comfy:
    def __init__(self, url: str | None = None):
        self.url = (url or config()['comfy_url']).rstrip('/')
        self.client_id = uuid.uuid4().hex

    def _req(self, path: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 60):
        req = urllib.request.Request(self.url + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')
            raise ComfyError(f'{path}: HTTP {e.code}: {explain(body)}') from None

    def get_json(self, path: str):
        return json.loads(self._req(path).decode('utf-8'))

    def alive(self) -> dict:
        return self.get_json('/system_stats')

    def upload(self, local: Path, subfolder: str = 'lemo') -> str:
        """Upload any file (image, audio, video) to the input folder; returns the name a Load* node expects."""
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(local.name)[0] or 'application/octet-stream'
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{local.name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
            + local.read_bytes() + b'\r\n',
            f'--{boundary}\r\nContent-Disposition: form-data; name="subfolder"\r\n\r\n{subfolder}\r\n'.encode(),
            f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'.encode(),
            f'--{boundary}--\r\n'.encode(),
        ]
        r = json.loads(self._req('/upload/image', b''.join(parts), {'Content-Type': f'multipart/form-data; boundary={boundary}'},
                                 timeout=600).decode('utf-8'))
        return f"{r['subfolder']}/{r['name']}" if r.get('subfolder') else r['name']

    def submit(self, workflow: dict) -> str:
        body = json.dumps({'prompt': workflow, 'client_id': self.client_id}, ensure_ascii=False).encode('utf-8')
        r = json.loads(self._req('/prompt', body, {'Content-Type': 'application/json'}).decode('utf-8'))
        if r.get('node_errors'):
            raise ComfyError('workflow rejected: ' + explain(json.dumps(r)))
        return r['prompt_id']

    def wait(self, prompt_id: str, timeout: float = 3600, every: float = 2.0) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            h = self.get_json(f'/history/{prompt_id}')
            if prompt_id in h:
                entry = h[prompt_id]
                st = entry.get('status', {})
                if st.get('status_str') == 'error':
                    msgs = [m[1] for m in st.get('messages', []) if m[0] == 'execution_error']
                    detail = '; '.join(f"node {m.get('node_id')} ({m.get('node_type')}): {m.get('exception_message', '').strip()}" for m in msgs)
                    raise ComfyError(f'ComfyUI failed: {detail or st}')
                if st.get('completed', True) and entry.get('outputs') is not None:
                    return entry
            time.sleep(every)
        raise ComfyError(f'timed out after {timeout:.0f} s; the job ({prompt_id}) may still be running in ComfyUI')

    def download(self, entry: dict, out_dir: Path, stem: str, only: list[str] | None = None) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for node_id, outs in entry.get('outputs', {}).items():
            if only and node_id not in only:
                continue
            for kind, items in outs.items():
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict) or 'filename' not in it or it.get('type') == 'temp':
                        continue
                    q = urllib.parse.urlencode({'filename': it['filename'], 'subfolder': it.get('subfolder', ''), 'type': it.get('type', 'output')})
                    data = self._req(f'/view?{q}', timeout=600)
                    ext = Path(it['filename']).suffix
                    p = out_dir / f'{stem}{ext}'
                    n = 1
                    while p in files:   # several outputs in one run: name_1, name_2…; a rerun overwrites the old files
                        p = out_dir / f'{stem}_{n}{ext}'
                        n += 1
                    p.write_bytes(data)
                    files.append(p)
        if not files:
            raise ComfyError('the workflow finished but produced no downloadable output (does it end in a Save* node?)')
        return files


def explain(body: str) -> str:
    """Turn ComfyUI's error JSON into one readable line."""
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]
    out = []
    if isinstance(d.get('error'), dict):
        out.append(f"{d['error'].get('message', '')} {d['error'].get('details', '')}".strip())
    for nid, ne in (d.get('node_errors') or {}).items():
        for e in ne.get('errors', []):
            out.append(f"node {nid} ({ne.get('class_type')}): {e.get('message')} {e.get('details', '')}".strip())
    return '; '.join(out) or body[:500]


def load_workflow(path: Path) -> dict:
    wf = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(wf, dict) and 'nodes' in wf and 'links' in wf:
        raise ComfyError(f'{path.name} is the UI save format. In ComfyUI use Workflow → Export (API) and use that file.')
    if not isinstance(wf, dict) or not all(isinstance(v, dict) and 'class_type' in v for v in wf.values()):
        raise ComfyError(f'{path.name} is not an API-format workflow')
    return wf


def load_profile(path: Path) -> tuple[dict, dict]:
    """Returns (workflow, profile). A bare workflow gets an empty profile."""
    d = json.loads(path.read_text(encoding='utf-8'))
    if 'workflow' in d and 'slots' in d:
        wp = Path(d['workflow'])
        if not wp.is_absolute():
            wp = path.parent / wp
        return load_workflow(wp), d
    return load_workflow(path), {}


def targets(spec) -> list[tuple[str, str]]:
    out = []
    for s in spec if isinstance(spec, list) else [spec]:
        nid, _, inp = s.partition('.')
        if not inp:
            raise ComfyError(f'slot target {s!r} must look like "node_id.input_name"')
        out.append((nid, inp))
    return out


def coerce(v: str):
    """--set values: numbers become numbers, true/false booleans, everything else stays text."""
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    return {'true': True, 'false': False}.get(v.lower(), v)


def fill(comfy: Comfy, wf: dict, prof: dict, values: dict, patches: dict) -> dict:
    """Apply profile defaults, the caller's values, random seeds and uploads; returns the values actually used."""
    slots = prof.get('slots', {})
    used = dict(prof.get('defaults', {}))
    used.update(values)
    for k in prof.get('random', []):
        if used.get(k) in (None, '', -1):
            used[k] = random.randint(1, 2 ** 31 - 1)
    unknown = set(used) - set(slots)
    if unknown:
        raise ComfyError(f'unknown slot(s) {sorted(unknown)}; the profile has {sorted(slots)}')
    for k, v in used.items():
        if k in prof.get('uploads', []) and v:
            local = Path(str(v))
            if not local.is_file():
                raise ComfyError(f'slot {k}: file not found: {local}')
            v = comfy.upload(local)
        for nid, inp in targets(slots[k]):
            if nid not in wf:
                raise ComfyError(f'slot {k}: node {nid} is not in the workflow')
            wf[nid]['inputs'][inp] = v
    for target, v in patches.items():
        (nid, inp), = targets(target)
        if nid not in wf:
            raise ComfyError(f'--patch {target}: node {nid} is not in the workflow')
        wf[nid]['inputs'][inp] = v
    return used


def run(profile: Path, out_dir: Path, name: str, values: dict | None = None, patches: dict | None = None,
        url: str | None = None, timeout: float = 3600) -> list[Path]:
    """Library entry point: fill a profile, run it, download the outputs. Returns the downloaded files."""
    comfy = Comfy(url)
    wf, prof = load_profile(profile)
    used = fill(comfy, wf, prof, values or {}, patches or {})
    t0 = time.time()
    pid = comfy.submit(wf)
    entry = comfy.wait(pid, timeout)
    files = comfy.download(entry, out_dir, name, prof.get('outputs'))
    meta = {'profile': str(profile), 'values': used, 'patches': patches or {}, 'prompt_id': pid,
            'seconds': round(time.time() - t0, 1), 'files': [f.name for f in files]}
    (out_dir / f'{name}.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    return files


SLOT_HINTS = {'text': 'prompt', 'prompt': 'prompt', 'seed': 'seed', 'noise_seed': 'seed', 'width': 'width', 'height': 'height',
              'image': 'image', 'audio': 'audio', 'video': 'video', 'filename_prefix': 'prefix', 'length': 'length',
              'steps': 'steps', 'duration': 'duration', 'seconds': 'duration', 'lyrics': 'lyrics', 'tags': 'tags'}


def cmd_slots(path: Path) -> None:
    wf = load_workflow(path)
    suggest = {}
    for nid, n in sorted(wf.items(), key=lambda kv: (len(kv[0]), kv[0])):
        title = n.get('_meta', {}).get('title', '')
        scalars = {k: v for k, v in n['inputs'].items() if not isinstance(v, list)}
        print(f"{nid:>5}  {n['class_type']}{f'  «{title}»' if title else ''}")
        for k, v in scalars.items():
            s = json.dumps(v, ensure_ascii=False)
            print(f'         {k} = {s[:90]}{"…" if len(s) > 90 else ""}')
            if k in SLOT_HINTS:
                suggest.setdefault(SLOT_HINTS[k], []).append(f'{nid}.{k}')
    print('\nsuggested profile slots (check each; e.g. a negative prompt is also a "text"):')
    print(json.dumps({'workflow': path.name, 'slots': {k: v[0] if len(v) == 1 else v for k, v in suggest.items()}},
                     ensure_ascii=False, indent=1))


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('check')
    s = sub.add_parser('slots')
    s.add_argument('workflow', type=Path)
    r = sub.add_parser('run')
    r.add_argument('profile', type=Path)
    r.add_argument('--out', type=Path, required=True)
    r.add_argument('--name', default=None)
    r.add_argument('--set', action='append', default=[], metavar='slot=value')
    r.add_argument('--set-json', action='append', default=[], metavar='slot=json')
    r.add_argument('--patch', action='append', default=[], metavar='node.input=value')
    r.add_argument('--timeout', type=float, default=3600)
    r.add_argument('--url')
    a = ap.parse_args()
    try:
        if a.cmd == 'check':
            c = Comfy()
            st = c.alive()
            dev = (st.get('devices') or [{}])[0]
            print(f"ComfyUI {st.get('system', {}).get('comfyui_version', '?')} at {c.url} · {dev.get('name', '?')} · "
                  f"VRAM free {dev.get('vram_free', 0) / 2**30:.1f} / {dev.get('vram_total', 0) / 2**30:.1f} GB")
        elif a.cmd == 'slots':
            cmd_slots(a.workflow)
        else:
            vals = {}
            for kv in a.set:
                k, _, v = kv.partition('=')
                vals[k] = coerce(v)
            for kv in a.set_json:
                k, _, v = kv.partition('=')
                vals[k] = json.loads(v)
            patches = {}
            for kv in a.patch:
                k, _, v = kv.partition('=')
                patches[k] = coerce(v)
            files = run(a.profile, a.out, a.name or a.profile.stem, vals, patches, a.url, a.timeout)
            for f in files:
                print(f)
    except urllib.error.URLError as e:
        print(f'comfy.py: ComfyUI is not reachable at {Comfy(getattr(a, "url", None)).url} ({e.reason}). Start it on port 8189 first.',
              file=sys.stderr)
        sys.exit(2)
    except (ComfyError, json.JSONDecodeError, OSError) as e:
        print(f'comfy.py: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
