# -*- coding: utf-8 -*-
"""Локальный HTTP-обзор цеха: плитки станков, как в Veyon Master."""

import json
import os
import posixpath
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


WEB_DIR_NAME = "web"
REMOTE_TIMEOUT_SEC = 2.5


class ShopState(object):
    """Потокобезопасное состояние локального станка и список удалённых."""

    def __init__(self):
        self.lock = threading.Lock()
        self.local = {
            "id": "local",
            "name": "Станок ЧПУ",
            "online": False,
            "window_found": False,
            "has_data": False,
            "updated_at": "",
            "window_title": "",
            "process": "",
            "X": "",
            "Y": "",
            "WorkingTime": "",
            "SurplusTime": "",
            "Speed": "",
            "DetailNo": "",
            "error": "",
        }
        self.preview = b""
        self.preview_type = "image/bmp"
        self.title = "Обзор цеха ЧПУ"
        self.include_local = True
        self.remotes = []  # [{id, name, url}, ...]

    def update_local(self, fields, preview=None, preview_type=None):
        with self.lock:
            self.local.update(fields)
            if preview is not None:
                self.preview = preview
            if preview_type:
                self.preview_type = preview_type

    def snapshot_local(self):
        with self.lock:
            return dict(self.local)

    def preview_copy(self):
        with self.lock:
            return self.preview, self.preview_type


def load_machines_file(path):
    """Читает machines.json: заголовок обзора и список удалённых станков."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    remotes = []
    for item in data.get("machines") or []:
        url = (item.get("url") or "").strip().rstrip("/")
        ident = (item.get("id") or "").strip()
        if not url or not ident:
            continue
        remotes.append({
            "id": ident,
            "name": item.get("name") or ident,
            "url": url,
        })
    title = data.get("title") or ""
    return title, remotes


def _json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _fetch_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=REMOTE_TIMEOUT_SEC) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _fetch_bytes(url):
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=REMOTE_TIMEOUT_SEC) as resp:
        content_type = resp.headers.get("Content-Type") or "application/octet-stream"
        return resp.read(), content_type


def _remote_tile(remote):
    tile = {
        "id": remote["id"],
        "name": remote["name"],
        "online": False,
        "window_found": False,
        "has_data": False,
        "updated_at": "",
        "window_title": "",
        "process": "",
        "X": "",
        "Y": "",
        "WorkingTime": "",
        "SurplusTime": "",
        "Speed": "",
        "DetailNo": "",
        "error": "",
        "preview_url": "/api/preview?machine=%s" % remote["id"],
    }
    try:
        payload = _fetch_json(remote["url"] + "/api/state")
        if isinstance(payload, dict):
            tile.update(payload)
        tile["id"] = remote["id"]
        tile["name"] = remote["name"]
        tile["preview_url"] = "/api/preview?machine=%s" % remote["id"]
        tile["online"] = True
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError, TimeoutError) as exc:
        tile["error"] = str(exc)
        tile["online"] = False
    return tile


def build_fleet(state):
    """Собирает плитки: локальный станок + удалённые мониторы из machines.json."""
    machines = []
    with state.lock:
        title = state.title
        include_local = state.include_local
        remotes = list(state.remotes)
        local = dict(state.local)

    if include_local:
        local_tile = dict(local)
        local_tile["preview_url"] = "/api/preview?machine=%s" % local.get("id", "local")
        machines.append(local_tile)

    if remotes:
        workers = min(8, len(remotes))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            machines.extend(pool.map(_remote_tile, remotes))

    return {"title": title, "machines": machines}


def lookup_remote(state, machine_id):
    with state.lock:
        remotes = list(state.remotes)
        local_id = state.local.get("id") or "local"
    if not machine_id or machine_id == local_id or machine_id == "local":
        return None
    for remote in remotes:
        if remote["id"] == machine_id:
            return remote
    return None


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CncShopOverview/1.0"

    def log_message(self, fmt, *args):
        # Тише стандартного access-лога http.server
        return

    def _send(self, code, body, content_type, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload, code=200):
        self._send(code, _json_bytes(payload), "application/json; charset=utf-8")

    def do_GET(self):
        parsed = self.path.split("?", 1)
        path = parsed[0]
        query = parsed[1] if len(parsed) > 1 else ""
        params = {}
        if query:
            for part in query.split("&"):
                if "=" in part:
                    key, value = part.split("=", 1)
                    params[key] = urllib.parse.unquote(value)

        state = self.server.shop_state

        if path == "/api/state":
            self._send_json(state.snapshot_local())
            return
        if path == "/api/fleet":
            self._send_json(build_fleet(state))
            return
        if path == "/api/preview":
            self._serve_preview(state, params.get("machine") or "local")
            return
        self._serve_static(path)

    def _serve_preview(self, state, machine_id):
        remote = lookup_remote(state, machine_id)
        if remote is None:
            data, content_type = state.preview_copy()
            if not data:
                self._send(404, b"no preview", "text/plain; charset=utf-8")
                return
            self._send(200, data, content_type)
            return
        try:
            data, content_type = _fetch_bytes(remote["url"] + "/api/preview")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            self._send(502, b"preview unavailable", "text/plain; charset=utf-8")
            return
        self._send(200, data, content_type)

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        rel = posixpath.normpath(path).lstrip("/")
        if rel.startswith("..") or "/../" in "/%s/" % rel:
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return
        web_root = os.path.abspath(self.server.web_root)
        full = os.path.abspath(os.path.join(web_root, rel.replace("/", os.sep)))
        if not full.startswith(web_root + os.sep) and full != web_root:
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return
        if not os.path.isfile(full):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(full)[1].lower()
        types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".bmp": "image/bmp",
            ".ico": "image/x-icon",
            ".svg": "image/svg+xml",
        }
        with open(full, "rb") as fh:
            body = fh.read()
        self._send(200, body, types.get(ext, "application/octet-stream"))


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, shop_state, web_root):
        self.shop_state = shop_state
        self.web_root = web_root
        ThreadingHTTPServer.__init__(self, server_address, DashboardHandler)


def start_dashboard(shop_state, host, port, web_root):
    """Запускает HTTP-сервер в фоне. Возвращает (server, thread, bound_port)."""
    server = DashboardServer((host, port), shop_state, web_root)
    thread = threading.Thread(target=server.serve_forever, name="shop-dashboard")
    thread.daemon = True
    thread.start()
    bound_port = server.server_address[1]
    return server, thread, bound_port
