"""Load a motion-lens run into dense numpy tracks."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# per-element state vector written by instrument.js sample()
FIELDS = ['x', 'y', 'w', 'h', 'opacity', 'tx', 'ty', 'sx', 'sy', 'rot']
F = {k: i for i, k in enumerate(FIELDS)}


@dataclass
class Run:
    dir: Path
    meta: dict
    elements: dict            # id -> descriptor
    inspect: dict
    anims: list
    t: np.ndarray             # ms, per frame
    phase: list               # phase name per frame
    scroll: np.ndarray        # scroll position used by the page (lenis animated scroll if present, else scrollY)
    scroll_y: np.ndarray      # native scrollY
    lenis: np.ndarray | None  # [animated, target, isScrolling] per frame
    mouse: np.ndarray         # mx, my per frame (nan when unknown)
    wheel: np.ndarray         # wheel delta dispatched on this frame
    shots: dict               # frame index -> image filename
    tracks: dict = field(default_factory=dict)  # element id -> (N, 10) array, nan before known
    extra: dict = field(default_factory=dict)   # st, video, cam per frame

    @property
    def fps(self):
        return self.meta.get('fps', 60)

    def phase_ranges(self):
        """[(name, start, end_exclusive)] merged from per-frame phase labels."""
        out, cur, start = [], None, 0
        for i, p in enumerate(self.phase):
            if p != cur:
                if cur is not None:
                    out.append((cur, start, i))
                cur, start = p, i
        if cur is not None:
            out.append((cur, start, len(self.phase)))
        return out

    def label(self, eid):
        d = self.elements.get(eid, {})
        txt = f' "{d["text"][:24]}"' if d.get('text') else ''
        return f'{d.get("sel", eid)}{txt}'


def load_run(path) -> Run:
    d = Path(path)
    meta = json.loads((d / 'meta.json').read_text())
    elements = {e['id']: e for e in json.loads((d / 'elements.json').read_text())}
    inspect = json.loads((d / 'inspect.json').read_text()) if (d / 'inspect.json').exists() else {}
    anims = json.loads((d / 'animations.json').read_text()) if (d / 'animations.json').exists() else []
    rows = [json.loads(l) for l in (d / 'samples.jsonl').read_text().splitlines() if l.strip()]
    n = len(rows)
    t = np.array([r['t'] for r in rows], float)
    phase = [r['ph'] for r in rows]
    sy = np.array([r.get('sy', 0) for r in rows], float)
    has_lenis = any('lenis' in r for r in rows)
    lenis = np.array([r.get('lenis', [np.nan, np.nan, 0]) for r in rows], float) if has_lenis else None
    vpos = np.array([r.get('vpos', 0) for r in rows], float)
    virtual = np.nanmax(np.abs(sy)) < 1 and np.nanmax(np.abs(vpos)) > 50
    if virtual:  # the site scrolls its own layers; the native scroll position never moves
        sy = vpos
    scroll = np.where(np.isnan(lenis[:, 0]), sy, lenis[:, 0]) if has_lenis else sy.copy()
    mouse = np.array([[r['in'].get('mx') if r.get('in', {}).get('mx') is not None else np.nan,
                       r['in'].get('my') if r.get('in', {}).get('my') is not None else np.nan] for r in rows], float)
    wheel = np.array([r.get('in', {}).get('wheel', 0) for r in rows], float)
    shots = {r['i']: r['shot'] for r in rows if r.get('shot')}
    tracks = {}
    for i, r in enumerate(rows):
        for k, st in r.get('e', {}).items():
            eid = int(k)
            if eid not in tracks:
                tracks[eid] = np.full((n, len(FIELDS)), np.nan)
            tracks[eid][i] = st
    for eid, a in tracks.items():  # forward fill (samples are sparse: only changes are written)
        for i in range(1, n):
            if np.isnan(a[i, 0]) and not np.isnan(a[i - 1, 0]):
                a[i] = a[i - 1]
    extra = {'st': [r.get('st') for r in rows], 'video': [r.get('video') for r in rows], 'cam': [r.get('cam') for r in rows]}
    extra['virtualScroll'] = bool(virtual)
    return Run(d, meta, elements, inspect, anims, t, phase, scroll, sy, lenis, mouse, wheel, shots, tracks, extra)
