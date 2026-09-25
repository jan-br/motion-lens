---
name: motion-lens
description: Use when you need to understand how a website moves or feels rather than how it looks — intros/loaders, scroll-driven or pinned sections, parallax, smooth scroll, hover/micro-interactions, cursor followers, WebGL/three.js scenes, page transitions — when studying a reference site (lusion, Apple product pages, awwwards-style studios), recreating its motion, or verifying the motion of a page you built. Screenshots alone miss timing, easing and scroll/pointer behaviour.
---

# motion-lens

## Overview
You cannot watch video. motion-lens drives a real headless Chrome (GPU) on a **deterministic virtual clock**, steps the page frame by frame through load, scroll (real wheel input) and pointer paths, and turns it into **still images you can read + a structured report** with measured timings, fitted easings/springs and the page's own declared animation definitions (CSS/WAAPI, GSAP/ScrollTrigger, Lenis, Framer Motion, three.js, shaders).

## Run it
```bash
MOTION_LENS=<plugin root>/bin/motion-lens   # plugin root = this skill's base dir/../..
# if it says "chrome-headless-shell not found", run: $MOTION_LENS setup
$MOTION_LENS recon <url|local-file|dir> --out <dir>           # intro + hover tour + scroll sweep + introspection
```
| mode | use for |
|---|---|
| `recon` | first look at any page (≈20–60 s) |
| `timeline --intro 6` | loaders / intro choreography, frame-exact |
| `scroll --step 300` | scroll storytelling, pins, scrubs, reveals (`--max-steps`, `--hold`) |
| `pointer --target "#cta"` | hover states / magnetic buttons of one element anywhere on the page (auto-scrolls to it) |
| `pointer --start-scroll <px> --targets 10` | hover tour of everything visible at a scroll position |
| `inspect` | just the stack + declared definitions |

Optional second opinion from a model that *can* watch video: capture with `--film`, then `$MOTION_LENS review <dir> [--focus "..."]` → `gemini-review.md` (timestamped choreography/pacing/feel critique; needs `GEMINI_API_KEY`, loaded from `~/.config/motion-lens/env`). Use it for feel, not numbers; verify its claims against the sheets.

Local projects: pass the file or folder (served over http automatically). Run `$MOTION_LENS --help` for all options.

`recon` hovers only elements in the **first viewport**; for anything lower use `pointer --target`. Element page positions are in `elements.json` (`rect` = x, page y, w, h).

## Read the output (in this order)
If `report.md` already answers the question (especially with declared values), images are only for confirmation.
1. `report.md`: its **"Images to read"** list is ordered; read those images with the Read tool.
2. `sheets/*.png`: labelled contact sheets (t=ms / scroll=px / +ms since hover) and hover crop strips. The drawn arrow = pointer position (headless has none).
3. `plots/*-easing.png` (measured dots vs fits), `plots/scroll-map.png`, `plots/*-heat.png` (where pixels change, incl. WebGL).
4. `report.json` for exact numbers; `shaders/` for captured GLSL/WGSL; `frames/` for any single frame at full res.

For how to interpret each field and known limits, read `references/reading-guide.md`.

## Rules
- **Prefer declared over measured when both exist** (declared = the page's own CSS/GSAP values). Measured values matter when nothing is declared (JS/rAF/WebGL) and for how it actually feels.
- `~name` = nearest named ease; the bezier fit is the precise curve. `≈` durations include an inferred slow tail.
- A ⚠ on a hover target means the pointer landed on another layer; re-run `pointer` on that section.
- Virtual-scroll sites (scroll position never moves) get estimated positions: trust the scroll sheet over per-element translation.
- Image-heavy: for a full teardown, delegate to the `motion-analyst` subagent so frames stay out of your context.
- To check your own build: capture it, compare its report/sheets against the reference run.
