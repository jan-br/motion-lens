# motion-lens

Give Claude Code (or any agent that reads images and text) a structured way to perceive **how a website moves**:
intros and loaders, scroll storytelling, pinned and parallax sections, smooth scroll, hover micro-interactions,
cursor effects and WebGL scenes.

An agent can't watch video, and screenshots miss timing, easing and interaction. motion-lens gives it:

1. **Frame-exact capture.** It runs chrome-headless-shell in `--deterministic-mode` on the GPU and drives it over
   the DevTools protocol. A virtual clock steps every frame (CSS, JS/requestAnimationFrame, WebGL, video), with real
   mouse-wheel scrolling and scripted pointer paths. Heavy WebGL sites get captured at an exact 60 fps regardless of
   how fast they render.
2. **Introspection.** It reads CSS/Web Animations from DevTools. It exposes GSAP and ScrollTrigger, and Lenis, even
   when they are bundled without globals (it rewrites their scripts). It also reads Framer Motion props from React,
   three.js scenes through the devtools hook, and captured GLSL/WGSL shaders. It detects scroll-scrubbed video,
   canvas image sequences and IntersectionObserver triggers.
3. **Measurement.** Per-element motion is fitted to cubic-bezier curves and named eases, with a free sub-frame start
   and true duration. It also fits damped springs (ζ, ω), smooth-scroll lerp, and cursor-follower lerp and lag. It
   detects staggers and loops, and classifies scroll behaviour as pinned, parallax, scrubbed, toggle or triggered
   once, using a scroll-back pass.
4. **Readable output.** Labelled contact sheets, hover crop strips with the pointer drawn in, easing plots, scroll
   maps and motion heatmaps, plus `report.md` / `report.json`.

## Install
```bash
bin/motion-lens setup        # chrome-headless-shell -> ~/.cache/motion-lens, python deps
# as a Claude Code plugin (skill + motion-analyst subagent):
claude plugin marketplace add ~/projects/motion-lens && claude plugin install motion-lens@motion-lens
```
Needs Node ≥ 21, Python 3 (numpy, scipy, pillow, matplotlib; OpenCV optional) and ffmpeg (not required). A GPU is
recommended; use `--no-gpu` for SwiftShader.

## Use
```bash
bin/motion-lens recon https://lusion.co --out runs/lusion      # first look (intro + hover tour + scroll sweep)
bin/motion-lens timeline <url> --intro 6                        # loaders / intro choreography
bin/motion-lens scroll <url> --step 300 --max-steps 80          # scroll storytelling
bin/motion-lens pointer <url> --start-scroll 2400 --targets 10  # hover states in a section
bin/motion-lens inspect <url>                                   # stack + declared definitions only
bin/motion-lens <mode> ./my-site/                               # local files/folders are served automatically
bin/motion-lens analyze <run-dir>                               # re-run the analysis
bin/motion-lens validate                                        # ground-truth test suite
```
Then read `report.md` and the images it lists. `skills/motion-lens/references/reading-guide.md` explains every
field and its limits.

## Accuracy
`fixtures/ground-truth.html` contains animations with known definitions: a CSS keyframe intro, a GSAP stagger, a
rAF spring, a pinned and scrubbed ScrollTrigger, a CSS scroll-timeline, IntersectionObserver reveals, a hover
transition with overshoot, a lerp cursor follower and Lenis. `bin/motion-lens validate` checks that they are
recovered:

| truth | measured |
|---|---|
| CSS 800 ms `cubic-bezier(0.16,1,0.3,1)` | ≈785 ms, fit (0.153, 1.033, 0.347, 0.993) |
| GSAP `power3.out` 1000 ms, stagger 0.12 | power3.out, 1000 ms, stagger 122 ms |
| spring ζ=0.35, ω=14 | ζ=0.35, ω=14.0 |
| hover 300 ms `cubic-bezier(0.34,1.56,0.64,1)` | ≈298 ms, fit (0.34, 1.593, 0.682, 0.985) |
| Lenis lerp 0.1 / follower lerp 0.15 | 0.101 / 0.15 |
| pin + 600 px scrub, 600 ms IO reveal | pinned, 600 px scrubbed, triggered once 600 ms ease-out |

It has also been exercised on lusion.co (virtual scroll, three.js r158, 80+ shaders), apple.com/airpods-pro
(pinned sections, scroll-scrubbed video, a 30 px / 700 ms reveal system) and basement.studio (three.js, Framer
Motion props).

## Layout
`capture/`: Node, no dependencies (CDP client, deterministic session, script rewriter, in-page instrumentation,
modes). `analyze/`: Python (loading, fitting, events, visuals, report). `skills/`, `agents/`, `.claude-plugin/`:
Claude Code packaging. `fixtures/`: ground truth. `tests/validate.py`: accuracy suite.

## Limits
Headless Chrome only (sites that detect bots or GPU tier may differ). DOM measurements don't see inside a canvas;
use the sheets, heatmaps and shaders there. Scroll positions are quantised to the sweep step, and on virtual-scroll
sites they are estimated. Frame-rate jank isn't measured, because the clock is virtual.
