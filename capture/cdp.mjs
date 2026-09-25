// Minimal Chrome DevTools Protocol client over WebSocket (no dependencies).
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, existsSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const withTimeout = (p, ms, what) =>
  Promise.race([p, sleep(ms).then(() => { throw new Error(`timeout after ${ms}ms: ${what}`); })]);

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

/** Locate chrome-headless-shell: $MOTION_LENS_CHROME, then the bundled .browsers dir, then Playwright's cache. */
export function findHeadlessShell() {
  if (process.env.MOTION_LENS_CHROME) return process.env.MOTION_LENS_CHROME;
  const home = process.env.HOME || '';
  const roots = [join(home, '.cache', 'motion-lens', 'browsers'), join(ROOT, '.browsers', 'chrome-headless-shell'), join(home, '.cache', 'ms-playwright')];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    const stack = [root];
    while (stack.length) {
      const d = stack.pop();
      let entries = [];
      try { entries = readdirSync(d, { withFileTypes: true }); } catch { continue; }
      for (const e of entries) {
        const p = join(d, e.name);
        if (e.isFile() && e.name === 'chrome-headless-shell') return p;
        if (e.isDirectory() && d.split('/').length - root.split('/').length < 4) stack.push(p);
      }
    }
  }
  throw new Error('chrome-headless-shell not found. Run: bin/motion-lens setup');
}

export async function launchBrowser({ exe = findHeadlessShell(), args = [], gpu = true } = {}) {
  const userDataDir = mkdtempSync(join(tmpdir(), 'motion-lens-'));
  const flags = [
    '--remote-debugging-port=0', `--user-data-dir=${userDataDir}`, '--no-first-run', '--no-default-browser-check',
    '--hide-scrollbars', '--mute-audio', '--autoplay-policy=no-user-gesture-required',
    // headless reports (hover:none)/(pointer:coarse) otherwise, and sites switch off cursor effects
    '--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4',
    ...(gpu ? ['--use-angle=vulkan', '--enable-features=Vulkan', '--ignore-gpu-blocklist'] : ['--enable-unsafe-swiftshader']),
    ...args, 'about:blank',
  ];
  const proc = spawn(exe, flags, { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  const wsUrl = await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('browser start timeout\n' + stderr.slice(-2000))), 20000);
    proc.stderr.on('data', (d) => {
      stderr += d;
      const m = /DevTools listening on (ws:\/\/\S+)/.exec(stderr);
      if (m) { clearTimeout(t); resolve(m[1]); }
    });
    proc.on('exit', (c) => reject(new Error(`browser exited (${c})\n` + stderr.slice(-2000))));
  });
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws connect failed')); });
  let nextId = 0;
  const pending = new Map();
  const listeners = new Set();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej, method } = pending.get(m.id);
      pending.delete(m.id);
      if (m.error) rej(new Error(`${method}: ${m.error.message}${m.error.data ? ' ' + m.error.data : ''}`));
      else res(m.result);
    } else for (const l of listeners) l(m);
  };
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
    const id = ++nextId;
    pending.set(id, { res, rej, method });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const on = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
  const waitFor = (method, sessionId, timeout = 15000, pred = () => true) => new Promise((res, rej) => {
    const off = on((m) => {
      if (m.method === method && (!sessionId || m.sessionId === sessionId) && pred(m.params)) { clearTimeout(t); off(); res(m.params); }
    });
    const t = setTimeout(() => { off(); rej(new Error('timeout waiting for ' + method)); }, timeout);
  });
  const version = await send('Browser.getVersion');
  const close = async () => {
    try { ws.close(); } catch { /* ignore */ }
    proc.kill('SIGKILL');
    await sleep(100);
    try { rmSync(userDataDir, { recursive: true, force: true }); } catch { /* ignore */ }
  };
  return { send, on, waitFor, close, version, stderr: () => stderr };
}
