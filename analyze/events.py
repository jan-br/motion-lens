"""Turn dense tracks into motion facts: time-based events (with easing/spring fits), loops, staggers,
scroll behaviour (pinned / parallax / scrubbed / triggered), smooth-scroll smoothing, hover effects, cursor followers."""
from __future__ import annotations

import numpy as np

from fit import fit_easing, fit_easing_duration, fit_spring, fit_lerp, lag_frames
from load import F

EPS = {'cx': 0.6, 'cy': 0.6, 'opacity': 0.004, 'scale': 0.0015, 'rot': 0.3}
MIN_AMP = {'cx': 3, 'cy': 3, 'opacity': 0.04, 'scale': 0.01, 'rot': 2}


def channels(a, scroll_y=None):
    """Visual channels for one element track (N,10): centre x, centre y (page-compensated when scroll_y is given),
    opacity, own transform scale, unwrapped rotation."""
    cy = a[:, F['y']] + a[:, F['h']] / 2
    if scroll_y is not None:
        cy = cy + scroll_y
    rot = a[:, F['rot']].copy()
    ok = np.isfinite(rot)
    if ok.sum() > 1:
        rot[ok] = np.degrees(np.unwrap(np.radians(rot[ok])))
    return {
        'cx': a[:, F['x']] + a[:, F['w']] / 2,
        'cy': cy,
        'opacity': a[:, F['opacity']],
        'scale': a[:, F['sx']],
        'rot': rot,
    }


def _segments(active, merge_gap=3):
    segs, i, n = [], 0, len(active)
    while i < n:
        if not active[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and (active[j + 1] or (j + 1 + merge_gap < n and active[j + 1:j + 1 + merge_gap + 1].any())):
            j += 1
        segs.append((i, j))
        i = j + 1
    return segs


def time_events(run, eid, lo, hi, phase, static_mask=None, only=None):
    """Motion events of one element in frames [lo, hi) (optionally only where static_mask is true)."""
    a = run.tracks[eid]
    ch = channels(a, run.scroll_y)
    if only:
        ch = {k: v for k, v in ch.items() if k in only}
    out = []
    t = run.t
    for name, v in ch.items():
        seg = v[lo:hi]
        if np.isnan(seg).all():
            continue
        d = np.abs(np.diff(seg, prepend=seg[0]))
        d[np.isnan(d)] = 0
        active = d > EPS[name]
        if static_mask is not None:
            active &= static_mask[lo:hi]
        tiny = 0.011 if name in ('cx', 'cy', 'rot') else 0.0009 if name == 'scale' else 0.001  # sampling resolution
        prev_end = -1
        for s, e in _segments(active):
            if s <= prev_end:
                continue
            while s > 0 and np.isfinite(seg[s - 1]) and abs(seg[s] - seg[s - 1]) > tiny and (static_mask is None or static_mask[lo + s - 1]):
                s -= 1
            while True:  # extend through slow tails, tolerating short rounding plateaus (e.g. at an overshoot peak)
                nxt = None
                for g in range(1, 4):
                    j = e + g
                    if j >= len(seg) or not np.isfinite(seg[j]) or (static_mask is not None and not static_mask[lo + j]):
                        break
                    if abs(seg[j] - seg[j - 1]) > tiny:
                        nxt = j
                        break
                if nxt is None:
                    break
                e = nxt
            prev_end = e
            s0 = max(s - 1, 0)
            v0, v1 = seg[s0], seg[e]
            amp = v1 - v0
            rng = np.nanmax(seg[s0:e + 1]) - np.nanmin(seg[s0:e + 1])
            if rng < MIN_AMP[name]:
                continue
            ev = {'eid': eid, 'channel': name, 'phase': phase, 'f0': lo + s0, 'f1': lo + e,
                  't0': round(float(t[lo + s0])), 'duration_ms': round(float(t[lo + e] - t[lo + s0])),
                  'from': round(float(v0), 3), 'to': round(float(v1), 3)}
            frac = (e - s + 1) / max(1, hi - lo)
            span = seg[s0:e + 1]
            # loop: runs through (nearly) the whole window with steady velocity or oscillation
            if frac > 0.85 and (e - s) > 30:
                vel = np.diff(span)
                ev['kind'] = 'loop'
                ev['rate_per_s'] = round(float(np.nanmean(vel) * run.fps), 3)
                ev['steady'] = bool(np.nanstd(vel) < 0.2 * (abs(np.nanmean(vel)) + 1e-9))
                out.append(ev)
                continue
            if e >= len(seg) - 2 and (e - s0) > 2 and np.isfinite(seg[-1]) and abs(seg[-1] - seg[-3]) > tiny:
                ev['kind'] = 'cut'    # still moving when the capture window ended: no reliable fit
                out.append(ev)
                continue
            if e - s0 <= 2:
                ev['kind'] = 'jump'   # one/two-frame change: layout shift, state swap, not an animation
                out.append(ev)
                continue
            ev['kind'] = 'tween'
            # true resting value: where the element sits before its next motion (or at the window end)
            rest_end = e
            while rest_end + 1 < len(seg) and not active[rest_end + 1] and np.isfinite(seg[rest_end + 1]):
                rest_end += 1
            v_rest = seg[rest_end]
            if abs(v_rest - v1) < 0.05 * abs(amp) + 1e-9:
                amp = v_rest - v0
                ev['to'] = round(float(v_rest), 3)
            if abs(amp) < 0.3 * rng or abs(amp) < MIN_AMP[name]:
                ev['kind'] = 'bounce'  # goes out and comes back (wiggle, pulse, loop cycle): no single easing
                ev['range'] = round(float(rng), 3)
                out.append(ev)
                continue
            if abs(amp) > 1e-9 and (e - s0) >= 3:
                tt_ms = t[lo + s0:lo + e + 1] - t[lo + s0]
                p = (span - v0) / amp
                fe = fit_easing_duration(tt_ms, p, float(tt_ms[-1]), bezier=False)
                ev['_samples'] = (tt_ms, p)
                ev['easing'] = fe
                if fe['overshoot'] > 0.03:
                    ts = (t[lo + s0:lo + e + 1] - t[lo + s0]) / 1000
                    ev['spring'] = fit_spring(ts, p)
            out.append(ev)
    return out


def group_events(run, events):
    """Merge identical motions (parent/child duplicates) and detect staggers."""
    used, groups = set(), []
    evs = sorted(events, key=lambda e: (e['channel'], e['t0']))
    for i, e in enumerate(evs):
        if i in used:
            continue
        g = [e]
        used.add(i)
        for j in range(i + 1, len(evs)):
            f = evs[j]
            if j in used or f['channel'] != e['channel'] or f['kind'] != e['kind']:
                continue
            if abs(f['t0'] - e['t0']) <= 17 and abs(f['duration_ms'] - e['duration_ms']) <= 34 and abs((f['to'] - f['from']) - (e['to'] - e['from'])) <= max(2, 0.05 * abs(e['to'] - e['from'])):
                g.append(f)
                used.add(j)
        groups.append(g)
    # stagger: groups with same channel/duration/delta, starts in arithmetic progression
    motions = []
    for g in groups:
        rep = max(g, key=lambda e: (run.elements.get(e['eid'], {}).get('rect', [0, 0, 0, 0])[2] * run.elements.get(e['eid'], {}).get('rect', [0, 0, 0, 0])[3]))
        motions.append({**rep, 'elements': [x['eid'] for x in g]})
    staggers, taken = [], set()
    ms = sorted(range(len(motions)), key=lambda k: motions[k]['t0'])
    for a in ms:
        if a in taken or motions[a]['kind'] != 'tween':
            continue
        A = motions[a]
        chain = [a]
        for b in ms:
            if b in taken or b in chain or motions[b]['kind'] != 'tween':
                continue
            B = motions[b]
            if B['channel'] == A['channel'] and abs(B['duration_ms'] - A['duration_ms']) <= 50 and abs((B['to'] - B['from']) - (A['to'] - A['from'])) <= max(3, 0.1 * abs(A['to'] - A['from'])) and B['t0'] > motions[chain[-1]]['t0']:
                chain.append(b)
        if len(chain) >= 3:
            starts = np.array([motions[c]['t0'] for c in chain], float)
            gaps = np.diff(starts)
            if gaps.std() <= max(12, 0.25 * gaps.mean()):
                staggers.append({'channel': A['channel'], 'count': len(chain), 'stagger_ms': round(float(gaps.mean())), 'first_t0': int(starts[0]),
                                 'duration_ms': A['duration_ms'], 'members': [motions[c]['eid'] for c in chain], 'easing': A.get('easing')})
                taken.update(chain)
    return motions, staggers


def scroll_behaviour(run, lo, hi, steps, back=None, exclude=()):
    """Per element during the scroll sweep: fixed/pinned ranges, parallax speed, and every changing channel
    classified as scrubbed (scroll-linked), toggle (plays on enter, reverses on scroll back) or triggered once."""
    if not steps:
        return []
    vh = run.meta['viewport'][1]
    frames = [s['frame'] for s in steps]
    pos = np.array([run.scroll[f] for f in frames])
    bframes = [s['frame'] for s in (back or [])]
    bpos = np.array([run.scroll[f] for f in bframes]) if bframes else np.array([])
    results = []
    for eid, a in run.tracks.items():
        if eid in exclude:
            continue
        chv = channels(a)                     # viewport coordinates (for pin / parallax)
        chp = channels(a, run.scroll_y)       # page coordinates (for scroll-driven translation)
        cy = chv['cy'][frames]
        h = a[frames, F['h']]
        if np.isnan(cy).all():
            continue
        visible = (cy + h / 2 > -vh * 0.25) & (cy - h / 2 < vh * 1.25)
        info = {'eid': eid}
        segs = []
        for k in range(1, len(frames)):
            if not (visible[k] or visible[k - 1]) or np.isnan(cy[k]) or np.isnan(cy[k - 1]):
                continue
            dp = pos[k] - pos[k - 1]
            if abs(dp) < 5:
                continue
            segs.append((pos[k - 1], pos[k], (cy[k] - cy[k - 1]) / dp))
        pins = [(a0, b0) for a0, b0, sl in segs if abs(sl) < 0.04]
        if pins:
            merged = []
            for a0, b0 in pins:
                if merged and abs(merged[-1][1] - a0) < 1:
                    merged[-1][1] = b0
                else:
                    merged.append([a0, b0])
            merged = [[round(x), round(y)] for x, y in merged if y - x >= 100]
            span = (pos.max() - pos.min()) or 1
            if merged and sum(y - x for x, y in merged) > 0.9 * span:
                info['fixed'] = True        # position: fixed / always in the viewport
            elif merged:
                info['pinned'] = merged
        para = [sl for a0, b0, sl in segs if abs(sl) >= 0.04]
        if len(para) >= 2 and not pins:
            f = float(np.median(para))
            if abs(f + 1) > 0.08:
                info['parallax_speed'] = round(-f, 2)
        moved_by_layout = bool(pins) or 'parallax_speed' in info
        virtual = bool(run.extra.get('virtualScroll'))
        for name in ('cx', 'cy', 'opacity', 'scale', 'rot'):
            if name == 'cy' and (moved_by_layout or virtual):  # virtual scroll: page position is only estimated
                continue
            v = chp[name]
            seg = v[lo:hi]
            if np.isnan(seg).all():
                continue
            rng = float(np.nanmax(seg) - np.nanmin(seg))
            if rng < MIN_AMP[name] * (3 if name in ('cx', 'cy') else 1):
                continue
            d = v[frames]
            vmin, vmax = np.nanmin(seg), np.nanmax(seg)
            norm = (d - vmin) / (vmax - vmin)
            intermediate = int(np.sum((norm > 0.1) & (norm < 0.9)))
            rev = None
            if len(bframes):
                u = v[bframes]
                order = np.argsort(pos)
                ok = np.isfinite(d[order])
                if ok.sum() >= 2:
                    dp_, dv_ = pos[order][ok], d[order][ok]
                    m = (bpos >= dp_.min()) & (bpos <= dp_.max()) & np.isfinite(u)
                    if m.sum():
                        rev = float(np.mean(np.abs(u[m] - np.interp(bpos[m], dp_, dv_))) / (rng or 1))
            changing = np.where(np.abs(np.diff(d)) > MIN_AMP[name] / 2)[0]
            where = [round(float(pos[changing[0]])), round(float(pos[min(changing[-1] + 1, len(pos) - 1)]))] if len(changing) else None
            if name == 'rot' and rng > 300:
                kind = 'loop (time-driven)'
            elif intermediate >= 1:
                kind = 'scrubbed (scroll-linked)'
            elif rev is not None and rev < 0.25:
                kind = 'toggle (plays on enter, reverses on scroll back)'
            else:
                kind = 'triggered once (plays on enter)'
            c = {'channel': name, 'range': [round(float(vmin), 3), round(float(vmax), 3)], 'kind': kind, 'scroll_range': where,
                 'intermediate_steps': intermediate, 'rewind_error': None if rev is None else round(rev, 2)}
            if kind.startswith(('toggle', 'triggered')):
                evs = [e for e in time_events(run, eid, lo, hi, 'scroll', only=[name]) if e['kind'] == 'tween']
                if evs:
                    e0 = evs[0]
                    c['duration_ms'] = e0['duration_ms']
                    c['easing'] = e0.get('easing')
                    ey = a[e0['f0'], F['y']]
                    c['trigger'] = {'scroll': round(float(run.scroll[e0['f0']])), 'element_top_in_viewport': round(float(ey / vh), 2) if np.isfinite(ey) else None}
            info.setdefault('scrollChanges', []).append(c)
        if len(info) > 1:
            results.append(info)
    return results


def smooth_scroll(run, lo, hi):
    """Estimate scroll smoothing (lerp per frame) from wheel input to scroll position."""
    if run.lenis is not None and np.isfinite(run.lenis[lo:hi, 1]).any():
        r = fit_lerp(run.lenis[lo:hi, 0], run.lenis[lo:hi, 1])
        if r:
            r['source'] = 'lenis animatedScroll vs targetScroll'
            return r
    y = run.scroll_y[lo:hi]
    # target = where each wheel burst settles: next settled value
    target = y.copy()
    last = y[-1]
    for i in range(len(y) - 1, -1, -1):
        if i + 1 < len(y) and abs(y[i + 1] - y[i]) < 0.3:
            last = y[i]
        target[i] = last
    r = fit_lerp(y, target)
    if r:
        r['source'] = 'native scrollY settle curve'
    return r


def followers(run, lo, hi):
    """Elements that follow the pointer (cursor followers, magnetic effects)."""
    mx, my = run.mouse[lo:hi, 0], run.mouse[lo:hi, 1]
    if np.isnan(mx).all() or np.nanstd(mx) < 20:
        return []
    mxf = np.where(np.isnan(mx), np.nanmean(mx), mx)
    myf = np.where(np.isnan(my), np.nanmean(my), my)
    out = []
    for eid, a in run.tracks.items():
        ch = channels(a)
        cx, cy = ch['cx'][lo:hi], ch['cy'][lo:hi]
        if np.isnan(cx).any() or np.nanstd(cx) < 10:
            continue
        c = np.corrcoef(cx, mxf)[0, 1]
        if not np.isfinite(c) or c < 0.8:
            continue
        # the page sees each pointer move one or two frames after dispatch: fit with the best input latency
        cands = []
        for lat in (-1, 0, 1, 2):
            if lat < 0:
                tx = np.concatenate([mxf[-lat:], np.full(-lat, mxf[-1])])
                ty = np.concatenate([myf[-lat:], np.full(-lat, myf[-1])])
            else:
                tx = np.concatenate([np.full(lat, mxf[0]), mxf[:len(mxf) - lat]])
                ty = np.concatenate([np.full(lat, myf[0]), myf[:len(myf) - lat]])
            fx, fy = fit_lerp(cx, tx), fit_lerp(cy, ty)
            if fx:
                cands.append((fx['rmse_px'], lat, fx, fy))
        if not cands:
            continue
        _, lat, lx, ly = min(cands, key=lambda c: c[0])
        if lx:
            lx['input_latency_frames'] = lat
        out.append({'eid': eid, 'corr_x': round(float(c), 3), 'follow_ratio': round(float(np.nanstd(cx) / np.nanstd(mxf)), 2),
                    'lerp_x': lx, 'lerp_y': ly, 'lag': lag_frames(cx, mxf)})
    return out


def add_bezier(ev):
    """Exact cubic-bezier fit for an event that will be shown (expensive; done lazily)."""
    smp = ev.pop('_samples', None)
    e = ev.get('easing')
    if smp is None or not e or e.get('bezier'):
        return ev
    tt_ms, p = smp
    T = e.get('est_duration_ms') or tt_ms[-1]
    ts = e.get('start_offset_ms') or 0
    fb = fit_easing(np.clip((np.asarray(tt_ms) - ts) / T, 0, 1), p)
    e['bezier'], e['bezier_css'], e['bezier_rmse'] = fb['bezier'], fb['bezier_css'], fb['bezier_rmse']
    return ev


def strip_samples(obj):
    if isinstance(obj, dict):
        obj.pop('_samples', None)
        for v in obj.values():
            strip_samples(v)
    elif isinstance(obj, list):
        for v in obj:
            strip_samples(v)
    return obj


def smooth_scroll_steps(run, steps):
    """Scroll smoothing from each wheel step: the approach of the scroll position to where it settles."""
    ks = []
    for a, b in zip([None] + steps[:-1], steps):
        f1 = b['frame']
        f0 = a['frame'] + 1 if a else max(0, f1 - 120)
        w = np.where(np.abs(run.wheel[f0:f1]) > 0)[0]
        if not len(w):
            continue
        start = f0 + w[-1] + 1
        y = run.scroll[start:f1 + 1]
        if len(y) < 8 or abs(y[-1] - y[0]) < 20:
            continue
        r = fit_lerp(y, np.full(len(y), y[-1]))
        if r and 0 < r['lerp_per_frame'] < 1 and r['rmse_px'] < 0.05 * abs(y[-1] - y[0]) + 2:
            ks.append(r['lerp_per_frame'])
    if len(ks) < 2:
        return None
    return {'lerp_per_frame': round(float(np.median(ks)), 3), 'rmse_px': None, 'samples': len(ks), 'spread': [round(float(min(ks)), 3), round(float(max(ks)), 3)],
            'source': 'settle curve after each wheel step'}
