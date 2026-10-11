"""Render the real Bench Space template with a labelled local empty fixture.

This does not run benchmarks, admit real receipts, contact the Hub, or publish.
The source CSP stays in force; only its results digest placeholder is finalized.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deploy/bench-plane"


def local_fixture(template: str) -> bytes:
    pins = re.findall(r"(engine|retrieval|quant): \{repo: '([^']+)', revision: '([^']+)', genesis: '([^']+)'\}", template)
    assert len(pins) == 3
    data = {
        "schema_version": "szl-bench-results/v2", "generated_at": "2026-09-04T00:00:00Z",
        "data_state": "EMPTY_HONEST", "count": 0,
        "results_sha256": hashlib.sha256(b"[]").hexdigest(), "results": [],
        "sources": {plane: {"repo": repo, "revision": revision, "genesis": genesis,
                            "receipt_count": 1, "receipt_head": genesis,
                            "integrity": "VERIFIED_UNSIGNED_EMPTY_CHAIN"}
                    for plane, repo, revision, genesis in pins},
    }
    return json.dumps(data, separators=(",", ":")).encode()


def run(output: Path) -> None:
    template = (PACKAGE / "szl-bench-suite.index.html").read_text(encoding="utf-8")
    payload = local_fixture(template)
    index = template.replace("__RESULTS_JSON_SHA256__", hashlib.sha256(payload).hexdigest()).encode()
    # Closed fixture routes: request text selects a tuple but never supplies a
    # header value, filename, directory enumeration, or MIME-type inference.
    content = {
        "/": (index, "text/html; charset=utf-8"),
        "/index.html": (index, "text/html; charset=utf-8"),
        "/results.json": (payload, "application/json"),
        "/szl/SOURCE.json": ((PACKAGE / "szl/SOURCE.json").read_bytes(), "application/json"),
        "/szl/szl-design-system.css": ((PACKAGE / "szl/szl-design-system.css").read_bytes(), "text/css"),
        "/szl/logos/szl_favicon.svg": ((PACKAGE / "szl/logos/szl_favicon.svg").read_bytes(), "image/svg+xml"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            data, media = content.get(path, (b"LOCAL FIXTURE: absent", "text/plain; charset=utf-8"))
            self.send_response(200 if path in content else 404)
            self.send_header("Content-Type", media)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "LOCAL EMPTY_HONEST UI FIXTURE; no benchmark or publication claim",
              "at": datetime.now(timezone.utc).isoformat(), "checks": [], "errors": [],
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("deploy/bench-plane/szl-bench-suite.index.html",
                                             "deploy/bench-plane/szl/SOURCE.json",
                                             "deploy/bench-plane/szl/szl-design-system.css")}}

    def check(name, ok, detail=None):
        report["checks"].append({"name": name, "pass": bool(ok), "detail": detail})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            options = {"headless": True}
            if os.environ.get("CHROME_PATH"):
                options.update(executable_path=os.environ["CHROME_PATH"], args=["--disable-dev-shm-usage", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            browser = playwright.chromium.launch(**options)
            report["browser"] = browser.version
            context = browser.new_context(reduced_motion="reduce")
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") and route.request.method == "GET" else route.abort())
            page = context.new_page()
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            for width in (320, 390, 768, 1280, 1920):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(base, wait_until="networkidle")
                expect(page.locator("#status-badge")).to_have_text("EMPTY_HONEST")
                check(f"{width} fixture resolves without a false measurement", page.locator("tbody tr.empty").count() == 3)
                page.keyboard.press("Tab")
                expect(page.locator(".skip-link")).to_be_focused()
                page.keyboard.press("Enter")
                check(f"{width} skip reaches main", page.locator("main").evaluate("e=>e===document.activeElement"))
                check(f"{width} skipped heading clears the sticky navigation", page.locator("h1").bounding_box()["y"] >= page.locator("header.nav").bounding_box()["y"] + page.locator("header.nav").bounding_box()["height"])
                check(f"{width} page fits viewport", page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"))
                for plane in ("engine", "retrieval", "quant"):
                    button = page.locator(f'button[data-target="{plane}"]')
                    button.click()
                    expect(button).to_have_attribute("aria-pressed", "true")
                    region = page.locator(f"#{plane} .table-wrap")
                    region.focus()
                    check(f"{width} {plane} table has a named keyboard region", region.get_attribute("role") == "region" and bool(region.get_attribute("aria-labelledby")) and region.evaluate("e=>e===document.activeElement"))
                    if region.evaluate("e=>e.scrollWidth>e.clientWidth"):
                        page.keyboard.press("ArrowRight")
                        page.wait_for_function("id=>document.querySelector('#'+id+' .table-wrap').scrollLeft>0", arg=plane)
                        check(f"{width} {plane} table scrolls by keyboard", True)
                    box = button.bounding_box()
                    check(f"{width} {plane} control has a44px target", box["height"] >= 43.99 and box["width"] >= 43.99)
                check(f"{width} headers keep explicit column semantics", page.locator("th:not([scope=col])").count() == 0)
                if width in (320, 1280):
                    page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
                    page.screenshot(path=str(output / f"bench-{width}.png"), full_page=True, timeout=60000)
            page.emulate_media(forced_colors="active")
            selected = page.locator('button[aria-pressed="true"]')
            selected.focus()
            check("forced-color keyboard selection remains outlined", selected.evaluate("e=>getComputedStyle(e).outlineStyle!=='none'"))
            page.emulate_media(forced_colors="none")
            page.route("**/results.json", lambda route: route.fulfill(status=503, content_type="application/json", body='{"fixture":"UNAVAILABLE"}'))
            page.reload(wait_until="networkidle")
            expect(page.locator("#status-badge")).to_have_text("UNAVAILABLE")
            check("failed evidence stays unavailable", "No result" in page.locator("#provenance").inner_text())
            nojs = browser.new_context(java_script_enabled=False)
            nojs_page = nojs.new_page()
            nojs_page.goto(base)
            check("no-JavaScript explanation remains visible", nojs_page.locator("noscript").is_visible())
            nojs.close()
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    failed = [item for item in report["checks"] if not item["pass"]]
    print(json.dumps({"checks": len(report["checks"]), "failures": failed, "errors": report["errors"]}))
    if failed or report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
