# -*- coding: utf-8 -*-
"""访问计数器（SQLite 版）
GET /count  -> 计数+1（自己的访问不算），返回总数与访客信息
GET /visits -> 返回全部访问记录（新到旧）
"""
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE, "visitors.db")
OLD_COUNT = os.path.join(BASE, "visitor_count.json")
OLD_LOG = os.path.join(BASE, "visitor_log.jsonl")

UPLOAD_DIR = r"C:\inetpub\wwwroot\video\uploads"
MAX_UPLOAD = 90 * 1024 * 1024  # 90MB（Cloudflare 免费版单请求上限 100MB）
ALLOWED_EXT = {".mp4", ".webm", ".mov", ".m4v"}

GEO_CACHE = {}

SELF_IPS = {"127.0.0.1", "::1", "localhost"}
SELF_PREFIXES = ("2409:895a:",)


def is_self(ip):
    ip = (ip or "").strip().lower()
    if ip in SELF_IPS:
        return True
    for p in SELF_PREFIXES:
        if ip.startswith(p):
            return True
    return False


def db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS counter (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        count INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT, ip TEXT, country TEXT, region TEXT, city TEXT, isp TEXT, ua TEXT,
        browser TEXT, os TEXT, device TEXT, referer TEXT, screen TEXT, lang TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT, name TEXT, content TEXT, ip TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS likes (
        post TEXT NOT NULL,
        ip TEXT NOT NULL,
        time TEXT,
        PRIMARY KEY (post, ip)
    )""")
    conn.execute("INSERT OR IGNORE INTO counter (id, count) VALUES (1, 0)")
    # 老库补列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(visits)").fetchall()}
    for col in ("browser", "os", "device", "referer", "screen", "lang"):
        if col not in cols:
            try:
                conn.execute("ALTER TABLE visits ADD COLUMN %s TEXT" % col)
            except Exception:
                pass
    conn.commit()
    return conn


def migrate_once():
    """把旧的 JSON 文件数据导进 SQLite（只导一次）"""
    conn = db()
    try:
        n = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
        c = conn.execute("SELECT count FROM counter WHERE id = 1").fetchone()[0]
        if n == 0 and c == 0:
            old_count = 0
            if os.path.isfile(OLD_COUNT):
                try:
                    old_count = int(json.load(open(OLD_COUNT, encoding="utf-8")).get("count", 0))
                except Exception:
                    old_count = 0
            if os.path.isfile(OLD_LOG):
                lines = open(OLD_LOG, encoding="utf-8").read().splitlines()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        conn.execute(
                            "INSERT INTO visits (time, ip, country, region, city, isp, ua) VALUES (?,?,?,?,?,?,?)",
                            (r.get("time", ""), r.get("ip", ""), r.get("country", ""), r.get("region", ""),
                             r.get("city", ""), r.get("isp", ""), r.get("ua", "")),
                        )
                    except Exception:
                        continue
            conn.execute("UPDATE counter SET count = ? WHERE id = 1", (max(old_count, 0),))
            conn.commit()
            print("migrated: count=%s" % old_count)
    except Exception as e:
        print("migrate error:", e)
    finally:
        conn.close()


def get_count():
    conn = db()
    try:
        return int(conn.execute("SELECT count FROM counter WHERE id = 1").fetchone()[0])
    finally:
        conn.close()


def incr_count():
    conn = db()
    try:
        conn.execute("UPDATE counter SET count = count + 1 WHERE id = 1")
        conn.commit()
        return int(conn.execute("SELECT count FROM counter WHERE id = 1").fetchone()[0])
    finally:
        conn.close()


def add_visit(record):
    conn = db()
    try:
        conn.execute(
            "INSERT INTO visits (time, ip, country, region, city, isp, ua, browser, os, device, referer, screen, lang) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.get("time", ""), record.get("ip", ""), record.get("country", ""), record.get("region", ""),
             record.get("city", ""), record.get("isp", ""), record.get("ua", ""),
             record.get("browser", ""), record.get("os", ""), record.get("device", ""),
             record.get("referer", ""), record.get("screen", ""), record.get("lang", "")),
        )
        conn.commit()
    finally:
        conn.close()


def all_visits():
    conn = db()
    try:
        cur = conn.execute(
            "SELECT time, ip, country, region, city, isp, ua, browser, os, device, referer "
            "FROM visits ORDER BY id DESC"
        )
        rows = cur.fetchall()
        return [tuple((r + ("",) * 11)[:11]) for r in rows]
    finally:
        conn.close()


def add_message(name, content, ip):
    conn = db()
    try:
        conn.execute(
            "INSERT INTO messages (time, name, content, ip) VALUES (?,?,?,?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), name, content, ip),
        )
        conn.commit()
    finally:
        conn.close()


def all_messages():
    conn = db()
    try:
        cur = conn.execute("SELECT id, time, name, content FROM messages ORDER BY id DESC")
        return cur.fetchall()
    finally:
        conn.close()


def like_post(post, ip):
    """点赞（同一 IP 对同一帖子只算一次）"""
    conn = db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO likes (post, ip, time) VALUES (?,?,?)",
            (post, ip, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM likes WHERE post = ?", (post,)).fetchone()[0]
        mine = conn.execute("SELECT COUNT(*) FROM likes WHERE post = ? AND ip = ?", (post, ip)).fetchone()[0]
        return n, mine > 0
    finally:
        conn.close()


def like_counts():
    conn = db()
    try:
        rows = conn.execute("SELECT post, COUNT(*) FROM likes GROUP BY post").fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        conn.close()


def safe_name(name):
    """只保留文件名里的安全字符"""
    name = os.path.basename(str(name or "")).strip()
    keep = []
    for ch in name:
        if ch.isalnum() or ch in "._-":
            keep.append(ch)
        elif ch in " ()":
            keep.append("_")
    name = "".join(keep)
    if not name:
        name = "video"
    return name[:80]


def list_uploads():
    out = []
    if not os.path.isdir(UPLOAD_DIR):
        return out
    for fn in os.listdir(UPLOAD_DIR):
        p = os.path.join(UPLOAD_DIR, fn)
        if not os.path.isfile(p):
            continue
        st = os.stat(p)
        out.append({
            "name": fn,
            "size": st.st_size,
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            "url": "/video/uploads/" + fn,
        })
    out.sort(key=lambda x: x["time"], reverse=True)
    return out


def client_ip(handler):
    h = handler.headers
    for key in ("CF-Connecting-IP", "X-Forwarded-For", "X-Real-IP"):
        v = h.get(key)
        if v:
            return v.split(",")[0].strip()
    return handler.client_address[0]


def geo_lookup(ip):
    if ip in GEO_CACHE:
        return GEO_CACHE[ip]
    info = {"country": "", "region": "", "city": "", "isp": ""}
    try:
        u = "http://ip-api.com/json/%s?lang=zh-CN&fields=status,country,regionName,city,isp" % ip
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "replace"))
        if d.get("status") == "success":
            info = {
                "country": d.get("country") or "",
                "region": d.get("regionName") or "",
                "city": d.get("city") or "",
                "isp": d.get("isp") or "",
            }
    except Exception:
        pass
    GEO_CACHE[ip] = info
    return info


def parse_ua(ua):
    ua = str(ua or "")
    browser = "其他"
    if "MicroMessenger" in ua:
        browser = "微信"
    elif "QQ/" in ua or "QQBrowser" in ua:
        browser = "QQ/QQ浏览器"
    elif "Edg/" in ua:
        browser = "Edge"
    elif "Chrome/" in ua:
        browser = "Chrome"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Safari/" in ua:
        browser = "Safari"
    elif "curl" in ua:
        browser = "命令行"

    osname = "其他"
    if "Windows NT" in ua:
        osname = "Windows"
    elif "Android" in ua:
        osname = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        osname = "iOS"
    elif "Mac OS X" in ua:
        osname = "macOS"
    elif "Linux" in ua:
        osname = "Linux"

    device = "电脑"
    if "iPad" in ua or "Tablet" in ua:
        device = "平板"
    elif "Mobile" in ua or "Android" in ua or "iPhone" in ua:
        device = "手机"
    return browser, osname, device



class Handler(BaseHTTPRequestHandler):
    def _headers(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/count", "/count/", "/", "/api/count"):
            ip = client_ip(self)
            g = geo_lookup(ip)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            ua = (self.headers.get("User-Agent") or "")
            browser, osname, device = parse_ua(ua)
            if is_self(ip):
                self._headers()
                self.wfile.write(json.dumps({
                    "count": get_count(), "ip": ip, "country": g["country"], "region": g["region"],
                    "city": g["city"], "isp": g["isp"], "time": now, "self": True,
                    "browser": browser, "os": osname, "device": device,
                }, ensure_ascii=False).encode("utf-8"))
                return
            n = incr_count()
            add_visit({
                "time": now, "ip": ip, "country": g["country"], "region": g["region"],
                "city": g["city"], "isp": g["isp"], "ua": ua[:200],
                "browser": browser, "os": osname, "device": device,
                "referer": (self.headers.get("Referer") or "")[:120],
                "screen": "",
                "lang": "",
            })
            self._headers()
            self.wfile.write(json.dumps({
                "count": n, "ip": ip, "country": g["country"], "region": g["region"],
                "city": g["city"], "isp": g["isp"], "time": now, "self": False,
                "browser": browser, "os": osname, "device": device,
            }, ensure_ascii=False).encode("utf-8"))
        elif path == "/peek":
            self._headers()
            self.wfile.write(json.dumps({"count": get_count()}).encode("utf-8"))
        elif path in ("/likes", "/likes/"):
            self._headers()
            self.wfile.write(json.dumps({"likes": like_counts()}, ensure_ascii=False).encode("utf-8"))
        elif path in ("/videos", "/videos/"):
            self._headers()
            self.wfile.write(json.dumps({"videos": list_uploads()}, ensure_ascii=False).encode("utf-8"))
        elif path in ("/visits", "/visits/"):
            rows = all_visits()
            visits = []
            for (t, ip, country, region, city, isp, ua, browser, osname, device, referer) in rows:
                visits.append({
                    "time": t or "",
                    "ip": ip or "",
                    "loc": " · ".join([x for x in (country, region, city) if x]),
                    "isp": isp or "",
                    "browser": browser or "",
                    "os": osname or "",
                    "device": device or "",
                    "referer": referer or "",
                    "ua": (ua or "")[:60],
                })
            self._headers()
            self.wfile.write(json.dumps({"total": get_count(), "visits": visits}, ensure_ascii=False).encode("utf-8"))
        elif path in ("/messages", "/messages/"):
            rows = all_messages()
            msgs = [{"id": r[0], "time": r[1] or "", "name": r[2] or "", "content": r[3] or ""} for r in rows]
            self._headers()
            self.wfile.write(json.dumps({"messages": msgs}, ensure_ascii=False).encode("utf-8"))
        else:
            self._headers(404)
            self.wfile.write(b'{"error":"not found"}')

    def do_POST(self):
        path = self.path.split("?")[0]
        if path in ("/messages", "/messages/"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(min(length, 10000)) if length > 0 else b"{}"
                data = json.loads(raw.decode("utf-8", "replace") or "{}")
                name = str(data.get("name") or "").strip()[:20] or "匿名"
                content = str(data.get("content") or "").strip()[:500]
                if not content:
                    self._headers(400)
                    self.wfile.write(b'{"ok":false,"error":"content empty"}')
                    return
                ip = client_ip(self)
                add_message(name, content, ip)
                self._headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            except Exception as e:
                self._headers(500)
                self.wfile.write(json.dumps({"ok": False, "error": str(e)[:100]}).encode("utf-8"))
        elif path in ("/like", "/like/"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(min(length, 5000)) if length > 0 else b"{}"
                data = json.loads(raw.decode("utf-8", "replace") or "{}")
                post = str(data.get("post") or "").strip()[:20]
                if not post:
                    self._headers(400)
                    self.wfile.write(b'{"ok":false,"error":"post required"}')
                    return
                ip = client_ip(self)
                n, mine = like_post(post, ip)
                self._headers()
                self.wfile.write(json.dumps({"ok": True, "count": n, "liked": mine}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._headers(500)
                self.wfile.write(json.dumps({"ok": False, "error": str(e)[:100]}).encode("utf-8"))
        elif path in ("/upload", "/upload/"):
            try:
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = {}
                for kv in qs.split("&"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        params[k] = urllib.parse.unquote(v)
                name = safe_name(params.get("name") or "video.mp4")
                ext = os.path.splitext(name)[1].lower()
                if ext not in ALLOWED_EXT:
                    self._headers(400)
                    self.wfile.write(b'{"ok":false,"error":"unsupported type"}')
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_UPLOAD:
                    self._headers(413)
                    self.wfile.write(json.dumps({"ok": False, "error": "file too large (max 90MB)"}).encode())
                    return
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                fn = "%s-%s%s" % (stamp, os.path.splitext(name)[0][:50], ext)
                dst = os.path.join(UPLOAD_DIR, fn)
                remain = length
                with open(dst, "wb") as f:
                    while remain > 0:
                        chunk = self.rfile.read(min(1 << 20, remain))
                        if not chunk:
                            break
                        f.write(chunk)
                        remain -= len(chunk)
                self._headers()
                self.wfile.write(json.dumps({"ok": True, "url": "/video/uploads/" + fn, "size": length}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._headers(500)
                self.wfile.write(json.dumps({"ok": False, "error": str(e)[:120]}).encode("utf-8"))
        else:
            self._headers(404)
            self.wfile.write(b'{"error":"not found"}')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    migrate_once()
    server = ThreadingHTTPServer(("127.0.0.1", 8081), Handler)
    server.serve_forever()
