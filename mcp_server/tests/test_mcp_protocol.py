#!/usr/bin/env python3
"""MCP protocol conformance test.

The tool suite (test_mcp_tools.py) proves what the tools DO; this file proves
the server SPEAKS the protocol correctly. The split has a concrete reason: 171
tool checks were passing while the server was answering notifications, and not
one of them caught it.
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "mcp_server" / "server.py"
PYTHON = ROOT / "Sensei" / ".venv" / "bin" / "python"
SAMPLE_ALS = Path.home() / "Desktop" / "solo" / "Turtle.als"

checks = []
failures = []


def check(label, condition, detail=""):
    if condition:
        checks.append(label)
    else:
        failures.append("%s  %s" % (label, detail))


class Client:
    def __init__(self):
        self.process = subprocess.Popen(
            [str(PYTHON), str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, cwd=str(ROOT),
        )
        self.lock = threading.Lock()

    def send(self, message):
        self.send_raw(json.dumps(message))

    def send_raw(self, line):
        with self.lock:
            self.process.stdin.write(line + "\n")
            self.process.stdin.flush()

    def read(self, timeout=90):
        """Read one line. Returns None on timeout."""
        result = {}

        def reader():
            result["line"] = self.process.stdout.readline()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout)
        line = result.get("line")
        return json.loads(line) if line else None

    def request(self, method, params=None, request_id=1):
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        return self.read()

    def call(self, name, arguments=None, request_id=1):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}}, request_id)

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except Exception:
            self.process.kill()


def payload_of(response):
    text = response["result"]["content"][0]["text"]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


client = Client()
try:
    # --- 7) protocol version negotiation ---
    response = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    check("the requested protocol version comes back", response["result"]["protocolVersion"] == "2025-06-18", response["result"]["protocolVersion"])
    caps = response["result"]["capabilities"]
    check("the tools capability is declared", "tools" in caps, caps)
    check("the resources capability is declared", "resources" in caps, caps)
    check("the prompts capability is declared", "prompts" in caps, caps)

    response = client.request("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, 2)
    check("an unsupported version falls back to ours", response["result"]["protocolVersion"] == "2025-06-18", response["result"]["protocolVersion"])

    # --- 1) notifications ---
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    client.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 999}})
    client.send({"jsonrpc": "2.0", "method": "voidedNotificationThatDoesNotExist"})
    response = client.request("ping", {}, 3)
    check("notifications are NEVER answered", response is not None and response.get("id") == 3, response)

    # --- malformed requests ---
    client.send_raw("{ bozuk json")
    response = client.read()
    check("malformed JSON returns -32700", response and response["error"]["code"] == -32700, response)
    client.send_raw('"valid json but not a request"')
    response = client.read()
    check("valid JSON that is not a request returns -32600", response and response["error"]["code"] == -32600, response)
    response = client.request("no/such/method", {}, 4)
    check("an unknown method returns -32601", response["error"]["code"] == -32601, response)

    # --- 2) schema validation ---
    response = client.call("project_inspect", {}, 5)
    text = response["result"]["content"][0]["text"]
    check("a missing required field gives a meaningful error", "missing required argument" in text, text[:120])
    check("a missing field does not leak a raw KeyError", "KeyError" not in text and "'als_path'" not in text.replace("missing required argument(s): als_path", ""), text[:120])

    response = client.call("project_inspect", {"als_path": 123}, 6)
    check("the wrong type is refused", "must be string" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:120])

    response = client.call("chain_evidence", {"rol": "kick"}, 7)
    check("an unknown argument is refused", "unknown argument" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:120])

    response = client.call("midi_generate", {"role": "guitar"}, 8)
    check("a value outside the enum is refused", "must be one of" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:120])

    response = client.call("__no_such_tool__", {}, 9)
    check("an unknown tool returns isError", response["result"]["isError"], response)

    # --- 3) path restriction ---
    response = client.call("project_inspect", {"als_path": "/etc/hosts"}, 10)
    check("a path outside the allowed roots is refused", "path_outside_allowed_roots" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:140])

    response = client.call("project_inspect", {"als_path": str(Path.home() / "Desktop" / "notes.txt")}, 11)
    check("a file that is not an .als is refused", "not_an_als_file" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:140])

    response = client.call("project_inspect", {"als_path": str(Path.home() / ".ssh" / "id_rsa.als")}, 12)
    check("a sensitive directory is refused", "path_denied" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:140])

    response = client.call("projects_arrangement_shapes", {"roots": ["/etc"], "limit": 1}, 13)
    check("scan roots are restricted too", "path_outside_allowed_roots" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:140])

    # --- pagination ---
    response = client.request("tools/list", {}, 100)
    first_page = response["result"]["tools"]
    check("tools/list is paginated", len(first_page) == 10, len(first_page))
    check("a nextCursor is given", "nextCursor" in response["result"], list(response["result"]))
    seen = list(first_page)
    cursor = response["result"].get("nextCursor")
    pages = 1
    while cursor:
        response = client.request("tools/list", {"cursor": cursor}, 100 + pages)
        seen.extend(response["result"]["tools"])
        cursor = response["result"].get("nextCursor")
        pages += 1
    names_seen = [tool["name"] for tool in seen]
    check("the pages cover every tool", len(names_seen) == len(set(names_seen)) and len(names_seen) >= 28, len(names_seen))
    response = client.request("tools/list", {"cursor": "!!bozuk!!"}, 120)
    check("a broken cursor returns -32602", response.get("error", {}).get("code") == -32602, response)
    response = client.request("ping", {}, 121)
    check("the server stays up after a broken cursor", response.get("result") == {}, response)

    # --- 5) resources ---
    response = client.request("resources/list", {}, 14)
    resources = response["result"]["resources"]
    check("resources are listed", len(resources) >= 3, len(resources))
    check("every resource has a uri and a mimeType", all("uri" in r and "mimeType" in r for r in resources), resources[:1])
    check("resource entries do not leak internal file paths", all("path" not in r for r in resources), resources[:1])

    uri = resources[0]["uri"]
    response = client.request("resources/read", {"uri": uri}, 15)
    check("a resource can be read", response["result"]["contents"][0]["uri"] == uri, response.get("result"))
    check("the resource content is not empty", len(response["result"]["contents"][0]["text"]) > 100)

    response = client.request("resources/read", {"uri": "loom://yok/boyle"}, 16)
    check("an unknown resource returns an error", "error" in response, response)

    # --- 5) prompts ---
    response = client.request("prompts/list", {}, 17)
    prompts = response["result"]["prompts"]
    check("prompts are listed", len(prompts) >= 3, len(prompts))
    check("the prompt template is not leaked to the client", all("template" not in p for p in prompts), prompts[:1])

    response = client.request("prompts/get", {"name": "audit_project", "arguments": {"als_path": "/tmp/x.als"}}, 18)
    check("a prompt is produced", "/tmp/x.als" in response["result"]["messages"][0]["content"]["text"], response.get("result"))

    response = client.request("prompts/get", {"name": "audit_project", "arguments": {}}, 19)
    check("a missing prompt argument is refused", "error" in response, response)

    response = client.request("prompts/get", {"name": "__yok__", "arguments": {}}, 20)
    check("an unknown prompt is refused", "error" in response, response)

    # --- 6) response discipline ---
    if SAMPLE_ALS.exists():
        response = client.call("project_analyze_mixer", {"als_path": str(SAMPLE_ALS)}, 21)
        check("structured content comes back too", "structuredContent" in response["result"], list(response["result"]))
        for block in response["result"]["content"]:
            check("the text block is under the limit", len(block["text"]) <= 24000 or block["text"].startswith("[truncated]"), len(block["text"]))
            break

    response = client.call("palette_read", {}, 22)
    blocks = response["result"]["content"]
    total = sum(len(block["text"]) for block in blocks)
    check("a large response is truncated or stays within the limit",
          all(len(b["text"]) <= 24000 or b["text"].startswith("[truncated]") for b in blocks), total)

    # --- 4) concurrency, progress and cancellation ---
    started = time.time()
    client.send({"jsonrpc": "2.0", "id": 30, "method": "tools/call", "params": {
        "name": "projects_arrangement_shapes",
        "arguments": {"roots": [str(Path.home() / "Desktop" / "solo")], "limit": 25},
        "_meta": {"progressToken": "tok1"},
    }})
    time.sleep(0.6)
    client.send({"jsonrpc": "2.0", "id": 31, "method": "ping", "params": {}})
    ping_at = long_at = None
    progress_count = 0
    while long_at is None:
        message = client.read()
        if message is None:
            break
        if message.get("method") == "notifications/progress":
            progress_count += 1
            check("the progress notification carries its token", message["params"].get("progressToken") == "tok1") if progress_count == 1 else None
        elif message.get("id") == 31:
            ping_at = time.time() - started
        elif message.get("id") == 30:
            long_at = time.time() - started
    check("ping is answered during a long call", ping_at is not None and long_at is not None and ping_at < long_at, (ping_at, long_at))
    check("progress notifications are sent", progress_count > 5, progress_count)

    client.send({"jsonrpc": "2.0", "id": 40, "method": "tools/call", "params": {
        "name": "projects_arrangement_shapes",
        "arguments": {"roots": [str(Path.home() / "Desktop")], "limit": 60},
    }})
    time.sleep(0.5)
    client.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 40}})
    cancelled_response = None
    while cancelled_response is None:
        message = client.read()
        if message is None:
            break
        if message.get("id") == 40:
            cancelled_response = message
    text = cancelled_response["result"]["content"][0]["text"] if cancelled_response else ""
    check("a cancelled call is stopped", "cancelled_by_client" in text, text[:140])
finally:
    client.close()

print("%d checks passed:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print()
    print("FAILED:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("PROTOCOL CONFORMANT")
