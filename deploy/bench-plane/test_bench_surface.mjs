/** Node-only UI contract regression tests. All measured rows below are test fixtures.
 * This verifies rendering logic, not a browser, deployed Space, or real benchmark.
 * Run: node test_bench_surface.mjs
 */
import assert from 'node:assert/strict';
import {createHash, webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const htmlPath = fileURLToPath(new URL('./szl-bench-suite.index.html', import.meta.url));
const htmlBytes = readFileSync(htmlPath);
// HTML parsing normalizes CRLF/CR before CSP hashes are compared with inline text.
const html = htmlBytes.toString('utf8').replace(/\r\n?/g, '\n');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, 'expected exactly one inline script');
assert.equal((html.match(/<script>/g) || []).length, 1);
new vm.Script(script, {filename: htmlPath});
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
for (const tag of ['style', 'script']) {
  const content = html.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`))?.[1];
  assert.ok(content, `${tag} block exists`);
  const digest = createHash('sha256').update(content).digest('base64');
  assert.ok(html.includes(`${tag}-src 'sha256-${digest}'`), `${tag} CSP digest matches normalized source`);
}
const sourcePins = vm.runInNewContext(`(${script.match(/const sourcePins = (\{[\s\S]*?\n  \});/)[1]})`);
const python = readFileSync(new URL('./finish_bench_plane.py', import.meta.url), 'utf8');
for (const [plane, pin] of Object.entries(sourcePins)) {
  const spec = python.match(new RegExp(`plane="${plane}",\\s+repo="([^"]+)",\\s+port=\\d+,\\s+revision="([^"]+)",\\s+genesis="([^"]+)"`));
  assert.ok(spec, `${plane} controller source spec exists`);
  assert.deepEqual([pin.repo, pin.revision, pin.genesis], spec.slice(1), `${plane} source pins match controller`);
}

class Element {
  constructor(tag = '') {
    this.tagName = tag;
    this.children = [];
    this.textContent = '';
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.hidden = false;
  }
  set innerHTML(_value) { throw new Error('unsafe innerHTML write'); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}

function emptyPayload() {
  return {
    schema_version: 'szl-bench-results/v2',
    generated_at: '2026-09-04T00:00:00Z',
    data_state: 'EMPTY_HONEST',
    count: 0,
    results_sha256: sha256('[]'),
    sources: Object.fromEntries(Object.entries(sourcePins).map(([plane, pin]) => [plane, {
      ...pin, receipt_count: 1, receipt_head: pin.genesis, integrity: 'VERIFIED_UNSIGNED_EMPTY_CHAIN'
    }])),
    results: []
  };
}

function measuredPayload() {
  const data = emptyPayload();
  data.results = Object.entries(sourcePins).map(([plane, pin], index) => ({
    plane,
    machine: {cpu: 'i9-14900HX', ram_gb: 128, gpu: 'RTX 4000 Ada 20GB'},
    measured_at: '2026-09-04T00:00:00.123456Z',
    method: 'UI TEST FIXTURE ONLY',
    metrics: plane === 'engine'
      ? {model: '<img src=x onerror=alert(1)> TEST FIXTURE', precision: 'fixture', prompt_tps: 1, decode_tps: 2, peak_vram_gb: 3}
      : plane === 'retrieval'
        ? {corpus: 'TEST FIXTURE', method: 'fixture', ndcg10: 0.1, recall100: 0.2, mrr: 0.3, p50_ms: 1}
        : {model: 'TEST FIXTURE', precision: 'fixture', perplexity: 1, decode_tps: 2, peak_vram_gb: 3},
    receipt: String(index + 1).repeat(64),
    source_revision: pin.revision,
    workload: {model_revision: 'TEST FIXTURE', data_revision: 'TEST FIXTURE', configuration_sha256: 'a'.repeat(64)},
    artifacts: {'fixture.json': 'b'.repeat(64)},
    hardware_evidence_sha256: 'c'.repeat(64),
    receipt_auth: {alg: 'hmac-sha256', key_id: 'szl-bench-node-hmac-v1'}
  }));
  for (const row of data.results) {
    Object.assign(data.sources[row.plane], {receipt_count: 2, receipt_head: row.receipt, integrity: 'VERIFIED_CHAIN_AND_HMAC_MEASUREMENTS'});
  }
  data.count = data.results.length;
  data.data_state = 'MEASURED';
  data.results_sha256 = sha256(JSON.stringify(data.results));
  return data;
}

async function runPage(data, options = {}) {
  const bytes = options.bytes ?? Buffer.from(JSON.stringify(data));
  const nodes = Object.fromEntries(['status', 'status-badge', 'status-detail', 'provenance', 'stamp', ...Object.keys(sourcePins)].map(id => [id, new Element()]));
  nodes['status-badge'].textContent = 'CHECKING';
  const bodies = Object.fromEntries(Object.keys(sourcePins).map(plane => [plane, new Element('tbody')]));
  const buttons = Object.keys(sourcePins).map(plane => {
    const button = new Element('button');
    button.dataset.target = plane;
    return button;
  });
  const document = {
    getElementById: id => nodes[id],
    querySelectorAll: query => query === 'nav button' ? buttons : [],
    querySelector: query => query.startsWith('meta[') ? {content: options.digest ?? sha256(bytes)} : bodies[query.match(/data-plane="([^"]+)"/)?.[1]],
    createElement: tag => new Element(tag)
  };
  let cancelled = false;
  let released = false;
  let reads = 0;
  let offset = 0;
  const reader = {
    async read() {
      reads += 1;
      if (options.oversizedStream) return {done: false, value: new Uint8Array(4194305)};
      if (options.invalidChunk) return {done: false, value: 'invalid'};
      if (offset >= bytes.length) return {done: true};
      const value = bytes.subarray(offset, Math.min(offset + 37, bytes.length));
      offset += value.length;
      return {done: false, value};
    },
    async cancel() { cancelled = true; },
    releaseLock() { released = true; }
  };
  const fetch = async (url, init) => {
    assert.equal(url, 'results.json');
    assert.equal(init.redirect, 'error');
    assert.equal(init.credentials, 'omit');
    if (options.networkError) throw new Error('simulated network failure');
    return {
      ok: !options.httpError,
      status: options.httpError ?? 200,
      headers: {get: name => name === 'content-type' ? (options.contentType ?? 'application/json; charset=utf-8') : (options.contentLength ?? null)},
      body: options.noStream ? null : {getReader: () => reader}
    };
  };
  vm.runInNewContext(script, {document, fetch, crypto: webcrypto, AbortController, TextDecoder, Uint8Array, setTimeout, clearTimeout}, {filename: htmlPath});
  const deadline = Date.now() + 2000;
  while (nodes['status-badge'].textContent === 'CHECKING' && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 1));
  assert.notEqual(nodes['status-badge'].textContent, 'CHECKING', 'load must reach a terminal UI state');
  return {nodes, bodies, buttons, cancelled, released, reads};
}

let passed = 0;
async function test(name, callback) {
  await callback();
  passed += 1;
  process.stdout.write(`PASS ${name}\n`);
}
async function rejects(data, options = {}) {
  const page = await runPage(data, options);
  assert.equal(page.nodes['status-badge'].textContent, 'UNAVAILABLE');
  for (const body of Object.values(page.bodies)) {
    assert.equal(body.children.length, 1);
    assert.equal(body.children[0].className, 'empty');
    assert.match(body.children[0].children[0].textContent, /^UNAVAILABLE/);
  }
  return page;
}

await test('validated empty payload renders EMPTY_HONEST in every plane', async () => {
  const page = await runPage(emptyPayload());
  assert.equal(page.nodes['status-badge'].textContent, 'EMPTY_HONEST');
  for (const body of Object.values(page.bodies)) assert.match(body.children[0].children[0].textContent, /^EMPTY_HONEST/);
  assert.equal(page.released, true);
});
await test('valid fixture measurements render all three tables using text nodes', async () => {
  const page = await runPage(measuredPayload());
  assert.equal(page.nodes['status-badge'].textContent, 'MEASURED');
  for (const [plane, body] of Object.entries(page.bodies)) {
    assert.equal(body.children.length, 1);
    assert.equal(body.children[0].className, 'measured');
    assert.equal(body.children[0].children.length, plane === 'retrieval' ? 8 : 7);
  }
  assert.match(page.bodies.engine.children[0].children[0].textContent, /^<img /);
  page.buttons[1].listeners.click();
  assert.equal(page.nodes.retrieval.hidden, false);
  assert.equal(page.nodes.engine.hidden, true);
  assert.equal(page.buttons[1].attributes['aria-pressed'], 'true');
});
await test('network failure is unavailable', () => rejects(emptyPayload(), {networkError: true}));
await test('HTTP error is unavailable', () => rejects(emptyPayload(), {httpError: 503}));
await test('wrong content type is unavailable', () => rejects(emptyPayload(), {contentType: 'text/html'}));
await test('unfinalized page digest is unavailable', () => rejects(emptyPayload(), {digest: '__RESULTS_JSON_SHA256__'}));
await test('mismatched raw bytes are unavailable', () => rejects(emptyPayload(), {digest: '0'.repeat(64)}));
await test('oversized declared body is rejected before reads', async () => {
  const page = await rejects(emptyPayload(), {contentLength: '4194305'});
  assert.equal(page.reads, 0);
});
await test('oversized streamed body is cancelled at the limit', async () => {
  const page = await rejects(emptyPayload(), {oversizedStream: true});
  assert.equal(page.cancelled, true);
  assert.equal(page.released, true);
  assert.equal(page.reads, 1);
});
await test('streaming support is required', () => rejects(emptyPayload(), {noStream: true}));
await test('malformed stream chunk is unavailable', () => rejects(emptyPayload(), {invalidChunk: true}));
await test('invalid UTF-8 is unavailable even with matching bytes', () => rejects(null, {bytes: Buffer.from([0xc3, 0x28])}));
await test('malformed JSON is unavailable even with matching bytes', () => rejects(null, {bytes: Buffer.from('{')}));
for (const [name, mutate] of [
  ['unsupported schema', data => { data.schema_version = 'other'; }],
  ['source revision mismatch', data => { data.sources.engine.revision = '0'.repeat(40); }],
  ['contradictory count', data => { data.count += 1; }],
  ['contradictory measurement integrity', data => { data.sources.engine.integrity = 'VERIFIED_UNSIGNED_EMPTY_CHAIN'; }],
  ['wrong receipt key metadata', data => { data.results[0].receipt_auth.key_id = 'unknown'; }],
  ['duplicate receipt', data => { data.results[1].receipt = data.results[0].receipt; }],
  ['impossible calendar date', data => { data.results[0].measured_at = '2026-02-30T00:00:00Z'; }],
  ['hash array coercion', data => { data.results[0].hardware_evidence_sha256 = ['a'.repeat(64)]; }],
  ['out-of-range retrieval metric', data => { data.results[1].metrics.ndcg10 = 1.1; }]
]) {
  await test(`${name} is unavailable`, async () => {
    const data = measuredPayload();
    mutate(data);
    await rejects(data);
  });
}
process.stdout.write(`UI contract: ${passed} tests passed; CSP/source pins verified; HTML SHA-256 ${sha256(htmlBytes)}\n`);
