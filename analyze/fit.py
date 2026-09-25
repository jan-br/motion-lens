"""Curve fitting for motion: cubic-bezier easing + nearest named ease, damped springs, per-frame lerp/smoothing."""
from __future__ import annotations

import math
import numpy as np
from scipy.optimize import least_squares


# ----------------------------------------------------------------------------- bezier
def bezier_y_at_x(x1, y1, x2, y2, xs):
    """Evaluate a CSS cubic-bezier(x1,y1,x2,y2) at progress xs (vectorised Newton + bisection)."""
    xs = np.clip(np.asarray(xs, dtype=float), 0, 1)
    cx, bx = 3 * x1, 3 * (x2 - x1) - 3 * x1
    ax = 1 - cx - bx
    cy, by = 3 * y1, 3 * (y2 - y1) - 3 * y1
    ay = 1 - cy - by
    s = xs.copy()
    for _ in range(6):
        xv = ((ax * s + bx) * s + cx) * s - xs
        d = (3 * ax * s + 2 * bx) * s + cx
        ok = np.abs(d) > 1e-6
        s = np.clip(np.where(ok, s - xv / np.where(ok, d, 1), s), 0, 1)
    bad = np.abs(((ax * s + bx) * s + cx) * s - xs) > 1e-5
    if bad.any():  # bisection only where Newton did not converge (flat derivative regions)
        xb = xs[bad]
        lo, hi = np.zeros_like(xb), np.ones_like(xb)
        sb = xb.copy()
        for _ in range(22):
            sb = (lo + hi) / 2
            xv = ((ax * sb + bx) * sb + cx) * sb
            lo = np.where(xv < xb, sb, lo)
            hi = np.where(xv >= xb, sb, hi)
        s[bad] = sb
    return ((ay * s + by) * s + cy) * s


# named eases (normalised 0..1 -> 0..1)
def _back_out(t, s=1.70158):
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2


def _elastic_out(t):
    c4 = 2 * math.pi / 3
    return np.where(t <= 0, 0, np.where(t >= 1, 1, 2 ** (-10 * t) * np.sin((t * 10 - 0.75) * c4) + 1))


NAMED = {
    'linear': lambda t: t,
    'css ease': lambda t: bezier_y_at_x(0.25, 0.1, 0.25, 1, t),
    'css ease-in': lambda t: bezier_y_at_x(0.42, 0, 1, 1, t),
    'css ease-out': lambda t: bezier_y_at_x(0, 0, 0.58, 1, t),
    'css ease-in-out': lambda t: bezier_y_at_x(0.42, 0, 0.58, 1, t),
    'sine.out': lambda t: np.sin(t * math.pi / 2),
    'sine.inOut': lambda t: -(np.cos(math.pi * t) - 1) / 2,
    'power1.out (quad)': lambda t: 1 - (1 - t) ** 2,
    'power2.out (cubic)': lambda t: 1 - (1 - t) ** 3,
    'power3.out (quart)': lambda t: 1 - (1 - t) ** 4,
    'power4.out (quint)': lambda t: 1 - (1 - t) ** 5,
    'expo.out': lambda t: np.where(t >= 1, 1, 1 - 2 ** (-10 * t)),
    'circ.out': lambda t: np.sqrt(1 - (t - 1) ** 2),
    'power1.in (quad)': lambda t: t ** 2,
    'power2.in (cubic)': lambda t: t ** 3,
    'power3.in (quart)': lambda t: t ** 4,
    'expo.in': lambda t: np.where(t <= 0, 0, 2 ** (10 * t - 10)),
    'power1.inOut (quad)': lambda t: np.where(t < .5, 2 * t * t, 1 - (-2 * t + 2) ** 2 / 2),
    'power2.inOut (cubic)': lambda t: np.where(t < .5, 4 * t ** 3, 1 - (-2 * t + 2) ** 3 / 2),
    'power3.inOut (quart)': lambda t: np.where(t < .5, 8 * t ** 4, 1 - (-2 * t + 2) ** 4 / 2),
    'power4.inOut (quint)': lambda t: np.where(t < .5, 16 * t ** 5, 1 - (-2 * t + 2) ** 5 / 2),
    'expo.inOut': lambda t: np.where(t <= 0, 0, np.where(t >= 1, 1, np.where(t < .5, 2 ** (20 * t - 10) / 2, (2 - 2 ** (-20 * t + 10)) / 2))),
    'back.out(1.7)': _back_out,
    'elastic.out': _elastic_out,
}


def fit_easing(t, p):
    """t, p: normalised time/progress samples (0..1). Returns bezier fit + ranked named eases."""
    t, p = np.asarray(t, float), np.asarray(p, float)
    ranked = sorted(((float(np.sqrt(np.mean((f(t) - p) ** 2))), name) for name, f in NAMED.items()))

    def resid(v):
        return bezier_y_at_x(v[0], v[1], v[2], v[3], t) - p

    best = None
    for guess in ([0.25, 0.1, 0.25, 1], [0.16, 1, 0.3, 1], [0.34, 1.56, 0.64, 1]):
        try:
            r = least_squares(resid, guess, bounds=([0, -1, 0, -1], [1, 2.5, 1, 2.5]))
        except Exception:
            continue
        if best is None or r.cost < best.cost:
            best = r
    bez = [round(float(v), 3) for v in best.x] if best is not None else None
    bez_rmse = float(np.sqrt(np.mean(resid(best.x) ** 2))) if best is not None else None
    return {
        'bezier': bez, 'bezier_css': f'cubic-bezier({", ".join(str(v) for v in bez)})' if bez else None,
        'bezier_rmse': round(bez_rmse, 4) if bez_rmse is not None else None,
        'named': [{'name': n, 'rmse': round(e, 4)} for e, n in ranked[:3]],
        'overshoot': round(float(max(0.0, p.max() - 1)), 4),
    }


def fit_easing_duration(t_ms, p, obs_ms, frame_ms=1000 / 60, bezier=True):
    """Fit named eases with a free sub-frame start offset and a free true duration T (slow tails fall below the
    sampling resolution, so the visible motion ends early). t_ms: time since the last still frame; p: progress
    normalised by the element's final resting value."""
    t_ms, p = np.asarray(t_ms, float), np.asarray(p, float)
    # coarse vectorised grid over (start offset, duration) for every named ease, then refine the top few
    TS = np.linspace(0, frame_ms * 2, 7)
    TT = np.linspace(max(obs_ms * 0.6, 1), obs_ms * 2.2 + 1, 24)
    coarse = []
    for name, f in NAMED.items():
        g = np.clip((t_ms[None, None, :] - TS[:, None, None]) / TT[None, :, None], 0, 1)
        err = np.sqrt(np.mean((f(g) - p[None, None, :]) ** 2, axis=2))
        k = np.unravel_index(np.argmin(err), err.shape)
        coarse.append((float(err[k]), name, (TS[k[0]], TT[k[1]])))
    coarse.sort(key=lambda c: c[0])
    best = []
    for _, name, guess in coarse[:3]:
        f = NAMED[name]
        def resid(v):
            ts, T = v
            return f(np.clip((t_ms - ts) / T, 0, 1)) - p
        try:
            lo_b, hi_b = [0, max(obs_ms * 0.6, 1)], [frame_ms * 2, obs_ms * 2.2 + 1]
            x0 = [min(max(guess[0], lo_b[0] + 1e-6), hi_b[0] - 1e-6), min(max(guess[1], lo_b[1] + 1e-6), hi_b[1] - 1e-6)]
            r = least_squares(resid, x0, bounds=(lo_b, hi_b))
        except Exception:
            continue
        best.append((float(np.sqrt(np.mean(r.fun ** 2))), name, r.x))
    best.sort(key=lambda b: b[0])
    e, name, (ts, T) = best[0]
    if bezier:
        fe = fit_easing(np.clip((t_ms - ts) / T, 0, 1), p)
    else:
        fe = {'bezier': None, 'bezier_css': None, 'bezier_rmse': None, 'overshoot': round(float(max(0.0, np.max(p) - 1)), 4)}
    fe['named'] = [{'name': n, 'rmse': round(v, 4), 'duration_ms': round(float(x[1]))} for v, n, x in best[:3]]
    fe['est_duration_ms'] = round(float(T))
    fe['start_offset_ms'] = round(float(ts), 1)
    return fe


# ----------------------------------------------------------------------------- springs
def spring_curve(t, zeta, wn):
    t = np.maximum(0, t)
    if zeta < 1:
        wd = wn * math.sqrt(1 - zeta * zeta)
        return 1 - np.exp(-zeta * wn * t) * (np.cos(wd * t) + (zeta * wn / wd) * np.sin(wd * t))
    return 1 - np.exp(-wn * t) * (1 + wn * t)


def fit_spring(t_s, p):
    """Fit a mass-1 damped spring (from rest) to progress samples; t_s in seconds from motion start."""
    t_s, p = np.asarray(t_s, float), np.asarray(p, float)

    def resid(v):
        return spring_curve(t_s - v[2], v[0], v[1]) - p

    best = None
    for z0 in (0.2, 0.4, 0.7):
        for w0 in (6, 12, 20):
            try:
                r = least_squares(resid, [z0, w0, 0.0], bounds=([0.02, 0.5, -0.1], [1.5, 80, 0.2]))
            except Exception:
                continue
            if best is None or r.cost < best.cost:
                best = r
    if best is None:
        return None
    zeta, wn, t0 = best.x
    return {'zeta': round(float(zeta), 3), 'omega_n': round(float(wn), 2), 'stiffness': round(float(wn * wn), 1), 'damping': round(float(2 * zeta * wn), 2),
            'rmse': round(float(np.sqrt(np.mean(best.fun ** 2))), 4), 'settle_ms': round(float(4 / (zeta * wn) * 1000)) if zeta < 1 else None}


# ----------------------------------------------------------------------------- smoothing / lerp
def fit_lerp(x, target):
    """Per-frame exponential smoothing x[n+1] = x[n] + k*(target[n] - x[n]); returns k (lerp factor)."""
    x, target = np.asarray(x, float), np.asarray(target, float)
    d = target[:-1] - x[:-1]
    dx = x[1:] - x[:-1]
    m = np.abs(d) > 2
    if m.sum() < 6:
        return None
    k = float(np.sum(dx[m] * d[m]) / np.sum(d[m] * d[m]))
    pred = x[:-1][m] + k * d[m]
    rmse = float(np.sqrt(np.mean((pred - x[1:][m]) ** 2)))
    return {'lerp_per_frame': round(k, 3), 'rmse_px': round(rmse, 2), 'samples': int(m.sum())}


def lag_frames(x, target, max_lag=30):
    """Frame lag that best aligns a follower with its target (cross-correlation of velocities)."""
    x, target = np.diff(np.asarray(x, float)), np.diff(np.asarray(target, float))
    if len(x) < 10 or np.std(x) < 1e-6 or np.std(target) < 1e-6:
        return None
    best, best_l = -2, 0
    for l in range(0, min(max_lag, len(x) - 5)):
        a, b = x[l:], target[:len(target) - l]
        c = float(np.corrcoef(a, b)[0, 1])
        if c > best:
            best, best_l = c, l
    return {'lag_frames': best_l, 'corr': round(best, 3)}


if __name__ == '__main__':
    tt = np.linspace(0, 1, 49)
    for true in ([0.16, 1, 0.3, 1], [0.34, 1.56, 0.64, 1], [0.42, 0, 0.58, 1]):
        f = fit_easing(tt, bezier_y_at_x(*true, tt))
        print('bezier', true, '->', f['bezier'], 'rmse', f['bezier_rmse'], 'named', f['named'][0])
    f = fit_easing(tt, 1 - (1 - tt) ** 4)
    print('power3.out ->', f['named'][:2])
    ts = np.arange(0, 1.5, 1 / 60)
    print('spring', fit_spring(ts, spring_curve(ts, 0.35, 14)))
    xs = [0.0]
    for n in range(80):
        xs.append(xs[-1] + 0.15 * (300 - xs[-1]))
    print('lerp', fit_lerp(xs, [300] * len(xs)))
