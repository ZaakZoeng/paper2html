"""Single-user local web application. Bind to loopback; use an SSH tunnel remotely."""
import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import threading
import uuid
import shutil
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .agent import Agent
from .provider import CompatibleProvider, ModelConfig, ProviderError
from .store import Store
from . import __version__

WEB = Path(__file__).parent / "web"
MAX_PDF = 25 * 1024 * 1024
MAX_BODY = 36 * 1024 * 1024
METADATA_FIELDS = {"title", "authors", "journal", "journal_abbr", "publication_date", "doi", "short_name"}


def version_status():
    """Compare loaded code with the release on disk without importing new code."""
    source_version = None
    try:
        source = Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
        match = re.search(r'''^__version__\s*=\s*["']([^"']+)["']''', source, re.MULTILINE)
        if match:
            source_version = match.group(1)
    except OSError:
        pass
    return {'version': __version__, 'source_version': source_version,
            'restart_required': source_version is not None and source_version != __version__}


def model_config(cfg):
    if not isinstance(cfg, dict):
        raise ValueError('config 必须是对象')
    return ModelConfig(base_url=cfg.get('base_url') or os.environ.get('READ4MD_BASE_URL', ''),
        model=cfg.get('model') or os.environ.get('READ4MD_MODEL', ''),
        api_key=cfg.get('api_key') or os.environ.get('READ4MD_API_KEY', ''),
        max_tokens=int(cfg.get('max_tokens', 8000)), timeout=int(cfg.get('timeout', 180)),
        json_mode=bool(cfg.get('json_mode', False)), vision=bool(cfg.get('vision', True)),
        metadata_lookup=bool(cfg.get('metadata_lookup', True)))


class Application:
    def __init__(self, data_dir, workers=2, reports_dir=None):
        self.store = Store(data_dir, reports_dir)
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="paper-agent")
        self.slots = threading.BoundedSemaphore(workers + 2)
        self.token = secrets.token_urlsafe(32)

    def submit(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("请求必须是 JSON 对象")
        cfg = payload.get("config", {})
        if not isinstance(cfg, dict):
            raise ValueError("config 必须是对象")
        config = model_config(cfg)
        overrides = payload.get("metadata") or {}
        if not isinstance(overrides, dict) or set(overrides) - METADATA_FIELDS or any(not isinstance(v, str) or not v.strip() for v in overrides.values()):
            raise ValueError("补充出版信息只能包含指定字段的非空字符串")
        figure_plan = payload.get('figure_plan')
        if figure_plan is not None and (not isinstance(figure_plan,dict) or len(json.dumps(figure_plan)) > 100000):
            raise ValueError('figure_plan 必须是小于100 KB的JSON对象')
        encoded = payload.get("pdf_base64")
        if not isinstance(encoded, str) or len(encoded) > MAX_BODY:
            raise ValueError("请上传不超过 25 MB 的 PDF")
        try:
            pdf = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("PDF 编码无效") from None
        if len(pdf) > MAX_PDF or not pdf.startswith(b"%PDF-"):
            raise ValueError("文件必须是小于 25 MB 的有效 PDF")
        filename = payload.get("filename", "paper.pdf")
        if not isinstance(filename, str):
            raise ValueError("文件名格式错误")
        filename = Path(filename).name[:180]
        regenerate = payload.get("regenerate", False)
        if type(regenerate) is not bool:
            raise ValueError("regenerate 必须是布尔值")
        independent = payload.get('independent', False)
        if type(independent) is not bool or independent and regenerate:
            raise ValueError('独立对比与替换报告不能同时选择')
        safe_config = asdict(config)
        safe_config.pop('api_key')
        pdf_hash = hashlib.sha256(pdf).hexdigest()
        identity = {'pdf': pdf_hash, 'config': safe_config, 'metadata': overrides, 'figure_plan': figure_plan}
        def fingerprint_for(config=None, **schema):
            return hashlib.sha256(json.dumps({**identity, 'config': config or safe_config, **schema}, sort_keys=True,
                ensure_ascii=False).encode('utf-8')).hexdigest()
        fingerprint = fingerprint_for(schema=1)
        resume_from = payload.get('resume_from') or None
        previous = None
        if resume_from is not None:
            if not isinstance(resume_from, str) or not re.fullmatch('[a-f0-9]{32}', resume_from):
                raise ValueError('续跑任务 ID 无效')
            previous = self.store.get(resume_from)
            if not previous or previous['status'] != 'failed':
                raise ValueError('只能续跑已失败或重启中断的任务')
            compatible = {fingerprint}
            legacy_config = {k: v for k, v in safe_config.items() if k != 'metadata_lookup'}
            if config.metadata_lookup and previous.get('version') == '0.3.0':
                compatible.add(fingerprint_for(config=legacy_config, version='0.3.0'))
            if config.metadata_lookup and previous.get('version') == '0.3.1':
                compatible.add(fingerprint_for(config=legacy_config, schema=1))
            if previous.get('fingerprint') not in compatible:
                raise ValueError('续跑配置不一致或旧任务没有检查点：请使用相同 PDF、接口、模型、选项和补充信息，或清空续跑 ID 重新运行')
            independent, regenerate = previous.get('independent', False), previous.get('regenerate', False)
        if not self.slots.acquire(blocking=False):
            raise RuntimeError("任务队列已满，请稍后重试")
        job_id = uuid.uuid4().hex
        try:
            self.store.create(job_id, filename, config.model)
            self.store.update(job_id, independent=independent, regenerate=regenerate, fingerprint=fingerprint,
                              endpoint=config.base_url, resume_from=resume_from, version=__version__)
            if not regenerate and not independent:
                existing = self.store.claim("sha256:" + hashlib.sha256(pdf).hexdigest(), job_id)
                if existing:
                    self.store.update(job_id, status="duplicate", stage="相同 PDF 已有任务", existing_job=existing)
                    self.slots.release()
                    return self.store.get(job_id)
            work = self.store.root / job_id
            work.mkdir(mode=0o700)
            (work / "source.pdf").write_bytes(pdf)
            if previous:
                checkpoints = self.store.root/resume_from/'checkpoints'
                if checkpoints.is_dir():
                    shutil.copytree(checkpoints, work/'checkpoints')
            future = self.pool.submit(Agent(self.store, CompatibleProvider(config)).run, job_id, overrides, regenerate, figure_plan)
            future.add_done_callback(lambda _: self.slots.release())
            return self.store.get(job_id)
        except Exception:
            self.slots.release()
            self.store.release(job_id)
            if self.store.get(job_id):
                self.store.update(job_id, status="failed", stage="任务创建失败", error="请检查磁盘空间或服务状态")
            raise


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Paper2HTML/" + __version__

        def log_message(self, fmt, *args):
            pass  # Do not log user inputs, URLs or secrets.

        def allowed_host(self):
            return self.headers.get("Host", "") in {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}

        def send(self, status, body, content_type="application/json; charset=utf-8", download=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            if isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if download:
                self.send_header("Content-Disposition", 'attachment; filename="' + download + '"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, {"error": "仅允许本机地址访问"})
            path = urlsplit(self.path).path
            if path == "/api/config":
                return self.send(200, {**version_status(), "csrf_token": app.token, "max_pdf_mb": 25, "defaults": {"base_url":os.environ.get("READ4MD_BASE_URL", ""), "model":os.environ.get("READ4MD_MODEL", "")}})
            if path == '/api/jobs':
                return self.send(200, {'jobs': app.store.list_jobs()})
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in static:
                filename, content_type = static[path]
                return self.send(200, (WEB / filename).read_bytes(), content_type)
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})(?:/files/(markdown|html|evidence|content|validation|figures|provenance|bundle|diagnostics))?", path)
            if match:
                job_id, artifact = match.groups()
                job = app.store.get(job_id)
                if not job:
                    return self.send(404, {"error": "任务不存在"})
                if artifact is None:
                    return self.send(200, job)
                if artifact == 'diagnostics':
                    diagnostic = app.store.root/job_id/'diagnostics.jsonl'
                    if diagnostic.is_file():
                        return self.send(200, diagnostic.read_bytes(), 'application/x-ndjson; charset=utf-8', 'diagnostics.jsonl')
                    return self.send(404, {'error': '尚无校验诊断记录'})
                name = job.get("artifacts", {}).get(artifact)
                if job["status"] != "completed" or not name:
                    return self.send(404, {"error": "结果尚不可用"})
                base = app.store.root / job.get('output_dir',job_id)
                filepath = (app.store.root / job_id / name) if artifact == 'bundle' else (base / name)
                filepath = filepath.resolve()
                if not filepath.is_relative_to(app.store.root):
                    return self.send(404, {"error":"无效的结果路径"})
                if not filepath.is_file():
                    return self.send(404, {"error": "结果文件已被移除，请重新生成"})
                content_type = {"markdown": "text/markdown; charset=utf-8", "html": "text/html; charset=utf-8", "bundle":"application/zip"}.get(artifact, "application/json; charset=utf-8")
                download = name if artifact != "html" or urlsplit(self.path).query == "download=1" else None
                return self.send(200, filepath.read_bytes(), content_type, download)
            self.send(404, {"error": "路径不存在"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.allowed_host() or self.headers.get("X-Paper2HTML-Token") != app.token or (origin and origin != "http://" + self.headers.get("Host", "")):
                return self.send(403, {"error": "请求来源或会话令牌无效，请刷新页面"})
            if self.path not in ('/api/jobs', '/api/test'):
                return self.send(404, {"error": "路径不存在"})
            if self.path == '/api/jobs' and version_status()['restart_required']:
                return self.send(409, {"error": "项目已更新，当前后台服务仍在运行旧版本。请在启动服务的终端按 Ctrl+C，重新执行启动命令并刷新网页后续跑。"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "请发送 application/json"})
            payload = {}
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_BODY:
                    return self.send(413, {"error": "请求体为空或超过 36 MB"})
                self.connection.settimeout(30)
                payload = json.loads(self.rfile.read(length))
                if self.path == '/api/test':
                    if not isinstance(payload, dict):
                        raise ValueError('请求必须是 JSON 对象')
                    config = model_config(payload.get('config', {}))
                    config.max_tokens = 1024
                    if not app.slots.acquire(blocking=False):
                        raise RuntimeError('任务队列已满，请稍后重试')
                    try:
                        provider = CompatibleProvider(config)
                        result = provider.complete_json('只输出 JSON 对象。', '请返回 {"ok":true}')
                        if result.get('ok') is not True:
                            raise ValueError('接口可连接，但模型未返回预期 JSON')
                        return self.send(200, {'ok': True, 'usage': provider.usage})
                    finally:
                        app.slots.release()
                job = app.submit(payload)
                self.send(202, job)
            except ValueError as exc:
                key = str(payload.get('config', {}).get('api_key', '')) if isinstance(payload, dict) and isinstance(payload.get('config'), dict) else ''
                message = str(exc) if not isinstance(exc, json.JSONDecodeError) else '请求不是有效 JSON'
                self.send(400, {"error": message.replace(key, '[REDACTED]') if key else message})
            except (TypeError, AttributeError):
                self.send(400, {"error": "请求参数无效，请检查 PDF、API 地址、模型、token 上限和补充信息格式"})
            except ProviderError as exc:
                self.send(502, {'error': str(exc)})
            except RuntimeError:
                self.send(429, {"error": "任务队列已满，请稍后重试"})
            except (TimeoutError, ConnectionError):
                self.close_connection = True
            except Exception:
                self.send(500, {"error": "服务无法创建任务，请检查本地磁盘及运行环境"})

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the local Paper2HTML agent")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port 必须为 0–65535；0 表示自动分配可用端口')
    os.umask(0o077)
    from .locking import directory_lock
    # Hold a cross-platform process lock before job recovery.
    args.data_dir.mkdir(parents=True,exist_ok=True)
    try:
        with directory_lock(args.data_dir/'.server.lock'):
            app = Application(args.data_dir, args.workers, args.reports_dir)
            try:
                server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(app))
            except OSError as exc:
                app.pool.shutdown(wait=True)
                parser.exit(1, f'无法监听 127.0.0.1:{args.port}：{exc}\n'
                            '该端口可能被占用、被系统保留或受到安全策略限制。\n'
                            '请尝试 --port 18765，或 --port 0 自动分配端口；以启动后打印的 URL 为准。\n'
                            'Windows 可运行 netsh interface ipv4 show excludedportrange protocol=tcp 检查保留范围。\n')
            app.store.recover()
            print(f"Read4MD / Paper2HTML v{__version__}: http://127.0.0.1:{server.server_port}", flush=True)
            print(f"报告目录: {app.store.reports}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\n正在等待运行中的任务结束…", flush=True)
            finally:
                server.server_close()
                app.pool.shutdown(wait=True)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
