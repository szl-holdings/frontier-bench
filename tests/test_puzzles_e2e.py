import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from puzzles.families import generate_futoshiki
from puzzles.render import render
from puzzles.probe import main as probe_main


def make_mock_handler(responses):
    """responses: dict prompt_sha256 -> completion text."""
    from puzzles.render import prompt_sha256

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            prompt = body["messages"][0]["content"]
            text = responses.get(prompt_sha256(prompt), "I cannot determine this.")
            out = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": text}}]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass

    return Handler


class TestProbeEndToEnd(unittest.TestCase):
    def test_measured_run_with_mock_endpoint(self):
        from puzzles.render import prompt_sha256

        # the probe derives its puzzle seed from --seed; mirror it exactly
        probe_seed = 11 * 1000003 + 0 * 131 + 4
        puzzle, _ = generate_futoshiki(probe_seed, 4)
        good = json.dumps({"solution": puzzle["certified_solution"]})
        # wrong answer: swap two values in the first row
        bad_sol = dict(puzzle["certified_solution"])
        bad_sol["r1c1"], bad_sol["r1c2"] = bad_sol["r1c2"], bad_sol["r1c1"]
        bad = json.dumps({"solution": bad_sol})
        good_sha = prompt_sha256(render(puzzle["spec"]))
        responses = {good_sha: good, prompt_sha256("other"): bad}
        server = HTTPServer(("127.0.0.1", 0), make_mock_handler(responses))
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "receipt.json")
                os.environ["TEST_PUZZLE_ENDPOINT_E2E"] = (
                    f"http://127.0.0.1:{port}/v1"
                )
                rc = probe_main(
                    [
                        "--seed", "11",
                        "--plan", "f4:1",
                        "--model", "mock",
                        "--model-revision", "0" * 40,
                        "--source-revision", "0" * 40,
                        "--endpoint-env", "TEST_PUZZLE_ENDPOINT_E2E",
                        "--out", out,
                    ]
                )
                self.assertEqual(rc, 0)
                with open(out, "r", encoding="utf-8") as handle:
                    receipt = json.load(handle)
                self.assertEqual(receipt["state"], "MEASURED")
                # the mock answers the one certified f4 puzzle correctly
                self.assertEqual(receipt["score"], "1/1")
                self.assertEqual(len(receipt["rows"]), 1)
                self.assertEqual(receipt["rows"][0]["verdict"], "PASS")
                self.assertIn("receipt_sha256", receipt)
        finally:
            server.shutdown()
            del os.environ["TEST_PUZZLE_ENDPOINT_E2E"]


if __name__ == "__main__":
    unittest.main()
