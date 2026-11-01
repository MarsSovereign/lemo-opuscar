"""Generate a film's AI plates in one go through ComfyUI (Qwen-Image 2.1, MOSS, FLUX3… any profile), then a contact sheet.

    python mars/gen_images.py <profile.json> <shots.json> <out_dir> [--redo id,id] [--reseed id,id] [--set slot=value …] [--no-sheet]

shots.json = [{"id": "s01", "prompt": "…", "seed"?: 123, "width"?: 1344, "height"?: 768, "image"?: "ref.png", …}, …]
  every key except id is a profile slot (see mars/comfy.py). --set gives a value to every shot (e.g. --set steps=8).
Output: <out_dir>/<id>.png (+ <id>.json: the exact values, seed included) and <out_dir>/_sheet.jpg for review.
A shot whose output exists with the same values is skipped, so an interrupted batch continues where it stopped.
--redo ids makes those again with the same seed (after a workflow change); --reseed ids draws a new seed (a bad image).
Images are plates for the page: text, labels and captions are drawn by the page, never asked of the model.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comfy import ComfyError, coerce, run  # noqa: E402
from common import LIB, child_env, python, utf8_console  # noqa: E402


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('profile', type=Path)
    ap.add_argument('shots', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--redo', default='')
    ap.add_argument('--reseed', default='')
    ap.add_argument('--set', action='append', default=[])
    ap.add_argument('--no-sheet', action='store_true')
    a = ap.parse_args()

    shots = json.loads(a.shots.read_text(encoding='utf-8'))
    if isinstance(shots, dict):
        shots = shots.get('shots', [])
    ids = [s.get('id') for s in shots]
    if not shots or None in ids or len(set(ids)) != len(ids):
        sys.exit('gen_images.py: shots.json must be a list of shots, each with a unique "id"')
    redo = {x for x in a.redo.split(',') if x}
    reseed = {x for x in a.reseed.split(',') if x}
    common = {k: coerce(v) for k, v in (kv.partition('=')[::2] for kv in a.set)}
    a.out.mkdir(parents=True, exist_ok=True)

    made = []
    for s in shots:
        sid = s['id']
        vals = {**common, **{k: v for k, v in s.items() if k != 'id'}}
        meta_f = a.out / f'{sid}.json'
        old = json.loads(meta_f.read_text(encoding='utf-8')) if meta_f.exists() else None
        outs = [a.out / f for f in (old or {}).get('files', [])]
        if 'seed' not in vals and old and sid not in reseed:
            vals['seed'] = old['values'].get('seed')        # keep the seed a shot was made with
        if sid in reseed:
            vals['seed'] = random.randint(1, 2 ** 31 - 1)
        if old and outs and all(f.exists() for f in outs) and sid not in redo | reseed and \
                all(old['values'].get(k) == v for k, v in vals.items() if v is not None):
            print(f'{sid}  (kept)')
            made += outs
            continue
        vals = {k: v for k, v in vals.items() if v is not None}
        try:
            files = run(a.profile, a.out, sid, vals)
        except ComfyError as e:
            sys.exit(f'gen_images.py: shot {sid}: {e}\n  rerun the same command to continue from here')
        print(f'{sid}  → {", ".join(f.name for f in files)}', flush=True)
        made += files

    imgs = [f for f in made if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')]
    if imgs and not a.no_sheet:
        sheet = a.out / '_sheet.jpg'
        r = subprocess.run([python(), str(LIB / 'core' / 'render' / 'sheet.py'), str(sheet), *map(str, imgs), '--cols', '4', '--w', '480'],
                           env=child_env(), capture_output=True, text=True, encoding='utf-8')
        print(f'sheet: {sheet}' if r.returncode == 0 else f'(no contact sheet: {r.stderr.strip()[:200]})')


if __name__ == '__main__':
    main()
