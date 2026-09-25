#!/usr/bin/env python3
"""Optional "watch it play" second opinion: turn a capture's frames into a real-time video and ask Gemini (which can
watch video) for a qualitative motion critique. Needs GEMINI_API_KEY (bin/motion-lens loads ~/.config/motion-lens/env).
Numbers stay with motion-lens' own measurements; Gemini is asked for choreography, pacing and feel.

usage: review.py <run-dir> [--model gemini-2.5-pro] [--fps 10] [--focus "text"]
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

API = 'https://generativelanguage.googleapis.com'


def build_video(d: Path, out: Path):
    rows = [json.loads(l) for l in (d / 'samples.jsonl').read_text().splitlines() if l.strip()]
    shots = [(r['i'], r['t'], r['ph'], r['shot']) for r in rows if r.get('shot')]
    if len(shots) < 2:
        raise SystemExit('no frames to review (capture has <2 images)')
    lines, marks, vt, last_ph = ['ffconcat version 1.0'], [], 0.0, None
    for k, (i, t, ph, name) in enumerate(shots):
        nxt = shots[k + 1][1] if k + 1 < len(shots) else t + 100
        dur = max(1 / 60, min(0.5, (nxt - t) / 1000))   # real time between kept frames, gaps between phases capped
        group = ph.split(':')[0]
        if group != last_ph:
            marks.append({'video_s': round(vt, 2), 'phase': ph, 'virtual_ms': round(t)})
            last_ph = group
        lines += [f"file '{(d / 'frames' / name).resolve()}'", f'duration {dur:.4f}']
        vt += dur
    lines.append(f"file '{(d / 'frames' / shots[-1][3]).resolve()}'")
    lst = Path(tempfile.mkdtemp()) / 'list.ffconcat'
    lst.write_text('\n'.join(lines))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(lst), '-vf', 'scale=960:-2,fps=30',
                    '-c:v', 'libx264', '-crf', '24', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(out)], check=True)
    return marks, round(vt, 1)


def http(method, url, data=None, headers=None, raw=False):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read()
        return (r.headers, body) if raw else json.loads(body)


def upload(path: Path, key: str):
    size = path.stat().st_size
    hdrs, _ = http('POST', f'{API}/upload/v1beta/files', json.dumps({'file': {'display_name': path.name}}).encode(), {
        'x-goog-api-key': key, 'X-Goog-Upload-Protocol': 'resumable', 'X-Goog-Upload-Command': 'start',
        'X-Goog-Upload-Header-Content-Length': str(size), 'X-Goog-Upload-Header-Content-Type': 'video/mp4', 'Content-Type': 'application/json'}, raw=True)
    up_url = hdrs['X-Goog-Upload-URL']
    info = http('POST', up_url, path.read_bytes(), {'Content-Length': str(size), 'X-Goog-Upload-Offset': '0', 'X-Goog-Upload-Command': 'upload, finalize'})
    f = info['file']
    for _ in range(120):
        if f.get('state') == 'ACTIVE':
            return f
        if f.get('state') == 'FAILED':
            raise SystemExit('Gemini could not process the video')
        time.sleep(2)
        f = http('GET', f"{API}/v1beta/{f['name']}", headers={'x-goog-api-key': key})
    raise SystemExit('timed out waiting for Gemini to process the video')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run')
    ap.add_argument('--model', default=os.environ.get('MOTION_LENS_GEMINI_MODEL', 'gemini-2.5-pro'))
    ap.add_argument('--fps', type=float, default=10, help='frames per second Gemini samples from the video (default 10)')
    ap.add_argument('--focus', default='', help='what to pay particular attention to')
    a = ap.parse_args()
    key = os.environ.get('GEMINI_API_KEY')
    if not key:
        raise SystemExit('GEMINI_API_KEY not set (put it in ~/.config/motion-lens/env)')
    d = Path(a.run)
    video = d / 'review.mp4'
    marks, length = build_video(d, video)
    report = (d / 'report.md').read_text()[:12000] if (d / 'report.md').exists() else ''
    meta = json.loads((d / 'meta.json').read_text())
    prompt = f"""You are a senior motion designer reviewing how a website moves. The video is a frame-exact capture of {meta.get('url')} \
({meta['viewport'][0]}x{meta['viewport'][1]} viewport, headless Chrome, scripted input: no visible system cursor; a drawn white arrow marks the pointer during hover phases).
It is {length}s long and made of phases (video second → phase): {json.dumps(marks)}.
Scroll phases show one settled frame per wheel step unless the capture used --film, so motion between steps may be skipped there.
Precise measurements already exist (below); do not re-estimate numbers from the video, and point out anything in them that looks wrong.

Give a critique with video timestamps (mm:ss):
1. Choreography: order, overlap, hierarchy of what moves; what leads, what follows.
2. Pacing and rhythm: where it breathes, where it rushes or drags.
3. Easing character and weight: snappy, floaty, springy, mechanical — per notable motion.
4. Transitions between states/sections, and how scroll feels.
5. Interaction feedback on hover/cursor.
6. What makes it feel premium (or not), and 3 concrete ideas to borrow or improve.
{('Focus especially on: ' + a.focus) if a.focus else ''}

Measured report (motion-lens):
{report}"""
    f = upload(video, key)
    body = {'contents': [{'parts': [
        {'file_data': {'mime_type': 'video/mp4', 'file_uri': f['uri']}, 'video_metadata': {'fps': a.fps}},
        {'text': prompt}]}], 'generationConfig': {'temperature': 0.4}}
    r = http('POST', f'{API}/v1beta/models/{a.model}:generateContent', json.dumps(body).encode(),
             {'x-goog-api-key': key, 'Content-Type': 'application/json'})
    try:
        text = ''.join(p.get('text', '') for p in r['candidates'][0]['content']['parts'])
    except (KeyError, IndexError):
        raise SystemExit('unexpected Gemini response: ' + json.dumps(r)[:500])
    try:
        http('DELETE', f"{API}/v1beta/{f['name']}", headers={'x-goog-api-key': key})
    except Exception:
        pass
    usage = r.get('usageMetadata', {})
    out = d / 'gemini-review.md'
    out.write_text(f"# Gemini review ({a.model}, {a.fps} fps sampling, {length}s video)\n\n"
                   f"_Qualitative second opinion from a model that watched `review.mp4`. Numbers: trust report.md._\n\n{text}\n\n"
                   f"---\nphases in video: {json.dumps(marks)} · tokens: {usage.get('totalTokenCount')}\n")
    rep = d / 'report.md'
    if rep.exists() and 'gemini-review.md' not in rep.read_text():
        with rep.open('a') as fh:
            fh.write('\n## Watched review\nGemini watched `review.mp4` (real-time footage of this capture): see `gemini-review.md`. '
                     'Qualitative only (choreography, pacing, feel); it can misread details, so check claims against the sheets.\n')
    print(out)


if __name__ == '__main__':
    main()
