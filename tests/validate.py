#!/usr/bin/env python3
"""Ground-truth validation: capture fixtures/ground-truth.html and check that motion-lens recovers its known
animation definitions (see fixtures/ground-truth.json) within tolerance. Exit code 1 on any failure."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / 'bin' / 'motion-lens'
FIX = ROOT / 'fixtures' / 'ground-truth.html'


def capture(mode, *args):
    out = Path(tempfile.mkdtemp(prefix=f'ml-validate-{mode}-'))
    r = subprocess.run([str(BIN), mode, str(FIX), '--out', str(out), *args], capture_output=True, text=True)
    if not (out / 'report.json').exists():
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit(f'capture {mode} failed')
    return json.loads((out / 'report.json').read_text()), out


results = []


def check(name, ok, got):
    results.append((name, bool(ok), got))


def near(v, target, tol):
    return v is not None and abs(v - target) <= tol


def motion(R, sel_end, channel):
    return next((m for m in R['intro']['motions'] if m['element'].split(' "')[0].endswith(sel_end) and m['channel'] == channel), None)


def main():
    R, d = capture('recon')
    # --- intro ---
    hero = motion(R, '#hero-title', 'cy')
    e = (hero or {}).get('easing') or {}
    check('CSS intro: duration ≈800ms', near(e.get('est_duration_ms'), 800, 60), e.get('est_duration_ms'))
    b = e.get('bezier') or [9, 9, 9, 9]
    check('CSS intro: bezier ≈ (0.16,1,0.3,1)', all(abs(x - y) < 0.12 for x, y in zip(b, [0.16, 1, 0.3, 1])), b)
    decl = json.dumps(hero.get('declared')) if hero else ''
    check('CSS intro: declared 800ms cubic-bezier(0.16, 1, 0.3, 1)', '800' in decl and '0.16, 1, 0.3, 1' in decl, decl[:120])
    card = motion(R, 'div.card', 'cy')
    ce = (card or {}).get('easing') or {}
    check('GSAP cards: named power3.out', (ce.get('named') or [{}])[0].get('name', '').startswith('power3.out'), (ce.get('named') or [{}])[0].get('name'))
    check('GSAP cards: duration ≈1000ms', near(ce.get('est_duration_ms'), 1000, 60), ce.get('est_duration_ms'))
    st = next((s for s in R['intro']['staggers'] if s['channel'] == 'cy'), {})
    check('GSAP cards: stagger ≈120ms ×4', near(st.get('stagger_ms'), 120, 8) and st.get('count') == 4, (st.get('stagger_ms'), st.get('count')))
    check('GSAP declared: power3.out stagger 0.12', 'power3.out' in json.dumps(card.get('declared') if card else '') and '0.12' in json.dumps(card.get('declared') if card else ''), '')
    sp = (motion(R, '#spring', 'cx') or {}).get('spring') or {}
    check('spring: ζ≈0.35', near(sp.get('zeta'), 0.35, 0.04), sp.get('zeta'))
    check('spring: ω≈14', near(sp.get('omega_n'), 14, 1.0), sp.get('omega_n'))
    loop = motion(R, '#spinner', 'rot') or {}
    check('CSS loop: 180°/s', near(loop.get('rate_per_s'), 180, 2), loop.get('rate_per_s'))
    # --- scroll ---
    S = R['scroll']
    els = {b['element'].split(' "')[0].split(' > ')[-1]: b for b in S['elements']}
    pb = els.get('#pin-box', {})
    cx = next((c for c in pb.get('scrollChanges', []) if c['channel'] == 'cx'), {})
    check('ScrollTrigger: #pin-box pinned', bool(pb.get('pinned')), pb.get('pinned'))
    check('ScrollTrigger: x scrubbed 600px', 'scrubbed' in cx.get('kind', '') and near(cx['range'][1] - cx['range'][0], 600, 5), cx.get('range'))
    rv = els.get('p.reveal', {})
    op = next((c for c in rv.get('scrollChanges', []) if c['channel'] == 'opacity'), {})
    check('IO reveal: triggered once', 'triggered once' in op.get('kind', ''), op.get('kind'))
    check('IO reveal: ≈600ms', near((op.get('easing') or {}).get('est_duration_ms') or op.get('duration_ms'), 600, 50), op.get('duration_ms'))
    sb = els.get('#sda-bar', {})
    check('CSS scroll-timeline: scrubbed scale', any(c['channel'] == 'scale' and 'scrubbed' in c['kind'] for c in sb.get('scrollChanges', [])), '')
    sm = S.get('smoothing') or {}
    check('Lenis smoothing: lerp≈0.10', near(sm.get('lerp_per_frame'), 0.10, 0.01), sm.get('lerp_per_frame'))
    check('Lenis declared: lerp 0.1', (R['declared'].get('lenis') or [{}])[0].get('lerp') == 0.1, '')
    sts = R['declared'].get('scrollTriggers') or []
    check('ScrollTrigger declared: scrub 1, pin', any(s['scrub'] == 1 and s['pin'] for s in sts), len(sts))
    check('stack: gsap + lenis detected', bool(R['stack'].get('gsap')) and bool(R['stack'].get('lenis')), R['stack'])
    check('WebGL: shaders captured', (R['webgl'].get('shaders') or 0) >= 2, R['webgl'].get('shaders'))
    io = R['declared'].get('intersectionObservers') or [{}]
    check('IntersectionObserver: threshold 0.2', io[0].get('threshold') == 0.2, io[0].get('threshold'))
    # --- pointer ---
    P, _ = capture('pointer', '--start-scroll', '2400')
    hov = next((h for h in P['hover'] if h['target'].get('sel') == '#btn'), {})
    up = next((m for m in hov.get('motions', []) if m['channel'] == 'scale' and m['to'] > m['from']), {})
    ue = up.get('easing') or {}
    check('hover: scale 1→1.08', near(up.get('to'), 1.08, 0.01), up.get('to'))
    check('hover: ≈300ms', near(ue.get('est_duration_ms'), 300, 25), ue.get('est_duration_ms'))
    hb = ue.get('bezier') or [9, 9, 9, 9]
    check('hover: bezier ≈ (0.34,1.56,0.64,1)', all(abs(x - y) < 0.12 for x, y in zip(hb, [0.34, 1.56, 0.64, 1])), hb)
    fol = next((f for f in P['followers'] if '#follower' in f['element']), {})
    check('cursor follower: lerp≈0.15', near((fol.get('lerp_x') or {}).get('lerp_per_frame'), 0.15, 0.02), (fol.get('lerp_x') or {}).get('lerp_per_frame'))

    w = max(len(n) for n, _, _ in results)
    for n, ok, got in results:
        print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(w)}  {got if not ok or got != '' else ''}")
    fails = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - fails}/{len(results)} checks passed")
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
