// motion-lens page instrumentation. Injected before any page script (Page.addScriptToEvaluateOnNewDocument).
// Everything lives under window.__ml; hooks are wrapped in try/catch so a failure never breaks the page.
(() => {
  if (window.__ml) return;
  const ML = (window.__ml = { v: 1, shaders: [], shaderKeys: new Set(), io: [], media: {}, draw: {}, three: { revision: null, scenes: [], renderers: [], camera: null }, errors: [] });
  const safe = (fn) => { try { fn(); } catch (e) { ML.errors.push(String(e && e.message || e)); } };
  const now = () => performance.now();

  // ---------- Three.js devtools hook: receives scenes + renderers (r106+) ----------
  safe(() => {
    const hub = new EventTarget();
    hub.addEventListener('register', (e) => { ML.three.revision = e.detail && e.detail.revision; });
    hub.addEventListener('observe', (e) => {
      const o = e.detail;
      if (!o) return;
      if (o.isScene && !ML.three.scenes.includes(o)) {
        ML.three.scenes.push(o);
        const prev = o.onAfterRender;
        o.onAfterRender = function (renderer, scene, camera) {
          safe(() => {
            if (camera && camera.position) ML.three.camera = { x: camera.position.x, y: camera.position.y, z: camera.position.z, qx: camera.quaternion.x, qy: camera.quaternion.y, qz: camera.quaternion.z, qw: camera.quaternion.w, fov: camera.fov };
          });
          if (typeof prev === 'function') return prev.apply(this, arguments);
        };
      }
      if (o.isWebGLRenderer || o.isWebGPURenderer) if (!ML.three.renderers.includes(o)) ML.three.renderers.push(o);
    });
    Object.defineProperty(window, '__THREE_DEVTOOLS__', { value: hub, configurable: true, writable: true });
  });

  // ---------- shader capture (WebGL1/2 + WebGPU) ----------
  const addShader = (kind, src) => {
    if (typeof src !== 'string' || ML.shaders.length >= 400) return;
    const key = kind + ':' + src.length + ':' + src.slice(0, 64) + src.slice(-64);
    if (ML.shaderKeys.has(key)) return;
    ML.shaderKeys.add(key);
    ML.shaders.push({ kind, src, t: now() });
  };
  safe(() => {
    for (const C of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
      if (!C) continue;
      const types = new WeakMap();
      const cs = C.prototype.createShader;
      C.prototype.createShader = function (type) { const s = cs.call(this, type); if (s) types.set(s, type === this.VERTEX_SHADER ? 'vertex' : 'fragment'); return s; };
      const ss = C.prototype.shaderSource;
      C.prototype.shaderSource = function (shader, src) { safe(() => addShader('glsl-' + (types.get(shader) || '?'), src)); return ss.call(this, shader, src); };
    }
    if (window.GPUDevice) {
      const cm = GPUDevice.prototype.createShaderModule;
      GPUDevice.prototype.createShaderModule = function (d) { safe(() => addShader('wgsl', d && d.code)); return cm.call(this, d); };
    }
  });

  // ---------- canvas contexts ----------
  safe(() => {
    const gc = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, opts) {
      safe(() => { this.__ml_ctx = type; });
      return gc.call(this, type, opts);
    };
  });

  // ---------- IntersectionObserver (reveal triggers) ----------
  safe(() => {
    const IO = window.IntersectionObserver;
    if (!IO) return;
    window.IntersectionObserver = function (cb, opts) {
      const rec = { rootMargin: (opts && opts.rootMargin) || '0px', threshold: (opts && opts.threshold) ?? 0, targets: 0 };
      ML.io.push(rec);
      const inst = new IO(cb, opts);
      const ob = inst.observe.bind(inst);
      inst.observe = (el) => { rec.targets++; safe(() => { el.__ml_io = true; }); return ob(el); };
      return inst;
    };
    window.IntersectionObserver.prototype = IO.prototype;
  });

  // ---------- media scrubbing (Apple-style scroll-scrubbed video) ----------
  safe(() => {
    const d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime');
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
      configurable: true, enumerable: d.enumerable,
      get() { return d.get.call(this); },
      set(v) {
        safe(() => { const k = this.__ml_id || (this.__ml_id = 'm' + Object.keys(ML.media).length); const r = ML.media[k] || (ML.media[k] = { sets: 0, src: this.currentSrc || this.src || '', last: null }); r.sets++; r.last = v; });
        return d.set.call(this, v);
      },
    });
  });

  // ---------- canvas image sequences (drawImage with many distinct sources) ----------
  safe(() => {
    const di = CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage = function (img) {
      safe(() => {
        const src = img && (img.currentSrc || img.src);
        if (src) { const c = this.canvas; const k = c.__ml_id || (c.__ml_id = 'c' + Object.keys(ML.draw).length); const r = ML.draw[k] || (ML.draw[k] = { srcs: new Set(), calls: 0 }); r.calls++; if (r.srcs.size < 2000) r.srcs.add(src); }
      });
      return di.apply(this, arguments);
    };
  });

  // =============================== sampling ===============================
  const tracked = new Map(); // id -> element
  let nextId = 1;
  const last = new Map();    // id -> last emitted state string
  const selectorOf = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let e = el;
    for (let d = 0; e && e.nodeType === 1 && d < 4; d++, e = e.parentElement) {
      let p = e.tagName.toLowerCase();
      const cls = [...e.classList].filter((c) => c.length < 40).slice(0, 2);
      if (cls.length) p += '.' + cls.map((c) => CSS.escape(c)).join('.');
      else if (e.parentElement) { const i = [...e.parentElement.children].indexOf(e); p += `:nth-child(${i + 1})`; }
      parts.unshift(p);
      if (e.id) { parts[0] = '#' + CSS.escape(e.id); break; }
    }
    return parts.join(' > ');
  };
  const describe = (el) => {
    const r = el.getBoundingClientRect();
    const txt = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('alt') || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    return { id: el.__ml_tid, sel: selectorOf(el), tag: el.tagName.toLowerCase(), text: txt, rect: [Math.round(r.x), Math.round(r.y + scrollY), Math.round(r.width), Math.round(r.height)], canvas: el.__ml_ctx || null, io: !!el.__ml_io, gsap: !!el._gsap };
  };

  /** Find elements worth tracking; re-callable (adds new ones). Returns descriptors of newly added elements. */
  ML.discover = (cap = 700) => {
    const added = [];
    const all = document.body ? document.body.getElementsByTagName('*') : [];
    const scored = [];
    for (const el of all) {
      if (el.__ml_tid) continue;
      const tag = el.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'LINK' || tag === 'META' || tag === 'BR') continue;
      const r = el.getBoundingClientRect();
      if (r.width * r.height < 64) continue;
      const cs = getComputedStyle(el);
      if (cs.display === 'none') continue;
      let score = 0;
      if (cs.transform !== 'none') score += 4;
      if (+cs.opacity < 1) score += 3;
      if (cs.willChange !== 'auto') score += 3;
      if (el._gsap) score += 5;
      if (el.getAnimations && el.getAnimations().length) score += 4;
      if (cs.position === 'sticky' || cs.position === 'fixed') score += 3;
      if (/^(H1|H2|H3|IMG|VIDEO|CANVAS|SVG|BUTTON|A|P|SPAN|PICTURE|FIGURE)$/.test(tag)) score += 2;
      if (cs.clipPath !== 'none' || cs.filter !== 'none') score += 2;
      if (el.children.length === 0) score += 1;
      scored.push([score, el]);
    }
    scored.sort((a, b) => b[0] - a[0]);
    for (const [, el] of scored) {
      if (tracked.size >= cap) break;
      el.__ml_tid = nextId++;
      tracked.set(el.__ml_tid, el);
      added.push(describe(el));
    }
    return added;
  };

  const r2 = (v) => Math.round(v * 100) / 100;
  ML.gsapSeen = [];
  let gsapHooked = false;
  const hookGsap = () => {
    const g = window.__ml_gsap || window.gsap;
    if (gsapHooked || !g || !g.ticker || !g.globalTimeline) return;
    gsapHooked = true;
    const seen = new WeakSet();
    const snap = () => safe(() => {
      for (const t of g.globalTimeline.getChildren(true, true, true)) {
        if (seen.has(t) || ML.gsapSeen.length >= 400) continue;
        seen.add(t);
        ML.gsapSeen.push({ firstSeenMs: Math.round(performance.now()), ...summarizeTween(t), scrollTrigger: !!(t.scrollTrigger) });
      }
    });
    snap();
    g.ticker.add(snap);
  };
  /** Per-frame sample: scroll + library state + the elements whose state changed since the last sample. */
  ML.sample = () => {
    hookGsap();
    const out = { sx: r2(scrollX), sy: r2(scrollY), vw: innerWidth, vh: innerHeight, dh: document.documentElement.scrollHeight, e: {} };
    for (const [id, el] of tracked) {
      if (!el.isConnected) continue;
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      let tx = 0, ty = 0, sx = 1, sy = 1, rot = 0;
      if (cs.transform !== 'none') {
        const m = new DOMMatrixReadOnly(cs.transform);
        tx = m.m41; ty = m.m42; sx = Math.hypot(m.m11, m.m12); sy = Math.hypot(m.m21, m.m22); rot = Math.atan2(m.m12, m.m11) * 180 / Math.PI;
      }
      const st = [r2(r.x), r2(r.y), r2(r.width), r2(r.height), r2(+cs.opacity), r2(tx), r2(ty), Math.round(sx * 1000) / 1000, Math.round(sy * 1000) / 1000, r2(rot)];
      const k = st.join(',');
      if (last.get(id) !== k) { last.set(id, k); out.e[id] = st; }
    }
    safe(() => { if (window.__ml_lenis && window.__ml_lenis.length) { const l = window.__ml_lenis[window.__ml_lenis.length - 1]; out.lenis = [r2(l.animatedScroll ?? l.scroll ?? 0), r2(l.targetScroll ?? 0), l.isScrolling ? 1 : 0]; } });
    safe(() => {
      const g = window.__ml_gsap;
      const ST = g && g.core && g.core.globals && g.core.globals().ScrollTrigger;
      if (ST) out.st = ST.getAll().map((s) => Math.round(s.progress * 1000) / 1000);
    });
    safe(() => { const v = [...document.querySelectorAll('video')]; if (v.length) out.video = v.slice(0, 6).map((x) => r2(x.currentTime)); });
    safe(() => { if (ML.three.camera) { const c = ML.three.camera; out.cam = [r2(c.x), r2(c.y), r2(c.z), Math.round(c.qx * 1e4) / 1e4, Math.round(c.qy * 1e4) / 1e4, Math.round(c.qz * 1e4) / 1e4, Math.round(c.qw * 1e4) / 1e4, c.fov]; } });
    return out;
  };

  /** Elements a user would hover/click, currently in the viewport. */
  ML.interactive = (max = 16) => {
    const out = [];
    for (const el of document.querySelectorAll('a, button, [role=button], input, select, textarea, [onclick], [data-cursor], *')) {
      if (out.length >= max) break;
      const r = el.getBoundingClientRect();
      if (r.width < 8 || r.height < 8 || r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
      const cs = getComputedStyle(el);
      const isInteractive = /^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/.test(el.tagName) || el.getAttribute('role') === 'button' || cs.cursor === 'pointer';
      if (!isInteractive || cs.visibility === 'hidden' || +cs.opacity === 0) continue;
      if (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer' && !/^(A|BUTTON)$/.test(el.tagName)) continue;
      if (out.some((o) => o.el.contains(el) || el.contains(o.el))) continue;
      out.push({ el, x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height });
    }
    return out.map((o) => ({ sel: selectorOf(o.el), text: (o.el.innerText || o.el.getAttribute('aria-label') || '').trim().slice(0, 40), x: Math.round(o.x), y: Math.round(o.y), w: Math.round(o.w), h: Math.round(o.h), tid: o.el.__ml_tid || null }));
  };

  /** Current centre of an element (layouts shift after intros) + whether a pointer there would hit it. */
  ML.locate = (sel) => {
    let el = null;
    try { el = document.querySelector(sel); } catch { /* ignore */ }
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const x = r.x + r.width / 2, y = r.y + r.height / 2;
    const hitEl = document.elementFromPoint(x, y);
    return { x: Math.round(x), y: Math.round(y), w: Math.round(r.width), h: Math.round(r.height), hit: !!hitEl && (hitEl === el || el.contains(hitEl) || hitEl.contains(el)), hitSel: hitEl ? selectorOf(hitEl) : null };
  };

  /** A nearby point where the pointer rests on nothing interactive (to "leave" a hover target cleanly). */
  ML.neutralPoint = (x, y, w, h) => {
    const isInteractive = (el) => { for (let e = el; e && e !== document.body; e = e.parentElement) { if (/^(A|BUTTON|INPUT|SELECT|TEXTAREA|LABEL)$/.test(e.tagName) || e.getAttribute('role') === 'button') return true; if (getComputedStyle(e).cursor === 'pointer') return true; } return false; };
    const cands = [];
    for (const d of [140, 220, 320]) for (const [dx, dy] of [[0, 1], [0, -1], [-1, 0], [1, 0], [-1, 1], [1, 1]]) cands.push([x + dx * (w / 2 + d), y + dy * (h / 2 + d)]);
    for (const [cx, cy] of cands) {
      if (cx < 10 || cy < 10 || cx > innerWidth - 10 || cy > innerHeight - 10) continue;
      const el = document.elementFromPoint(cx, cy);
      if (!el || !isInteractive(el)) return { x: Math.round(cx), y: Math.round(cy) };
    }
    return { x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.66) };
  };

  // =============================== introspection ===============================
  const easeName = (e) => (typeof e === 'string' ? e : e && (e.name || e._ease || (e.toString && e.toString().slice(0, 80))) || null);
  const summarizeTween = (t) => {
    const v = t.vars || {};
    const targets = safe2(() => t.targets().slice(0, 6).map((el) => (el && el.nodeType === 1 ? selectorOf(el) : typeof el)), []);
    const props = {};
    for (const k of Object.keys(v)) if (!/^(duration|ease|delay|stagger|scrollTrigger|onComplete|onUpdate|onStart|repeat|yoyo|paused|immediateRender|overwrite|id|callbackScope|data|runBackwards|startAt|keyframes|inherit|lazy|defaults)$/.test(k) && (typeof v[k] === 'number' || typeof v[k] === 'string')) props[k] = v[k];
    return { type: t.getChildren ? 'timeline' : 'tween', duration: typeof v.duration === 'number' ? v.duration : (t.duration && t.duration()), totalDuration: t.totalDuration && t.totalDuration(), delay: t.delay && t.delay(), start: t.startTime && t.startTime(), ease: easeName(v.ease), stagger: typeof v.stagger === 'object' ? JSON.stringify(v.stagger) : v.stagger, repeat: v.repeat, yoyo: v.yoyo, props, targets, from: !!v.runBackwards };
  };
  function safe2(fn, dflt) { try { return fn(); } catch { return dflt; } }

  ML.inspect = () => {
    const R = { url: location.href, title: document.title, viewport: [innerWidth, innerHeight], scrollHeight: document.documentElement.scrollHeight, libs: {}, notes: [] };
    // --- fingerprint ---
    const L = R.libs;
    L.gsap = window.__ml_gsap ? window.__ml_gsap.version : (window.gsapVersions ? String(window.gsapVersions) : (window.gsap ? window.gsap.version : null));
    const g = window.__ml_gsap || window.gsap;
    const ST = safe2(() => (g && g.core.globals().ScrollTrigger) || window.ScrollTrigger, null);
    L.scrollTrigger = !!ST;
    L.lenis = window.lenisVersion || (window.__ml_lenis ? 'yes' : null);
    L.locomotive = window.locomotiveScrollVersion || (document.querySelector('[data-scroll-container]') ? 'likely' : null);
    L.three = ML.three.revision || window.__THREE__ || (document.querySelector('canvas[data-engine^="three"]') ? 'yes' : null);
    L.framerMotion = !!(window.MotionIsMounted || window.MotionHandoffAnimation || window.MotionHasOptimisedAnimation || window.__framer_importFromPackage);
    L.lottie = !!(window.lottie || window.bodymovin || document.querySelector('[id*="__lottie_element_"], lottie-player, dotlottie-player'));
    L.rive = !!document.querySelector('canvas.rive, rive-canvas') || !!window.rive;
    L.barba = !!window.barba; L.swup = !!window.swup;
    L.react = !!document.querySelector('[data-reactroot], #__next') || !!window.__NEXT_DATA__ || !!window.next;
    L.nextjs = !!(window.__NEXT_DATA__ || window.next);
    L.webflow = !!window.Webflow;
    L.spline = !!document.querySelector('spline-viewer, canvas[data-spline]');
    // --- GSAP ---
    if (g) {
      R.gsap = { version: g.version, tweens: [], scrollTriggers: [], seen: ML.gsapSeen.slice(0, 300) };
      safe(() => { for (const t of g.globalTimeline.getChildren(true, true, true).slice(0, 150)) R.gsap.tweens.push(summarizeTween(t)); });
      if (ST) safe(() => {
        for (const s of ST.getAll().slice(0, 80)) {
          const v = s.vars || {};
          R.gsap.scrollTriggers.push({ trigger: v.trigger && v.trigger.nodeType === 1 ? selectorOf(v.trigger) : String(v.trigger || ''), start: Math.round(s.start), end: Math.round(s.end), startVar: String(v.start ?? ''), endVar: String(v.end ?? ''), scrub: v.scrub ?? false, pin: !!v.pin, snap: v.snap ? JSON.stringify(v.snap).slice(0, 80) : null, toggleActions: v.toggleActions || null, animation: s.animation ? summarizeTween(s.animation) : null, progress: Math.round(s.progress * 1000) / 1000 });
        }
      });
    }
    // --- Lenis ---
    if (window.__ml_lenis && window.__ml_lenis.length) {
      R.lenis = window.__ml_lenis.map((l) => { const o = l.options || {}; const out = {}; for (const k of Object.keys(o)) { const v = o[k]; if (typeof v === 'number' || typeof v === 'boolean' || typeof v === 'string') out[k] = v; else if (typeof v === 'function') out[k] = 'fn:' + v.toString().slice(0, 60); else if (v && typeof v === 'object' && !(v.nodeType) && v !== window) out[k] = safe2(() => JSON.parse(JSON.stringify(v)), '[obj]'); } return out; });
    }
    // --- Web Animations / CSS ---
    R.cssAnimations = [];
    safe(() => {
      for (const a of document.getAnimations().slice(0, 120)) {
        const eff = a.effect;
        const tl = a.timeline;
        const tlType = tl ? tl.constructor.name : null;
        R.cssAnimations.push({
          type: a.constructor.name, name: a.animationName || a.transitionProperty || a.id || '',
          target: eff && eff.target ? selectorOf(eff.target) : null,
          timing: eff ? (({ duration, delay, easing, iterations, direction, fill }) => ({ duration: typeof duration === 'object' ? String(duration) : duration, delay, easing, iterations: iterations === Infinity ? 'infinite' : iterations, direction, fill }))(eff.getTiming()) : null,
          keyframes: eff ? eff.getKeyframes().slice(0, 6).map((k) => { const o = {}; for (const [kk, vv] of Object.entries(k)) if (kk !== 'composite' && kk !== 'computedOffset') o[kk] = typeof vv === 'object' ? String(vv) : vv; return o; }) : null,
          timeline: tlType, range: tlType && tlType !== 'DocumentTimeline' ? [String(a.rangeStart), String(a.rangeEnd)] : null,
        });
      }
    });
    // --- CSS transitions declared on elements (hover affordances) ---
    R.transitions = [];
    safe(() => {
      const seen = new Set();
      for (const el of document.querySelectorAll('a, button, [role=button], *')) {
        if (R.transitions.length >= 60) break;
        const cs = getComputedStyle(el);
        if (!cs.transitionDuration || cs.transitionDuration === '0s') continue;
        const key = cs.transitionProperty + '|' + cs.transitionDuration + '|' + cs.transitionTimingFunction;
        if (seen.has(key)) continue;
        seen.add(key);
        R.transitions.push({ example: selectorOf(el), property: cs.transitionProperty, duration: cs.transitionDuration, delay: cs.transitionDelay, easing: cs.transitionTimingFunction });
      }
    });
    // --- Three.js scene summary ---
    if (ML.three.scenes.length) safe(() => {
      R.three = { revision: ML.three.revision, scenes: [] };
      for (const sc of ML.three.scenes.slice(0, 12)) {
        const s = { meshes: 0, lights: 0, points: 0, lines: 0, materials: {}, shaderUniforms: new Set() };
        sc.traverse((o) => {
          if (o.isMesh) s.meshes++; if (o.isLight) s.lights++; if (o.isPoints) s.points++; if (o.isLine) s.lines++;
          const mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
          for (const m of mats) { s.materials[m.type] = (s.materials[m.type] || 0) + 1; if (m.uniforms) Object.keys(m.uniforms).slice(0, 30).forEach((u) => s.shaderUniforms.add(u)); }
        });
        s.shaderUniforms = [...s.shaderUniforms].slice(0, 40);
        R.three.scenes.push(s);
      }
    });
    R.shaders = ML.shaders.length;
    R.canvases = [...document.querySelectorAll('canvas')].slice(0, 10).map((c) => ({ sel: selectorOf(c), ctx: c.__ml_ctx || null, size: [c.width, c.height], css: [Math.round(c.clientWidth), Math.round(c.clientHeight)] }));
    R.imageSequences = Object.entries(ML.draw).map(([k, r]) => ({ canvas: k, calls: r.calls, distinctImages: r.srcs.size, sample: [...r.srcs].slice(0, 3) })).filter((x) => x.distinctImages > 8);
    R.videos = [...document.querySelectorAll('video')].slice(0, 10).map((v) => ({ sel: selectorOf(v), src: (v.currentSrc || v.src || '').slice(0, 120), duration: v.duration, autoplay: v.autoplay, loop: v.loop, muted: v.muted, scrubbed: v.__ml_id && ML.media[v.__ml_id] ? ML.media[v.__ml_id].sets : 0 }));
    R.intersectionObservers = ML.io.slice(0, 40);
    R.pinSpacers = document.querySelectorAll('.pin-spacer').length;
    R.stickies = safe2(() => [...document.querySelectorAll('body *')].filter((e) => getComputedStyle(e).position === 'sticky').slice(0, 20).map(selectorOf), []);
    R.fonts = safe2(() => [...new Set([...document.fonts].filter((f) => f.status === 'loaded').map((f) => `${f.family.replace(/"/g, '')} ${f.weight} ${f.style}`))].slice(0, 30), []);
    R.sections = safe2(() => [...document.body.children].filter((e) => e.getBoundingClientRect().height > 100).slice(0, 40).map((e) => { const r = e.getBoundingClientRect(); return { sel: selectorOf(e), top: Math.round(r.top + scrollY), height: Math.round(r.height) }; }), []);
    // --- React/Framer motion props (fiber walk) ---
    if (L.framerMotion || L.react) safe(() => {
      const found = [];
      for (const el of document.querySelectorAll('body *')) {
        if (found.length >= 40) break;
        const fk = Object.keys(el).find((k) => k.startsWith('__reactFiber$'));
        if (!fk) continue;
        let f = el[fk];
        for (let d = 0; f && d < 4; d++, f = f.return) {
          const p = f.memoizedProps;
          if (p && (p.animate || p.whileInView || p.whileHover || p.transition || p.variants)) {
            const pick = {};
            for (const k of ['initial', 'animate', 'whileInView', 'whileHover', 'whileTap', 'exit', 'transition', 'variants', 'viewport']) if (p[k] !== undefined) pick[k] = safe2(() => JSON.parse(JSON.stringify(p[k], (kk, vv) => (typeof vv === 'function' ? 'fn' : vv))), '[unserializable]');
            found.push({ sel: selectorOf(el), props: pick });
            break;
          }
        }
      }
      if (found.length) R.motionProps = found;
    });
    // --- Lottie ---
    safe(() => { const lt = window.lottie || window.bodymovin; if (lt && lt.getRegisteredAnimations) R.lottie = lt.getRegisteredAnimations().slice(0, 10).map((a) => ({ name: a.name, frameRate: a.frameRate, totalFrames: a.totalFrames, loop: a.loop, w: a.animationData && a.animationData.w, h: a.animationData && a.animationData.h })); });
    R.hookErrors = ML.errors.slice(0, 20);
    return R;
  };
  ML.shaderDump = () => ML.shaders.map((s) => ({ kind: s.kind, src: s.src }));
})();
