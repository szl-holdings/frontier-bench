// Execute the actual generic browser consumer with synthetic DOM and fetch only.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

if (process.permission) {
  for (const scope of ["fs.write", "child", "worker", "addons", "wasi"]) {
    assert.equal(process.permission.has(scope), false, `unexpected ${scope} permission`);
  }
  assert.equal(process.permission.has("fs.read", "C:/Windows/win.ini"), false);
}

const source = readFileSync(new URL("../site/app.js", import.meta.url), "utf8");
const original = JSON.parse(readFileSync(process.argv[2], "utf8"));

class Element {
  constructor() { this.children = []; this.dataset = {}; this.textContent = ""; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
}

async function run(bundle, mode = "ok") {
  const elements = new Map();
  const document = {
    createElement() { return new Element(); },
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, new Element());
      return elements.get(selector);
    },
    querySelectorAll() { return []; },
  };
  const requests = [];
  const context = vm.createContext({ document, Intl, console: { error() {} },
    fetch: async (url) => {
      requests.push(url);
      assert.ok(["results.json", "deployment.json"].includes(url));
      if (mode === "network") throw new Error("synthetic unavailable");
      return { ok: mode !== "http", json: async () => {
        if (mode === "json") throw new Error("synthetic invalid JSON");
        return url === "results.json" ? bundle.results : bundle.deployment;
      } };
    },
  }, { codeGeneration: { strings: false, wasm: false } });
  await vm.runInContext(source, context, { timeout: 1000 });
  assert.deepEqual(requests, ["results.json", "deployment.json"]);
  const allText = () => [...elements.values()].map(function text(node) {
    return node.textContent + " " + node.children.map(text).join(" ");
  }).join(" ");
  return { elements, context, allText };
}

function clone() { return JSON.parse(JSON.stringify(original)); }
function assertUnverified(ui) {
  assert.equal(ui.elements.get(".status-strip").dataset.state, "unverified");
  assert.equal(ui.elements.get("#load-state").textContent, "UNVERIFIED assertions loaded");
  assert.equal(ui.elements.get("#receipt-count").textContent, "3");
  assert.match(ui.elements.get("#deployment-state").textContent, /3 unverified assertion\(s\)/);
  assert.doesNotMatch(ui.allText(), /Verified publication loaded|\bmeasured receipt\(s\)/);
  for (const plane of ["engine", "retrieval", "quant"]) {
    assert.equal(ui.elements.get(`#count-${plane}`).textContent, "1");
    assert.match(ui.allText(), /UNVERIFIED assertion · receipt/);
  }
}

assertUnverified(await run(clone()));
const forged = clone();
forged.results.evidence_state = "MEASURED";
forged.results.results.forEach(row => Object.assign(row, { status: "MEASURED", authenticated: true }));
Object.assign(forged.deployment.truth, { results_are_measured_only: true, authenticity: "VERIFIED", receipt_admission: "VERIFIED" });
assertUnverified(await run(forged));
const legacy = clone();
delete legacy.results.evidence_state;
legacy.results.results.forEach(row => { delete row.status; });
assertUnverified(await run(legacy));
const empty = clone();
empty.results.results = [];
empty.results.count = 0;
empty.deployment.truth.receipt_rows = 0;
const emptyUI = await run(empty);
assert.equal(emptyUI.elements.get(".status-strip").dataset.state, "empty");
assert.match(emptyUI.elements.get("#load-state").textContent, /^EMPTY_HONEST/);
const malformed = clone();
malformed.results.count = 0;
const malformedUI = await run(malformed);
assert.equal(malformedUI.elements.get(".status-strip").dataset.state, "failed");
assert.match(malformedUI.elements.get("#load-state").textContent, /^UNAVAILABLE/);
for (const mode of ["network", "http", "json"]) {
  const ui = await run(clone(), mode);
  assert.equal(ui.elements.get(".status-strip").dataset.state, "failed");
  assert.match(ui.elements.get("#load-state").textContent, /^UNAVAILABLE/);
  assert.equal(ui.elements.get("#receipt-count").textContent, "—");
}
const ui = await run(clone());
ui.context.renderFailure();
assert.equal(ui.elements.get("#receipt-count").textContent, "—");
assert.equal(ui.elements.get("#source-list").children.length, 0);
for (const plane of ["engine", "retrieval", "quant"]) {
  assert.equal(ui.elements.get(`#count-${plane}`).textContent, "—");
}
console.log("consumer provenance: 9 cases passed");
