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

# 数据规范：状态/类别的合法取值（与 PRD §4 台账状态一致）
VALID_STATUS = ("draft", "confirmed", "archived")
VALID_CATEGORY = ("", "零件图", "装配图")
VALID_ISSUE_STATUS = ("open", "accepted", "ignored")
# 标题栏字段：列与 params_json 双向保持一致（单一数据源）
TITLE_KEYS = ("drawing_no", "title", "material", "scale", "qty",
              "category", "version", "draw_date")
DATE_RE = re.compile(r"^\d{4}(-\d{1,2}(-\d{1,2})?)?$")


def _validate_drawing(fields, current):
    """保存前校验，返回带字段名的错误描述；通过则返回 None。current 为库中现有行（新增时为 None）。"""
    if "params" in fields and not isinstance(fields["params"], dict):
        return "参数 params 必须是 JSON 对象"
    if "pages" in fields:
        try:
            int(fields["pages"])
        except (TypeError, ValueError):
            return "字段 pages（页数）必须是整数"
    status = fields.get("status")
    if status is None:
        status = (current or {}).get("status", "confirmed")
    if status not in VALID_STATUS:
        return f"字段 status 取值非法（应为 {'/'.join(VALID_STATUS)}）"
    if "category" in fields:
        cat = str(fields.get("category") or "").strip()
        if cat not in VALID_CATEGORY:
            return "字段 category 只能是：零件图 或 装配图"
    dd = fields.get("draw_date")
    if dd is None:
        dd = (current or {}).get("draw_date", "")
    if dd and not DATE_RE.fullmatch(str(dd).strip()):
        return "字段 draw_date 日期格式应为 YYYY-MM-DD"
    no = fields.get("drawing_no")
    if no is None:
        no = (current or {}).get("drawing_no", "")
    if status == "confirmed" and not str(no or "").strip():
        return "字段 drawing_no：入库（confirmed）状态必须填写图号"
    return None


def _normalize_params(params):
    """AI 提取结果入库前的规范化：类别杂值归一、文本去首尾空白。"""
    if not isinstance(params, dict):
        return {}
    if str(params.get("category") or "").strip() not in VALID_CATEGORY:
        params["category"] = ""
    for k in TITLE_KEYS:
        if isinstance(params.get(k), str):
            params[k] = params[k].strip()
    return params


def _unify_title_params(fields, current):
    """标题栏字段与 params_json 对齐：任一侧修改都同步到另一侧。"""
    params = fields.get("params")
    if isinstance(params, dict):
        for k in TITLE_KEYS:
            if k in fields:
                params[k] = fields[k]
            elif k not in fields and k in params:
                fields[k] = params[k]
        fields["params"] = params
    elif any(k in fields for k in TITLE_KEYS):
        base = dict((current or {}).get("params") or {})
        for k in TITLE_KEYS:
            if k in fields:
                base[k] = fields[k]
        fields["params"] = base


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
        if path == "/api/export":
            # 全量导出：每条含完整 params 与 issues，供下游系统/备份使用
            items = []
            for d in db.list_drawings():
                d["issues"] = db.list_issues(d["id"])
                items.append(d)
            return self._ok({"exported_at": db.now(), "count": len(items),
                             "items": items})
        if path == "/api/query/history":
            return self._ok(db.query_history(self._qs("limit") or 50))
        if path == "/api/pdf":
            did = int(self._qs("id") or 0)
            d = db.get_drawing(did)
            pdf = db.resolve_pdf_path(d.get("pdf_path", "")) if d else ""
            if not pdf or not os.path.isfile(pdf):
                return self._err("原图文件不存在", 404)
            with open(pdf, "rb") as f:
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
            # 上传即建档（提取前先占位，参数稍后补）；库里存相对路径保证可迁移
            did = db.insert_drawing({
                "file_name": name,
                "pdf_path": os.path.join("data", "pdfs", fid + ".pdf"),
                "pages": int(payload.get("pages") or 1),
                "status": "draft",
            })
            return self._ok({"drawing_id": did})

        if path == "/api/extract":
            images = _images_from_payload(payload, self)
            params = _normalize_params(ai.extract_params(images))
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
            rules = payload.get("rules")
            if rules is not None:
                if not isinstance(rules, list) or not rules:
                    return self._err("请至少勾选一个检查项")
                rules = [str(r) for r in rules][:20]
            issues = ai.audit_drawing(images, rules)
            did = int(payload.get("drawing_id") or 0)
            if did:
                db.replace_issues(did, issues)
            return self._ok({"issues": issues, "count": len(issues)})

        if path == "/api/drawings/save":
            did = int(payload.get("id") or 0)
            fields = {k: v for k, v in payload.items() if k != "id"}
            for k in TITLE_KEYS + ("file_name",):
                if isinstance(fields.get(k), str):
                    fields[k] = fields[k].strip()
            current = db.get_drawing(did) if did else None
            if did and not current:
                return self._err("图纸不存在", 404)
            err = _validate_drawing(fields, current)
            if err:
                return self._err(err, 400)
            _unify_title_params(fields, current)
            if did:
                db.update_drawing(did, fields)
            else:
                did = db.insert_drawing(fields)
            return self._ok({"id": did})

        if path == "/api/drawings/delete":
            db.delete_drawing(int(payload.get("id") or 0))
            return self._ok()

        if path == "/api/issues/update":
            st = payload.get("status", "open")
            if st not in VALID_ISSUE_STATUS:
                return self._err(f"字段 status 取值非法（应为 {'/'.join(VALID_ISSUE_STATUS)}）")
            db.update_issue(int(payload.get("id") or 0), st)
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
