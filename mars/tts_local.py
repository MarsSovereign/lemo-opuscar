"""Narration from the local TTS service (OpenMontage services/local_tts: IndexTTS2.5, CosyVoice3, …).
Same input and output as core/tts/tts.py and tts_zh.py, so every downstream step (asr_check, mix, subtitles) is unchanged:

    python mars/tts_local.py lines.json out_dir [--voice <name>] [--speed 1.0] [--url http://127.0.0.1:9880] [--redo id,id]

lines.json = [{"id", "text", "say"?, "voice"?, "speed"?, "take"?}, ...]
  say    what is spoken when it differs from text (numbers written out, polyphones marked: IndexTTS2.5 takes <行|HANG2>)
  voice  a reference clip name in the service's LOCAL_TTS_VOICES_DIR (default: --voice, then mars/config.json tts_voice,
         then the service's LOCAL_TTS_DEFAULT_VOICE)
  speed  0.5-2.0 (IndexTTS2.5 uses its native duration_factor; never speed narration up afterwards)
  take   any value; change it to get a new reading of one line (the service has no seed), e.g. after asr_check flags it
Output: out_dir/<id>.wav (24 kHz mono, leading/trailing silence trimmed) and out_dir/dur.json.
Readings are cached in out_dir/.cache/ by backend + model + voice + speed + text + take; --redo ids ignores the cache for those lines.

Start the service first (one model in GPU memory at a time; stop it before H3/LongCat runs). Exit codes: 0 done, 1 bad input,
2 service unreachable or not loaded.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import config, utf8_console  # noqa: E402

SR = 24000


def http_json(url: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def synth(url: str, text: str, voice: str | None, speed: float) -> bytes:
    body = json.dumps({'text': text, 'voice': voice or None, 'speed': speed}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url.rstrip('/') + '/tts', data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(detail).get('detail', detail)
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f'HTTP {e.code}: {detail}') from None


def decode(data: bytes):
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(io.BytesIO(data), dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if sr != SR:
        import soxr
        y = soxr.resample(y, sr, SR)
    return np.asarray(y, dtype='float32')


def trim(y):
    """Same trim as tts_zh.py: drop silence under 2 % of the peak, keep 30 ms before and 100 ms after."""
    import numpy as np
    if not len(y):
        return y
    nz = np.where(np.abs(y) > np.abs(y).max() * .02)[0]
    return y[max(0, nz[0] - int(.03 * SR)): nz[-1] + int(.10 * SR)] if len(nz) else y


def main() -> None:
    utf8_console()
    cfg = config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('lines')
    ap.add_argument('out_dir')
    ap.add_argument('--voice', default=cfg.get('tts_voice') or None)
    ap.add_argument('--speed', type=float, default=1.0)
    ap.add_argument('--url', default=cfg['tts_url'])
    ap.add_argument('--redo', default='', help='comma-separated line ids to synthesise again')
    a = ap.parse_args()

    try:
        lines = json.loads(Path(a.lines).read_text(encoding='utf-8'))
        assert isinstance(lines, list) and all('id' in L and L.get('text') for L in lines)
    except (OSError, json.JSONDecodeError, AssertionError) as e:
        sys.exit(f'tts_local.py: {a.lines} must be a JSON list of {{"id", "text", …}} ({e})')
    ids = [str(L['id']) for L in lines]
    if len(set(ids)) != len(ids):
        sys.exit('tts_local.py: line ids must be unique')
    redo = {x for x in a.redo.split(',') if x}
    if redo - set(ids):
        sys.exit(f'tts_local.py: --redo names unknown ids: {sorted(redo - set(ids))}')

    try:
        h = http_json(a.url.rstrip('/') + '/health')
    except (OSError, ValueError) as e:
        print(f'tts_local.py: the local TTS service at {a.url} is not reachable ({e}).\n'
              '  Start it from the OpenMontage folder with the model\'s own .venv (see OpenMontage services/local_tts/README.md),\n'
              '  or point --url / mars/config.json "tts_url" at it.', file=sys.stderr)
        sys.exit(2)
    if not h.get('loaded'):
        print(f"tts_local.py: the service is up but its model is not loaded (status {h.get('status')}): {h.get('error') or 'still loading'}",
              file=sys.stderr)
        sys.exit(2)
    known = set(h.get('voices') or [])
    for L in lines:
        v = L.get('voice') or a.voice
        if v and known and v not in known:
            sys.exit(f"tts_local.py: line '{L['id']}': voice {v!r} is not on the service; it has {sorted(known)}")
        if not v and h.get('needs_voice') and not h.get('default_voice'):
            sys.exit(f"tts_local.py: {h['backend']} clones a reference voice: pass --voice, set a line's \"voice\", "
                     f"or set tts_voice in mars/config.json. Voices: {sorted(known)}")
        sp = float(L.get('speed', a.speed))
        if not 0.5 <= sp <= 2.0:
            sys.exit(f"tts_local.py: line '{L['id']}': speed must be 0.5-2.0, got {sp}")
    print(f"tts_local.py: {h['backend']} · {h.get('model')} · default voice {h.get('default_voice') or a.voice or '-'}")

    import soundfile as sf
    out = Path(a.out_dir)
    cache = out / '.cache'
    cache.mkdir(parents=True, exist_ok=True)
    for p in cache.glob('*.part'):
        p.unlink()
    dur = {}
    for L in lines:
        lid = str(L['id'])
        voice = L.get('voice') or a.voice or h.get('default_voice')
        speed = float(L.get('speed', a.speed))
        say = L.get('say', L['text'])
        key = hashlib.sha1(json.dumps([h['backend'], h.get('model'), voice, speed, say, L.get('take')],
                                      ensure_ascii=False).encode('utf-8')).hexdigest()[:16]
        raw = cache / f'{key}.wav'
        y = None
        if raw.exists() and lid not in redo:
            try:
                y = decode(raw.read_bytes())
                if len(y) < 0.15 * SR or float(abs(y).max()) < 1e-3:
                    y = None
            except Exception:
                y = None
            if y is None:
                raw.unlink()
                print(f"tts_local.py: cached reading of '{lid}' was damaged; synthesising again", file=sys.stderr)
        if y is None:
            try:
                data = synth(a.url, say, voice, speed)
            except (RuntimeError, OSError) as e:
                print(f"tts_local.py: line '{lid}' failed: {e}", file=sys.stderr)
                sys.exit(2 if isinstance(e, OSError) else 1)
            part = raw.with_suffix('.part')
            part.write_bytes(data)
            os.replace(part, raw)
            y = decode(data)
        y = trim(y)
        if not len(y) or float(abs(y).max()) < 1e-3:
            print(f"warning: line '{lid}' is silent", file=sys.stderr)
        sf.write(str(out / f'{lid}.wav'), y, SR)
        dur[lid] = round(len(y) / SR, 3)
        print(lid, dur[lid], L['text'], flush=True)
    (out / 'dur.json').write_text(json.dumps(dur, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
