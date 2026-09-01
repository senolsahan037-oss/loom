#!/usr/bin/env python3
"""MCP protokol uyum testi.

Arac testi (test_mcp_tools.py) araclarin ISINI dogrular; bu dosya sunucunun
PROTOKOLU dogru konustugunu dogrular. Ayrimin sebebi somut: 171 arac kontrolu
gecerken sunucu bildirimlere yanit veriyordu ve hicbiri bunu yakalamadi.
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
        """Bir satir okur. Zaman asiminda None."""
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
    # --- 7) surum pazarligi ---
    response = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    check("istenen protokol surumu geri donuyor", response["result"]["protocolVersion"] == "2025-06-18", response["result"]["protocolVersion"])
    caps = response["result"]["capabilities"]
    check("tools yetenegi bildiriliyor", "tools" in caps, caps)
    check("resources yetenegi bildiriliyor", "resources" in caps, caps)
    check("prompts yetenegi bildiriliyor", "prompts" in caps, caps)

    response = client.request("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, 2)
    check("desteklenmeyen surum icin kendi surumumuz donuyor", response["result"]["protocolVersion"] == "2025-06-18", response["result"]["protocolVersion"])

    # --- 1) bildirimler ---
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    client.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 999}})
    client.send({"jsonrpc": "2.0", "method": "voidedNotificationThatDoesNotExist"})
    response = client.request("ping", {}, 3)
    check("bildirimlere YANIT VERILMIYOR", response is not None and response.get("id") == 3, response)

    # --- hatali istekler ---
    client.send_raw("{ bozuk json")
    response = client.read()
    check("bozuk JSON -32700 donuyor", response and response["error"]["code"] == -32700, response)
    client.send_raw('"gecerli json ama istek degil"')
    response = client.read()
    check("JSON ama istek olmayan mesaj -32600 donuyor", response and response["error"]["code"] == -32600, response)
    response = client.request("no/such/method", {}, 4)
    check("bilinmeyen metot -32601 donuyor", response["error"]["code"] == -32601, response)

    # --- 2) sema dogrulama ---
    response = client.call("project_inspect", {}, 5)
    text = response["result"]["content"][0]["text"]
    check("eksik zorunlu alan anlamli hata veriyor", "missing required argument" in text, text[:120])
    check("eksik alan ham KeyError sizdirmiyor", "KeyError" not in text and "'als_path'" not in text.replace("missing required argument(s): als_path", ""), text[:120])

    response = client.call("project_inspect", {"als_path": 123}, 6)
    check("yanlis tip reddediliyor", "must be string" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:120])

    response = client.call("chain_evidence", {"rol": "kick"}, 7)
    check("bilinmeyen argüman reddediliyor", "unknown argument" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:120])

    response = client.call("midi_generate", {"role": "guitar"}, 8)
    check("enum disi deger reddediliyor", "must be one of" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:120])

    response = client.call("__no_such_tool__", {}, 9)
    check("bilinmeyen arac isError donduruyor", response["result"]["isError"], response)

    # --- 3) yol kisiti ---
    response = client.call("project_inspect", {"als_path": "/etc/hosts"}, 10)
    check("izinli kok disindaki yol reddediliyor", "path_outside_allowed_roots" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:140])

    response = client.call("project_inspect", {"als_path": str(Path.home() / "Desktop" / "notes.txt")}, 11)
    check(".als olmayan dosya reddediliyor", "not_an_als_file" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:140])

    response = client.call("project_inspect", {"als_path": str(Path.home() / ".ssh" / "id_rsa.als")}, 12)
    check("hassas dizin reddediliyor", "path_denied" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:140])

    response = client.call("projects_arrangement_shapes", {"roots": ["/etc"], "limit": 1}, 13)
    check("tarama kokleri de kisitli", "path_outside_allowed_roots" in response["result"]["content"][0]["text"], response["result"]["content"][0]["text"][:140])

    # --- sayfalama ---
    response = client.request("tools/list", {}, 100)
    first_page = response["result"]["tools"]
    check("tools/list sayfalaniyor", len(first_page) == 10, len(first_page))
    check("nextCursor veriliyor", "nextCursor" in response["result"], list(response["result"]))
    seen = list(first_page)
    cursor = response["result"].get("nextCursor")
    pages = 1
    while cursor:
        response = client.request("tools/list", {"cursor": cursor}, 100 + pages)
        seen.extend(response["result"]["tools"])
        cursor = response["result"].get("nextCursor")
        pages += 1
    names_seen = [tool["name"] for tool in seen]
    check("sayfalar butun araclari kapsiyor", len(names_seen) == len(set(names_seen)) and len(names_seen) >= 24, len(names_seen))
    response = client.request("tools/list", {"cursor": "!!bozuk!!"}, 120)
    check("bozuk imlec -32602 donuyor", response.get("error", {}).get("code") == -32602, response)
    response = client.request("ping", {}, 121)
    check("bozuk imlecten sonra sunucu ayakta", response.get("result") == {}, response)

    # --- 5) resources ---
    response = client.request("resources/list", {}, 14)
    resources = response["result"]["resources"]
    check("resources listeleniyor", len(resources) >= 3, len(resources))
    check("her resource'un uri ve mimeType'i var", all("uri" in r and "mimeType" in r for r in resources), resources[:1])
    check("resource kayitlari ic dosya yolunu sizdirmiyor", all("path" not in r for r in resources), resources[:1])

    uri = resources[0]["uri"]
    response = client.request("resources/read", {"uri": uri}, 15)
    check("resource okunabiliyor", response["result"]["contents"][0]["uri"] == uri, response.get("result"))
    check("resource icerigi bos degil", len(response["result"]["contents"][0]["text"]) > 100)

    response = client.request("resources/read", {"uri": "loom://yok/boyle"}, 16)
    check("bilinmeyen resource hata donduruyor", "error" in response, response)

    # --- 5) prompts ---
    response = client.request("prompts/list", {}, 17)
    prompts = response["result"]["prompts"]
    check("promptlar listeleniyor", len(prompts) >= 3, len(prompts))
    check("prompt sablonu istemciye sizdirilmiyor", all("template" not in p for p in prompts), prompts[:1])

    response = client.request("prompts/get", {"name": "audit_project", "arguments": {"als_path": "/tmp/x.als"}}, 18)
    check("prompt uretiliyor", "/tmp/x.als" in response["result"]["messages"][0]["content"]["text"], response.get("result"))

    response = client.request("prompts/get", {"name": "audit_project", "arguments": {}}, 19)
    check("eksik prompt argümani reddediliyor", "error" in response, response)

    response = client.request("prompts/get", {"name": "__yok__", "arguments": {}}, 20)
    check("bilinmeyen prompt reddediliyor", "error" in response, response)

    # --- 6) yanit disiplini ---
    if SAMPLE_ALS.exists():
        response = client.call("project_analyze_mixer", {"als_path": str(SAMPLE_ALS)}, 21)
        check("yapisal icerik de donuyor", "structuredContent" in response["result"], list(response["result"]))
        for block in response["result"]["content"]:
            check("metin blogu limitin altinda", len(block["text"]) <= 24000 or block["text"].startswith("[truncated]"), len(block["text"]))
            break

    response = client.call("palette_read", {}, 22)
    blocks = response["result"]["content"]
    total = sum(len(block["text"]) for block in blocks)
    check("buyuk yanit kirpiliyor veya limitte kaliyor",
          all(len(b["text"]) <= 24000 or b["text"].startswith("[truncated]") for b in blocks), total)

    # --- 4) eszamanlilik + ilerleme + iptal ---
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
            check("ilerleme bildiriminde token var", message["params"].get("progressToken") == "tok1") if progress_count == 1 else None
        elif message.get("id") == 31:
            ping_at = time.time() - started
        elif message.get("id") == 30:
            long_at = time.time() - started
    check("uzun cagri sirasinda ping yanitlaniyor", ping_at is not None and long_at is not None and ping_at < long_at, (ping_at, long_at))
    check("ilerleme bildirimi gonderiliyor", progress_count > 5, progress_count)

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
    check("iptal edilen cagri durduruluyor", "cancelled_by_client" in text, text[:140])
finally:
    client.close()

print("%d kontrol gecti:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print()
    print("BASARISIZ:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("PROTOKOL UYUMLU")
