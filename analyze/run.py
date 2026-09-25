#!/usr/bin/env python3
"""Analyse a motion-lens capture directory -> report.md, report.json, sheets/*.png, plots/*.png."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from load import load_run  # noqa: E402
from events import time_events, group_events, scroll_behaviour, smooth_scroll, smooth_scroll_steps, followers, channels, add_bezier, strip_samples  # noqa: E402
import visuals as V  # noqa: E402


def fmt_change(ch, a, b):
    """Positions/rotation as a delta (absolute page coordinates mean little); opacity/scale as from → to."""
    if ch in ('cx', 'cy'):
        return f"Δ{'x' if ch == 'cx' else 'y'} {b - a:+.0f}px"
    if ch == 'rot':
        return f"Δ{b - a:+.1f}°"
    return f"{a:.3g} → {b:.3g}"


def fmt_ease(e):
    if not e:
        return ''
    n = e['named'][0]
    s = f"~{n['name']}"
    if e.get('bezier_css') and (e.get('bezier_rmse') or 1) < n['rmse']:
        s += f" · fit {e['bezier_css']}"
    if e.get('overshoot', 0) > 0.02:
        s += f" · overshoot {e['overshoot']:.0%}"
    return s


def declared_for(run, eid, t0=None, phase=None):
    """Declared CSS/WAAPI animations (CDP) and GSAP tweens that target this element."""
    sel = run.elements.get(eid, {}).get('sel', '')
    tail = sel.split(' > ')[-1]
    out = []
    for a in run.anims:
        tg = a.get('target') or ''
        if tg and (tg == tail or sel.endswith(tg) or (tg.startswith('#') and tg in sel)):
            if phase and a.get('phase') and not str(a['phase']).startswith(phase.split(':')[0]) and phase != 'intro':
                continue
            src = a.get('source') or {}
            eas = src.get('easing')
            kfe = [x for x in (src.get('keyframeEasings') or []) if x and x != 'linear']
            out.append({'via': a['type'], 'name': a.get('name'), 'duration_ms': src.get('duration'), 'delay_ms': src.get('delay'),
                        'easing': kfe[0] if eas in (None, 'linear') and kfe else eas, 'iterations': src.get('iterations')})
    for tw in (run.inspect.get('gsap') or {}).get('seen', []):
        if any(t == sel or (t and sel.endswith(t.split(' > ')[-1]) and t.split(' > ')[-1] == tail) for t in tw.get('targets', [])):
            out.append({'via': 'gsap', 'duration_ms': round((tw.get('duration') or 0) * 1000), 'delay_ms': round((tw.get('delay') or 0) * 1000),
                        'easing': tw.get('ease'), 'stagger': tw.get('stagger'), 'props': tw.get('props'), 'scrollTrigger': tw.get('scrollTrigger')})
    # de-duplicate
    seen, uniq = set(), []
    for d in out:
        k = json.dumps(d, sort_keys=True)
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq[:4]


def fmt_decl(ds):
    parts = []
    for d in ds:
        s = f"{d['via']}"
        if d.get('name'):
            s += f" `{d['name']}`"
        if d.get('duration_ms') is not None:
            s += f" {d['duration_ms']}ms"
        if d.get('delay_ms'):
            s += f" +{d['delay_ms']}ms delay"
        if d.get('easing'):
            s += f" {d['easing']}"
        if d.get('stagger'):
            s += f" stagger {d['stagger']}"
        if d.get('iterations') in ('infinite', None) and d['via'] != 'gsap' and d.get('iterations') is None:
            pass
        parts.append(s)
    return '; '.join(parts)


def main(rundir):
    run = load_run(rundir)
    d = Path(rundir)
    (d / 'sheets').mkdir(exist_ok=True)
    (d / 'plots').mkdir(exist_ok=True)
    R = {'url': run.meta['url'], 'meta': {k: run.meta.get(k) for k in ('mode', 'viewport', 'fps', 'browser', 'capturedAt', 'captureSeconds', 'frames', 'load', 'error', 'reducedMotion', 'mobile')}}
    images = []
    ranges = run.phase_ranges()
    ph = {}
    for name, a, b in ranges:
        ph.setdefault(name.split(':')[0], []).append((name, a, b))
    fps = run.fps
    lab_t = lambda f: f"t={run.t[f]:.0f}ms"

    # ---------- activity overview ----------
    n = len(run.t)
    activity = np.zeros(n)
    for eid, a in run.tracks.items():
        ch = channels(a, run.scroll_y)
        mv = np.zeros(n, bool)
        for k, v in ch.items():
            dv = np.abs(np.diff(v, prepend=v[0]))
            mv |= np.nan_to_num(dv) > (0.6 if k in ('cx', 'cy') else 0.004)
        activity += mv
    images.append(('plots/timeline.png', 'overview: phases, elements changing per frame, scroll position, pointer x', V.plot_timeline(run, d / 'plots/timeline.png', activity)))

    # ---------- followers (pointer phases) ----------
    fol = []
    ptr = [(a, b) for nm, a, b in ranges if nm.startswith(('hover', 'sweep', 'pointer'))]
    if ptr:
        fol = followers(run, min(a for a, b in ptr), max(b for a, b in ptr))
    fol_ids = {f['eid'] for f in fol}
    R['followers'] = [{**f, 'element': run.label(f['eid'])} for f in fol]

    # ---------- intro (time-based) ----------
    R['intro'] = {}
    if 'intro' in ph:
        _, lo, hi = ph['intro'][0]
        evs = []
        for eid in run.tracks:
            evs += time_events(run, eid, lo, hi, 'intro')
        motions, staggers = group_events(run, evs)
        jumps = [m for m in motions if m['kind'] == 'jump']
        motions = [m for m in motions if m['kind'] != 'jump']
        for m in motions[:60]:
            if m['kind'] == 'tween':
                add_bezier(m)
            m['element'] = run.label(m['eid'])
            m['declared'] = declared_for(run, m['eid'], m['t0'], 'intro')
        for m in motions[60:]:
            m['element'] = run.label(m['eid'])
        R['intro'] = {'motions': motions, 'staggers': [{**s, 'members': [run.label(x) for x in s['members']]} for s in staggers],
                      'jumps': {'count': sum(len(j['elements']) for j in jumps), 'times_ms': sorted({j['t0'] for j in jumps})[:20]}}
        shots = [f for f in range(lo, hi) if f in run.shots]
        images.append(('sheets/intro.png', 'intro timeline frames (virtual time since navigation)', V.contact_sheet(run, shots, d / 'sheets/intro.png', lab_t, title='intro / load timeline')))
        p, energy = V.diff_heatmap(run, shots, d / 'plots/intro-heat.png', 'where pixels change during the intro (bright = more motion)')
        if p:
            images.append(('plots/intro-heat.png', 'intro motion heatmap (includes canvas/WebGL)', p))
        images.append(('plots/intro-easing.png', 'intro motions: measured progress vs fitted easing', V.plot_events(run, motions, d / 'plots/intro-easing.png', 'intro motions: measured progress (dots) vs fits')))

    # ---------- hover ----------
    R['hover'] = []
    for name, lo, hi in ph.get('hover', []):
        tgt = next((p.get('target') for p in run.meta['phases'] if p['name'] == name), {}) or {}
        # attribute only motion near the hovered target, and nothing that was already moving before the hover
        pre = max(0, lo - 24)
        busy = set()
        for eid, a in run.tracks.items():
            ch = channels(a, run.scroll_y)
            for k, v in ch.items():
                w = v[pre:lo]
                if len(w) > 1 and np.isfinite(w).sum() > 1 and np.nanmax(np.abs(np.diff(w))) > (0.6 if k in ('cx', 'cy') else 0.004):
                    busy.add(eid)
                    break
        if tgt:
            pad = max(80, 0.5 * max(tgt.get('w', 0), tgt.get('h', 0)))
            bx0, by0, bx1, by1 = tgt['x'] - tgt['w'] / 2 - pad, tgt['y'] - tgt['h'] / 2 - pad, tgt['x'] + tgt['w'] / 2 + pad, tgt['y'] + tgt['h'] / 2 + pad
        evs = []
        for eid, a in run.tracks.items():
            if eid in fol_ids or eid in busy:
                continue
            if tgt:
                x, y, w, h = a[lo, 0], a[lo, 1], a[lo, 2], a[lo, 3]
                if not np.isfinite(x) or x > bx1 or x + w < bx0 or y > by1 or y + h < by0 or w * h > 0.5 * run.meta['viewport'][0] * run.meta['viewport'][1]:
                    continue
            evs += [e for e in time_events(run, eid, lo, hi, name) if e['kind'] in ('tween', 'bounce')]
        motions, _ = group_events(run, evs)
        for m in motions[:12]:
            add_bezier(m)
            m['element'] = run.label(m['eid'])
            m['declared'] = declared_for(run, m['eid'], m['t0'], name)
        motions = motions[:12]
        css = [a for a in run.anims if a.get('phase') == name]
        R['hover'].append({'target': tgt, 'motions': motions, 'cssStarted': css[:12]})
        k = name.split(':')[1]
        if tgt:
            pad = max(60, 0.6 * max(tgt.get('w', 0), tgt.get('h', 0)))
            box = (tgt['x'] - tgt['w'] / 2 - pad, tgt['y'] - tgt['h'] / 2 - pad, tgt['x'] + tgt['w'] / 2 + pad, tgt['y'] + tgt['h'] / 2 + pad)
            shots = [f for f in range(lo, hi) if f in run.shots]
            t0 = run.t[lo]
            out = V.crop_strip(run, shots, box, d / f'sheets/hover-{k}.png', lambda f: f"+{run.t[f] - t0:.0f}ms", title=f"hover {k}: {tgt.get('sel', '')[:60]} \"{tgt.get('text', '')[:30]}\" (approach → hold → leave)")
            if out:
                images.append((f'sheets/hover-{k}.png', f"hover {k}: {' '.join((tgt.get('text') or tgt.get('sel') or '').split())}", out))
    for name, lo, hi in ph.get('sweep', []):
        shots = [f for f in range(lo, hi) if f in run.shots]
        t0 = run.t[lo]
        images.append(('sheets/sweep.png', 'cursor sweep across the viewport (cursor-reactive / WebGL raycast effects)', V.contact_sheet(run, shots, d / 'sheets/sweep.png', lambda f: f"+{run.t[f] - t0:.0f}ms x={run.mouse[f, 0]:.0f}", title='cursor sweep', max_tiles=30)))
        p, _ = V.diff_heatmap(run, shots, d / 'plots/sweep-heat.png', 'where pixels change while the cursor sweeps')
        if p:
            images.append(('plots/sweep-heat.png', 'cursor sweep motion heatmap', p))

    # ---------- scroll ----------
    R['scroll'] = {}
    res = (run.meta.get('result') or {}).get('scroll')
    if res and 'scroll' in ph:
        down, back = (res['down'], res['back']) if isinstance(res, dict) else (res, [])
        _, lo, hi = ph['scroll'][0]
        hi2 = ph['scroll-back'][0][2] if 'scroll-back' in ph else hi
        beh = scroll_behaviour(run, lo, hi2, down, back, exclude=fol_ids)
        for b in beh:
            b['element'] = run.label(b['eid'])
        sm = smooth_scroll(run, lo, hi) if run.lenis is not None else smooth_scroll_steps(run, down)
        if sm and sm.get('lerp_per_frame'):
            k = sm['lerp_per_frame']
            if 0 < k < 1:
                sm['time_constant_ms'] = round(-1000 / (fps * np.log(1 - k)))
                sm['settle_95_ms'] = round(3 * sm['time_constant_ms'])
        R['scroll'] = {'virtualScroll': run.extra.get('virtualScroll'), 'steps': len(down), 'maxScroll': round(float(max(s['pos'] for s in down))) if down else 0, 'smoothing': sm, 'elements': beh}
        frames = [s['frame'] for s in down]
        sl = 'vscroll' if run.extra.get('virtualScroll') else 'scroll'
        images.append(('sheets/scroll.png', 'scroll sweep: settled frame at every step (labels = scroll position' + (', virtual scroll measured from layer motion' if sl == 'vscroll' else '') + ')', V.contact_sheet(run, frames, d / 'sheets/scroll.png', lambda f: f"{sl}={run.scroll[f]:.0f}", title='scroll sweep (settled + hold at each step)', max_tiles=48)))
        if back:
            images.append(('sheets/scroll-back.png', 'scrolling back up: triggered effects stay, scrubbed effects rewind', V.contact_sheet(run, [s['frame'] for s in back], d / 'sheets/scroll-back.png', lambda f: f"back scroll={run.scroll[f]:.0f}", title='scroll back up', max_tiles=24)))
        sm_path = V.plot_scroll_map(run, beh, down, d / 'plots/scroll-map.png')
        if sm_path:
            images.append(('plots/scroll-map.png', 'which elements change at which scroll position', sm_path))
        # scrubbed video
        vids = [v for v in run.extra['video'][lo:hi] if v]
        if vids:
            arr = np.array([v[0] for v in vids])
            if arr.max() - arr.min() > 0.2:
                R['scroll']['videoScrub'] = {'from_s': round(float(arr.min()), 2), 'to_s': round(float(arr.max()), 2)}
        cams = [c for c in run.extra['cam'][lo:hi] if c]
        if cams:
            c = np.array(cams)
            R['scroll']['camera'] = {'moved': bool(np.ptp(c[:, :3], axis=0).max() > 0.01), 'range_xyz': [round(float(x), 3) for x in np.ptp(c[:, :3], axis=0)]}

    # ---------- stack / declared ----------
    ins = run.inspect
    R['stack'] = ins.get('libs', {})
    R['declared'] = {
        'lenis': ins.get('lenis'), 'scrollTriggers': (ins.get('gsap') or {}).get('scrollTriggers'),
        'gsapTweensSeen': len((ins.get('gsap') or {}).get('seen', [])), 'cssTransitions': ins.get('transitions'),
        'cssAnimations': ins.get('cssAnimations'), 'motionProps': ins.get('motionProps'), 'lottie': ins.get('lottie'),
        'intersectionObservers': ins.get('intersectionObservers'),
    }
    R['webgl'] = {'three': ins.get('three'), 'shaders': ins.get('shaders'), 'canvases': ins.get('canvases')}
    R['media'] = {'videos': ins.get('videos'), 'imageSequences': ins.get('imageSequences'), 'network': (run.meta.get('result') or {}).get('network')}
    R['images'] = [{'path': p, 'what': w} for p, w, ok in images if ok]
    strip_samples(R)
    (d / 'report.json').write_text(json.dumps(R, indent=1, default=lambda o: o.tolist() if hasattr(o, 'tolist') else str(o)))
    (d / 'report.md').write_text(render_md(run, R))
    print(d / 'report.md')


def render_md(run, R):
    L = []
    m = R['meta']
    L.append(f"# Motion report: {R['url']}\n")
    L.append(f"{m['mode']} capture · {m['viewport'][0]}×{m['viewport'][1]} · {m['fps']} fps virtual clock · {m.get('frames')} frames · {m.get('browser')} · captured {m.get('capturedAt', '')[:19]} in {m.get('captureSeconds')} s real time" + (" · **reduced motion**" if m.get('reducedMotion') else ''))
    if m.get('error'):
        L.append(f"\n> Capture error: {m['error']}")
    st = {k: v for k, v in R['stack'].items() if v}
    L.append('\n## Stack\n' + (', '.join(f"**{k}** {v if v is not True else ''}".strip() for k, v in st.items()) or 'nothing detected'))
    sm = (R.get('scroll') or {}).get('smoothing')
    decl_lenis = (R['declared'].get('lenis') or [None])[0]
    if sm:
        L.append(f"\n**Scroll smoothing (measured{', virtual scroll' if (R.get('scroll') or {}).get('virtualScroll') else ''}):** lerp {sm['lerp_per_frame']}/frame" + (f" → time constant ≈{sm['time_constant_ms']} ms, ~95% settled in ≈{sm['settle_95_ms']} ms" if sm.get('time_constant_ms') else '') + f" ({sm['source']})" + (f". Declared Lenis options: `{json.dumps(decl_lenis)[:200]}`" if decl_lenis else ''))
    run_x = getattr(run, 'extra', {}) or {}
    if run_x.get('scrollPhase') and not run_x.get('scrollMoved'):
        L.append("\n**Page did not scroll:** wheel input moved neither the native scroll position nor any layer "
                 "(single-screen page, drag/click navigation, a nested scroller or scroll locked by an overlay). "
                 "Scroll analysis is empty; check `sheets/scroll.png` and try `pointer`/`timeline` or a different entry URL.")
    L.append('\n## Images to read (in this order)')
    for i, im in enumerate(R['images'], 1):
        L.append(f"{i}. `{im['path']}`: {im['what']}")

    intro = R.get('intro') or {}
    if intro.get('motions'):
        L.append('\n## Intro / time-based motion (from navigation)')
        L.append('| element | property | start | duration | from → to | measured easing | declared |\n|---|---|---|---|---|---|---|')
        for mo in intro['motions'][:40]:
            if mo['kind'] == 'cut':
                L.append(f"| {mo['element'][:48]} | {mo['channel']} | {mo['t0']}ms | ≥{mo['duration_ms']}ms (cut off by capture window, use `--intro` longer) | {fmt_change(mo['channel'], mo['from'], mo['to'])} so far | | {fmt_decl(mo.get('declared', []))} |")
                continue
            if mo['kind'] == 'bounce':
                L.append(f"| {mo['element'][:48]} | {mo['channel']} | {mo['t0']}ms | {mo['duration_ms']}ms | out-and-back, range {mo.get('range')} | (oscillation / pulse) | {fmt_decl(mo.get('declared', []))} |")
                continue
            if mo['kind'] == 'loop':
                L.append(f"| {mo['element'][:48]} | {mo['channel']} | {mo['t0']}ms | loop | {mo['rate_per_s']}/s | {'steady' if mo.get('steady') else 'varying'} | {fmt_decl(mo.get('declared', []))} |")
                continue
            sp = mo.get('spring')
            ease = fmt_ease(mo.get('easing')) + (f" · spring ζ={sp['zeta']} ω={sp['omega_n']} (k={sp['stiffness']}, c={sp['damping']})" if sp else '')
            n = f" (×{len(mo['elements'])})" if len(mo['elements']) > 1 else ''
            e = mo.get('easing') or {}
            dur = f"≈{e['est_duration_ms']}ms" if e.get('est_duration_ms') else f"{mo['duration_ms']}ms"
            t0 = mo['t0'] + round(e.get('start_offset_ms') or 0)
            L.append(f"| {mo['element'][:48]}{n} | {mo['channel']} | {t0}ms | {dur} | {fmt_change(mo['channel'], mo['from'], mo['to'])} | {ease} | {fmt_decl(mo.get('declared', []))} |")
        if (intro.get('jumps') or {}).get('count'):
            L.append(f"\n- **Layout jumps** (instant changes, e.g. a preloader finishing or content swapping): {intro['jumps']['count']} element changes at {intro['jumps']['times_ms'][:8]} ms")
        for s in intro.get('staggers', []):
            e = s.get('easing') or {}
            L.append(f"\n- **Stagger:** {s['count']} elements, {s['channel']}, every **{s['stagger_ms']} ms** from {s['first_t0']} ms, each ≈{e.get('est_duration_ms') or s['duration_ms']} ms {fmt_ease(e)}")

    sc = R.get('scroll') or {}
    if sc.get('elements'):
        L.append(f"\n## Scroll behaviour ({sc['steps']} wheel steps to {sc['maxScroll']} px, then back up)")
        if sc.get('virtualScroll'):
            L.append('_Virtual scroll: the site moves its own layers, so scroll positions are estimated from layer motion and vertical translation is not classified per element; rely on the scroll sheet for layout motion._')
        fixed = [b for b in sc['elements'] if b.get('fixed') and not b.get('scrollChanges')]
        if fixed:
            L.append(f"- **Fixed / overlay layer** ({len(fixed)} elements stay in the viewport): " + ', '.join(sorted({b['element'].split(' > ')[0].split(' \"')[0] for b in fixed}))[:300])
        # group identical findings; interesting behaviours first
        def sig(b):
            parts = []
            if b.get('fixed'):
                parts.append('fixed')
            for a0, b0 in b.get('pinned', []):
                parts.append(f'pin{round(a0, -2)}-{round(b0, -2)}')
            if 'parallax_speed' in b:
                parts.append(f"px{b['parallax_speed']:.1f}")
            for c in b.get('scrollChanges', []):
                parts.append(f"{c['channel']}:{c['kind'][:8]}:{(c.get('scroll_range') or [0])[0] // 250}")
            return '|'.join(parts)
        def rank(b):
            r = 0
            if b.get('pinned'): r -= 5
            if 'parallax_speed' in b: r -= 3
            for c in b.get('scrollChanges', []):
                r -= 4 if c['kind'].startswith(('triggered', 'toggle')) else 2 if c['channel'] != 'cy' else 1
            return r
        groups = {}
        for b in sc['elements']:
            if b.get('fixed') and not b.get('scrollChanges'):
                continue
            groups.setdefault(sig(b), []).append(b)
        ordered = sorted(groups.values(), key=lambda g: (rank(g[0]), -len(g)))
        if len(ordered) > 30:
            L.append(f"- ({len(ordered) - 30} more behaviour groups in report.json)")
        for g in ordered[:30]:
            b = g[0]
            if len(g) > 1:
                b = {**b, 'element': f"{b['element'][:48]} (+{len(g) - 1} similar)"}
            bits = []
            if b.get('fixed'):
                bits.append('fixed in viewport')
            for a0, b0 in b.get('pinned', []):
                bits.append(f"**pinned** {a0}–{b0} px")
            if 'parallax_speed' in b:
                bits.append(f"**parallax** speed {b['parallax_speed']}× scroll")
            for c in b.get('scrollChanges', []):
                s = f"{c['channel']} {fmt_change(c['channel'], c['range'][0], c['range'][1]) if c['channel'] not in ('cx', 'cy') else f'range {c[chr(114)+chr(97)+chr(110)+chr(103)+chr(101)][1] - c[chr(114)+chr(97)+chr(110)+chr(103)+chr(101)][0]:.0f}px'} **{c['kind']}**"
                if c.get('scroll_range'):
                    s += f" over scroll {c['scroll_range'][0]}–{c['scroll_range'][1]}"
                if c.get('duration_ms'):
                    s += f", ≈{(c.get('easing') or {}).get('est_duration_ms') or c['duration_ms']} ms {fmt_ease(c.get('easing'))}"
                if c.get('trigger') and c['trigger'].get('element_top_in_viewport') is not None:
                    s += f", fires when element top is at {c['trigger']['element_top_in_viewport']:.0%} of the viewport"
                bits.append(s)
            L.append(f"- {b['element'][:60]}: " + '; '.join(bits))
        if sc.get('videoScrub'):
            L.append(f"- **Scroll-scrubbed video:** currentTime {sc['videoScrub']['from_s']}s → {sc['videoScrub']['to_s']}s")
        if (sc.get('camera') or {}).get('moved'):
            L.append(f"- **WebGL camera** moves during scroll: range xyz {sc['camera']['range_xyz']}")
    sts = R['declared'].get('scrollTriggers') or []
    if sts:
        L.append('\n**Declared ScrollTriggers:**\n\n| trigger | start → end (px) | vars | scrub | pin | animation |\n|---|---|---|---|---|---|')
        for s in sts[:30]:
            an = s.get('animation') or {}
            L.append(f"| {s['trigger'][:40]} | {s['start']} → {s['end']} | {s['startVar']} / {s['endVar']} | {s['scrub']} | {s['pin']} | {an.get('type', '')} {an.get('duration', '')}s {an.get('ease') or ''} {json.dumps(an.get('props') or {})[:60]} |")

    hv = [h for h in R.get('hover', []) if h['motions'] or h['cssStarted']]
    ptr_phase = any(p['name'] in ('pointer', 'sweep') for p in run.meta.get('phases', []))
    if ptr_phase and not R.get('hover'):
        L.append('\n## Hover / pointer interactions\nNo interactive elements in the viewport where the hover tour ran (recon tours only the first screen). '
                 'Run `pointer --target "<selector>"` for elements further down.')
    if R.get('hover'):
        L.append('\n## Hover / pointer interactions')
        if not hv:
            L.append('No hover response detected on the tested targets.')
        for h in hv:
            t = h['target']
            miss = '' if t.get('hitAtArrival', t.get('hit', True)) else ' ⚠ pointer did not land on the element (covered by another layer?)'
            L.append(f"- **{' '.join((t.get('text') or t.get('sel', '')[:40]).split())}** (`{t.get('sel', '')[:60]}`){miss}")
            for mo in h['motions'][:8]:
                e = mo.get('easing') or {}
                L.append(f"  - {mo['element'][:50]}: {mo['channel']} {fmt_change(mo['channel'], mo['from'], mo['to'])}, ≈{e.get('est_duration_ms') or mo['duration_ms']} ms {fmt_ease(e)} {('· declared ' + fmt_decl(mo['declared'])) if mo.get('declared') else ''}")
            for a in h['cssStarted'][:6]:
                s = a.get('source') or {}
                L.append(f"  - CSS {a['type']} `{a.get('name')}` on {a.get('target')}: {s.get('duration')}ms {s.get('easing')}")
    for f in R.get('followers', []):
        lx = f.get('lerp_x') or {}
        L.append(f"- **Cursor follower:** {f['element'][:50]} tracks the pointer (corr {f['corr_x']}), smoothing lerp ≈{lx.get('lerp_per_frame')}/frame, lag ≈{(f.get('lag') or {}).get('lag_frames')} frames")

    wg = R.get('webgl') or {}
    if wg.get('three') or wg.get('shaders'):
        L.append('\n## WebGL / canvas')
        if wg.get('three'):
            th = wg['three']
            L.append(f"- three.js r{th.get('revision')}: {len(th.get('scenes', []))} scene(s); " + '; '.join(f"{s['meshes']} meshes, materials {s['materials']}, uniforms {s['shaderUniforms'][:12]}" for s in th.get('scenes', [])[:4]))
        L.append(f"- {wg.get('shaders')} shader sources captured → `shaders/`")
        for c in (wg.get('canvases') or [])[:6]:
            L.append(f"- canvas {c['sel'][:50]} ctx={c['ctx']} {c['size'][0]}×{c['size'][1]}")
    md = R.get('media') or {}
    if md.get('imageSequences'):
        for s in md['imageSequences']:
            L.append(f"- **Canvas image sequence:** {s['distinctImages']} distinct frames drawn ({s['sample'][:1]})")
    net = md.get('network') or {}
    if net.get('frameSequences'):
        for s in net['frameSequences'][:5]:
            L.append(f"- **Numbered image requests:** {s['count']} × `{s['pattern']}`")
    decl = R['declared']
    if decl.get('cssTransitions'):
        L.append('\n## Declared CSS transitions (sample)')
        for tr in decl['cssTransitions'][:15]:
            L.append(f"- `{tr['example'][:50]}`: {tr['property'][:60]} {tr['duration']} {tr['easing']}" + (f" delay {tr['delay']}" if tr['delay'] not in ('0s',) else ''))
    if decl.get('motionProps'):
        L.append('\n## Framer Motion / Motion props (from React)')
        groups = {}
        for mp in decl['motionProps']:
            groups.setdefault(json.dumps(mp['props'], sort_keys=True), []).append(mp['sel'])
        for props, sels in list(groups.items())[:12]:
            L.append(f"- `{sels[0][:50]}`" + (f" (+{len(sels) - 1} more)" if len(sels) > 1 else '') + f": {props[:240]}")
    L.append('\n---\nMeasured values come from per-frame DOM sampling under a deterministic virtual clock; very slow tails (<0.01 px/frame) end a motion early. '
             '"declared" values come from the page itself (CSS/WAAPI via DevTools, GSAP via its timeline). Scroll positions are quantised to the sweep step. '
             'Canvas/WebGL content is only visible in the images and heatmaps, not in the element tables.')
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    main(sys.argv[1])
