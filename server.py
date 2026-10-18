#!/usr/bin/env python3
"""packet.social server. Python standard library only.

HTTP (plain text) listens on localhost; Caddy terminates TLS in front of it.
UDP datagrams on port 6969 are likes. Nothing is ever sent back over UDP.
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from base64 import b64decode
from collections import defaultdict, deque
from urllib.parse import parse_qs

DB_PATH = os.environ.get("PACKET_DB", "/var/lib/packet/packet.db")
HTTP_HOST = os.environ.get("PACKET_HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("PACKET_HTTP_PORT", "8080"))
UDP_HOST = os.environ.get("PACKET_UDP_HOST", "0.0.0.0")
UDP_PORT = int(os.environ.get("PACKET_UDP_PORT", "6969"))
PUBLIC_IP = os.environ.get("PACKET_IP", "46.101.187.251")

MAX_POST = 280            # bytes
MAX_FEED = 50             # posts returned by /feed
RATE_PER_MIN = 60         # requests per minute per address
REGISTER_PER_HOUR = 5     # registration attempts per hour per address
POSTS_PER_10MIN = 20      # posts per 10 minutes per handle
HANDLE_RE = re.compile(r"[a-z0-9_]{1,20}")
LIKE_RE = re.compile(rb"LIKE:([0-9]{1,12})\n?")

STATUS = {
    200: "OK", 201: "Created", 400: "Bad Request", 401: "Unauthorized",
    403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    409: "Conflict", 413: "Payload Too Large", 429: "Too Many Requests",
    431: "Request Header Fields Too Large", 500: "Internal Server Error",
}

# ---------------------------------------------------------------- database

db = sqlite3.connect(DB_PATH, isolation_level=None)
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=NORMAL")
db.executescript("""
CREATE TABLE IF NOT EXISTS meta  (k TEXT PRIMARY KEY, v BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS users (handle TEXT PRIMARY KEY, salt BLOB NOT NULL,
                                  hash BLOB NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                  handle TEXT NOT NULL, t INTEGER NOT NULL,
                                  body BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS likes (post_id INTEGER NOT NULL, who TEXT NOT NULL,
                                  t INTEGER NOT NULL, PRIMARY KEY (post_id, who));
""")

row = db.execute("SELECT v FROM meta WHERE k='secret'").fetchone()
if row is None:
    db.execute("INSERT INTO meta (k, v) VALUES ('secret', ?)", (os.urandom(32),))
    row = db.execute("SELECT v FROM meta WHERE k='secret'").fetchone()
SECRET = bytes(row[0])


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- limits

_hits = defaultdict(deque)


def allow(key, limit, window):
    """Sliding window counter. True if this hit is allowed."""
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


async def janitor():
    while True:
        await asyncio.sleep(300)
        now = time.monotonic()
        for k in [k for k, q in _hits.items() if not q or now - q[-1] > 3700]:
            _hits.pop(k, None)


# ---------------------------------------------------------------- helpers

def anon(ip):
    """Likes are stored against a keyed hash of the address, not the address."""
    return hmac.new(SECRET, ip.encode(), "sha256").hexdigest()[:20]


def hash_pw(password, salt):
    return hashlib.scrypt(password, salt=salt, n=2 ** 14, r=8, p=1, maxmem=64 * 1024 * 1024)


# Registration questions. Every answer is in the manual, or one command away.
QUESTIONS = [
    ("what is the IPv4 loopback address?", "127.0.0.1"),
    ("which port does HTTPS use?", "443"),
    ("which port does plain HTTP use?", "80"),
    ("which port does SSH use?", "22"),
    ("what status code means Not Found?", "404"),
    ("what status code means Unauthorized?", "401"),
    ("what status code means Created?", "201"),
    ("do likes travel over tcp or udp?", "udp"),
    ("which port do likes use?", "6969"),
    ("what is the maximum size of a post, in bytes?", "280"),
    ("how many bits are in an IPv4 address?", "32"),
    ("what is the IP address of packet.social?", PUBLIC_IP),
]


def challenge_for(ip, bucket):
    d = hmac.new(SECRET, ("%s|%d" % (ip, bucket)).encode(), "sha256").digest()
    return QUESTIONS[d[0] % len(QUESTIONS)]


def current_bucket():
    return int(time.time() // 600)


def clean_text(body):
    """Return str, or None if the body is not acceptable."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.strip():
        return None
    for ch in text:
        if unicodedata.category(ch) == "Cc" and ch not in "\n\t":
            return None  # no escape sequences, no NUL, no C1 controls
    return text


def parse_basic(headers):
    raw = headers.get("authorization", "")
    if not raw.lower().startswith("basic "):
        return None
    try:
        decoded = b64decode(raw[6:].strip(), validate=True)
    except Exception:
        return None
    handle, sep, password = decoded.partition(b":")
    if not sep:
        return None
    try:
        return handle.decode("ascii"), password
    except UnicodeDecodeError:
        return None


def check_login(headers):
    cred = parse_basic(headers)
    if cred is None:
        return None
    handle, password = cred
    row = db.execute("SELECT salt, hash FROM users WHERE handle=?", (handle,)).fetchone()
    if row is None:
        hash_pw(password, b"\0" * 16)  # keep timing similar for unknown handles
        return None
    if hmac.compare_digest(hash_pw(password, bytes(row[0])), bytes(row[1])):
        return handle
    return None


def fmt_post(pid, who, t, body, likes):
    return "#%d @%s t=%d likes=%d\n%s\n" % (pid, who, t, likes, body.decode("utf-8"))


POST_SQL = ("SELECT p.id, p.handle, p.t, p.body, "
            "(SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) FROM posts p ")


def counts():
    users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    posts = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    return users, posts


# ---------------------------------------------------------------- routes

class Reply(Exception):
    def __init__(self, code, body="", headers=None, ctype="text/plain; charset=utf-8"):
        self.code, self.body, self.headers, self.ctype = code, body, headers or {}, ctype


def text(code, body="", headers=None):
    raise Reply(code, body, headers)


def route(method, path, headers, body, ip):
    if method not in ("GET", "HEAD", "POST"):
        text(405, "405 Method Not Allowed\n", {"Allow": "GET, HEAD, POST"})
    is_post = method == "POST"

    if path == "/status":
        users, posts = counts()
        text(200, "200 OK\nusers: %d\nposts: %d\n" % (users, posts))

    if path == "/status.json":
        users, posts = counts()
        raise Reply(200, json.dumps({"users": users, "posts": posts}), ctype="application/json")

    if path == "/feed" or path == "/feed.json":
        if is_post:
            text(405, "405 Method Not Allowed\n", {"Allow": "GET, HEAD"})
        rows = db.execute(POST_SQL + "ORDER BY p.id DESC LIMIT ?", (MAX_FEED,)).fetchall()
        if path == "/feed.json":
            out = [{"id": r[0], "who": r[1], "t": r[2], "text": bytes(r[3]).decode("utf-8"),
                    "likes": r[4]} for r in rows]
            raise Reply(200, json.dumps(out), ctype="application/json")
        text(200, "\n".join(fmt_post(r[0], r[1], r[2], bytes(r[3]), r[4]) for r in rows))

    m = re.fullmatch(r"/post/([0-9]{1,12})", path)
    if m:
        if is_post:
            text(405, "405 Method Not Allowed\n", {"Allow": "GET, HEAD"})
        r = db.execute(POST_SQL + "WHERE p.id=?", (int(m.group(1)),)).fetchone()
        if r is None:
            text(404, "404 Not Found\n")
        text(200, fmt_post(r[0], r[1], r[2], bytes(r[3]), r[4]))

    m = re.fullmatch(r"/profile/([a-z0-9_]{1,20})", path)
    if m:
        if is_post:
            text(405, "405 Method Not Allowed\n", {"Allow": "GET, HEAD"})
        u = db.execute("SELECT created FROM users WHERE handle=?", (m.group(1),)).fetchone()
        if u is None:
            text(404, "404 Not Found\n")
        n = db.execute("SELECT COUNT(*) FROM posts WHERE handle=?", (m.group(1),)).fetchone()[0]
        text(200, "handle: %s\njoined: %d\nposts: %d\n" % (m.group(1), u[0], n))

    if path == "/register":
        if not is_post:
            question, _ = challenge_for(ip, current_bucket())
            text(200, "question: %s\nanswer with: answer=...\n" % question)
        return register(headers, body, ip)

    if path == "/post":
        if not is_post:
            text(405, "405 Method Not Allowed\n", {"Allow": "POST"})
        return create_post(headers, body)

    text(404, "404 Not Found\n")


def unauthorized():
    text(401, "401 Unauthorized\n", {"WWW-Authenticate": 'Basic realm="packet.social"'})


def register(headers, body, ip):
    if not allow("reg:" + ip, REGISTER_PER_HOUR, 3600):
        text(429, "429 Too Many Requests\n", {"Retry-After": "3600"})
    cred = parse_basic(headers)
    if cred is None:
        unauthorized()
    handle, password = cred
    if not HANDLE_RE.fullmatch(handle):
        text(400, "400 Bad Request\nhandle: lowercase letters, digits, underscore, 1 to 20 bytes\n")
    if not 8 <= len(password) <= 128:
        text(400, "400 Bad Request\npassword: 8 to 128 bytes\n")
    try:
        answer = parse_qs(body.decode("ascii")).get("answer", [""])[0].strip()
    except UnicodeDecodeError:
        answer = ""
    ok = False
    for bucket in (current_bucket(), current_bucket() - 1):
        if hmac.compare_digest(answer.lower(), challenge_for(ip, bucket)[1].lower()):
            ok = True
    if not ok:
        text(403, "403 Forbidden\nwrong answer, or the question changed. GET /register for the current one\n")
    salt = os.urandom(16)
    try:
        db.execute("INSERT INTO users (handle, salt, hash, created) VALUES (?,?,?,?)",
                   (handle, salt, hash_pw(password, salt), int(time.time())))
    except sqlite3.IntegrityError:
        text(409, "409 Conflict\nhandle is taken\n")
    log("register %s" % handle)
    text(201, "201 Created\n")


def create_post(headers, body):
    handle = check_login(headers)
    if handle is None:
        unauthorized()
    if not allow("post:" + handle, POSTS_PER_10MIN, 600):
        text(429, "429 Too Many Requests\n", {"Retry-After": "60"})
    if not body:
        text(400, "400 Bad Request\nempty body\n")
    msg = clean_text(body)
    if msg is None:
        text(400, "400 Bad Request\nbody must be UTF-8 text without control characters\n")
    cur = db.execute("INSERT INTO posts (handle, t, body) VALUES (?,?,?)",
                     (handle, int(time.time()), body))
    pid = cur.lastrowid
    log("post %d by %s" % (pid, handle))
    text(201, "201 Created\n", {"Location": "/post/%d" % pid})


# ---------------------------------------------------------------- http

async def serve(reader, writer, peer):
    async def send(code, body="", headers=None, ctype="text/plain; charset=utf-8", head=False):
        data = body.encode("utf-8") if isinstance(body, str) else body
        lines = ["HTTP/1.1 %d %s" % (code, STATUS.get(code, "Status")),
                 "Content-Type: " + ctype,
                 "Content-Length: %d" % len(data),
                 "Cache-Control: no-store",
                 "X-Content-Type-Options: nosniff",
                 "Connection: close"]
        for k, v in (headers or {}).items():
            lines.append("%s: %s" % (k, v))
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + (b"" if head else data))
        await writer.drain()

    try:
        request_line = await reader.readline()
        parts = request_line.decode("latin-1").split()
        if len(parts) != 3:
            return await send(400, "400 Bad Request\n")
        method, target, _ = parts
        headers = {}
        for _ in range(60):
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            k, sep, v = line.decode("latin-1").partition(":")
            if sep:
                headers[k.strip().lower()] = v.strip()
        else:
            return await send(431, "431 Request Header Fields Too Large\n")

        ip = peer[0]
        if ip in ("127.0.0.1", "::1"):
            ip = headers.get("x-forwarded-for", ip).split(",")[0].strip() or ip
        if not allow("req:" + ip, RATE_PER_MIN, 60):
            return await send(429, "429 Too Many Requests\n", {"Retry-After": "60"})

        path = target.split("?", 1)[0]
        body = b""
        if method == "POST":
            try:
                length = int(headers.get("content-length", ""))
            except ValueError:
                return await send(400, "400 Bad Request\ncontent-length required\n")
            if length < 0:
                return await send(400, "400 Bad Request\n")
            limit = MAX_POST if path == "/post" else 1024
            if length > limit:
                return await send(413, "413 Payload Too Large\nlimit: %d bytes\n" % limit)
            body = await reader.readexactly(length)

        try:
            route(method, path, headers, body, ip)
        except Reply as r:
            await send(r.code, r.body, r.headers, r.ctype, head=(method == "HEAD"))
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    except (ValueError, asyncio.LimitOverrunError):
        await send(431, "431 Request Header Fields Too Large\n")


async def handle_http(reader, writer):
    try:
        await asyncio.wait_for(serve(reader, writer, writer.get_extra_info("peername")), 15)
    except asyncio.TimeoutError:
        pass
    except Exception as exc:  # never let one request take the server down
        log("error: %r" % (exc,))
    finally:
        try:
            writer.close()
        except Exception:
            pass


# ---------------------------------------------------------------- udp likes

class Likes(asyncio.DatagramProtocol):
    def datagram_received(self, data, addr):
        ip = addr[0]
        if len(data) > 32 or not allow("udp:" + ip, RATE_PER_MIN, 60):
            return
        m = LIKE_RE.fullmatch(data)
        if not m:
            return
        pid = int(m.group(1))
        if db.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone():
            db.execute("INSERT OR IGNORE INTO likes (post_id, who, t) VALUES (?,?,?)",
                       (pid, anon(ip), int(time.time())))


# ---------------------------------------------------------------- main

async def main():
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(Likes, local_addr=(UDP_HOST, UDP_PORT))
    server = await asyncio.start_server(handle_http, HTTP_HOST, HTTP_PORT, limit=8192)
    log("http %s:%d  udp %s:%d  db %s" % (HTTP_HOST, HTTP_PORT, UDP_HOST, UDP_PORT, DB_PATH))
    asyncio.create_task(janitor())
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
