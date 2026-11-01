"""Cross-platform port of core/render/mux.sh (Windows has no sh/dd/od/awk). Same arguments, checks and output:

    python mars/mux.py video.mp4 mix.wav out.mp4 [fps=24] [grain=2]

Two-pass loudnorm to -14 LUFS / true peak -1.2 dB, film grain (0 = none), silence padding when the audio is short,
refuses missing / damaged / truncated audio, never leaves a partial file, prints the measured loudness.
LEMO_COLOR=bt709 writes limited-range BT.709 (default: full range, as video.mjs outputs). Exit 0 = film written, 1 = any failure.
Keep it in step with core/render/mux.sh when upstream changes that script.
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import tool, utf8_console  # noqa: E402

BENIGN = re.compile(r'Ignoring maximum wav data size|Packet corrupt \(stream = \d+, dts = NOPTS\)|corrupt input packet in stream \d+')
ERRWORDS = re.compile(r'error|invalid|corrupt|header missing|overread|truncat|incomplete', re.I)


def die(msg: str) -> None:
    print(f'mux.py: {msg}', file=sys.stderr)
    sys.exit(1)


def num(s: str) -> bool:
    return bool(re.fullmatch(r'[0-9.]+', s or ''))


def wav_truncated(path: str) -> bool:
    """True when a WAV header declares more audio than the file holds (a placeholder length 0 / 0xFFFFFFFF = unknown)."""
    try:
        with open(path, 'rb') as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b'RIFF' or head[8:12] != b'WAVE':
                return False
            size = os.path.getsize(path)
            off = 12
            while off + 8 <= size:
                f.seek(off)
                cid, ln = struct.unpack('<4sI', f.read(8))
                if cid == b'data':
                    if ln in (0, 0xFFFFFFFF):
                        return False
                    return off + 8 + ln > size
                off += 8 + ln + (ln & 1)
    except OSError:
        return False
    return False


def duration(ffprobe: str, path: str) -> float:
    r = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    try:
        return float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


def main() -> None:
    utf8_console()
    a = sys.argv[1:]
    if len(a) < 3:
        die('usage: python mars/mux.py video.mp4 mix.wav out.mp4 [fps=24] [grain=2]')
    V, A, O = a[0], a[1], a[2]
    FPS = a[3] if len(a) > 3 else '24'
    GR = a[4] if len(a) > 4 else '2'
    if not os.path.isfile(V):
        die(f'video not found: {V}')
    if not os.path.isfile(A):
        die(f'audio not found: {A}')
    if not num(FPS):
        die(f"fps must be a number, got '{FPS}'")
    if not num(GR):
        die(f"grain must be a number (0 = none), got '{GR}'")
    color = os.environ.get('LEMO_COLOR', '')
    if color not in ('', 'bt709'):
        die(f"LEMO_COLOR must be bt709 or unset, got '{color}'")
    ffmpeg, ffprobe = tool('ffmpeg'), tool('ffprobe')

    # first pass: measure loudness
    r = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', A],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if not r.stdout.strip():
        die(f"no audio stream in '{A}' (is it really the mixed audio file?)")
    r = subprocess.run([ffmpeg, '-hide_banner', '-nostats', '-progress', 'pipe:2', '-i', A,
                        '-af', 'loudnorm=I=-14:TP=-1.2:LRA=11:print_format=json', '-f', 'null', '-'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    M = r.stderr
    if r.returncode:
        die(f"ffmpeg cannot read the audio '{A}': " + ' '.join(M.strip().splitlines()[-2:]))
    if wav_truncated(A):
        die(f"the WAV file '{A}' is cut off: its header promises more audio than the file contains (was the write interrupted?)")
    errs = [ln for ln in M.splitlines() if ln.startswith('[') and ERRWORDS.search(ln)]
    bad = [ln for ln in errs if not BENIGN.search(ln)][:2]
    if bad:
        die(f"the audio '{A}' is damaged; ffmpeg reported while decoding it:\n" + '\n'.join(bad))
    dec = 0.0
    times = [ln.split('=', 1)[1] for ln in M.splitlines() if ln.startswith('out_time=')]
    if times and not times[-1].startswith('-') and ':' in times[-1]:
        h, m, s = times[-1].split(':')
        dec = int(h) * 3600 + int(m) * 60 + float(s)
    cont = duration(ffprobe, A)
    if 'Estimating duration from bitrate' not in M and cont > 0 and cont - dec > max(cont * 0.05, 0.15):
        die(f"only {dec:g} s of the {cont:g} s that '{A}' claims could be decoded: the file is damaged or cut off")
    if errs:
        print(f"mux.py: note: ffmpeg complained about the header/ending of '{A}' (typical of a WAV streamed with "
              f"'ffmpeg … -f wav -', whose length is a placeholder), but all {dec:g} s decoded; continuing", file=sys.stderr)
    j = re.search(r'\{[^{}]*"input_i"[^{}]*\}', M, re.S)
    meas = json.loads(j.group(0)) if j else {}
    keys = ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset')
    norm = all(meas.get(k) and not re.search(r'inf|nan', str(meas[k])) for k in keys)
    if norm:
        LN = (f"loudnorm=I=-14:TP=-1.2:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
              f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:offset={meas['target_offset']}:linear=true")
    else:
        LN = 'anull'
        print('mux.py: warning: the audio is silent or quieter than -70 LUFS, so loudness normalisation was skipped', file=sys.stderr)

    # pad with silence if the audio is shorter than the picture
    VD, AD = duration(ffprobe, V), duration(ffprobe, A)
    PAD = ''
    if AD > 0 and VD > 0 and AD < VD - 0.02:
        PAD = f',apad=whole_dur={VD}'
        print(f'mux.py: note: the audio ({AD} s) is shorter than the video ({VD} s); padding it with silence', file=sys.stderr)

    VF = f'fps={FPS}' if GR == '0' else f'fps={FPS},noise=c0s={GR}:allf=t'
    if color == 'bt709':
        VF += (',scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p,'
               'setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709')
        CARGS = ['-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709']
    else:
        VF += ',format=yuv420p'
        CARGS = []
    cmd = [ffmpeg, '-y', '-loglevel', 'error', '-i', V, '-i', A,
           '-filter_complex', f'[0:v]{VF}[v];[1:a]{LN},aresample=48000{PAD}[a]',
           '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'slow', '-crf', '19', '-r', FPS, *CARGS,
           '-c:a', 'aac', '-b:a', '256k', '-movflags', '+faststart', '-shortest', O]
    if subprocess.call(cmd):
        Path(O).unlink(missing_ok=True)
        die(f"ffmpeg failed while writing '{O}' (its message is above)")
    if not os.path.isfile(O) or os.path.getsize(O) == 0:
        Path(O).unlink(missing_ok=True)
        die(f"no output was written to '{O}'")
    print(O)
    r = subprocess.run([ffmpeg, '-hide_banner', '-nostats', '-i', O, '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    R = [ln for ln in r.stderr.splitlines() if re.match(r'^\s+(I|LRA|Peak):', ln)][:3]
    print('\n'.join(R))
    if norm:
        vals = {ln.split()[0]: ln.split()[1] for ln in R}
        try:
            oi, op = float(vals.get('I:', 'nan')), float(vals.get('Peak:', 'nan'))
            if oi < -15 or oi > -13 or op > -1:
                print(f'mux.py: warning: the film missed the target (-14 LUFS, true peak <= -1.2 dB): measured {oi} LUFS, peak {op} dB. '
                      'The mix is probably clipping or has very hot peaks: lower it and tame the peaks, then mux again', file=sys.stderr)
        except ValueError:
            pass


if __name__ == '__main__':
    main()
