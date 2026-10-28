"""Verify CLI composition through the actual client against a local HTTP fixture."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse


class HttpWorkflowTests(unittest.TestCase):
    """Exercise doctor, search, view, and export without a real Zotero library."""

    def test_read_only_workflow_with_real_client_and_subprocesses(self) -> None:
        item = {
            "key": "ABCD1234", "version": 7,
            "data": {
                "key": "ABCD1234", "version": 7,
                "itemType": "journalArticle", "title": "图神经网络",
                "creators": [], "date": "2025", "tags": [], "collections": [],
            },
        }
        requests_seen = []

        class LibraryHandler(BaseHTTPRequestHandler):
            """Serve the read endpoints needed for a small local library."""

            def do_GET(self) -> None:
                requests_seen.append((self.command, self.path))
                path = urlparse(self.path).path.rstrip("/")
                if path.endswith("/items/ABCD1234"):
                    payload = item
                elif path.endswith("/items"):
                    payload = [item]
                elif path.endswith("/collections"):
                    payload = []
                else:
                    self.send_error(404)
                    return
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Total-Results", "1")
                self.send_header("Last-Modified-Version", "7")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args) -> None:
                """Keep HTTP fixture logs out of test output."""

        with HTTPServer(("127.0.0.1", 0), LibraryHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    config = root / "config.yaml"
                    config.write_text(
                        "zotero:\n  mode: local\n  local_api: "
                        f"http://127.0.0.1:{server.server_port}/api\n"
                        f"  endpoint: http://127.0.0.1:{server.server_port}/web\n"
                        "  library_id: 123\n  api_key: fixture-key\n",
                        encoding="utf-8",
                    )
                    environment = dict(os.environ, REF_CONFIG=str(config))

                    def invoke(*arguments: str) -> str:
                        result = subprocess.run(
                            [sys.executable, "-c", "from ref.cli import app; app()",
                             "--non-interactive", *arguments],
                            env=environment, capture_output=True, text=True,
                            timeout=15,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        return result.stdout

                    self.assertTrue(json.loads(invoke("doctor", "--json"))["ok"])
                    checked = json.loads(invoke("doctor", "--web", "--json"))
                    self.assertTrue(checked["ok"])
                    self.assertEqual(checked["backend"], "web")
                    self.assertTrue(any("/web/users/123/items" in path for _, path in requests_seen))
                    results = json.loads(invoke("list", "--limit", "1", "--json"))
                    self.assertEqual(results["count"], 1)
                    key = results["items"][0]["key"]
                    self.assertEqual(key, "ABCD1234")
                    viewed = json.loads(invoke("view", key, "--json-only"))
                    self.assertEqual(viewed["title"], "图神经网络")
                    exported = root / "references.json"
                    invoke("export", key, "--format", "csljson", "--out", str(exported))
                    self.assertEqual(
                        json.loads(exported.read_text(encoding="utf-8"))[0]["title"],
                        viewed["title"],
                    )
            finally:
                server.shutdown()
                thread.join(timeout=5)
        self.assertTrue(requests_seen)
        self.assertTrue(all(method == "GET" for method, _ in requests_seen))


if __name__ == "__main__":
    unittest.main()
