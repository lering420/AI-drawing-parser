# -*- coding: utf-8 -*-
"""本地数据层：SQLite（Python 标准库），数据存于 data/ 目录，随程序文件夹一起拷走。"""
import json
import os
import sqlite3
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PDF_DIR = os.path.join(DATA_DIR, "pdfs")
DB_PATH = os.path.join(DATA_DIR, "app.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

for _d in (DATA_DIR, PDF_DIR):
    if not os.path.isdir(_d):
        os.makedirs(_d, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS drawings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name   TEXT,
    pdf_path    TEXT,
    pages       INTEGER DEFAULT 1,
    drawing_no  TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    material    TEXT DEFAULT '',
    scale       TEXT DEFAULT '',
    qty         TEXT DEFAULT '',
    category    TEXT DEFAULT '',
    version     TEXT DEFAULT '',
    draw_date   TEXT DEFAULT '',
    params_json TEXT DEFAULT '{}',
    status      TEXT DEFAULT 'confirmed',   -- confirmed / archived
    created_at  TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drawing_id  INTEGER NOT NULL,
    severity    TEXT DEFAULT 'general',     -- severe / general / hint
    category    TEXT DEFAULT '',
    page        INTEGER DEFAULT 1,
    location    TEXT DEFAULT '',
    problem     TEXT NOT NULL,
    suggestion  TEXT DEFAULT '',
    basis       TEXT DEFAULT '',
    status      TEXT DEFAULT 'open',        -- open / accepted / ignored
    created_at  TEXT,
    FOREIGN KEY (drawing_id) REFERENCES drawings(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS query_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    question   TEXT,
    answer     TEXT,
    hits_json  TEXT DEFAULT '[]',
    created_at TEXT
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def resolve_pdf_path(p):
    """库里只存相对路径（data/pdfs/xxx.pdf），读取时解析成绝对路径。
    兼容旧记录：若存的是其它电脑的绝对路径，退化为本机 data/pdfs 下的同名文件。"""
    if not p:
        return ""
    if os.path.isabs(p):
        local = os.path.join(PDF_DIR, os.path.basename(p))
        return local if os.path.isfile(local) else p
    return os.path.join(BASE_DIR, p)


def _migrate_pdf_paths(conn):
    """把历史绝对路径统一迁移为相对路径，保证整个文件夹拷到别的电脑后原图仍可打开。"""
    rows = conn.execute("SELECT id, pdf_path FROM drawings WHERE pdf_path <> ''").fetchall()
    for r in rows:
        p = r["pdf_path"]
        if os.path.isabs(p):
            conn.execute(
                "UPDATE drawings SET pdf_path=? WHERE id=?",
                (os.path.join("data", "pdfs", os.path.basename(p)), r["id"]),
            )


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate_pdf_paths(conn)


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------- 配置 ----------

def load_config():
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"api_key": "", "model": "deepseek-flash"}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------- 图纸 ----------

def _row_to_dict(r):
    d = dict(r)
    try:
        d["params"] = json.loads(d.pop("params_json") or "{}")
    except Exception:
        d["params"] = {}
    return d


def insert_drawing(rec):
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO drawings
               (file_name, pdf_path, pages, drawing_no, title, material, scale,
                qty, category, version, draw_date, params_json, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.get("file_name", ""),
                rec.get("pdf_path", ""),
                int(rec.get("pages", 1)),
                rec.get("drawing_no", ""),
                rec.get("title", ""),
                rec.get("material", ""),
                rec.get("scale", ""),
                rec.get("qty", ""),
                rec.get("category", ""),
                rec.get("version", ""),
                rec.get("draw_date", ""),
                json.dumps(rec.get("params", {}), ensure_ascii=False),
                rec.get("status", "confirmed"),
                ts, ts,
            ),
        )
        return cur.lastrowid


def list_drawings(status=None, keyword=""):
    q = "SELECT * FROM drawings"
    args = []
    conds = []
    if status in ("confirmed", "archived", "draft"):
        conds.append("status = ?")
        args.append(status)
    if keyword:
        conds.append("(drawing_no LIKE ? OR title LIKE ? OR material LIKE ? OR file_name LIKE ?)")
        like = "%" + keyword + "%"
        args.extend([like, like, like, like])
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY updated_at DESC"
    with connect() as conn:
        rows = conn.execute(q, args).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_drawing(did):
    with connect() as conn:
        r = conn.execute("SELECT * FROM drawings WHERE id=?", (did,)).fetchone()
    return _row_to_dict(r) if r else None


def update_drawing(did, fields):
    allow = {"file_name", "pages", "drawing_no", "title", "material", "scale",
             "qty", "category", "version", "draw_date", "status"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allow:
            sets.append(f"{k} = ?")
            args.append(v)
    if "params" in fields:
        sets.append("params_json = ?")
        args.append(json.dumps(fields["params"], ensure_ascii=False))
    if not sets:
        return
    sets.append("updated_at = ?")
    args.append(now())
    args.append(did)
    with connect() as conn:
        conn.execute(f"UPDATE drawings SET {', '.join(sets)} WHERE id=?", args)


def delete_drawing(did):
    with connect() as conn:
        r = conn.execute("SELECT pdf_path FROM drawings WHERE id=?", (did,)).fetchone()
        conn.execute("DELETE FROM issues WHERE drawing_id=?", (did,))
        conn.execute("DELETE FROM drawings WHERE id=?", (did,))
    pdf = resolve_pdf_path(r["pdf_path"]) if r and r["pdf_path"] else ""
    if pdf and os.path.isfile(pdf):
        try:
            os.remove(pdf)
        except OSError:
            pass


# ---------- 勘误 ----------

def replace_issues(drawing_id, issues):
    ts = now()
    with connect() as conn:
        conn.execute("DELETE FROM issues WHERE drawing_id=?", (drawing_id,))
        for it in issues:
            conn.execute(
                """INSERT INTO issues
                   (drawing_id, severity, category, page, location, problem,
                    suggestion, basis, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (drawing_id, it.get("severity", "general"), it.get("category", ""),
                 int(it.get("page", 1) or 1), it.get("location", ""),
                 it.get("problem", ""), it.get("suggestion", ""),
                 it.get("basis", ""), "open", ts),
            )


def list_issues(drawing_id):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM issues WHERE drawing_id=? ORDER BY CASE severity WHEN 'severe' THEN 0 WHEN 'general' THEN 1 ELSE 2 END, id",
            (drawing_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_issue(issue_id, status):
    with connect() as conn:
        conn.execute("UPDATE issues SET status=? WHERE id=?", (status, issue_id))


def issue_stats():
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.id, d.drawing_no, d.title,
                      SUM(CASE WHEN i.status='open' THEN 1 ELSE 0 END) AS open_cnt,
                      COUNT(i.id) AS total
               FROM drawings d LEFT JOIN issues i ON i.drawing_id = d.id
               GROUP BY d.id"""
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- 查询 ----------

def save_query(question, answer, hits):
    with connect() as conn:
        conn.execute(
            "INSERT INTO query_log (question, answer, hits_json, created_at) VALUES (?,?,?,?)",
            (question, answer, json.dumps(hits, ensure_ascii=False), now()),
        )


def catalog_digest(limit=200):
    """给智能查询用的台账摘要（轻量，控制 token 用量）。"""
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, drawing_no, title, material, category, qty, scale,
                      version, draw_date, status, pages, params_json
               FROM drawings ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        try:
            params = json.loads(r["params_json"] or "{}")
        except Exception:
            params = {}
        brief = {"图号": r["drawing_no"], "名称": r["title"], "材料": r["material"],
                 "类别": r["category"], "数量": r["qty"], "版本": r["version"],
                 "日期": r["draw_date"], "状态": r["status"], "id": r["id"]}
        # 参数里挑关键项摘要
        for key in ("热处理", "硬度", "表面粗糙度", "未注公差", "零件数"):
            if key in params:
                brief[key] = params[key]
        out.append(brief)
    return out


init_db()
