"""Bring outside media (H3 / LongCat clips, generated music, recordings) into a film, the deterministic way.

    python mars/media.py frames <clip.mp4> <film>/clips/<name> [--fps 24] [--size 1920x1080] [--quality 3]
        → <dir>/f00001.jpg … + <dir>/clip.json {fps, frames, dur, w, h} (+ <dir>/audio.wav when the clip has sound)
    python mars/media.py wav <in.mp3|flac|m4a|mp4> <out.wav> [--sr 48000]
        → 48 kHz stereo 16-bit WAV that soundfile (and so every mix.py) can read

Why frames and not <video>: render(t) must draw any frame on its own, in any order, in any worker. A <video> element
seeks asynchronously and can show the previous frame, so the film is extracted once to stills at the film's fps and the
page picks still number floor(t × fps) (mars/web/clip.js does it). Keep clips short; 5 s of 1080p JPEG is about 25 MB.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import tool, utf8_console  # noqa: E402


def probe(path: Path) -> dict:
    r = subprocess.run([tool('ffprobe'), '-v', 'error', '-show_entries', 'stream=codec_type,width,height:format=duration',
                        '-of', 'json', str(path)], capture_output=True, text=True, encoding='utf-8')
    if r.returncode:
        sys.exit(f'media.py: ffprobe cannot read {path}: {r.stderr.strip()[:300]}')
    return json.loads(r.stdout)


def frames(a) -> None:
    src, out = Path(a.src).resolve(), Path(a.out).resolve()
    info = probe(src)
    v = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
    if not v:
        sys.exit(f'media.py: {src} has no video stream')
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('f*.jpg'):
        old.unlink()
    vf = f'fps={a.fps}'
    w, h = v['width'], v['height']
    if a.size:
        w, h = map(int, a.size.lower().split('x'))
        vf += f',scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}'   # cover, centred
    ff = tool('ffmpeg')
    # cwd = the output folder and a relative pattern, so characters in the path never reach ffmpeg's pattern parser
    r = subprocess.run([ff, '-y', '-loglevel', 'error', '-i', str(src), '-vf', vf, '-q:v', str(a.quality), 'f%05d.jpg'], cwd=str(out))
    if r.returncode:
        sys.exit('media.py: ffmpeg failed extracting frames (its message is above)')
    n = len(list(out.glob('f*.jpg')))
    meta = {'src': src.name, 'fps': a.fps, 'frames': n, 'dur': round(n / a.fps, 4), 'w': w, 'h': h, 'audio': None}
    if any(s['codec_type'] == 'audio' for s in info['streams']):
        r = subprocess.run([ff, '-y', '-loglevel', 'error', '-i', str(src), '-vn', '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', 'audio.wav'],
                           cwd=str(out))
        if r.returncode == 0:
            meta['audio'] = 'audio.wav'
    (out / 'clip.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"{out}  {n} frames @ {a.fps} fps = {meta['dur']} s{'  + audio.wav' if meta['audio'] else ''}")


def wav(a) -> None:
    src, dst = Path(a.src).resolve(), Path(a.out).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + '.part.wav')
    r = subprocess.run([tool('ffmpeg'), '-y', '-loglevel', 'error', '-i', str(src), '-vn', '-ac', '2', '-ar', str(a.sr), '-c:a', 'pcm_s16le',
                        str(tmp)])
    if r.returncode or not tmp.exists():
        tmp.unlink(missing_ok=True)
        sys.exit('media.py: ffmpeg failed converting the audio (its message is above)')
    tmp.replace(dst)
    print(dst)


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('frames')
    f.add_argument('src')
    f.add_argument('out')
    f.add_argument('--fps', type=float, default=24)
    f.add_argument('--size')
    f.add_argument('--quality', type=int, default=3, help='JPEG quality 2 (best) … 31')
    w = sub.add_parser('wav')
    w.add_argument('src')
    w.add_argument('out')
    w.add_argument('--sr', type=int, default=48000)
    a = ap.parse_args()
    {'frames': frames, 'wav': wav}[a.cmd](a)


if __name__ == '__main__':
    main()
