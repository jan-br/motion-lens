---
name: motion-lens
description: Use when you need to understand how a website moves or feels rather than how it looks — intros/loaders, scroll-driven or pinned sections, parallax, smooth scroll, hover/micro-interactions, cursor followers, WebGL/three.js scenes, page transitions — when studying a reference site (lusion, Apple product pages, awwwards-style studios), recreating its motion, or verifying the motion of a page you built. Screenshots alone miss timing, easing and scroll/pointer behaviour.
---

# motion-lens

## Overview
You cannot watch video. motion-lens drives a real headless Chrome (GPU) on a **deterministic virtual clock** and steps the page frame by frame through load, scroll (real wheel input) and pointer paths. The output is **readable images plus a structured report**: measured timings, fitted easings and springs, and the page's own declared animations (CSS/WAAPI, GSAP/ScrollTrigger, Lenis, Framer Motion, three.js, shaders). An optional Gemini pass watches the footage and critiques how it feels.

A single run yields many images at 1–1.5k tokens each. **Don't read runs yourself by default. Delegate precise questions to `motion-analyst` subagents and keep only their compact answers.**

## Delegate by default
1. **Split** the need into precise, independent questions: one per site, section or interaction. Avoid a vague "analyse this site".
2. **Brief** each question to its own `motion-analyst` (Agent tool, `subagent_type: motion-lens:motion-analyst`). **Launch independent briefs in one message so they run in parallel**, with at most 4 captures at once.
3. **Follow up** on the same run through `SendMessage` to that agent; its images are still in its context. Alternatively, give a new agent the existing run dir. Don't recapture.
4. **Keep** the answers. Open one of the `KEY IMAGES` paths yourself only when you must see it, for example to judge your own build against the reference.

### Brief template
```
Target: <url | local path>   Run dir: <--out path to create | existing dir to reuse>
Question: <exact thing to find out, e.g. "duration, easing and stagger of the hero headline reveal">
Scope: <section / selector / scroll range / interaction; suggested mode, e.g. pointer --target "a.cta">
Context: <why, and what you'll do with it, e.g. "recreate in GSAP", "compare with my build at ./site">
Return: standard answer shape[, with SNIPPET][, ≤N words][, full teardown]
```
Examples of good questions:
- "What happens on hover over the nav links: properties, duration, easing, any cursor follower?"
- "Map scroll sections 0–6000 px: pins, scrubbed vs triggered reveals, smoothing."
- "Does the hero intro of my ./site match the reference timings? Report deltas only."
- "Full teardown": one agent, up to 800 words.

To check your own build, have one agent capture it and compare it against the reference run dir, reporting only the deltas.

### When to run it yourself instead
Only for a tiny check that needs one number or a yes/no and no images: run the CLI, then `grep` `report.md` or `report.json`. Don't read contact sheets in the main context unless the user asked to see them.

## CLI reference (for writing briefs)
`<plugin root>/bin/motion-lens`, where the plugin root is this skill's base dir/../..

| mode | use for |
|---|---|
| `recon` | first look at any page (≈20–60 s); hovers first-viewport elements only |
| `timeline --intro 6` | loaders / intro choreography, frame-exact |
| `scroll --step 300` | scroll storytelling, pins, scrubs, reveals (`--max-steps`, `--hold`) |
| `pointer --target "#cta"` | hover of one element anywhere (auto-scrolls to it) |
| `pointer --start-scroll <px> --targets 10` | hover tour at a scroll position |
| `inspect` | stack + declared definitions only |
| `--film`, then `review <dir> [--focus ".."]` | Gemini watches real-time footage and writes `gemini-review.md` (feel and pacing, not numbers) |

The agent knows how to read the outputs and their caveats (declared beats measured, ⚠ hover misses, virtual-scroll estimates). Field meanings are in `references/reading-guide.md`.
