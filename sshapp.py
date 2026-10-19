#!/usr/bin/env python3
"""packet.social SSH app.

Connect with:  ssh -p 2222 packet.social
Your SSH key is your account. The first time, you pick a handle. After that the
server recognises your key. Needs the python3-asyncssh package.

This process shares the SQLite database and the helper functions of server.py.
"""
import asyncio
import hmac
import os
import re
import sys
import textwrap
import time
import unicodedata

import asyncssh

import server as core

SSH_HOST = os.environ.get("PACKET_SSH_HOST", "0.0.0.0")
SSH_PORT = int(os.environ.get("PACKET_SSH_PORT", "2222"))
HOST_KEY = os.environ.get("PACKET_SSH_HOST_KEY", "/var/lib/packet/ssh_host_key")

IDLE = 900                 # seconds of silence before we hang up
CONN_PER_MIN = 20          # new connections per minute per address
MAX_PER_IP = 4             # open sessions per address
MAX_TOTAL = 60             # open sessions overall
NEW_ACCOUNTS_PER_HOUR = 3  # new handles per address per hour
FEED_DEFAULT, FEED_MAX = 10, 30

ACTIVE = {}                # address -> open sessions
db = core.db


def port_arg():
    return "" if SSH_PORT == 22 else "-p %d " % SSH_PORT


CHALLENGES = [
    "run ping -c 3 packet.social. What is the average round trip time in ms? Post it.",
    "run dig +short packet.social. Which IP address do you get? Is it an A or an AAAA record?",
    "run traceroute packet.social (tracert on Windows). How many hops to reach the server? Post the number.",
    "run curl -I https://packet.social/status. Which header tells the client how long the body is?",
    "run nc -vz packet.social 443. What does success tell you about TCP and the port?",
    "explain TCP versus UDP in one post. 280 bytes is plenty.",
    "run curl -v https://packet.social/status and find the TLS version. Which one is it?",
    "run echo -n hello | base64 and then decode it with base64 -d. Why is base64 not encryption?",
    "find your own local IP address. Is it in 10.x, 172.16-31.x or 192.168.x? Do not post the full address.",
    "what does TTL do in a ping reply, and what happens to a packet when it reaches 0?",
    "send a like by hand: echo -n 'LIKE:1' | nc -u -w1 packet.social 6969. Why does nothing come back?",
    "run arp -a. What is a MAC address, and which layer uses it?",
    "which port does DNS use, and is it TCP, UDP or both?",
    "run ssh -v %spacket.social and find the line that starts with kex: algorithm. Which algorithm did you get?" % port_arg(),
]
for _c in CHALLENGES:
    assert len(("Challenge #99: " + _c).encode()) <= core.MAX_POST, _c

LESSONS = [
    ("alive?", "ping -c 3 packet.social"),
    ("where is it?", "dig +short packet.social"),
    ("which road?", "traceroute packet.social"),
    ("speak HTTP", "curl -i https://packet.social/status"),
    ("send a UDP like", "echo -n 'LIKE:1' | nc -u -w1 packet.social 6969"),
    ("hard mode", "signed post, see the README"),
]

BADGE_TEXT = {
    "first_post": "first post from the terminal",
    "first_like": "first like",
    "hard_mode": "hard mode: posted with a hand signed request",
}

HELP = [
    ("help", "this list"),
    ("feed [n]", "newest posts (default %d, max %d)" % (FEED_DEFAULT, FEED_MAX)),
    ("read ID", "one post"),
    ("post [text]", "write a post, 280 bytes at most"),
    ("like ID", "like a post"),
    ("profile [name]", "a person (default: you)"),
    ("learn", "today's challenge and the learning ladder"),
    ("netinfo", "what the network shows about this connection"),
    ("status", "server numbers"),
    ("clear", "clear the screen"),
    ("exit", "disconnect"),
]
GUEST_ONLY = {"post", "like"}  # not allowed for guests

RESERVED_NOTE = "that handle is taken"


# ---------------------------------------------------------------- helpers

def key_string(key):
    parts = key.export_public_key("openssh").decode().split()
    return parts[0] + " " + parts[1]


def safe(text):
    """Strip control characters (and so escape sequences) from anything we print."""
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")


def ago(t):
    d = max(0, int(time.time()) - t)
    if d < 60:
        return "%ds ago" % d
    if d < 3600:
        return "%dm ago" % (d // 60)
    if d < 86400:
        return "%dh ago" % (d // 3600)
    return "%dd ago" % (d // 86400)


def handle_for_key(keystr):
    row = db.execute("SELECT handle FROM users WHERE pubkey=?", (keystr,)).fetchone()
    return row[0] if row else None


def like_id(fp):
    return hmac.new(core.SECRET, b"ssh:" + fp.encode(), "sha256").hexdigest()[:20]


def todays_challenge():
    return db.execute("SELECT id, body FROM posts WHERE handle='packet' ORDER BY id DESC LIMIT 1").fetchone()


def post_daily():
    day = int(time.time() // 86400)
    row = db.execute("SELECT v FROM meta WHERE k='last_daily'").fetchone()
    if row and int(bytes(row[0]).decode()) >= day:
        return
    n = db.execute("SELECT COUNT(*) FROM posts WHERE handle='packet'").fetchone()[0] + 1
    body = ("Challenge #%d: %s" % (n, CHALLENGES[(n - 1) % len(CHALLENGES)])).encode()
    db.execute("INSERT INTO posts (handle, t, body) VALUES ('packet', ?, ?)", (int(time.time()), body))
    db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('last_daily', ?)", (str(day).encode(),))
    core.log("daily challenge %d posted" % n)


async def daily_loop():
    while True:
        try:
            post_daily()
        except Exception as exc:
            core.log("daily error: %r" % (exc,))
        await asyncio.sleep(1800)


# ---------------------------------------------------------------- session

class Quit(Exception):
    pass


class Session:
    def __init__(self, process):
        self.p = process
        self.conn = process.channel.get_connection()
        self.keystr = process.get_extra_info("keystr")
        self.fp = process.get_extra_info("fp")
        self.ip = (process.get_extra_info("peername") or ("?", 0))[0]
        self.handle = None
        self.guest = self.keystr is None
        self.is_new = False

    # -- output
    def w(self, text=""):
        self.p.stdout.write(text.replace("\r\n", "\n") + "\n")

    def raw(self, text):
        self.p.stdout.write(text)

    @property
    def width(self):
        try:
            cols = self.p.get_terminal_size()[0]
        except Exception:
            cols = 0
        return max(40, min(cols or 80, 100))

    def wrap(self, text, indent="  "):
        out = []
        for line in safe(text).split("\n"):
            out.extend(textwrap.wrap(line, self.width - len(indent), replace_whitespace=False,
                                     break_long_words=True) or [""])
        return "\n".join(indent + l for l in out)

    # -- input
    async def read(self, prompt):
        self.raw(prompt)
        while True:
            try:
                line = await asyncio.wait_for(self.p.stdin.readline(), IDLE)
            except asyncio.TimeoutError:
                self.w("\nidle for %d minutes, goodbye." % (IDLE // 60))
                raise Quit
            except (asyncssh.TerminalSizeChanged, asyncssh.BreakReceived):
                continue
            except (asyncssh.SignalReceived, asyncssh.ConnectionLost, ConnectionError):
                raise Quit
            if line == "":
                raise Quit
            return line.strip()

    # -- flow
    async def run(self):
        command = self.p.command
        if not command:
            self.banner()
        if not self.guest:
            self.handle = handle_for_key(self.keystr)
            if self.handle is None:
                if command:
                    self.w("this key has no handle yet. Run: ssh %spacket.social" % port_arg())
                    return
                await self.onboard()
                if self.handle is None:
                    return
        if command:
            await self.dispatch(command.strip())
            return
        self.welcome()
        while True:
            who = self.handle or "guest"
            line = await self.read("%s@packet:~$ " % who)
            if not line:
                continue
            await self.dispatch(line)

    def banner(self):
        self.w()
        self.w("  [][]   packet.social")
        self.w("  [][]   a social network you reach with a terminal")
        self.w()

    def welcome(self):
        if self.guest:
            self.w("guest mode: you can read, but not post or like.")
            self.w("to join, make a key once, then connect without 'guest@':")
            self.w("  ssh-keygen -t ed25519")
            self.w("  ssh %spacket.social" % port_arg())
        else:
            self.w("welcome%s, @%s. Type help for commands." % ("" if self.is_new else " back", self.handle))
        c = todays_challenge()
        if c:
            self.w()
            self.w("today's challenge (post #%d):" % c[0])
            self.w(self.wrap(bytes(c[1]).decode("utf-8")))
        self.w()

    async def onboard(self):
        self.w("this key is new here. Pick a handle to create your account.")
        self.w("lowercase letters, digits and underscore, up to 20. Your key is your password,")
        self.w("so there is nothing else to remember. Keep your private key safe.")
        self.w()
        for _ in range(5):
            h = (await self.read("choose a handle: ")).lower()
            if not core.HANDLE_RE.fullmatch(h):
                self.w("not valid. Use a to z, 0 to 9 and underscore, 1 to 20 characters.")
                continue
            if h in core.RESERVED or db.execute("SELECT 1 FROM users WHERE handle=?", (h,)).fetchone():
                self.w(RESERVED_NOTE + ". Try another.")
                continue
            if not core.allow("newacct:" + self.ip, NEW_ACCOUNTS_PER_HOUR, 3600):
                self.w("too many new accounts from this address. Try again later.")
                return
            try:
                db.execute("INSERT INTO users (handle, salt, hash, created, pubkey) VALUES (?,?,?,?,?)",
                           (h, b"", b"", int(time.time()), self.keystr))
            except Exception:
                self.w(RESERVED_NOTE + ". Try another.")
                continue
            self.handle = h
            self.is_new = True
            core.log("ssh register %s" % h)
            self.w()
            self.w("done. You are @%s." % h)
            self.w()
            return
        self.w("too many tries, goodbye.")

    async def dispatch(self, line):
        cmd, _, rest = line.partition(" ")
        cmd, rest = cmd.lower(), rest.strip()
        if self.guest and cmd in GUEST_ONLY:
            self.w("guests cannot %s. Make a key and join, see above." % cmd)
            return
        fn = {
            "help": self.c_help, "?": self.c_help, "feed": self.c_feed, "read": self.c_read,
            "post": self.c_post, "like": self.c_like, "profile": self.c_profile, "whoami": self.c_profile,
            "learn": self.c_learn, "netinfo": self.c_netinfo, "status": self.c_status,
            "clear": self.c_clear, "exit": self.c_exit, "quit": self.c_exit, "logout": self.c_exit,
        }.get(cmd)
        if fn is None:
            self.w("command not found: %s. Type help." % safe(cmd)[:30])
            return
        await fn(rest)

    # -- commands
    async def c_help(self, rest):
        for name, desc in HELP:
            self.w("  %-16s %s" % (name, desc))

    def show_post(self, r):
        pid, who, t, body, likes = r
        self.w("#%d  @%s  %s  %d like%s" % (pid, who, ago(t), likes, "" if likes == 1 else "s"))
        self.w(self.wrap(bytes(body).decode("utf-8")))
        self.w()

    async def c_feed(self, rest):
        n = FEED_DEFAULT
        if rest:
            if not rest.isdigit():
                self.w("usage: feed [number]")
                return
            n = max(1, min(int(rest), FEED_MAX))
        rows = db.execute(core.POST_SQL + "ORDER BY p.id DESC LIMIT ?", (n,)).fetchall()
        if not rows:
            self.w("no posts yet. Be the first: post hello")
            return
        for r in rows:
            self.show_post(r)

    async def c_read(self, rest):
        if not rest.isdigit() or len(rest) > 12:
            self.w("usage: read 3")
            return
        r = db.execute(core.POST_SQL + "WHERE p.id=?", (int(rest),)).fetchone()
        if r is None:
            self.w("404 not found")
            return
        self.show_post(r)

    async def c_post(self, rest):
        if not rest:
            rest = await self.read("write your post (280 bytes max): ")
        if not rest:
            self.w("nothing posted.")
            return
        body = rest.encode("utf-8")
        if len(body) > core.MAX_POST:
            self.w("413 too long: %d bytes, the limit is %d." % (len(body), core.MAX_POST))
            return
        if core.clean_text(body) is None:
            self.w("400 text only, no control characters.")
            return
        if not core.allow("post:" + self.handle, core.POSTS_PER_10MIN, 600):
            self.w("429 slow down. %d posts per 10 minutes." % core.POSTS_PER_10MIN)
            return
        cur = db.execute("INSERT INTO posts (handle, t, body) VALUES (?,?,?)",
                         (self.handle, int(time.time()), body))
        core.log("ssh post %d by %s" % (cur.lastrowid, self.handle))
        self.w("201 created: post #%d (%d of %d bytes)" % (cur.lastrowid, len(body), core.MAX_POST))
        if db.execute("SELECT 1 FROM badges WHERE handle=? AND name='first_post'", (self.handle,)).fetchone() is None:
            core.award(self.handle, "first_post")
            self.w("badge earned: %s" % BADGE_TEXT["first_post"])

    async def c_like(self, rest):
        if not rest.isdigit() or len(rest) > 12:
            self.w("usage: like 3")
            return
        pid = int(rest)
        if db.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone() is None:
            self.w("404 not found")
            return
        if not core.allow("like:" + self.handle, core.RATE_PER_MIN, 60):
            self.w("429 slow down.")
            return
        cur = db.execute("INSERT OR IGNORE INTO likes (post_id, who, t) VALUES (?,?,?)",
                         (pid, like_id(self.fp), int(time.time())))
        if cur.rowcount:
            self.w("liked #%d." % pid)
            if db.execute("SELECT 1 FROM badges WHERE handle=? AND name='first_like'", (self.handle,)).fetchone() is None:
                core.award(self.handle, "first_like")
                self.w("badge earned: %s" % BADGE_TEXT["first_like"])
        else:
            self.w("you already liked #%d." % pid)
        self.w("the raw way, from any computer: echo -n 'LIKE:%d' | nc -u -w1 packet.social 6969" % pid)

    async def c_profile(self, rest):
        h = (rest or self.handle or "").lower()
        if not core.HANDLE_RE.fullmatch(h):
            self.w("usage: profile name")
            return
        u = db.execute("SELECT created FROM users WHERE handle=?", (h,)).fetchone()
        if u is None:
            self.w("404 not found")
            return
        n = db.execute("SELECT COUNT(*) FROM posts WHERE handle=?", (h,)).fetchone()[0]
        got = db.execute("SELECT COUNT(*) FROM likes WHERE post_id IN (SELECT id FROM posts WHERE handle=?)", (h,)).fetchone()[0]
        self.w("@%s" % h)
        self.w("  joined   %s" % time.strftime("%Y-%m-%d", time.gmtime(u[0])))
        self.w("  posts    %d" % n)
        self.w("  likes    %d received" % got)
        names = [r[0] for r in db.execute("SELECT name FROM badges WHERE handle=? ORDER BY t", (h,))]
        for name in names:
            self.w("  badge    %s" % BADGE_TEXT.get(name, name))
        if not names:
            self.w("  badge    none yet")

    async def c_learn(self, rest):
        c = todays_challenge()
        if c:
            self.w("today's challenge (post #%d):" % c[0])
            self.w(self.wrap(bytes(c[1]).decode("utf-8")))
            self.w()
        self.w("the ladder. Run these in a second terminal on your own computer:")
        for i, (name, cmd) in enumerate(LESSONS, 1):
            self.w("  %d. %-16s %s" % (i, name, cmd))
        self.w()
        self.w("answer a challenge with: post <your answer>")

    async def c_netinfo(self, rest):
        info = self.conn.get_extra_info
        peer = self.p.get_extra_info("peername") or ("?", 0)
        sock = self.p.get_extra_info("sockname") or ("?", 0)

        def g(name):
            try:
                v = info(name)
            except Exception:
                v = None
            return v if v else "n/a"

        self.w("this connection, as the server sees it:")
        self.w("  your address       %s port %s" % (peer[0], peer[1]))
        self.w("  server address     %s port %s" % (sock[0], sock[1]))
        self.w("  transport          TCP, with SSH on top")
        self.w("  cipher             %s" % g("send_cipher"))
        self.w("  your ssh client    %s" % safe(str(g("client_version")))[:60])
        if self.fp:
            self.w("  your key           %s" % self.fp)
        self.w()
        self.w("each line is a lesson: addresses (L3), ports and TCP (L4), SSH encryption (L6).")

    async def c_status(self, rest):
        users, posts = core.counts()
        self.w("users %d, posts %d, likes %d" % (users, posts, core.like_count()))

    async def c_clear(self, rest):
        self.raw("\x1b[2J\x1b[H")

    async def c_exit(self, rest):
        self.w("bye.")
        raise Quit


# ---------------------------------------------------------------- server

class PacketServer(asyncssh.SSHServer):
    def connection_made(self, conn):
        self.conn = conn
        self.ip = (conn.get_extra_info("peername") or ("?", 0))[0]
        self.counted = False
        if (not core.allow("sshconn:" + self.ip, CONN_PER_MIN, 60)
                or ACTIVE.get(self.ip, 0) >= MAX_PER_IP or sum(ACTIVE.values()) >= MAX_TOTAL):
            conn.abort()
            return
        ACTIVE[self.ip] = ACTIVE.get(self.ip, 0) + 1
        self.counted = True

    def connection_lost(self, exc):
        if self.counted:
            n = ACTIVE.get(self.ip, 1) - 1
            if n <= 0:
                ACTIVE.pop(self.ip, None)
            else:
                ACTIVE[self.ip] = n

    def begin_auth(self, username):
        return username != "guest"          # guest@ needs no key, read only

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        self.conn.set_extra_info(keystr=key_string(key), fp=key.get_fingerprint())
        return True                          # asyncssh still checks the client holds the key


async def handle(process):
    s = Session(process)
    try:
        await s.run()
    except Quit:
        pass
    except Exception as exc:                 # one bad session must never stop the server
        core.log("ssh error: %r" % (exc,))
        try:
            process.stdout.write("\nsomething broke on our side. Try again.\n")
        except Exception:
            pass
    finally:
        process.exit(0)


def load_host_key():
    if not os.path.exists(HOST_KEY):
        key = asyncssh.generate_private_key("ssh-ed25519")
        fd = os.open(HOST_KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key.export_private_key())
        core.log("created ssh host key %s" % HOST_KEY)
    return HOST_KEY


async def main():
    db.execute("INSERT OR IGNORE INTO users (handle, salt, hash, created) VALUES ('packet', ?, ?, ?)",
               (b"", b"", int(time.time())))
    await asyncssh.create_server(
        PacketServer, SSH_HOST, SSH_PORT,
        server_host_keys=[load_host_key()],
        process_factory=handle, encoding="utf-8", line_editor=True,
        login_timeout=30, keepalive_interval=30, keepalive_count_max=3)
    core.log("ssh %s:%d  db %s" % (SSH_HOST, SSH_PORT, core.DB_PATH))
    asyncio.create_task(daily_loop())
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncssh.Error):
        sys.exit(0)
