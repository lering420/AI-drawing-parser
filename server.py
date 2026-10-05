# -*- coding: utf-8 -*-
"""本地 HTTP 服务：静态页面 + 各模块 API。仅用 Python 标准库。"""
import base64
import json
import os
import re
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ai
import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
PORT = 8765

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
}

# 每次 AI 调用允许的最大图片数（DeepSeek 单请求限制内，同时控制成本）
MAX_PAGES = 12


def _json_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _images_from_payload(payload, handler):
    """校验并返回图片 data URL 列表。也支持附带 drawing_id 复用已存 PDF 的场景。"""
    images = payload.get("images") or []
    if not isinstance(images, list) or not images:
        raise ai.AIError("没有收到图纸图片。")
    if len(images) > MAX_PAGES:
        images = images[:MAX_PAGES]
    for u in images:
        if not isinstance(u, str) or not u.startswith("data:image"):
            raise ai.AIError("图片数据格式不正确。")
    return images


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---------- 基础 ----------

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data=None):
        self._send(200, {"ok": True, "data": data})

    def _err(self, msg, code=400):
        self._send(code, {"ok": False, "error": str(msg)})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path.startswith("/api/"):
                return self._api_get(path)
            return self._static(path)
        except ai.AIError as e:
            self._err(str(e), 400)
        except Exception as e:  # noqa: BLE001
            self._err("服务器内部错误：" + str(e), 500)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            if path.startswith("/api/"):
                return self._api_post(path)
            self._err("未找到接口", 404)
        except ai.AIError as e:
            self._err(str(e), 400)
        except Exception as e:  # noqa: BLE001
            self._err("服务器内部错误：" + str(e), 500)

    # ---------- 静态文件 ----------

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(PUBLIC_DIR, path.lstrip("/")))
        if not fp.startswith(PUBLIC_DIR) or not os.path.isfile(fp):
            return self._send(404, "Not Found", "text/plain; charset=utf-8")
        ext = os.path.splitext(fp)[1].lower()
        with open(fp, "rb") as f:
            body = f.read()
        self._send(200, body, MIME.get(ext, "application/octet-stream"))

    # ---------- GET API ----------

    def _api_get(self, path):
        if path == "/api/health":
            return self._ok({"status": "up"})
        if path == "/api/config":
            cfg = db.load_config()
            key = cfg.get("api_key", "")
            return self._ok({
                "has_key": bool(key),
                "key_masked": (key[:4] + "****" + key[-4:]) if len(key) > 8 else ("****" if key else ""),
                "model": cfg.get("model", "deepseek-flash"),
            })
        if path == "/api/drawings":
            return self._ok(db.list_drawings(
                status=self._qs("status") or None,
                keyword=self._qs("keyword") or "",
            ))
        m = re.fullmatch(r"/api/drawings/(\d+)", path)
        if m:
            d = db.get_drawing(int(m.group(1)))
            if not d:
                return self._err("图纸不存在", 404)
            d["issues"] = db.list_issues(d["id"])
            return self._ok(d)
        if path == "/api/issues/stats":
            return self._ok(db.issue_stats())
        if path == "/api/pdf":
            did = int(self._qs("id") or 0)
            d = db.get_drawing(did)
            if not d or not d.get("pdf_path") or not os.path.isfile(d["pdf_path"]):
                return self._err("原图文件不存在", 404)
            with open(d["pdf_path"], "rb") as f:
                body = f.read()
            return self._send(200, body, "application/pdf")
        return self._err("未找到接口", 404)

    def _qs(self, key):
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        for kv in qs.split("&"):
            if kv.startswith(key + "="):
                from urllib.parse import unquote
                return unquote(kv[len(key) + 1:])
        return None

    # ---------- POST API ----------

    def _api_post(self, path):
        payload = _json_body(self)

        if path == "/api/config":
            cfg = db.load_config()
            if "api_key" in payload:
                cfg["api_key"] = (payload.get("api_key") or "").strip()
            if payload.get("model"):
                cfg["model"] = payload["model"].strip()
            db.save_config(cfg)
            return self._ok({"saved": True})

        if path == "/api/config/test":
            msg = ai.test_key()
            return self._ok({"reply": msg})

        if path == "/api/upload":
            # PDF 以 base64 传输（浏览器端读文件）
            b64 = payload.get("pdf_base64", "")
            if not b64:
                return self._err("未收到 PDF 文件")
            name = payload.get("file_name") or "drawing.pdf"
            data = base64.b64decode(re.sub(r"^data:application/pdf;base64,", "", b64))
            fid = uuid.uuid4().hex[:12]
            fp = os.path.join(db.PDF_DIR, fid + ".pdf")
            with open(fp, "wb") as f:
                f.write(data)
            # 上传即建档（提取前先占位，参数稍后补）
            did = db.insert_drawing({
                "file_name": name, "pdf_path": fp,
                "pages": int(payload.get("pages") or 1),
                "status": "draft",
            })
            return self._ok({"drawing_id": did})

        if path == "/api/extract":
            images = _images_from_payload(payload, self)
            params = ai.extract_params(images)
            did = payload.get("drawing_id")
            if did:
                db.update_drawing(int(did), {
                    "drawing_no": params.get("drawing_no", ""),
                    "title": params.get("title", ""),
                    "material": params.get("material", ""),
                    "scale": params.get("scale", ""),
                    "qty": params.get("qty", ""),
                    "category": params.get("category", ""),
                    "version": params.get("version", ""),
                    "draw_date": params.get("draw_date", ""),
                    "params": params,
                    "pages": len(images),
                })
            return self._ok(params)

        if path == "/api/audit":
            images = _images_from_payload(payload, self)
            issues = ai.audit_drawing(images)
            did = int(payload.get("drawing_id") or 0)
            if did:
                db.replace_issues(did, issues)
            return self._ok({"issues": issues, "count": len(issues)})

        if path == "/api/drawings/save":
            did = int(payload.get("id") or 0)
            fields = {k: v for k, v in payload.items() if k != "id"}
            if did:
                db.update_drawing(did, fields)
            else:
                did = db.insert_drawing(fields)
            return self._ok({"id": did})

        if path == "/api/drawings/delete":
            db.delete_drawing(int(payload.get("id") or 0))
            return self._ok()

        if path == "/api/issues/update":
            db.update_issue(int(payload.get("id") or 0), payload.get("status", "open"))
            return self._ok()

        if path == "/api/query":
            question = (payload.get("question") or "").strip()
            if not question:
                return self._err("请输入查询问题")
            digest = db.catalog_digest()
            if not digest:
                return self._err("图纸库还是空的，先去「提取参数」入库图纸吧。")
            answer, hits = ai.smart_query(question, digest)
            hit_items = [d for d in db.list_drawings() if d["id"] in hits]
            db.save_query(question, answer, hits)
            return self._ok({"answer": answer, "hits": hit_items})

        return self._err("未找到接口", 404)


def main():
    db.init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print("=" * 56)
    print("  AI 工程图纸解析 — 已启动")
    print("  浏览器访问: " + url)
    print("  关闭本窗口即停止服务（数据都保存在 data 目录）")
    print("=" * 56)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
