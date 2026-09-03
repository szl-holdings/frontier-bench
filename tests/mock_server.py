import json, time
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path in ("/health", "/healthz", "/v1/models"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = ["Hello", " world", " from", " mock", " engine"]
            for c in chunks:
                time.sleep(0.01)
                payload = {"choices": [{"delta": {"content": c}}]}
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                self.wfile.flush()
            usage_payload = {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
            self.wfile.write(f"data: {json.dumps(usage_payload)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.send_response(404); self.end_headers()

def run(port=8899):
    server = HTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()

if __name__ == "__main__":
    run()
