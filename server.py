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
import subprocess
import sys
import tempfile
import time
import unicodedata
from base64 import b64decode, b64encode
from collections import defaultdict, deque
from urllib.parse import parse_qs

DB_PATH = os.environ.get("PACKET_DB", "/var/lib/packet/packet.db")
HTTP_HOST = os.environ.get("PACKET_HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("PACKET_HTTP_PORT", "8080"))
UDP_HOST = os.environ.get("PACKET_UDP_HOST", "0.0.0.0")
UDP_PORT = int(os.environ.get("PACKET_UDP_PORT", "6969"))
PUBLIC_IP = os.environ.get("PACKET_IP", "46.101.187.251")
TREASURE_PATH = os.environ.get("PACKET_TREASURE", "/var/lib/packet/treasure.json")

MAX_POST = 280            # bytes
MAX_FEED = 50             # posts returned by /feed
RATE_PER_MIN = 60         # requests per minute per address
REGISTER_PER_HOUR = 5     # registration attempts per hour per address
POSTS_PER_10MIN = 20      # posts per 10 minutes per handle
HANDLE_RE = re.compile(r"[a-z0-9_]{1,20}")
LIKE_RE = re.compile(rb"LIKE:([0-9]{1,12})\n?")
CLOCK_SKEW = 300          # seconds a signed request stays valid
NAMESPACE = "packet.social"
STARTED = time.time()
RESERVED = {"packet", "admin", "root", "system", "mod", "support", "help", "null", "everyone"}

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
CREATE TABLE IF NOT EXISTS badges (handle TEXT NOT NULL, name TEXT NOT NULL,
                                   t INTEGER NOT NULL, PRIMARY KEY (handle, name));
CREATE TABLE IF NOT EXISTS treasure_solves (week INTEGER NOT NULL, handle TEXT NOT NULL,
                                            t INTEGER NOT NULL, PRIMARY KEY (week, handle));
""")

if "pubkey" not in [c[1] for c in db.execute("PRAGMA table_info(users)")]:
    db.execute("ALTER TABLE users ADD COLUMN pubkey TEXT")  # accounts before keys have none

row = db.execute("SELECT v FROM meta WHERE k='secret'").fetchone()
if row is None:
    db.execute("INSERT INTO meta (k, v) VALUES ('secret', ?)", (os.urandom(32),))
    row = db.execute("SELECT v FROM meta WHERE k='secret'").fetchone()
SECRET = bytes(row[0])


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- limits

_hits = defaultdict(deque)
_seen = {}  # signature hash -> expiry, so a captured request cannot be replayed


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
        wall = time.time()
        for k in [k for k, exp in _seen.items() if exp < wall]:
            _seen.pop(k, None)


# ---------------------------------------------------------------- helpers

def anon(ip):
    """Likes are stored against a keyed hash of the address, not the address."""
    return hmac.new(SECRET, ip.encode(), "sha256").hexdigest()[:20]


def parse_pubkey(line):
    """Return (b64, None) for a valid ssh-ed25519 public key, else (None, reason)."""
    parts = line.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        return None, "pubkey: must be an ssh-ed25519 public key (ssh-keygen -t ed25519)"
    try:
        blob = b64decode(parts[1], validate=True)
    except Exception:
        return None, "pubkey: not valid base64"
    # wire format: string "ssh-ed25519" then string of 32 key bytes
    if blob != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + blob[-32:] or len(blob) != 51:
        return None, "pubkey: not a valid ed25519 key"
    return parts[1], None


def verify_sig(handle, pubkey_b64, message, sig_b64):
    """Check an ssh-keygen -Y sign signature with ssh-keygen -Y verify."""
    try:
        sig = b64decode(sig_b64, validate=True)
    except Exception:
        return False
    if len(sig) > 1000 or not sig.startswith(b"-----BEGIN SSH SIGNATURE-----"):
        return False
    with tempfile.TemporaryDirectory() as d:
        allowed, sigfile = os.path.join(d, "allowed"), os.path.join(d, "sig")
        with open(allowed, "w") as f:
            f.write('%s namespaces="%s" ssh-ed25519 %s\n' % (handle, NAMESPACE, pubkey_b64))
        with open(sigfile, "wb") as f:
            f.write(sig)
        try:
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", allowed, "-I", handle,
                                "-n", NAMESPACE, "-s", sigfile],
                               input=message, capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0


def check_signed(headers, message_for, pubkey_b64=None):
    """Verify the Handle, Time and Signature headers. Returns the handle, or raises Reply."""
    handle = headers.get("handle", "")
    ts_raw = headers.get("time", "")
    sig = headers.get("signature", "")
    if not (handle and ts_raw and sig):
        unauthorized()
    if not HANDLE_RE.fullmatch(handle) or not ts_raw.isdigit() or len(ts_raw) > 12:
        unauthorized()
    ts = int(ts_raw)
    skew = int(time.time()) - ts
    if abs(skew) > CLOCK_SKEW:
        text(401, "401 Unauthorized\nyour clock is %d seconds off. Time must be within %d seconds. "
                  "Check your computer's date and time.\n" % (skew, CLOCK_SKEW))
    if pubkey_b64 is None:
        row = db.execute("SELECT pubkey FROM users WHERE handle=?", (handle,)).fetchone()
        if row is None or not row[0]:
            unauthorized()
        pubkey_b64 = row[0].split()[1]
    key = hashlib.sha256(sig.encode()).hexdigest()
    if key in _seen:
        text(401, "401 Unauthorized\nthis exact request was already used. "
                  "Run the commands again to make a fresh one.\n")
    if not verify_sig(handle, pubkey_b64, message_for(handle, ts_raw), sig):
        unauthorized()
    _seen[key] = time.time() + 2 * CLOCK_SKEW
    return handle


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


def fmt_post(pid, who, t, body, likes):
    return "#%d @%s t=%d likes=%d\n%s\n" % (pid, who, t, likes, body.decode("utf-8"))


POST_SQL = ("SELECT p.id, p.handle, p.t, p.body, "
            "(SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) FROM posts p ")


def counts():
    users = db.execute("SELECT COUNT(*) FROM users WHERE handle != 'packet'").fetchone()[0]
    posts = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    return users, posts


def like_count():
    return db.execute("SELECT COUNT(*) FROM likes").fetchone()[0]


def award(handle, name):
    db.execute("INSERT OR IGNORE INTO badges (handle, name, t) VALUES (?,?,?)",
               (handle, name, int(time.time())))


# ---------------------------------------------------------------- treasure hunt
# The hunt lives in one JSON file made by treasure_make.py on the owner's computer.
# The server holds the locked file, clues 1 to 3 and a salted hash of the password.
# It never holds the wallet keys, the password, or clue 4 (that one lives in DNS).

def load_treasure():
    try:
        with open(TREASURE_PATH) as f:
            t = json.load(f)
        int(t["week"]); t["clues"][2]; t["chest"]; t["hash"]; t["salt"]
        return t
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def check_passphrase(t, phrase):
    try:
        got = hashlib.scrypt(phrase.encode(), salt=bytes.fromhex(t["salt"]),
                             n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got, bytes.fromhex(t["hash"]))
    except (ValueError, KeyError):
        return False


def treasure_winner(week):
    r = db.execute("SELECT handle FROM treasure_solves WHERE week=? ORDER BY t, rowid LIMIT 1",
                   (week,)).fetchone()
    return r[0] if r else None


def treasure_record(week, handle):
    """Record a correct password. Returns True if this is the first solve of the week."""
    first = treasure_winner(week) is None
    db.execute("INSERT OR IGNORE INTO treasure_solves (week, handle, t) VALUES (?,?,?)",
               (week, handle, int(time.time())))
    return first and treasure_winner(week) == handle


def treasure_intro(t):
    lines = ["packet.social treasure hunt, week %d" % int(t["week"]), ""]
    if t.get("prize"):
        lines.append("prize: %s" % t["prize"])
    lines += [
        "A wallet's keys are locked inside a file. Open it first, move the coins, keep them.",
        "",
        "1. get the locked file:   curl -O https://packet.social/treasure/chest.enc",
        "2. unlock it:            openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -a -in chest.enc",
        "3. the password is 4 clues joined in order with nothing between them.",
        "   each clue is 8 characters: lowercase letters a to z and digits 2 to 7.",
        "",
    ]
    for i, h in enumerate(t.get("hints", []), 1):
        lines.append("clue %d: %s" % (i, h))
    lines += ["", "Opened it? Prove it in the SSH app with: claim <password>", ""]
    return "\n".join(lines)


def treasure_route(method, path):
    t = load_treasure()
    if t is None:
        text(404, "404 Not Found\nno treasure hunt is running right now\n")
    if path == "/treasure/chest.enc":
        if method not in ("GET", "HEAD"):
            text(405, "405 Method Not Allowed\n", {"Allow": "GET, HEAD"})
        text(200, t["chest"].strip() + "\n")
    if method in ("GET", "HEAD"):
        text(200, treasure_intro(t), {"X-Clue-1": t["clues"][0]})
    text(405, "405 Method Not Allowed\n", {"Allow": "GET, HEAD", "X-Clue-3": t["clues"][2]})


# ---------------------------------------------------------------- routes

class Reply(Exception):
    def __init__(self, code, body="", headers=None, ctype="text/plain; charset=utf-8"):
        self.code, self.body, self.headers, self.ctype = code, body, headers or {}, ctype


def text(code, body="", headers=None):
    raise Reply(code, body, headers)


def route(method, path, headers, body, ip):
    if path in ("/treasure", "/treasure/chest.enc"):
        return treasure_route(method, path)

    if method not in ("GET", "HEAD", "POST"):
        text(405, "405 Method Not Allowed\n", {"Allow": "GET, HEAD, POST"})
    is_post = method == "POST"

    if path == "/status":
        users, posts = counts()
        text(200, "200 OK\nusers: %d\nposts: %d\nlikes: %d\n" % (users, posts, like_count()))

    if path == "/status.json":
        users, posts = counts()
        raise Reply(200, json.dumps({"users": users, "posts": posts, "likes": like_count(),
                                     "uptime": int(time.time() - STARTED)}),
                    ctype="application/json")

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
        badges = [r[0] for r in db.execute(
            "SELECT name FROM badges WHERE handle=? ORDER BY t", (m.group(1),))]
        text(200, "handle: %s\njoined: %d\nposts: %d\nbadges: %s\n"
                  % (m.group(1), u[0], n, ", ".join(badges) or "none"))

    if path == "/register":
        if not is_post:
            question, _ = challenge_for(ip, current_bucket())
            text(200, REGISTER_HELP % question)
        return register(headers, body, ip)

    if path == "/post":
        if not is_post:
            text(405, "405 Method Not Allowed\n", {"Allow": "POST"})
        return create_post(headers, body)

    text(404, "404 Not Found\n")


REGISTER_HELP = """question: %s

HOW TO ANSWER
Type your answer in the A= part of the first line. It is not case sensitive,
so UDP and udp both work. Change H= to the handle you want (lowercase letters,
digits and underscore, up to 20).

Do this once, to make your key pair (a private key and a public key).
Press Enter at the passphrase prompt for none, or choose one:
  ssh-keygen -t ed25519 -f ~/.ssh/packet_social

Then copy these three lines. Change only H= and A= on the first line:
  H=yourhandle; A=YOUR_ANSWER; T=$(date +%%s)
  S=$(printf '%%s' "register $H $T" | ssh-keygen -Y sign -n packet.social -f ~/.ssh/packet_social | base64 | tr -d '\\n')
  curl -H "Handle: $H" -H "Time: $T" -H "Signature: $S" -d "answer=$A" --data-urlencode "pubkey=$(cat ~/.ssh/packet_social.pub)" https://packet.social/register

Your private key never leaves your computer. No password is ever sent.
The question changes every 10 to 20 minutes. If you get 403, ask again.
"""

WELCOME = """201 Created

==================================================
  welcome to packet.social, @%(h)s
==================================================

Your account exists. You proved you own your key, so no password is needed,
ever. Keep ~/.ssh/packet_social safe. If you lose it, this handle is gone.

POST something (change only the B= text, then copy all three lines):
  H=%(h)s; B='hello from the wire'; T=$(date +%%s)
  S=$(printf '%%s' "$H $T $B" | ssh-keygen -Y sign -n packet.social -f ~/.ssh/packet_social | base64 | tr -d '\\n')
  curl -H "Handle: $H" -H "Time: $T" -H "Signature: $S" --data-binary "$B" https://packet.social/post

READ the feed (no key needed):
  curl https://packet.social/feed

LIKE post 3 (a UDP packet, nothing prints back):
  echo -n 'LIKE:3' | nc -u -w1 packet.social 6969

Posts are 280 bytes at most, and can never be edited or deleted.
Every signed request is only valid for 5 minutes.
Stuck? Read the guide: https://github.com/kblssd/packet-social-web#readme
"""


def unauthorized():
    text(401, "401 Unauthorized\nmissing or bad signature. Check the Handle, Time and Signature "
              "headers, and that you used the same key you registered with.\n")


def register(headers, body, ip):
    if not allow("reg:" + ip, REGISTER_PER_HOUR, 3600):
        text(429, "429 Too Many Requests\n", {"Retry-After": "3600"})
    handle = headers.get("handle", "")
    if not HANDLE_RE.fullmatch(handle):
        text(400, "400 Bad Request\nhandle: lowercase letters, digits, underscore, 1 to 20 bytes\n")
    if handle in RESERVED:
        text(409, "409 Conflict\nhandle is taken\n")
    try:
        form = parse_qs(body.decode("utf-8"))
    except UnicodeDecodeError:
        form = {}
    answer = form.get("answer", [""])[0].strip()
    ok = False
    for bucket in (current_bucket(), current_bucket() - 1):
        if hmac.compare_digest(answer.lower(), challenge_for(ip, bucket)[1].lower()):
            ok = True
    if not ok:
        text(403, "403 Forbidden\nwrong answer, or the question changed. GET /register for the current one\n")
    pubkey = form.get("pubkey", [""])[0].strip()
    b64, why = parse_pubkey(pubkey)
    if b64 is None:
        text(400, "400 Bad Request\n%s\n" % why)
    check_signed(headers, lambda h, t: ("register %s %s" % (h, t)).encode(), pubkey_b64=b64)
    try:
        db.execute("INSERT INTO users (handle, salt, hash, created, pubkey) VALUES (?,?,?,?,?)",
                   (handle, b"", b"", int(time.time()), "ssh-ed25519 " + b64))
    except sqlite3.IntegrityError:
        text(409, "409 Conflict\nhandle is taken\n")
    log("register %s" % handle)
    text(201, WELCOME % {"h": handle})


def create_post(headers, body):
    handle = headers.get("handle", "")
    if HANDLE_RE.fullmatch(handle) and not allow("post:" + handle, POSTS_PER_10MIN, 600):
        text(429, "429 Too Many Requests\n", {"Retry-After": "60"})
    if not body:
        text(400, "400 Bad Request\nempty body\n")
    msg = clean_text(body)
    if msg is None:
        text(400, "400 Bad Request\nbody must be UTF-8 text without control characters\n")
    handle = check_signed(headers, lambda h, t: ("%s %s " % (h, t)).encode() + body)
    cur = db.execute("INSERT INTO posts (handle, t, body) VALUES (?,?,?)",
                     (handle, int(time.time()), body))
    pid = cur.lastrowid
    award(handle, "hard_mode")  # posted with a hand signed request
    log("post %d by %s" % (pid, handle))
    text(201, "201 Created\nlike it: echo -n 'LIKE:%d' | nc -u -w1 packet.social 6969\n" % pid,
         {"Location": "/post/%d" % pid})


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
