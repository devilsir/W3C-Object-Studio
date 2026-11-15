from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
import hashlib
import json
import logging
import mimetypes
import shutil
import tempfile
import threading
import uuid
import webbrowser

_SERVER = None
_THREAD = None
_VIEWER_ROOT = Path(__file__).resolve().parent.parent / "viewer"
_SESSIONS: "OrderedDict[str, Path]" = OrderedDict()
_SCENES: dict[str, dict] = {}
_LOCK = threading.RLock()
_MAX_SESSIONS = 16
_URL_CACHE: "OrderedDict[str, tuple[str, str]]" = OrderedDict()
LOGGER = logging.getLogger("wc3studio")


def _session(sid: str | None) -> Path | None:
    if not sid:
        return None
    with _LOCK:
        path = _SESSIONS.get(sid)
        if path and path.exists():
            _SESSIONS.move_to_end(sid)
            return path
    return None


def _cleanup_sessions() -> None:
    with _LOCK:
        removed=set()
        while len(_SESSIONS) > _MAX_SESSIONS:
            sid, path = _SESSIONS.popitem(last=False)
            removed.add(sid)
            _SCENES.pop(sid, None)
            shutil.rmtree(path, ignore_errors=True)
        if removed:
            for key,(sid,_url) in list(_URL_CACHE.items()):
                if sid in removed:
                    _URL_CACHE.pop(key,None)


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    @staticmethod
    def _safe_session_path(root: Path, rel: str) -> Path | None:
        root = root.resolve()
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def _json(self, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/session_manifest.json":
            sid = (query.get("sid") or [""])[0]
            root = _session(sid)
            manifest = {"sid": sid, "files": []}
            if root:
                files = []
                for p in root.rglob("*"):
                    if p.is_file():
                        files.append(p.relative_to(root).as_posix())
                manifest["files"] = sorted(files)
            self._json(manifest)
            return
        if path == "/session_scene.json":
            sid = (query.get("sid") or [""])[0]
            with _LOCK:
                scene = dict(_SCENES.get(sid) or {})
            self._json({"sid": sid, "scene": scene})
            return
        if path.startswith("/session/"):
            bits = path[len("/session/"):].split("/", 1)
            if len(bits) != 2:
                self.send_error(404)
                return
            sid, rel = bits[0], unquote(bits[1])
            root = _session(sid)
            target = self._safe_session_path(root, rel) if root else None
            if not target or not target.is_file():
                self.send_error(404)
                return
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "public, max-age=3600, immutable")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return super().do_GET()

    def do_POST(self):
        parsed=urlparse(self.path)
        if parsed.path != "/viewer_log":
            self.send_error(404)
            return
        try:
            length=min(int(self.headers.get("Content-Length","0") or 0),65536)
            payload=json.loads(self.rfile.read(length).decode("utf-8","replace") or "{}")
            level=str(payload.get("level","info")).casefold()
            event=str(payload.get("event","viewer"))[:120]
            detail=payload.get("detail",{})
            message="VIEWER_JS %s %s" % (event, json.dumps(detail,ensure_ascii=False,default=str)[:12000])
            if level=="error": LOGGER.error(message)
            elif level=="warning": LOGGER.warning(message)
            else: LOGGER.info(message)
            self._json({"ok":True})
        except Exception as exc:
            LOGGER.debug("viewer_log POST failed: %s",exc,exc_info=True)
            self.send_response(204); self.end_headers()

    def translate_path(self, path):
        parsed = urlparse(path).path.lstrip("/")
        if not parsed:
            parsed = "index.html"
        root = _VIEWER_ROOT.resolve()
        target = (root / parsed).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return str(root / "404")
        return str(target)


class _PooledHTTPServer(HTTPServer):
    """Small bounded request pool for the embedded viewer.

    The previous ``ThreadingHTTPServer`` created one native thread for every model,
    texture and JS log request.  A single FX preview with 45 assets produced dozens
    of short-lived Windows threads; a few minutes of browsing reached Thread-800+.
    Keep browser parallelism, but cap the native thread footprint.
    """
    allow_reuse_address = True

    def __init__(self, server_address, handler_cls, *, max_workers: int = 8):
        super().__init__(server_address, handler_cls)
        self.max_workers = max(2, int(max_workers))
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="wc3-viewer-req")
        self._stats_lock = threading.Lock()
        self._active_requests = 0
        self._peak_requests = 0
        self._request_count = 0

    def process_request(self, request, client_address):
        try:
            self._executor.submit(self._process_request_pooled, request, client_address)
        except Exception:
            self.shutdown_request(request)
            raise

    def _process_request_pooled(self, request, client_address):
        with self._stats_lock:
            self._active_requests += 1
            self._request_count += 1
            self._peak_requests = max(self._peak_requests, self._active_requests)
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            try:
                self.shutdown_request(request)
            finally:
                with self._stats_lock:
                    self._active_requests = max(0, self._active_requests - 1)

    def request_stats(self) -> dict[str, int]:
        with self._stats_lock:
            return {
                "active": self._active_requests,
                "peak": self._peak_requests,
                "total": self._request_count,
                "workers": self.max_workers,
            }

    def server_close(self):
        try:
            super().server_close()
        finally:
            self._executor.shutdown(wait=False, cancel_futures=True)


def _ensure_server():
    global _SERVER, _THREAD
    if _SERVER is not None:
        return _SERVER
    _SERVER = _PooledHTTPServer(("127.0.0.1", 0), _Handler, max_workers=8)
    _THREAD = threading.Thread(target=_SERVER.serve_forever, daemon=True, name="wc3-viewer-http")
    _THREAD.start()
    LOGGER.info("Viewer HTTP server ready: %s workers=%d", _SERVER.server_address, _SERVER.max_workers)
    return _SERVER


def _viewer_source_key(files, main_model, scene, compact: bool) -> str:
    rows=[]
    candidates=list(files or [])
    if main_model:
        candidates.append(main_model)
    seen=set()
    for item in candidates:
        try:
            p=Path(item).resolve()
            key=str(p).casefold()
            if key in seen or not p.is_file():
                continue
            seen.add(key)
            st=p.stat()
            rows.append((str(p),int(st.st_size),int(st.st_mtime_ns)))
        except Exception:
            rows.append((str(item),0,0))
    scene_text=json.dumps(scene or {},sort_keys=True,ensure_ascii=False,default=str)
    raw=json.dumps({"files":rows,"main":str(main_model or ""),"scene":scene_text,"compact":bool(compact)},sort_keys=True,ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8","replace")).hexdigest()


def prepare_viewer_url(
    files: list[str | Path] | None = None,
    main_model: str | Path | None = None,
    *,
    compact: bool = False,
    scene: dict | None = None,
) -> str:
    """Create an isolated viewer session and return its localhost URL.

    ``scene`` is optional metadata for richer previews.  In ability-test mode it
    can describe a hero model plus one or more spell-effect models.  Paths in
    ``hero_model`` and each effect ``model`` entry are converted to the copied
    session filenames so the browser never needs access to the user's disk.
    """
    source_key=_viewer_source_key(files,main_model,scene,compact)
    with _LOCK:
        cached=_URL_CACHE.get(source_key)
        if cached:
            cached_sid,cached_url=cached
            if _session(cached_sid):
                _URL_CACHE.move_to_end(source_key)
                LOGGER.debug("Viewer session cache hit sid=%s",cached_sid)
                return cached_url
            _URL_CACHE.pop(source_key,None)

    sid = uuid.uuid4().hex
    session_dir = Path(tempfile.mkdtemp(prefix=f"wc3_model_view_{sid[:8]}_"))

    copied: dict[Path, str] = {}
    used_names: set[str] = set()

    def copy_one(item: str | Path) -> str | None:
        src = Path(item)
        if not src.is_file():
            return None
        resolved = src.resolve()
        if resolved in copied:
            return copied[resolved]
        name = src.name
        stem, suffix = src.stem, src.suffix
        n = 2
        key = name.casefold()
        while key in used_names or (session_dir / name).exists():
            name = f"{stem}_{n}{suffix}"
            key = name.casefold()
            n += 1
        shutil.copy2(src, session_dir / name)
        copied[resolved] = name
        used_names.add(key)
        return name

    for item in files or []:
        copy_one(item)

    model_name = copy_one(main_model) if main_model else None

    scene_out: dict = {}
    if scene:
        scene_out = {k: v for k, v in scene.items() if k not in {"hero_model", "effects", "actors"}}
        hero = scene.get("hero_model")
        if hero:
            scene_out["hero_model"] = copy_one(hero)
        actor_rows = []
        for item in scene.get("actors") or []:
            row = dict(item)
            model_path = row.pop("model", None)
            copied_name = copy_one(model_path) if model_path else None
            if copied_name:
                row["model"] = copied_name
                actor_rows.append(row)
        if actor_rows:
            scene_out["actors"] = actor_rows
        effect_rows = []
        for item in scene.get("effects") or []:
            row = dict(item)
            model_path = row.pop("model", None)
            copied_name = copy_one(model_path) if model_path else None
            if copied_name:
                row["model"] = copied_name
                effect_rows.append(row)
        if effect_rows:
            scene_out["effects"] = effect_rows

    with _LOCK:
        _SESSIONS[sid] = session_dir
        if scene_out:
            _SCENES[sid] = scene_out
        _cleanup_sessions()

    server = _ensure_server()
    host, port = server.server_address
    session_params = [f"sid={quote(sid)}"]
    if model_name:
        session_params.append(f"model={quote(model_name)}")
    if scene_out:
        session_params.append("scene=1")
    if compact:
        # Embedded mode keeps one document/WebGL context alive. Changing only the
        # fragment triggers the viewer's hashchange loader instead of reloading
        # the whole page and vendor JS on every character/HD/SD switch.
        url=f"http://{host}:{port}/?compact=1#" + "&".join(session_params)
    else:
        url=f"http://{host}:{port}/?" + "&".join(session_params)
    with _LOCK:
        _URL_CACHE[source_key]=(sid,url)
        _URL_CACHE.move_to_end(source_key)
        while len(_URL_CACHE)>_MAX_SESSIONS*2:
            _URL_CACHE.popitem(last=False)
    stats=_SERVER.request_stats() if isinstance(_SERVER,_PooledHTTPServer) else {}
    LOGGER.debug(
        "Viewer session prepared sid=%s files=%d compact=%s sessions=%d http_active=%s http_peak=%s http_total=%s workers=%s",
        sid,len(list(files or [])),compact,len(_SESSIONS),stats.get("active","-"),stats.get("peak","-"),stats.get("total","-"),stats.get("workers","-")
    )
    return url


def launch_viewer(
    files: list[str | Path] | None = None,
    main_model: str | Path | None = None,
    *,
    scene: dict | None = None,
):
    """Open the bundled full viewer in the system browser."""
    url = prepare_viewer_url(files, main_model, compact=False, scene=scene)
    webbrowser.open(url)
    return url
