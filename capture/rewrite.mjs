// Intercept script responses and expose bundled library instances (GSAP, Lenis) that never touch `window`.
// Research-verified hooks:
//   GSAP core always runs `(win.gsapVersions || (win.gsapVersions = [])).push(gsap.version)`
//   Lenis' constructor always runs `window.lenisVersion = version` with `this` = the instance.
const RULES = [
  {
    name: 'gsap',
    test: /gsapVersions\s*=\s*\[\]\s*\)\s*\)\s*\.push\(/,
    apply: (s) => s.replace(/(gsapVersions\s*=\s*\[\]\s*\)\s*\)\s*\.push\(\s*)([\w$]+)/g, '$1(window.__ml_gsap=window.__ml_gsap||$2)'),
  },
  {
    name: 'lenis',
    test: /window\.lenisVersion\s*=/,
    apply: (s) => s.replace(/window\.lenisVersion\s*=/g, '((window.__ml_lenis=window.__ml_lenis||[]).push(this)),window.lenisVersion='),
  },
];

export async function installScriptRewriter(browser, sessionId, log = () => {}) {
  const S = (m, p) => browser.send(m, p, sessionId);
  let rewritten = 0;
  const hits = [];
  await S('Fetch.enable', { patterns: [{ resourceType: 'Script', requestStage: 'Response' }] });
  browser.on(async (m) => {
    if (m.sessionId !== sessionId || m.method !== 'Fetch.requestPaused') return;
    const { requestId, responseStatusCode, responseHeaders = [], request } = m.params;
    const passThrough = () => S('Fetch.continueResponse', { requestId }).catch(() => S('Fetch.continueRequest', { requestId }).catch(() => {}));
    if (!responseStatusCode || responseStatusCode >= 300) return passThrough();
    try {
      const { body, base64Encoded } = await S('Fetch.getResponseBody', { requestId });
      let src = base64Encoded ? Buffer.from(body, 'base64').toString('utf8') : body;
      const applied = [];
      for (const r of RULES) if (r.test.test(src)) { const next = r.apply(src); if (next !== src) { src = next; applied.push(r.name); } }
      if (!applied.length) return passThrough();
      rewritten++;
      hits.push({ url: request.url, rules: applied });
      log(`rewrote ${applied.join('+')} in ${request.url.slice(0, 100)}`);
      const headers = responseHeaders.filter((h) => !/^(content-length|content-encoding|content-security-policy)$/i.test(h.name));
      await S('Fetch.fulfillRequest', {
        requestId, responseCode: responseStatusCode, responseHeaders: headers, body: Buffer.from(src, 'utf8').toString('base64'),
      });
    } catch (e) {
      log('rewrite failed: ' + e.message);
      passThrough();
    }
  });
  return { count: () => rewritten, hits };
}
