# Reading a motion-lens run

## Run folder
| file | contents |
|---|---|
| `report.md` / `report.json` | analysis (below) |
| `sheets/intro.png` | frames from navigation, label `t=` virtual ms (network-aware: time pauses while assets download) |
| `sheets/hover-N.png` | crop around hover target N: approach → hold (0.6 s) → leave; label `+ms` since the hover phase began |
| `sheets/sweep.png` | full-frame cursor sweep (3 horizontal passes) for cursor-reactive / WebGL raycast effects |
| `sheets/scroll.png` | one settled frame per wheel step (after settle + 0.6 s hold), label = scroll px (`vscroll` = estimated) |
| `sheets/scroll-back.png` | scrolling back up: scrubbed effects rewind, triggered ones stay |
| `plots/timeline.png` | phases, #elements changing per frame, scroll position, pointer x |
| `plots/*-heat.png` | accumulated pixel change (the only view of canvas/WebGL motion besides the sheets) |
| `plots/intro-easing.png` | measured normalised progress (dots) vs bezier fit and nearest named ease |
| `plots/scroll-map.png` | rows = element·channel, x = scroll px, colour = normalised value |
| `frames/`, `samples.jsonl` | raw frames + per-frame element states (x,y,w,h,opacity,tx,ty,sx,sy,rot), scroll, pointer |
| `inspect.json`, `animations.json`, `shaders/` | page introspection, CSS/WAAPI animation events (DevTools), GLSL/WGSL |

## Report fields
- **channels**: `cx`/`cy` = element centre (page-compensated), `opacity`, `scale` (own transform), `rot` (degrees, unwrapped).
- **kinds**: `tween` (fitted), `loop` (steady rate per second), `bounce` (out-and-back, e.g. pulse/wiggle), `jump` (≤2-frame change: layout shift/state swap — summarised as "Layout jumps").
- **easing**: `~name` nearest of CSS/GSAP/easings.net set, with its fitted duration; `fit cubic-bezier(...)` exact curve; `overshoot` beyond 1.0; springs as ζ (damping ratio), ω (rad/s), k=ω², c=2ζω (mass 1).
- **start / ≈duration**: fitted true start (sub-frame) and duration; the visible part ends earlier because tails move <0.01 px/frame.
- **declared**: what the page itself defines — `CSSAnimation`/`CSSTransition`/`WebAnimation` (from Chrome DevTools, exact), `gsap` (tween vars snapshot, exact).
- **Scroll behaviour**: `pinned a–b px` (viewport position constant while scrolling), `parallax speed k×` (1 = normal scroll), `fixed in viewport`, and per channel `scrubbed (scroll-linked)` (intermediate values at settled positions), `toggle` (binary, reverses on scroll back), `triggered once` (binary, stays) with duration/easing and the element's viewport position when it fired.
- **Scroll smoothing**: lerp per frame (Lenis-style) → time constant and ~95 % settle time.
- **Cursor follower**: element tracking the pointer, lerp/frame and lag.

- **gemini-review.md** (only after `review`): Gemini watched `review.mp4`, the frames played back in real time (phase start times listed at the end). Good at overall choreography, pacing and "feel"; it can invent or misplace details (e.g. call a parallax a pin), so confirm specifics in the sheets and report. Without `--film`, scroll phases are one frame per step, so footage there jumps.

## Limits
- Headless Chrome, 1440×900 default, `(hover:hover)` forced on. Sites that detect bots or GPU tier may differ.
- Measured timings are only as good as DOM sampling: canvas/WebGL content has no element data — read the sheets/heatmaps and shaders.
- Scroll positions are quantised to the sweep step (default 0.3 × viewport height); narrow ranges need `--step` smaller.
- Virtual-scroll sites: positions estimated from layer motion; vertical translation is not classified per element.
- Hover attribution lists motion near the target that started during the hover; the CSS transition list can include elements crossed on the way.
- Real-time jank/FPS is not measured (the clock is virtual).
