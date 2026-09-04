"use strict";

const planes = ["engine", "retrieval", "quant"];
const labels = { engine: "Engine", retrieval: "Retrieval", quant: "Quantization" };
const repositories = {
  engine: "szl-holdings/frontier-bench",
  retrieval: "szl-holdings/retrieval-bench",
  quant: "szl-holdings/quant-curve",
};

function text(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function shortDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function emptyPanel(plane) {
  const wrapper = document.createElement("div");
  wrapper.className = "empty-state";
  const inner = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = `No measured ${labels[plane].toLowerCase()} receipts yet`;
  const message = document.createElement("p");
  message.textContent = "The verification chain is present, but no MEASURED row has been admitted. This is an honest empty state, not a zero score.";
  inner.append(title, message);
  wrapper.append(inner);
  return wrapper;
}

function unavailablePanel(plane) {
  const wrapper = document.createElement("div");
  wrapper.className = "empty-state";
  const inner = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = `${labels[plane]} evidence unavailable`;
  const message = document.createElement("p");
  message.textContent = "The source-bound publication files could not be loaded or did not match the expected schema. No benchmark claim is shown.";
  inner.append(title, message);
  wrapper.append(inner);
  return wrapper;
}

function validatePublication(results, deployment) {
  if (results?.schema !== "szl.bench-suite.results/v1" || !Array.isArray(results.results)) {
    throw new Error("results schema mismatch");
  }
  if (results.count !== results.results.length || !Array.isArray(results.sources) || results.sources.length !== 3) {
    throw new Error("results proof set is incomplete");
  }
  if (deployment?.schema !== "szl.bench-suite.deployment/v1" || deployment.target !== "betterwithage/szl-bench-suite") {
    throw new Error("deployment schema mismatch");
  }
  if (deployment.publisher !== "szl-holdings/frontier-bench" || deployment.truth?.receipt_rows !== results.results.length) {
    throw new Error("deployment truth mismatch");
  }
  for (const plane of planes) {
    const source = results.sources.find((candidate) => candidate.plane === plane);
    if (source?.repository !== repositories[plane] || !/^[0-9a-f]{40}$/.test(source.revision || "")) {
      throw new Error(`invalid ${plane} source binding`);
    }
    const deploymentSource = deployment.sources?.find((candidate) => candidate.plane === plane);
    if (JSON.stringify(deploymentSource) !== JSON.stringify(source)) {
      throw new Error(`deployment ${plane} source mismatch`);
    }
  }
  for (const row of results.results) {
    const source = results.sources.find((candidate) => candidate.plane === row.plane);
    if (!source || row.source_repository !== source.repository || row.source_revision !== source.revision) {
      throw new Error("result source binding mismatch");
    }
    if (!/^[0-9a-f]{64}$/.test(row.receipt || "")) throw new Error("invalid receipt digest");
  }
}

function resultCard(row) {
  const card = document.createElement("article");
  card.className = "result-card";
  const heading = document.createElement("h3");
  heading.textContent = text(row.method);
  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = `${shortDate(row.measured_at)} · ${text(row.machine?.gpu || row.machine?.cpu)}`;
  const metrics = document.createElement("dl");
  metrics.className = "metric-list";
  Object.entries(row.metrics || {}).forEach(([key, value]) => {
    const item = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = key.replaceAll("_", " ");
    const definition = document.createElement("dd");
    definition.textContent = text(value);
    item.append(term, definition);
    metrics.append(item);
  });
  const receipt = document.createElement("p");
  receipt.className = "receipt";
  receipt.textContent = `receipt ${text(row.receipt).slice(0, 16)}…`;
  card.append(heading, meta, metrics, receipt);
  return card;
}

function renderResults(payload) {
  document.querySelector(".status-strip").dataset.state = "verified";
  document.querySelector("#load-state").textContent = "Verified publication loaded";
  document.querySelector("#receipt-count").textContent = String(payload.results.length);
  document.querySelector("#source-count").textContent = String(payload.sources?.length || 0);
  document.querySelector("#published-at").textContent = shortDate(payload.generated_at);

  planes.forEach((plane) => {
    const rows = payload.results.filter((row) => row.plane === plane);
    document.querySelector(`#count-${plane}`).textContent = String(rows.length);
    const panel = document.querySelector(`[data-panel="${plane}"]`);
    panel.replaceChildren();
    if (!rows.length) {
      panel.append(emptyPanel(plane));
      return;
    }
    const grid = document.createElement("div");
    grid.className = "result-grid";
    rows.forEach((row) => grid.append(resultCard(row)));
    panel.append(grid);
  });

  const sourceList = document.querySelector("#source-list");
  sourceList.replaceChildren();
  (payload.sources || []).forEach((source) => {
    const row = document.createElement("div");
    row.className = "source-row";
    const plane = document.createElement("strong");
    plane.textContent = labels[source.plane] || text(source.plane);
    const link = document.createElement("a");
    link.href = `https://github.com/${source.repository}/commit/${source.revision}`;
    link.textContent = `${source.repository}@${source.revision}`;
    link.rel = "noreferrer";
    row.append(plane, link);
    sourceList.append(row);
  });
}

function renderFailure() {
  document.querySelector(".status-strip").dataset.state = "failed";
  document.querySelector("#load-state").textContent = "Verified evidence unavailable";
  planes.forEach((plane) => {
    const panel = document.querySelector(`[data-panel="${plane}"]`);
    panel.replaceChildren(unavailablePanel(plane));
  });
}

function installTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function select(tab) {
    tabs.forEach((candidate) => {
      const selected = candidate === tab;
      candidate.setAttribute("aria-selected", String(selected));
      candidate.tabIndex = selected ? 0 : -1;
      document.querySelector(`#${candidate.getAttribute("aria-controls")}`).hidden = !selected;
    });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      select(tabs[next]);
      tabs[next].focus();
    });
  });
}

async function boot() {
  installTabs();
  try {
    const [resultsResponse, deploymentResponse] = await Promise.all([
      fetch("results.json", { cache: "no-store" }),
      fetch("deployment.json", { cache: "no-store" }),
    ]);
    if (!resultsResponse.ok || !deploymentResponse.ok) throw new Error("publication files unavailable");
    const [results, deployment] = await Promise.all([resultsResponse.json(), deploymentResponse.json()]);
    validatePublication(results, deployment);
    renderResults(results);
    document.querySelector("#deployment-state").textContent = `${deployment.publisher} · ${deployment.truth?.receipt_rows ?? 0} measured receipt(s)`;
  } catch (error) {
    renderFailure();
    document.querySelector("#deployment-state").textContent = "Deployment proof unavailable";
    console.error(error);
  }
}

boot();
