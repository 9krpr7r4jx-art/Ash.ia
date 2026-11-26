"""
Ash.ia — lanceur tout-en-un.
Usage : python ash.py
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import urllib.error
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------- CONFIG ----------
OLLAMA_URL   = "http://127.0.0.1:11434/api/chat"
OLLAMA_TAGS  = "http://127.0.0.1:11434/api/tags"
# 3B est le meilleur compromis pour 8 Go de RAM
DEFAULT_MODEL = "qwen2.5-coder:3b" 
HOST      = "127.0.0.1"
API_PORT  = 8000
SITE_PORT = 8080
BASE_DIR   = Path(__file__).parent.resolve()
INDEX_FILE = BASE_DIR / "index.html"

# Options Ollama adaptées à la RAM et à la vitesse
OLLAMA_OPTIONS = {
    "temperature": 0.6,       
    "top_p": 0.9,
    "top_k": 40,
    "repeat_penalty": 1.1,
    "num_predict": 2048,      # Assez pour du code long, mais évite de surcharger
    "num_ctx": 4096,          # Réduit pour épargner la RAM (8 Go)
}

def log(msg):
    print(f"[Ash.ia] {msg}", flush=True)

def http_get_ok(url, timeout=1.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False

def wait_for(url, label, max_seconds=15):
    log(f"Waiting for {label} ({url})...")
    start = time.time()
    while time.time() - start < max_seconds:
        if http_get_ok(url):
            log(f"{label} ready ✔")
            return True
        time.sleep(0.5)
    log(f"⚠ {label} not responding after {max_seconds}s")
    return False

def port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((HOST, port))
        return True
    except OSError:
        return False
    finally:
        s.close()

def ensure_ollama():
    if http_get_ok(OLLAMA_TAGS):
        log("Ollama already running ✔")
        return None
    log("Starting Ollama...")
    env = os.environ.copy()
    env["OLLAMA_ORIGINS"] = "*"
    kwargs = {"env": env, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(["ollama", "serve"], **kwargs)
    except FileNotFoundError:
        log("❌ Ollama not found. Install it from https://ollama.com")
        sys.exit(1)
    if not wait_for(OLLAMA_TAGS, "Ollama", 30):
        log("❌ Ollama failed to start.")
        sys.exit(1)
    return proc

def check_model():
    try:
        with urllib.request.urlopen(OLLAMA_TAGS, timeout=5) as r:
            data = json.loads(r.read())
        names = [m.get("name", "") for m in data.get("models", [])]
        base = DEFAULT_MODEL.split(":")[0]
        if any(DEFAULT_MODEL in n or n.startswith(base) for n in names):
            log(f"Model {DEFAULT_MODEL} present ✔")
            return
        log(f"Model {DEFAULT_MODEL} missing — downloading (may take a while)...")
        subprocess.run(["ollama", "pull", DEFAULT_MODEL], check=False)
    except Exception as e:
        log(f"⚠ Could not check model: {e}")

class AshAPI(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def log_message(self, fmt, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as e:
            self._error_json(400, f"Invalid JSON: {e}")
            return

        model    = data.get("model") or DEFAULT_MODEL
        messages = data.get("messages", [])

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": True,
            "options": OLLAMA_OPTIONS
        }, ensure_ascii=False).encode("utf-8")

        try:
            req = urllib.request.Request(
                OLLAMA_URL, data=payload,
                headers={"Content-Type": "application/json"}
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self._cors()
            self.end_headers()

            with urllib.request.urlopen(req, timeout=600) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        out = json.dumps({"error": chunk["error"]}, ensure_ascii=False) + "\n"
                        self.wfile.write(out.encode("utf-8")); self.wfile.flush()
                        break
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        out = json.dumps({"content": piece}, ensure_ascii=False) + "\n"
                        self.wfile.write(out.encode("utf-8")); self.wfile.flush()
                    if chunk.get("done"):
                        break
            self.wfile.write(b'{"done": true}\n')
            self.wfile.flush()
        except urllib.error.URLError as e:
            self._error_json(502, f"Ollama unreachable: {e.reason}")
        except Exception as e:
            self._error_json(502, str(e))

    def _error_json(self, code, msg):
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({"error": msg}, ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass

class SiteHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            file_path = INDEX_FILE
        else:
            file_path = (BASE_DIR / path.lstrip("/")).resolve()
            try:
                file_path.relative_to(BASE_DIR)
            except ValueError:
                self.send_error(403)
                return
        if not file_path.is_file():
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css":  "text/css; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg":  "image/svg+xml",
            ".png":  "image/png",
            ".ico":  "image/x-icon",
        }.get(file_path.suffix.lower(), "application/octet-stream")
        try:
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self.send_error(500)

def run_server(handler, port, name):
    try:
        server = ThreadingHTTPServer((HOST, port), handler)
    except OSError as e:
        log(f"❌ {name}: cannot open port {port} — {e}")
        return
    log(f"{name} on http://{HOST}:{port}")
    try:
        server.serve_forever()
    except Exception:
        log(f"❌ {name} crashed:")
        traceback.print_exc()

def main():
    print("=" * 40)
    print("        Ash.ia — Starting")
    print("=" * 40)

    if not INDEX_FILE.exists():
        log(f"❌ index.html not found in {BASE_DIR}")
        sys.exit(1)

    if not port_free(API_PORT):
        log(f"❌ Port {API_PORT} is already in use.")
        sys.exit(1)
    if not port_free(SITE_PORT):
        log(f"❌ Port {SITE_PORT} is already in use.")
        sys.exit(1)

    ollama_proc = ensure_ollama()
    check_model()

    threading.Thread(target=run_server, args=(AshAPI, API_PORT, "API Ash.ia"), daemon=True).start()
    threading.Thread(target=run_server, args=(SiteHandler, SITE_PORT, "Site"), daemon=True).start()

    wait_for(f"http://{HOST}:{API_PORT}/health", "API", 10)
    wait_for(f"http://{HOST}:{SITE_PORT}/index.html", "Site", 10)

    url = f"http://{HOST}:{SITE_PORT}/index.html"
    log(f"Opening {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    log("Ash.ia is ready. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Shutting down...")
        if ollama_proc:
            try: ollama_proc.terminate()
            except Exception: pass
        sys.exit(0)

if __name__ == "__main__":
    # On ne lance que le serveur API, on ne démarre pas Ollama (il est sur ton PC)
    print("Ash.ia API démarrée")
    run_server(AshAPI, API_PORT, "API Ash.ia")