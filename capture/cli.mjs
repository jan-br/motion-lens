// motion-lens capture CLI.  node --experimental-websocket capture/cli.mjs <mode> <url|path> [options]
import { mkdirSync, writeFileSync, existsSync, statSync } from 'node:fs';
import { join, resolve, basename, dirname } from 'node:path';
import { openSession } from './session.mjs';
import { Recorder, appendLog } from './recorder.mjs';
import { serveDir } from './serve.mjs';

const HELP = `motion-lens capture
usage: motion-lens <mode> <url|local-path> [options]
modes:
  recon     intro timeline + full scroll sweep + hover tour of the first screen + introspection (default)
  timeline  frame-exact capture from navigation (loaders, intros, loops)
  scroll    wheel-driven scroll sweep with settle + hold at every step
  pointer   hover tour of interactive elements + cursor sweep across the viewport
  inspect   introspection only (libraries, GSAP/ScrollTrigger, Lenis, CSS/WAAPI, Three.js, shaders, media)
options:
  --out DIR            run directory (default ./motion-lens-runs/<host>-<mode>-<time>)
  --viewport WxH       default 1440x900 (use --mobile for touch/mobile emulation)
  --fps N              virtual frame rate for stepping/sampling (default 60)
  --intro S            seconds of intro timeline to capture (default: timeline 5, recon 3)
  --intro-shot N       keep every Nth intro frame as an image (default 2)
  --step PX            scroll distance per sweep step (default 0.3 * viewport height)
  --max-steps N        cap on sweep steps (default 60)
  --hold S             seconds to hold at each scroll step for time-based reveals (default 0.6)
  --scroll-shots N     also keep every Nth frame while scrolling/settling (default 0 = settled only)
  --start-scroll PX    scroll here (wheel) before pointer/timeline work
  --targets N          hover targets in the pointer tour (default 8)
  --target SELECTOR    pointer mode: scroll this element into view and hover only it
  --no-sweep           skip the cursor sweep in pointer mode
  --reduced-motion     emulate prefers-reduced-motion: reduce
  --no-gpu             software rendering (SwiftShader)
  --no-rewrite         do not rewrite scripts to expose bundled GSAP/Lenis
  --png                lossless frames (default JPEG q82)
  --film               keep every 2nd frame in all phases (smooth 30 fps footage, e.g. for motion-lens review)
`;

function parseArgs(argv) {
  const o = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { o._.push(a); continue; }
    const k = a.slice(2);
    if (['mobile', 'reduced-motion', 'no-gpu', 'no-rewrite', 'png', 'no-sweep', 'help', 'film'].includes(k)) o[k] = true;
    else o[k] = argv[++i];
  }
  return o;
}

const ease = (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2);

async function scrollTo(rec, px, { fps }) {
  rec.move(rec.s.width / 2, rec.s.height / 2);
  let left = px;
  while (left > 0) { const d = Math.min(100, left); rec.wheel(d); left -= d; await rec.frame(); await rec.frame(); }
  await settle(rec, { fps });
}

let nativeScroll = false;
const posOf = (r) => { if (r.sy > 0.5) nativeScroll = true; return r.lenis ? r.lenis[0] : nativeScroll ? r.sy : r.vpos; };

async function settle(rec, { fps, maxFrames = 4 * fps, shotEvery = 0 }) {
  let prev = rec.last, still = 0, n = 0;
  while (n < maxFrames) {
    const r = await rec.frame({ shot: shotEvery > 0 && n % shotEvery === 0 });
    n++;
    const moving = Math.abs(posOf(r) - posOf(prev)) > 0.5 || (r.lenis && r.lenis[2]) || JSON.stringify(r.st) !== JSON.stringify(prev.st);
    still = moving ? 0 : still + 1;
    prev = r;
    if (still >= 6) break;
  }
  return n;
}

async function scrollSweep(rec, o) {
  const { fps } = o;
  const stepPx = Number(o.step) || Math.round(rec.s.height * 0.3);
  const maxSteps = Number(o['max-steps']) || 60;
  const holdFrames = Math.round((Number(o.hold ?? 0.6)) * fps);
  const shotsDuring = Number(o['scroll-shots']) || 0;
  rec.beginPhase('scroll', { stepPx, holdFrames });
  rec.move(rec.s.width / 2, rec.s.height / 2);
  await rec.frame({ shot: true });
  const steps = [];
  let lastPos = posOf(rec.last), stale = 0, sawSignal = false;
  for (let k = 0; k < maxSteps; k++) {
    const ticks = Math.max(1, Math.ceil(stepPx / 100));
    for (let j = 0; j < ticks; j++) { rec.wheel(stepPx / ticks); await rec.frame({ shot: shotsDuring > 0 }); await rec.frame(); }
    const settleFrames = await settle(rec, { fps, shotEvery: shotsDuring });
    if (holdFrames > 1) await rec.run(holdFrames - 1, { shotEvery: shotsDuring });
    const r = await rec.frame({ shot: true });
    const pos = posOf(r);
    steps.push({ step: k, frame: r.i, t: r.t, pos, sy: r.sy, settleFrames });
    if (pos > 1) sawSignal = true;
    stale = Math.abs(pos - lastPos) < 1 ? stale + 1 : 0;
    lastPos = pos;
    if (k % 3 === 2) await rec.discover();
    if (sawSignal && stale >= 2) break;
  }
  // scroll back up in coarser steps: scroll-linked effects rewind, triggered (stateful) ones stay changed
  const back = [];
  if (!o['no-back'] && steps.length > 1) {
    rec.beginPhase('scroll-back', { stepPx: stepPx * 3 });
    for (let k = 0; k < steps.length; k++) {
      const ticks = Math.max(1, Math.ceil((stepPx * 3) / 100));
      for (let j = 0; j < ticks; j++) { rec.wheel(-(stepPx * 3) / ticks); await rec.frame(); await rec.frame(); }
      await settle(rec, { fps });
      if (holdFrames > 1) await rec.run(Math.round(holdFrames / 2));
      const r = await rec.frame({ shot: true });
      back.push({ step: k, frame: r.i, t: r.t, pos: posOf(r), sy: r.sy });
      if (posOf(r) <= 1) break;
    }
  }
  return { down: steps, back };
}

/** Run frames until page motion reaches a steady state (loops allowed): the number of elements changing per
    frame stops dropping. Capped. */
async function waitIdle(rec, { fps, maxS = 4 }) {
  const counts = [];
  for (let k = 0; k < maxS * fps; k++) {
    const r = await rec.frame();
    counts.push(Object.keys(r.e).length);
    if (counts.length >= 60) {
      const recent = counts.slice(-20), window = counts.slice(-60);
      if (Math.max(...recent) <= Math.min(...window) + 1) return k;
    }
  }
  return maxS * fps;
}

async function pointerTour(rec, o) {
  const { fps } = o;
  const n = Number(o.targets) || 8;
  rec.beginPhase('settle');
  await waitIdle(rec, o);
  rec.beginPhase('pointer');
  let targets = await rec.s.evaluate(`__ml.interactive(${n})`);
  if (o.target) {
    const t = await rec.s.evaluate(`__ml.locate(${JSON.stringify(o.target)})`);
    targets = t ? [{ sel: o.target, text: '', ...t }] : [];
  }
  if (!targets.length) rec.log?.('no hover targets in the current viewport');
  rec.noTargets = !targets.length;
  const hold = Math.round(0.6 * fps), travel = Math.round(0.25 * fps);
  let { x, y } = rec.s.input.mouse;
  const glide = async (tx, ty, frames, shotEvery) => {
    const x0 = x, y0 = y;
    for (let f = 1; f <= frames; f++) { const u = ease(f / frames); x = x0 + (tx - x0) * u; y = y0 + (ty - y0) * u; rec.move(x, y); await rec.frame({ shot: f % shotEvery === 0 }); }
  };
  const tours = [];
  for (const [ti, tg0] of targets.entries()) {
    const now = await rec.s.evaluate(`__ml.locate(${JSON.stringify(tg0.sel)})`);
    if (!now || now.w < 4 || now.h < 4 || now.x < 0 || now.y < 0 || now.x > rec.s.width || now.y > rec.s.height) continue;
    const tg = { ...tg0, ...now, text: tg0.text || now.text || '' };
    rec.beginPhase(`hover:${ti}`, { target: tg });
    const startFrame = rec.i;
    await glide(tg.x, tg.y, travel, o.film ? 2 : 3);
    const check = await rec.s.evaluate(`__ml.locate(${JSON.stringify(tg0.sel)})`);
    rec.phases[rec.phases.length - 1].target.hitAtArrival = check ? check.hit : false;
    for (let f = 0; f < hold; f++) await rec.frame({ shot: f % (o.film ? 2 : 3) === 0 });
    const out = await rec.s.evaluate(`__ml.neutralPoint(${tg.x}, ${tg.y}, ${tg.w}, ${tg.h})`);
    await glide(out.x, out.y, Math.round(travel * 0.7), 3);
    for (let f = 0; f < Math.round(0.4 * fps); f++) await rec.frame({ shot: f % 3 === 0 });
    tours.push({ ...tg, startFrame, endFrame: rec.i - 1 });
  }
  if (!o['no-sweep']) {
    rec.beginPhase('sweep');
    const W = rec.s.width, H = rec.s.height;
    await glide(W * 0.08, H * 0.25, travel, 4);
    for (const [yy, dir] of [[0.25, 1], [0.5, -1], [0.75, 1]]) {
      await glide(dir > 0 ? W * 0.92 : W * 0.08, H * yy, fps, 3);
      await glide(dir > 0 ? W * 0.92 : W * 0.08, H * (yy + 0.25), Math.round(fps * 0.3), 3);
    }
    for (let f = 0; f < Math.round(0.5 * fps); f++) await rec.frame({ shot: f % 3 === 0 });
  }
  return tours;
}

async function main() {
  const o = parseArgs(process.argv.slice(2));
  if (o.help || o._.length === 0) { console.log(HELP); process.exit(0); }
  let [mode, target] = o._.length === 1 ? ['recon', o._[0]] : o._;
  if (!['recon', 'timeline', 'scroll', 'pointer', 'inspect'].includes(mode)) { console.error('unknown mode ' + mode + '\n' + HELP); process.exit(2); }
  o.fps = Number(o.fps) || 60;
  if (o.film) { o['intro-shot'] = o['intro-shot'] || 2; o['scroll-shots'] = o['scroll-shots'] || 2; }
  const [W, H] = (o.viewport || (o.mobile ? '390x844' : '1440x900')).split('x').map(Number);

  let url = target, server = null;
  if (!/^https?:\/\//.test(target)) {
    const p = resolve(target);
    if (!existsSync(p)) { console.error('not a URL or existing path: ' + target); process.exit(2); }
    const isDir = statSync(p).isDirectory();
    server = await serveDir(isDir ? p : dirname(p));
    url = `${server.url}/${isDir ? '' : basename(p)}`;
  }
  const host = /^https?:\/\//.test(target) ? new URL(target).hostname.replace(/^www\./, '') : basename(target).replace(/\.[^.]+$/, '');
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
  const dir = resolve(o.out || join('motion-lens-runs', `${host}-${mode}-${stamp}`));
  mkdirSync(dir, { recursive: true });
  const log = (m) => { console.log('  ' + m); appendLog(dir, m); };

  const session = await openSession({ width: W, height: H, fps: o.fps, mobile: !!o.mobile, gpu: !o['no-gpu'], rewrite: !o['no-rewrite'], reducedMotion: !!o['reduced-motion'], log });
  const rec = new Recorder(session, dir, { shotFormat: o.png ? 'png' : 'jpeg' });
  const t0 = Date.now();
  const meta = { url: target, resolvedUrl: url, mode, viewport: [W, H], fps: o.fps, mobile: !!o.mobile, reducedMotion: !!o['reduced-motion'], browser: session.browserVersion, capturedAt: new Date().toISOString() };
  const result = {};
  try {
    await rec.watchAnimations();
    rec.beginPhase('load');
    meta.load = await session.navigate(url);
    await rec.discover();
    const introS = Number(o.intro ?? (mode === 'timeline' ? 5 : mode === 'recon' ? 3 : 1));
    rec.beginPhase('intro');
    await rec.run(Math.round(introS * o.fps), { networkAware: true, shotEvery: mode === 'inspect' ? 0 : Number(o['intro-shot']) || (mode === 'recon' ? 6 : 2) });
    await rec.discover();
    if (o.target) {
      // scroll the target element into the middle of the viewport, then hover only it
      const pos = await session.evaluate(`(() => { const el = document.querySelector(${JSON.stringify(o.target)}); if (!el) return null; const r = el.getBoundingClientRect(); return Math.round(r.top + scrollY + r.height / 2 - innerHeight / 2); })()`);
      if (pos === null) log(`--target ${o.target}: no such element`);
      else if (pos > 0) o['start-scroll'] = pos;
    }
    if (o['start-scroll']) { rec.beginPhase('seek'); await scrollTo(rec, Number(o['start-scroll']), o); }
    if (mode === 'pointer' || mode === 'recon') {
      if (mode === 'recon') o['no-sweep'] = o['no-sweep'] ?? false;
      result.pointer = await pointerTour(rec, { ...o, targets: mode === 'recon' ? Math.min(Number(o.targets) || 6, 6) : o.targets });
    }
    if (mode === 'scroll' || mode === 'recon') result.scroll = await scrollSweep(rec, o);
    const inspect = await session.evaluate('__ml.inspect()', { timeout: 30000 });
    inspect.rewrites = session.rewrites.hits;
    writeFileSync(join(dir, 'inspect.json'), JSON.stringify(inspect, null, 1));
    const shaders = await session.evaluate('__ml.shaderDump()', { timeout: 30000 });
    if (shaders.length) {
      mkdirSync(join(dir, 'shaders'), { recursive: true });
      shaders.forEach((sh, i) => writeFileSync(join(dir, 'shaders', `${String(i).padStart(3, '0')}-${sh.kind}.${sh.kind === 'wgsl' ? 'wgsl' : 'glsl'}`), sh.src));
    }
    result.network = summarizeNetwork(session.requests);
  } catch (e) {
    log('ERROR ' + (e.stack || e.message));
    meta.error = String(e.message || e);
  } finally {
    meta.captureSeconds = Math.round((Date.now() - t0) / 100) / 10;
    meta.result = result;
    await rec.finish(meta);
    await session.close();
    server?.close();
  }
  console.log(dir);
  process.exit(meta.error ? 1 : 0);
}

function summarizeNetwork(reqs) {
  const byType = {};
  for (const r of reqs) byType[r.type || 'Other'] = (byType[r.type || 'Other'] || 0) + 1;
  // numbered image runs = likely frame sequences
  const seqs = {};
  for (const r of reqs) if (/\.(jpe?g|png|webp|avif)(\?|$)/i.test(r.url)) { const k = r.url.replace(/\d+(?=\D*$)/, '#'); seqs[k] = (seqs[k] || 0) + 1; }
  const frameSequences = Object.entries(seqs).filter(([, n]) => n >= 12).map(([pattern, count]) => ({ pattern: pattern.slice(0, 140), count }));
  const media = reqs.filter((r) => r.type === 'Media' || /\.(mp4|webm|m3u8|mpd)(\?|$)/i.test(r.url)).slice(0, 20).map((r) => r.url.slice(0, 140));
  const models = reqs.filter((r) => /\.(glb|gltf|fbx|obj|drc|ktx2|hdr|exr|riv|lottie)(\?|$)/i.test(r.url) || /lottie.*\.json/i.test(r.url)).slice(0, 30).map((r) => r.url.slice(0, 140));
  return { total: reqs.length, byType, frameSequences, media, assets3dOrVector: models };
}

main().catch((e) => { console.error(e); process.exit(1); });
