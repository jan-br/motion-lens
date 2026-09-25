// Records a run: frames, per-frame samples (jsonl), element descriptors, CSS/WAAPI animation events, introspection.
import { mkdirSync, writeFileSync, appendFileSync, createWriteStream } from 'node:fs';
import { join } from 'node:path';

export class Recorder {
  constructor(session, dir, { shotFormat = 'jpeg', quality = 82 } = {}) {
    this.s = session;
    this.dir = dir;
    this.shot = { format: shotFormat, quality: shotFormat === 'jpeg' ? quality : undefined };
    this.i = 0;
    this.phase = 'load';
    this.phases = [];
    this.elements = new Map();
    this.anims = [];
    this.animKeys = new Map();
    this.input = { mx: null, my: null, wheel: 0, wheelTotal: 0 };
    this.state = new Map();
    this.vpos = 0;
    this.native = false;
    mkdirSync(join(dir, 'frames'), { recursive: true });
    this.samples = createWriteStream(join(dir, 'samples.jsonl'));
  }

  /** Watch CSS animations/transitions/WAAPI starting (CDP Animation domain) with resolved keyframes. */
  async watchAnimations() {
    const { S } = this.s;
    await S('Animation.enable');
    this.s.onEvent?.((m) => {
      if (m.method !== 'Animation.animationStarted') return;
      const a = m.params.animation;
      // repeated identical animations (loops restarting, the same transition on many hovers) are counted, not re-logged
      const key = [a.type, a.name, a.source?.backendNodeId, a.source?.duration, a.source?.easing, this.phase].join('|');
      const dup = this.animKeys.get(key);
      if (dup) { dup.count = (dup.count || 1) + 1; return; }
      if (this.anims.length >= 3000) return;
      const rec = { t: Math.round(this.s.t), phase: this.phase, type: a.type, name: a.name, source: a.source ? { duration: a.source.duration, delay: a.source.delay, endDelay: a.source.endDelay, iterations: a.source.iterations, direction: a.source.direction, fill: a.source.fill, easing: a.source.easing, keyframeEasings: a.source.keyframesRule?.keyframes?.map((k) => k.easing) } : null, timeline: a.viewOrScrollTimeline || null };
      this.anims.push(rec);
      this.animKeys.set(key, rec);
      // target via the DOM node id (works even after the animation finished), then concrete keyframes
      if (a.source?.backendNodeId) S('DOM.describeNode', { backendNodeId: a.source.backendNodeId }).then(({ node }) => {
        const at = {}; for (let k = 0; k + 1 < (node.attributes || []).length; k += 2) at[node.attributes[k]] = node.attributes[k + 1];
        if (!rec.target) rec.target = at.id ? '#' + at.id : node.localName + (at.class ? '.' + at.class.trim().split(/\s+/).slice(0, 2).join('.') : '');
      }).catch(() => {});
      S('Animation.resolveAnimation', { animationId: a.id }).then(({ remoteObject }) => S('Runtime.callFunctionOn', {
        objectId: remoteObject.objectId, returnByValue: true,
        functionDeclaration: 'function(){ const t=this.effect&&this.effect.target; return { target: t ? (t.id ? "#"+t.id : t.tagName.toLowerCase()+(t.classList.length?"."+[...t.classList].slice(0,2).join("."):"")) : null, keyframes: this.effect ? this.effect.getKeyframes().slice(0,6).map(k=>{const o={};for(const [kk,vv] of Object.entries(k)) if(kk!=="composite"&&kk!=="computedOffset") o[kk]=typeof vv==="object"?String(vv):vv; return o;}) : null } }',
      })).then((r) => { const v = r.result.value || {}; if (!v.target) delete v.target; Object.assign(rec, v); }).catch(() => {});
    });
  }

  async discover() {
    const added = await this.s.evaluate('__ml.discover()');
    for (const d of added) this.elements.set(d.id, d);
    return added.length;
  }

  beginPhase(name, info = {}) {
    this.phase = name;
    this.phases.push({ name, startFrame: this.i, startT: Math.round(this.s.t), ...info });
  }

  /** Step one frame, sample the page, optionally keep the screenshot. */
  async frame({ shot = false, networkAware = false } = {}) {
    const f = await this.s.step({ shot: shot ? this.shot : null, networkAware, networkWaitMs: 1500 });
    const smp = await this.s.evaluate('__ml.sample()');
    // virtual scroll (sites that never move the native scroll position): the dominant vertical motion of
    // the changed elements, accumulated. Native scrollY wins as soon as it moves.
    if (smp.sy > 0.5) this.native = true;
    const dys = [];
    for (const [id, st] of Object.entries(smp.e)) {
      const prev = this.state.get(id);
      if (prev && st[3] > 20 && prev[3] > 20) dys.push(st[1] - prev[1]);
      this.state.set(id, st);
    }
    if (dys.length >= 3 && this.phase.startsWith('scroll')) {
      dys.sort((a, b) => a - b);
      const med = dys[dys.length >> 1];
      const agree = dys.filter((d) => Math.abs(d - med) < 1.5).length;
      if (Math.abs(med) > 0.3 && agree >= Math.max(3, dys.length * 0.4)) this.vpos -= med;
    }
    smp.vpos = Math.round(this.vpos * 100) / 100;
    const rec = { i: this.i, t: Math.round(f.t * 100) / 100, ph: this.phase, ...smp, in: { ...this.input } };
    this.input.wheel = 0;
    if (f.screenshot) {
      const name = `${String(this.i).padStart(6, '0')}.${this.shot.format === 'png' ? 'png' : 'jpg'}`;
      writeFileSync(join(this.dir, 'frames', name), f.screenshot);
      rec.shot = name;
    }
    this.samples.write(JSON.stringify(rec) + '\n');
    this.i++;
    this.last = rec;
    return rec;
  }

  async run(frames, { shotEvery = 0, onFrame, networkAware = false } = {}) {
    let r;
    for (let k = 0; k < frames; k++) {
      if (onFrame) await onFrame(k);
      r = await this.frame({ shot: shotEvery > 0 && k % shotEvery === 0, networkAware });
    }
    return r;
  }

  move(x, y) { this.input.mx = Math.round(x); this.input.my = Math.round(y); this.s.input.move(x, y); }
  wheel(dy) { this.input.wheel += dy; this.input.wheelTotal += dy; this.s.input.wheel(dy); }

  async finish(meta) {
    await new Promise((r) => this.samples.end(r));
    writeFileSync(join(this.dir, 'elements.json'), JSON.stringify([...this.elements.values()], null, 0));
    writeFileSync(join(this.dir, 'animations.json'), JSON.stringify(this.anims, null, 1));
    writeFileSync(join(this.dir, 'meta.json'), JSON.stringify({ ...meta, phases: this.phases, frames: this.i }, null, 2));
  }
}

export function appendLog(dir, line) { appendFileSync(join(dir, 'capture.log'), line + '\n'); }
