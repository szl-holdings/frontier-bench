"""Citation, immutable pin, receipt state, claim, and build-manifest guards.

Stdlib unittest only. Live GitHub readback runs when SZL_EXACT_HEAD_READBACK=1.
"""
import base64
import hashlib
import json
import os
import re
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_FIXTURE = ROOT / "tests" / "fixtures" / "szl-brand-pin.json"
RECEIPT_FIXTURE = ROOT / "tests" / "fixtures" / "receipt-state.json"
MANIFEST_FIXTURE = ROOT / "tests" / "fixtures" / "build-manifest.sha256"
CLAIM_FILES = ("README.md", "CITATION.cff", "site/index.html", "site/README.md")
BUILD_FILES = ("Dockerfile", "requirements.txt")
BUILD_DIRS = ("app", "site")
CLAIM_PATTERNS = (
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:tok/s|tokens/s|tokens per second)\b", re.I),
    re.compile(r"\b(?:speedup|outperforms|state[- ]of[- ]the[- ]art|SOTA|certified performance|faster than)\b", re.I),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds)\s+TTFT\b", re.I),
)


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_citation(text):
    data = {}
    authors = []
    current = None
    in_authors = False
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if line.startswith("  - "):
            if not in_authors:
                raise AssertionError(f"author item outside authors on line {line_no}")
            current = {}
            authors.append(current)
            remainder = line[4:]
            if remainder.strip():
                key, value = remainder.split(":", 1)
                current[key.strip()] = unquote(value)
            continue
        if line.startswith("    ") and in_authors:
            key, value = line.strip().split(":", 1)
            if current is None:
                raise AssertionError(f"author field without an author on line {line_no}")
            current[key.strip()] = unquote(value)
            continue
        if ":" not in line:
            raise AssertionError(f"unparsed citation line {line_no}")
        key, value = line.split(":", 1)
        key = key.strip()
        if key.lower() in {"doi", "doi-url"}:
            raise AssertionError(f"doi key on line {line_no}")
        if key == "authors":
            if value.strip():
                raise AssertionError("authors must be a list")
            in_authors = True
            continue
        in_authors = False
        data[key] = unquote(value)
    data["authors"] = authors
    return data


def claim_violations(text):
    return [pattern.pattern for pattern in CLAIM_PATTERNS if pattern.search(text)]


def admitted_measured(receipts):
    return [receipt for receipt in receipts if receipt.get("status") == "MEASURED"]


def build_manifest_bytes(root):
    paths = [root / name for name in BUILD_FILES]
    for folder in BUILD_DIRS:
        paths.extend(path for path in (root / folder).rglob("*") if path.is_file())
    lines = []
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        data = path.read_bytes().replace(b"\r\n", b"\n")
        digest = hashlib.sha256(data).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    return ("\n".join(lines) + "\n").encode("ascii")


def github_json(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "szl-frontier-bench-pin-readback",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


class CitationAndGuardTests(unittest.TestCase):
    def test_citation_file_is_truthful_and_has_no_doi(self):
        text = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        self.assertFalse(text.startswith("\ufeff"))
        parsed = parse_citation(text)
        self.assertEqual(parsed["cff-version"], "1.2.0")
        self.assertEqual(parsed["title"], "Frontier Bench")
        self.assertEqual(parsed["type"], "software")
        self.assertEqual(parsed["license"], "Apache-2.0")
        self.assertEqual(parsed["repository-code"], "https://github.com/szl-holdings/frontier-bench")
        self.assertEqual(parsed["url"], "https://github.com/szl-holdings/frontier-bench")
        self.assertIn("No DOI has been deposited", parsed["message"])
        self.assertNotIn("zenodo", parsed["message"].lower())
        for phrase in ("EMPTY_HONEST", "BLOCKED", "Admitted measured rows are zero", "no performance claim"):
            self.assertIn(phrase, parsed["abstract"])
        self.assertNotIn("version", parsed)
        self.assertNotIn("date-released", parsed)
        self.assertEqual(len(parsed["authors"]), 1)
        author = parsed["authors"][0]
        self.assertEqual(author["family-names"], "Lutar")
        self.assertEqual(author["given-names"], "Stephen P.")
        self.assertEqual(author["orcid"], "https://orcid.org/0009-0001-0110-4173")
        self.assertEqual(author["affiliation"], "SZL Holdings")

    def test_readme_pin_matches_the_verified_brand_commit(self):
        pin = json.loads(PIN_FIXTURE.read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        commit = pin["commit"]
        icon = pin["icon_path"]
        url = f"https://raw.githubusercontent.com/szl-holdings/szl-brand/{commit}/{icon}"
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertNotEqual(commit, pin["previous"])
        self.assertNotIn(pin["previous"], readme)
        self.assertNotIn("/main/", url)
        self.assertEqual(readme.count(url), 1)
        self.assertIn(f"szl-brand@{commit}; Orbit v2 icon.", readme)
        self.assertTrue(pin["icon_unchanged"])
        self.assertEqual(pin["ahead_by"], 22)
        self.assertEqual(pin["behind_by"], 0)
        self.assertEqual(pin["icon_sha256"], "6e2b7d72a3af3d9602b79de41df0194ede6496a9661f057108ec224c0efc92e6")
        self.assertEqual(pin["icon_git_blob"], "a6d0c6392df309ab69f6ee063ec094b4089b3d9b")
        self.assertEqual(pin["repository"], "szl-holdings/szl-brand")

    def test_exact_head_readback(self):
        if os.environ.get("SZL_EXACT_HEAD_READBACK") != "1":
            self.skipTest("set SZL_EXACT_HEAD_READBACK=1 for the live commit readback")
        pin = json.loads(PIN_FIXTURE.read_text(encoding="utf-8"))
        commit = github_json(f"https://api.github.com/repos/{pin['repository']}/commits/{pin['commit']}")
        self.assertEqual(commit["sha"], pin["commit"])
        icon = github_json(
            f"https://api.github.com/repos/{pin['repository']}/contents/{pin['icon_path']}?ref={pin['commit']}"
        )
        self.assertEqual(icon["sha"], pin["icon_git_blob"])
        raw = base64.b64decode("".join(icon["content"].split()))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), pin["icon_sha256"])
        previous = github_json(
            f"https://api.github.com/repos/{pin['repository']}/contents/{pin['icon_path']}?ref={pin['previous']}"
        )
        self.assertEqual(previous["sha"], pin["icon_git_blob"])

    def test_public_receipts_stay_blocked_and_empty(self):
        expected = json.loads(RECEIPT_FIXTURE.read_text(encoding="utf-8"))
        path = ROOT / expected["genesis_file"]
        receipts = sorted((ROOT / "receipts").glob("*.json"))
        self.assertEqual(receipts, [path])
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], expected["genesis_status"])
        self.assertEqual(payload["metrics"], {})
        self.assertEqual(payload["prev_hash"], "0" * 64)
        self.assertEqual(len(admitted_measured([payload])), expected["admitted_measured_rows"])
        self.assertEqual(admitted_measured([{"status": "MEASURED", "metrics": {"tok_s": 1}}]), [{"status": "MEASURED", "metrics": {"tok_s": 1}}])
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(expected["public_state"], readme)
        self.assertIn("zero admitted measured", readme)
        self.assertIn("BLOCKED genesis", readme)

    def test_public_claim_files_make_no_performance_claim(self):
        self.assertEqual(claim_violations("certified performance"), [CLAIM_PATTERNS[1].pattern])
        self.assertEqual(claim_violations("12 tok/s"), [CLAIM_PATTERNS[0].pattern])
        self.assertEqual(claim_violations("This repository makes no performance claim of its own."), [])
        for relative in CLAIM_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertEqual(claim_violations(text), [], relative)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("This repository makes no performance claim of its own.", readme)

    def test_source_manifest_is_reproducible(self):
        first = hashlib.sha256(build_manifest_bytes(ROOT)).hexdigest()
        second = hashlib.sha256(build_manifest_bytes(ROOT)).hexdigest()
        self.assertEqual(first, second)
        recorded = MANIFEST_FIXTURE.read_text(encoding="utf-8").strip()
        self.assertEqual(first, recorded)
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.14-slim", dockerfile)
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn(">=", requirements)


if __name__ == "__main__":
    unittest.main()
