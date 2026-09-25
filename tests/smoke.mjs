// Smoke test: deterministic clock, script rewriter, instrumentation hooks on the ground-truth fixture.
import { writeFileSync, mkdirSync } from 'node:fs';
import { openSession } from '../capture/session.mjs';
import { serveDir } from '../capture/serve.mjs';

const out = '/tmp/motion-lens-smoke';
mkdirSync(out, { recursive: true });
const srv = await serveDir(new URL('../fixtures', import.meta.url).pathname);
const s = await openSession({ width: 1280, height: 800, log: (m) => console.log('  [log]', m) });
try {
  const t0 = Date.now();
  const nav = await s.navigate(`${srv.url}/ground-truth.html`);
  console.log('nav', nav, 'real ms', Date.now() - t0);
  const desc = await s.evaluate('__ml.discover()');
  console.log('tracked elements', desc.length, desc.slice(0, 5).map((d) => d.sel));
  const byId = Object.fromEntries(desc.map((d) => [d.id, d.sel]));
  const want = ['#hero-title', '#spring', '#spinner'];
  const tracks = {};
  for (let i = 0; i < 150; i++) {
    const shot = i % 30 === 0 ? { format: 'jpeg', quality: 80 } : null;
    const f = await s.step({ shot });
    if (f.screenshot) writeFileSync(`${out}/f${String(i).padStart(3, '0')}.jpg`, f.screenshot);
    const smp = await s.evaluate('__ml.sample()');
    for (const [id, st] of Object.entries(smp.e)) { const sel = byId[id]; if (want.includes(sel) || sel?.startsWith('.cards')) (tracks[sel] ||= []).push([Math.round(f.t), st[1], st[4], st[5], st[9]]); }
  }
  console.log('150 frames real ms', Date.now() - t0);
  for (const [sel, arr] of Object.entries(tracks)) console.log(sel, 'samples', arr.length, 'first', JSON.stringify(arr.slice(0, 3)), 'last', JSON.stringify(arr.at(-1)));
  const info = await s.evaluate(`JSON.stringify({gsap: !!window.__ml_gsap, gsapVer: window.__ml_gsap && window.__ml_gsap.version, globalGsap: !!window.gsap, lenis: (window.__ml_lenis||[]).length, lenisLerp: window.__ml_lenis && window.__ml_lenis[0].options.lerp, shaders: __ml.shaders.length, io: __ml.io, anims: document.getAnimations().length, errors: __ml.errors, st: __ml.sample().st})`);
  console.log('hooks', info, 'rewrites', s.rewrites.count());
} finally { await s.close(); srv.close(); }
