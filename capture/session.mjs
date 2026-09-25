// Deterministic page session: chrome-headless-shell --deterministic-mode, one virtual clock from navigation
// onward, every frame produced on demand with HeadlessExperimental.beginFrame.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchBrowser, sleep, withTimeout } from './cdp.mjs';
import { installScriptRewriter } from './rewrite.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const INSTRUMENT = readFileSync(join(HERE, 'instrument.js'), 'utf8');

export async function openSession({
  width = 1440, height = 900, dpr = 1, fps = 60, gpu = true, mobile = false,
  instrument = true, rewrite = true, reducedMotion = false, log = () => {},
} = {}) {
  const browser = await launchBrowser({ gpu, args: ['--deterministic-mode'] });
  const { targetId } = await browser.send('Target.createTarget', { url: 'about:blank', enableBeginFrameControl: true });
  const { sessionId } = await browser.send('Target.attachToTarget', { targetId, flatten: true });
  const S = (method, params) => browser.send(method, params, sessionId);
  const dt = 1000 / fps;

  await S('Page.enable');
  await S('Runtime.enable');
  await S('Network.enable');
  await S('Page.setBypassCSP', { enabled: true });
  const ua = browser.version.userAgent.replace('HeadlessChrome', 'Chrome');
  await S('Network.setUserAgentOverride', { userAgent: ua });
  await S('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: dpr, mobile });
  if (reducedMotion) await S('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  if (instrument) await S('Page.addScriptToEvaluateOnNewDocument', { source: INSTRUMENT, runImmediately: true });
  const rewrites = rewrite ? await installScriptRewriter(browser, sessionId, log) : { count: () => 0 };

  // network bookkeeping (media streams keep "pending" forever, so we never block on idle for long)
  const inflight = new Map();
  const requests = [];
  browser.on((m) => {
    if (m.sessionId !== sessionId) return;
    if (m.method === 'Network.requestWillBeSent') {
      inflight.set(m.params.requestId, m.params.request.url);
      requests.push({ url: m.params.request.url, type: m.params.type, t: null });
    } else if (m.method === 'Network.loadingFinished' || m.method === 'Network.loadingFailed') inflight.delete(m.params.requestId);
  });

  let base = null; // virtualTimeTicksBase
  let vt = 0;      // virtual ms since the clock started
  let frameNo = 0;
  const pendingAcks = new Set();

  const evaluate = async (expression, { awaitPromise = false, timeout = 10000 } = {}) => {
    const r = await withTimeout(S('Runtime.evaluate', { expression, returnByValue: true, awaitPromise }), timeout, 'evaluate');
    if (r.exceptionDetails) throw new Error('page eval failed: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  };

  /** Advance the virtual clock by one frame and render it. */
  let networkStalled = false;
  async function step({ shot = null, networkAware = false, networkWaitMs = 20000 } = {}) {
    if (networkStalled) networkAware = false;
    const expired = browser.waitFor('Emulation.virtualTimeBudgetExpired', sessionId, networkAware ? networkWaitMs : 15000);
    const r0 = await S('Emulation.setVirtualTimePolicy', {
      policy: networkAware ? 'pauseIfNetworkFetchesPending' : 'advance', budget: dt, maxVirtualTimeTaskStarvationCount: 100,
    });
    if (base === null) base = r0.virtualTimeTicksBase;
    try { await expired; } catch (e) {
      if (!networkAware) throw e;
      // a fetch never finished (streaming media); stop waiting on the network
      log(`network did not idle within ${networkWaitMs} ms real time; continuing in plain advance mode (${inflight.size} pending)`);
      networkStalled = true;
      // the pending budget still expires once the fetches finish or on the next policy change
      return step({ shot, networkAware: false });
    }
    vt += dt;
    frameNo++;
    const r = await withTimeout(S('HeadlessExperimental.beginFrame', {
      frameTimeTicks: base + vt, interval: dt, ...(shot ? { screenshot: shot } : {}),
    }), 20000, 'beginFrame');
    return { t: vt, frame: frameNo, screenshot: r.screenshotData ? Buffer.from(r.screenshotData, 'base64') : null, damage: r.hasDamage };
  }

  /** Navigate with the virtual clock paused while the network is busy: load takes zero virtual time, intros start at t≈0. */
  async function navigate(url, { maxLoadFrames = 600 } = {}) {
    const loaded = browser.waitFor('Page.loadEventFired', sessionId, 120000).then(() => true, () => false);
    let isLoaded = false;
    loaded.then((v) => { isLoaded = v; });
    const { virtualTimeTicksBase } = await S('Emulation.setVirtualTimePolicy', { policy: 'pause' });
    base = virtualTimeTicksBase;
    await S('Page.navigate', { url });
    // pump frames with the network-aware budget until the load event (bounded)
    let n = 0;
    while (!isLoaded && n < maxLoadFrames) { await step({ networkAware: true }); n++; await sleep(0); }
    const tLoad = vt;
    log(`loaded=${isLoaded} after ${n} virtual frames (${tLoad.toFixed(0)} ms virtual), ${inflight.size} requests pending`);
    return { loaded: isLoaded, tLoad };
  }

  // ---- input (dispatched without awaiting; the renderer acks after the next frame) ----
  const dispatch = (p) => { const x = S('Input.dispatchMouseEvent', p).catch(() => {}); pendingAcks.add(x); x.finally(() => pendingAcks.delete(x)); };
  let mouse = { x: width / 2, y: height / 2 };
  const input = {
    move(x, y) { mouse = { x, y }; dispatch({ type: 'mouseMoved', x, y }); },
    wheel(deltaY, deltaX = 0, at = mouse) { dispatch({ type: 'mouseWheel', x: at.x, y: at.y, deltaX, deltaY }); },
    down(button = 'left') { dispatch({ type: 'mousePressed', x: mouse.x, y: mouse.y, button, clickCount: 1 }); },
    up(button = 'left') { dispatch({ type: 'mouseReleased', x: mouse.x, y: mouse.y, button, clickCount: 1 }); },
    async scrollTo(y) { await evaluate(`window.scrollTo(0, ${y})`); },
    get mouse() { return mouse; },
  };
  async function flushInput() { if (pendingAcks.size) await withTimeout(Promise.all([...pendingAcks]), 10000, 'input acks').catch(() => {}); }

  async function close() { await browser.close(); }

  const onEvent = (fn) => browser.on((m) => { if (m.sessionId === sessionId) fn(m); });

  return {
    S, evaluate, step, navigate, input, flushInput, close, requests, rewrites, onEvent,
    get t() { return vt; }, get frame() { return frameNo; }, dt, width, height, dpr, browserVersion: browser.version.product,
  };
}
