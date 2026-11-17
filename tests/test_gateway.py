"""Drive the MCP gateway with fake downstream servers over standard input and HTTP.

MCP means Model Context Protocol. No network connection leaves this machine.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_gateway.py --help.
"""

import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE = r"""
import json, os, sys
TOOLS = [
    {"name": "search", "description": "Search.", "inputSchema": {"type": "object"}},
    {"name": "read.page", "description": "Read.", "inputSchema": {"type": "object"}},
]
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method, params = message["method"], message.get("params", {})
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"],
                  "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        # Two pages prove that the gateway follows cursors.
        if params.get("cursor"):
            result = {"tools": TOOLS[1:]}
        else:
            result = {"tools": TOOLS[:1], "nextCursor": "page-2"}
    elif method == "tools/call":
        if params["name"] == "explode":
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                              "error": {"code": -32000, "message": "boom"}}), flush=True)
            continue
        text = params["name"] + ":" + json.dumps(params.get("arguments", {})) + ":" + os.environ.get("DOCS_TOKEN", "")
        result = {"content": [{"type": "text", "text": text}]}
    elif method == "prompts/list":
        result = {"prompts": [{"name": "summary"}]}
    elif method == "prompts/get":
        result = {"messages": [{"role": "user", "content": {"type": "text", "text": params["name"]}}]}
    elif method == "resources/list":
        result = {"resources": [{"uri": "docs://index", "name": "Index"}]}
    elif method == "resources/read":
        result = {"contents": [{"uri": params["uri"], "text": "index body"}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                          "error": {"code": -32601, "message": "no"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
"""


class Handler(http.server.BaseHTTPRequestHandler):
    """A Streamable HTTP server that answers tools/call with Server-Sent Events."""

    seen = []

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append((dict(self.headers), message))
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        method = message["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]}
        else:
            result = {"content": [{"type": "text", "text": "remote " + message["params"]["name"]}]}
        body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
        streamed = method == "tools/call"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if streamed else "application/json")
        self.send_header("Mcp-Session-Id", "session-1")
        self.end_headers()
        if streamed:
            self.wfile.write(
                b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            )
            self.wfile.write(f"event: message\ndata: {body}\n\n".encode())
        else:
            self.wfile.write(body.encode())

    def log_message(self, *arguments):
        pass


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        base = Path(self.folder.name)
        (base / "fake.py").write_text(FAKE)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        config = {
            "servers": {
                "docs": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(base / "fake.py")],
                },
                "issues": {
                    "transport": "http",
                    "url": f"http://127.0.0.1:{self.server.server_port}/mcp",
                    "bearer_token_env": "ISSUES_TOKEN",
                },
                "broken": {"transport": "stdio", "command": str(base / "missing")},
            }
        }
        self.config = base / "gateway.json"
        self.config.write_text(json.dumps(config))

    def exchange(self, *messages):
        lines = "".join(
            (message if isinstance(message, str) else json.dumps(message)) + "\n"
            for message in messages
        )
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/gateway.py"), "--config", str(self.config)],
            input=lines,
            text=True,
            capture_output=True,
            timeout=60,
            env={"PATH": "/usr/bin:/bin", "DOCS_TOKEN": "t1", "ISSUES_TOKEN": "t2"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()], result.stderr

    def request(self, identity, method, params=None):
        return {"jsonrpc": "2.0", "id": identity, "method": method, "params": params or {}}

    def test_one_entry_serves_every_downstream_tool_prompt_and_resource(self):
        answers, errors = self.exchange(
            self.request(1, "initialize", {"protocolVersion": "2025-06-18"}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            self.request(2, "tools/list"),
            self.request(3, "tools/call", {"name": "docs__search", "arguments": {"q": "x"}}),
            self.request(4, "tools/call", {"name": "docs__read_page"}),
            self.request(5, "tools/call", {"name": "issues__lookup"}),
            self.request(6, "prompts/get", {"name": "docs__summary"}),
            self.request(7, "resources/list"),
            self.request(8, "resources/read", {"uri": "docs://index"}),
            self.request(9, "ping"),
        )
        self.assertEqual([answer["id"] for answer in answers], list(range(1, 10)))
        self.assertEqual(answers[0]["result"]["serverInfo"]["name"], "fleet")
        names = [tool["name"] for tool in answers[1]["result"]["tools"]]
        self.assertEqual(names, ["docs__search", "docs__read_page", "issues__lookup"])
        self.assertEqual(answers[2]["result"]["content"][0]["text"], 'search:{"q": "x"}:t1')
        self.assertTrue(answers[3]["result"]["content"][0]["text"].startswith("read.page:"))
        self.assertEqual(answers[4]["result"]["content"][0]["text"], "remote lookup")
        self.assertEqual(answers[5]["result"]["messages"][0]["content"]["text"], "summary")
        self.assertEqual(
            answers[6]["result"]["resources"], [{"uri": "docs://index", "name": "Index"}]
        )
        self.assertEqual(answers[7]["result"]["contents"][0]["text"], "index body")
        self.assertEqual(answers[8]["result"], {})
        self.assertIn("broken did not start", errors)
        headers = [
            headers for headers, message in Handler.seen if message.get("method") == "tools/call"
        ]
        self.assertEqual(headers[-1]["Authorization"], "Bearer t2")
        self.assertEqual(headers[-1]["Mcp-Session-Id"], "session-1")

    def test_errors_return_to_the_agent_without_stopping_the_gateway(self):
        answers, _ = self.exchange(
            self.request(1, "initialize", {"protocolVersion": "1999-01-01"}),
            self.request(2, "tools/call", {"name": "unknown__tool"}),
            self.request(3, "sampling/createMessage"),
            "not json",
            self.request(4, "ping"),
        )
        self.assertEqual(answers[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("Unknown name", answers[1]["error"]["message"])
        self.assertEqual(answers[2]["error"]["code"], -32601)
        self.assertEqual(answers[3]["error"]["code"], -32700)
        self.assertEqual(answers[4]["result"], {})


if __name__ == "__main__":
    unittest.main()
