"""Build a film end to end without sh (Windows-native replacement for a film's build.sh):

    python mars/build.py <film_dir> [--fps 24] [--workers 3] [--size 1920x1080] [--grain 2] [--q 'k=v&…']
                                    [--name <file name>] [--from events|video|mix|mux] [--to events|video|mix|mux]

Steps (each one a library tool, run from the library root):
  events  node core/render/events.mjs   → <film>/events.json   (only when the page sets window.EV)
  video   node core/render/video.mjs    → <film>/out/video.mp4
  mix     <python> <film>/mix.py        → <film>/out/mix.wav   (the film's own mixer; LIB and FILM are in its environment)
  mux     python mars/mux.py            → <film>/<name>.mp4

Settings may also live in <film>/film.json: {"name", "fps", "workers", "size", "grain", "q", "mix"}; flags win.
A failed step stops the build with its exit code; rerun with --from <step> to continue.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import LIB, MARS, child_env, python, run, utf8_console  # noqa: E402

STEPS = ['events', 'video', 'mix', 'mux']


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('film')
    for k in ('fps', 'workers', 'size', 'grain', 'q', 'name', 'mix'):
        ap.add_argument(f'--{k}')
    ap.add_argument('--from', dest='start', choices=STEPS, default='events')
    ap.add_argument('--to', dest='stop', choices=STEPS, default='mux')
    a = ap.parse_args()

    film = Path(a.film).resolve()
    if not (film / 'index.html').is_file():
        sys.exit(f'no index.html in {film}')
    cfg = {}
    if (film / 'film.json').is_file():
        cfg = json.loads((film / 'film.json').read_text(encoding='utf-8'))
    get = lambda k, d=None: getattr(a, k) if getattr(a, k) is not None else cfg.get(k, d)  # noqa: E731
    fps, workers, grain = str(get('fps', 24)), str(get('workers', 3)), str(get('grain', 2))
    size, q, name = get('size'), get('q'), get('name', film.name)
    mix = film / get('mix', 'mix.py')
    page = [*(['--size', size] if size else []), *(['--q', q] if q else [])]
    node = shutil.which('node') or sys.exit('node not found: install Node 20+')
    out = film / 'out'
    out.mkdir(exist_ok=True)
    steps = STEPS[STEPS.index(a.start): STEPS.index(a.stop) + 1]

    def step(cmd: list) -> None:
        code = run(cmd, cwd=LIB)
        if code:
            sys.exit(f'✗ step failed (exit {code}); fix it and rerun with --from <step>')

    if 'events' in steps:
        step([node, 'core/render/events.mjs', film, *page])
    if 'video' in steps:
        step([node, 'core/render/video.mjs', film, '--fps', fps, '--workers', workers, '--out', out / 'video.mp4', *page])
    if 'mix' in steps:
        if mix.is_file():
            print('»', python(), mix, flush=True)
            code = subprocess.call([python(), str(mix)], cwd=str(film), env=child_env(LIB=str(LIB), FILM=str(film)))
            if code:
                sys.exit(f'✗ mix failed (exit {code})')
        elif not (out / 'mix.wav').is_file():
            sys.exit(f'✗ no {mix.name} in the film folder and no out/mix.wav: write the mixer first')
    if 'mux' in steps:
        step([python(), MARS / 'mux.py', out / 'video.mp4', out / 'mix.wav', film / f'{name}.mp4', fps, grain])
        print(f'✓ {film / (name + ".mp4")}')


if __name__ == '__main__':
    main()
