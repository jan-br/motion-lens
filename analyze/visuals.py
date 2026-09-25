"""Images a still-image reader can use: contact sheets, crop strips, diff heatmaps, easing plots, scroll maps."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from fit import NAMED, bezier_y_at_x

FONT_PATHS = ['/usr/share/fonts/TTF/DejaVuSansMono.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', '/usr/share/fonts/liberation/LiberationMono-Regular.ttf']


def _font(size):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _open(run, i, cursor=True):
    im = Image.open(run.dir / 'frames' / run.shots[i]).convert('RGB')
    if cursor and run.phase[i].startswith(('hover', 'sweep', 'pointer')) and np.isfinite(run.mouse[i]).all():
        # headless Chrome draws no pointer: mark where it is (white arrow with dark outline)
        x, y = run.mouse[i]
        d = ImageDraw.Draw(im)
        pts = [(x, y), (x, y + 22), (x + 6, y + 16), (x + 11, y + 26), (x + 15, y + 24), (x + 10, y + 14), (x + 18, y + 14)]
        d.polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))
    return im


def contact_sheet(run, frames, out, label, cols=5, tile_w=384, max_tiles=40, title=None):
    """Grid of frames with a label on each tile. Subsamples evenly when there are too many."""
    frames = [f for f in frames if f in run.shots]
    if not frames:
        return None
    if len(frames) > max_tiles:
        idx = np.linspace(0, len(frames) - 1, max_tiles).round().astype(int)
        frames = [frames[i] for i in idx]
    im0 = _open(run, frames[0])
    th = round(tile_w * im0.height / im0.width)
    rows = math.ceil(len(frames) / cols)
    top = 40 if title else 0
    sheet = Image.new('RGB', (cols * tile_w + (cols + 1) * 4, top + rows * (th + 26) + 4), (20, 20, 22))
    d = ImageDraw.Draw(sheet)
    f = _font(14)
    if title:
        d.text((10, 10), title, fill=(235, 235, 235), font=_font(18))
    for k, fr in enumerate(frames):
        im = _open(run, fr).resize((tile_w, th), Image.LANCZOS)
        x, y = 4 + (k % cols) * (tile_w + 4), top + 4 + (k // cols) * (th + 26)
        sheet.paste(im, (x, y))
        d.text((x + 2, y + th + 4), label(fr), fill=(210, 210, 210), font=f)
    sheet.save(out, optimize=True)
    return out


def crop_strip(run, frames, box, out, label, max_tiles=24, tile_h=220, title=None):
    """Same screen region across frames (e.g. a hover target), laid out left-to-right, wrapping rows."""
    frames = [f for f in frames if f in run.shots]
    if not frames:
        return None
    if len(frames) > max_tiles:
        idx = np.linspace(0, len(frames) - 1, max_tiles).round().astype(int)
        frames = [frames[i] for i in idx]
    W, H = run.meta['viewport']
    x0, y0, x1, y1 = [int(v) for v in (max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3]))]
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    tw = max(80, round(tile_h * (x1 - x0) / (y1 - y0)))
    if tw > 520:
        tw, tile_h = 520, round(520 * (y1 - y0) / (x1 - x0))
    cols = max(1, min(len(frames), 1800 // (tw + 4)))
    rows = math.ceil(len(frames) / cols)
    top = 36 if title else 0
    sheet = Image.new('RGB', (cols * (tw + 4) + 4, top + rows * (tile_h + 24) + 4), (20, 20, 22))
    d = ImageDraw.Draw(sheet)
    if title:
        d.text((8, 8), ' '.join(title.split()), fill=(235, 235, 235), font=_font(16))
    f = _font(13)
    for k, fr in enumerate(frames):
        im = _open(run, fr).crop((x0, y0, x1, y1)).resize((tw, tile_h), Image.LANCZOS)
        x, y = 4 + (k % cols) * (tw + 4), top + 4 + (k // cols) * (tile_h + 24)
        sheet.paste(im, (x, y))
        d.text((x + 2, y + tile_h + 3), label(fr), fill=(210, 210, 210), font=f)
    sheet.save(out, optimize=True)
    return out


def diff_heatmap(run, frames, out, title):
    """Where on screen pixels change across the given frames (catches WebGL/canvas motion the DOM can't see).
    Returns per-transition energy list and the output path."""
    frames = [f for f in frames if f in run.shots]
    if len(frames) < 3:
        return None, []
    acc, energy, prev = None, [], None
    for fr in frames:
        g = np.asarray(_open(run, fr, cursor=False).convert('L').resize((480, round(480 * run.meta['viewport'][1] / run.meta['viewport'][0]))), dtype=np.float32)
        if prev is not None:
            dd = np.abs(g - prev)
            acc = dd if acc is None else acc + dd
            energy.append((fr, float(dd.mean())))
        prev = g
    base = np.asarray(_open(run, frames[len(frames) // 2], cursor=False).resize((acc.shape[1], acc.shape[0])), dtype=np.float32) / 255
    h = acc / (acc.max() or 1)
    h = np.clip(h ** 0.5, 0, 1)
    cmap = plt.get_cmap('inferno')(h)[..., :3]
    mix = np.clip(base * 0.35 + cmap * (0.25 + 0.75 * h[..., None]), 0, 1)
    fig = plt.figure(figsize=(9.6, 9.6 * acc.shape[0] / acc.shape[1] + 0.5), dpi=100)
    plt.imshow(mix)
    plt.title(title, fontsize=10)
    plt.axis('off')
    plt.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out, energy


def plot_events(run, motions, out, title, max_n=12):
    """Small multiples: measured normalised progress vs time with the best named ease and bezier fit."""
    ms = [m for m in motions if m['kind'] == 'tween' and m.get('easing')][:max_n]
    if not ms:
        return None
    cols = 4
    rows = math.ceil(len(ms) / cols)
    fig, axs = plt.subplots(rows, cols, figsize=(cols * 3.4, rows * 2.9), squeeze=False)
    from events import channels
    for k, m in enumerate(ms):
        ax = axs[k // cols][k % cols]
        v = channels(run.tracks[m['eid']], run.scroll_y)[m['channel']][m['f0']:m['f1'] + 1]
        tt = run.t[m['f0']:m['f1'] + 1] - run.t[m['f0']]
        span = (m['to'] - m['from']) or 1
        p = (v - m['from']) / span
        tn = tt / (tt[-1] or 1)
        ax.plot(tt, p, 'o', ms=2.5, color='#ff5a1f', label='measured')
        e = m['easing']
        ts = e.get('start_offset_ms') or 0
        sp = m.get('spring')
        if sp:
            from fit import spring_curve
            xs = np.linspace(0, tt[-1], 200)
            ax.plot(xs, spring_curve(xs / 1000, sp['zeta'], sp['omega_n']), '-', lw=1.2, color='#2a3bff', label=f"spring ζ={sp['zeta']} ω={sp['omega_n']}")
        else:
            T = e.get('est_duration_ms') or tt[-1]
            xs = np.linspace(0, 1, 120)
            if e.get('bezier'):
                ax.plot(ts + xs * T, bezier_y_at_x(*e['bezier'], xs), '-', lw=1, color='#2a3bff', label='bezier fit')
            nm = e['named'][0]
            Tn = nm.get('duration_ms') or T
            ax.plot(ts + xs * Tn, NAMED[nm['name']](xs), '--', lw=1, color='#555', label=f"{nm['name']} {Tn}ms")
            ax.set_xlim(-10, max(tt[-1], ts + T) + 10)
        lab = run.label(m['eid'])
        ax.set_title(f"{lab[:28]}\n{m['channel']} {m['from']:.3g}→{m['to']:.3g}  {m['duration_ms']}ms", fontsize=7.5)
        ax.tick_params(labelsize=6)
        ax.set_xlabel('ms', fontsize=6)
        ax.legend(fontsize=5.5, loc='lower right')
        ax.grid(alpha=.3)
    for k in range(len(ms), rows * cols):
        axs[k // cols][k % cols].axis('off')
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def plot_timeline(run, out, activity):
    """Overview: phases, scroll position, pointer x and how many tracked elements change each frame."""
    fig, ax = plt.subplots(3, 1, figsize=(14, 6.5), sharex=True, gridspec_kw={'height_ratios': [1.3, 1, 1]})
    t = run.t / 1000
    colors = plt.get_cmap('tab10')
    for k, (name, a, b) in enumerate(run.phase_ranges()):
        for x in ax:
            x.axvspan(t[a], t[b - 1], color=colors(k % 10), alpha=0.08)
        ax[0].text(t[a], 1.02, name, transform=ax[0].get_xaxis_transform(), fontsize=7)
    ax[0].plot(t, activity, lw=0.8, color='#ff5a1f')
    ax[0].set_ylabel('# elements\nchanging', fontsize=8)
    ax[1].plot(t, run.scroll, lw=1, color='#2a3bff')
    ax[1].set_ylabel('scroll px', fontsize=8)
    ax[2].plot(t, run.mouse[:, 0], lw=1, color='#444')
    ax[2].set_ylabel('pointer x', fontsize=8)
    ax[2].set_xlabel('virtual time (s)', fontsize=8)
    for x in ax:
        x.grid(alpha=.3)
        x.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(out, dpi=100)
    plt.close(fig)
    return out


def plot_scroll_map(run, behaviours, steps, out, max_rows=30):
    """Rows = elements with scroll behaviour; x = scroll position; colour = normalised channel value at settled steps."""
    rows = []
    frames = [s['frame'] for s in steps]
    pos = np.array([run.scroll[f] for f in frames])
    from events import channels
    for b in behaviours:
        ch = channels(run.tracks[b['eid']], run.scroll_y)
        for c in b.get('scrollChanges', []):
            v = ch[c['channel']][frames]
            lo, hi = c['range']
            rows.append((f"{run.label(b['eid'])[:34]} · {c['channel']} · {c['kind'].split(' (')[0]}", (v - lo) / ((hi - lo) or 1)))
        for a0, b0 in b.get('pinned', []):
            rows.append((f"{run.label(b['eid'])[:34]} · pinned", ((pos >= a0) & (pos <= b0)).astype(float)))
    if not rows or np.ptp(pos) < 1:
        return None
    rows = rows[:max_rows]
    M = np.array([r[1] for r in rows])
    fig, ax = plt.subplots(figsize=(13, 0.32 * len(rows) + 1.6))
    ax.imshow(M, aspect='auto', cmap='viridis', interpolation='nearest', extent=[pos.min(), pos.max(), len(rows) - 0.5, -0.5])
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows], fontsize=7)
    ax.set_xlabel('scroll position (px) at settled steps', fontsize=8)
    ax.set_title('scroll map: what changes where (0 = start value, 1 = end value)', fontsize=9)
    ax.tick_params(axis='x', labelsize=7)
    fig.tight_layout()
    fig.savefig(out, dpi=100)
    plt.close(fig)
    return out
