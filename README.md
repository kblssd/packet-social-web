# packet.social

A social network you have to learn networking to use.

There is no app, no login button and no feed to scroll. You read and write to
[packet.social](https://packet.social) from a terminal, one request at a time, with tools
that are already on your computer: `curl` for posts and `nc` for likes. If you do not yet
know what a port, a header or a status code is, you will by the time you make your first post.

The website is only a front door. It has three pages: **home** (how to connect), **man**
(the full manual) and **get /feed** (a read only window onto the latest posts).
Everything else happens in your terminal.

> The original browser simulator of this idea lives in
> [kblssd/packet-social](https://github.com/kblssd/packet-social). This repo is the real thing.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Quick start in five commands](#2-quick-start-in-five-commands)
3. [Step by step for a first timer](#3-step-by-step-for-a-first-timer)
4. [Cheat sheet](#4-cheat-sheet)
5. [Rules and limits](#5-rules-and-limits)
6. [Status codes](#6-status-codes)
7. [When something goes wrong](#7-when-something-goes-wrong)
8. [Things to try (learn while you use it)](#8-things-to-try-learn-while-you-use-it)
9. [Glossary](#9-glossary)
10. [Run your own server](#10-run-your-own-server)
11. [Security notes and known limits](#11-security-notes-and-known-limits)

---

## 1. What you need

**A terminal.** This is the text window where you type commands.

| System  | How to open one |
| ------- | --------------- |
| macOS   | Press Cmd + Space, type `Terminal`, press Enter. |
| Windows | Open the Start menu, type `PowerShell` or `Windows Terminal`, press Enter. WSL (Ubuntu inside Windows) is the smoothest option and the commands below work unchanged. |
| Linux   | Press Ctrl + Alt + T, or search for Terminal. |

**Two tools:**

- `curl` sends requests to websites. It is built in on macOS and Linux, and on Windows 10 and 11
  as `curl.exe`.
- `nc` (netcat) sends raw network packets. It is built in on macOS. On Debian or Ubuntu install it
  with `sudo apt install netcat-openbsd`. Windows has no `nc`, see the Windows notes below.

Check that they exist:

```
curl --version
nc -h
```

(`nc -h` printing a help screen or an error about options is fine. "command not found" is not.)

**Quotes matter.** Always wrap text in plain single quotes: `'like this'`. Do not use curly
"smart" quotes. If you paste commands from a chat app or a document and get odd errors, retype the quotes.

**Windows notes (PowerShell).** In PowerShell, `curl` is an alias for something else. Type
`curl.exe` instead of `curl`. To send a like without `nc`, use this instead:

```
$u = New-Object Net.Sockets.UdpClient
$b = [Text.Encoding]::ASCII.GetBytes('LIKE:1')
$u.Send($b, $b.Length, 'packet.social', 6969) | Out-Null
```

---

## 2. Quick start in five commands

Replace `you` and `password` with your own. Commands that start with `curl -u` need your account.

```
# 1. is the server alive?
curl https://packet.social/status

# 2. ask for the registration question, then answer it and register
curl https://packet.social/register
curl -u 'you:password' -d 'answer=...' https://packet.social/register

# 3. read the feed
curl https://packet.social/feed

# 4. post
curl -u 'you:password' --data-binary 'hello from the wire' https://packet.social/post

# 5. like post number 3 (a UDP packet, no reply will ever come)
echo -n 'LIKE:3' | nc -u -w1 packet.social 6969
```

The rest of this page explains each one slowly.

---

## 3. Step by step for a first timer

### Step 1. Check that the server is alive

```
curl https://packet.social/status
```

You should see:

```
200 OK
users: 12
posts: 40
```

(Your numbers will differ.) `200 OK` is the server saying the request worked. You just made
your first HTTPS request. The `https` part means the conversation was encrypted.

### Step 2. Register

You will be asked one simple networking question. Every answer is in the
[manual](https://packet.social/#manual), on this page, or one command away. A search engine
is allowed. This is a lesson, not an exam.

Ask for your question:

```
curl https://packet.social/register
```

You get something like:

```
question: which port does HTTPS use?
answer with: answer=...
```

Work out the answer (here it is `443`), then register. Pick a **handle** (lowercase letters,
digits and underscore, up to 20 characters) and a **password** (8 to 128 characters). Use a
password you use nowhere else.

```
curl -u 'you:password' -d 'answer=443' https://packet.social/register
```

On success the server replies `201 Created`. Your account exists.

The question you are asked can be any one of these, so here is where to find each answer:

| Question | Where to look |
| -------- | ------------- |
| What is the IPv4 loopback address? | Run `ping localhost` and read the number in brackets. |
| What is the IP address of packet.social? | Run `nslookup packet.social`. |
| Which port does HTTPS use? / plain HTTP? / SSH? | The manual, or a web search for "default port HTTPS". |
| What status code means Not Found / Unauthorized / Created? | The [status codes](#6-status-codes) table below. |
| Do likes travel over tcp or udp? | The home page and the manual. |
| Which port do likes use? | The home page and the manual. |
| What is the maximum size of a post, in bytes? | The manual and [Rules and limits](#5-rules-and-limits). |
| How many bits are in an IPv4 address? | A web search. |

Answers are not case sensitive. A question stays valid for 10 to 20 minutes and is tied to your
internet address. If the question changes before you answer, just ask again and answer the new one.

> **Keep your password out of your history.** Typing it in a command saves it in your terminal
> history. If you leave off the password, curl asks for it and does not save it:
> `curl -u 'you' --data-binary 'hi' https://packet.social/post`
>
> **There is no password reset.** If you forget it, that account is gone. Register a new handle.

### Step 3. Read the feed

```
curl https://packet.social/feed
```

Reading needs no account. You see the newest 50 posts, newest first:

```
#5 @halfopen t=1785961012 likes=1
Still waiting on that SYN-ACK. I am not going to close the connection.

#4 @nagle t=1785957740 likes=0
I held this post for forty milliseconds before sending it. You are welcome.
```

Each post has an **id** (`#5`), an **author** (`@halfopen`), a **time** (`t=`) and a **like count**.

Times are "seconds since 1 January 1970" (called Unix time or epoch time). Nobody here says
"2 hours ago". Turn one into a readable date with:

```
date -r 1785961012        # macOS
date -d @1785961012       # Linux
```

You can also read one post, or one person:

```
curl https://packet.social/post/5
curl https://packet.social/profile/halfopen
```

### Step 4. Post

```
curl -u 'you:password' --data-binary 'hello from the wire' https://packet.social/post
```

You get `201 Created`. To see which id your post got, add `-i` to show the headers:

```
curl -i -u 'you:password' --data-binary 'my second post' https://packet.social/post
```

Look for the line `Location: /post/42`.

Things to know about posting:

- A post is at most **280 bytes**. Bytes, not characters: a plain letter is 1 byte, an accented
  letter is 2, an emoji is usually 4. Check a length with
  `printf '%s' 'your text' | wc -c`.
- Use `--data-binary`, not `-d`. The `-d` option removes newlines, so your post would be mangled.
- To post several lines, pipe them in:
  ```
  printf 'line one\nline two' | curl -u 'you:password' --data-binary @- https://packet.social/post
  ```
- Posts must be plain text. Control characters (the invisible ones terminals obey) are refused, so
  nobody can write a post that rewrites your screen.
- Posts cannot be edited or deleted. `DELETE` returns `405` and always will.

### Step 5. Like a post

A like is a single **UDP packet** sent to port 6969. Unlike everything above, there is no reply of
any kind. Nothing prints, and that is normal.

```
echo -n 'LIKE:5' | nc -u -w1 packet.social 6969
```

Breaking that down:

- `echo -n 'LIKE:5'` prints the text `LIKE:5` with no trailing newline (`-n`).
- `|` (a pipe) hands that text to the next command.
- `nc -u -w1 packet.social 6969` sends it as a UDP packet (`-u`) to packet.social on port 6969,
  and gives up after one second (`-w1`).

UDP has no delivery report and no retry. If your packet is lost on the way, nobody knows. To see
whether it arrived, read the post again and look at its `likes=` number:

```
curl https://packet.social/post/5
```

Each address can like each post once.

### Step 6. Look at the website

Open [packet.social](https://packet.social) in a browser. The status light at the top says `UP`
when the server is running, and the **get /feed** page shows the same posts you read from the terminal.
You cannot post, like or delete from the website. It is a window, not a door.

---

## 4. Cheat sheet

```
curl https://packet.social/status                          # is it alive, how many users and posts
curl https://packet.social/register                        # get a registration question
curl -u 'me:pw' -d 'answer=..' https://packet.social/register   # register
curl https://packet.social/feed                            # newest 50 posts
curl https://packet.social/feed.json                       # the same, as JSON
curl https://packet.social/post/7                          # one post
curl https://packet.social/profile/me                      # one person
curl -u 'me:pw' --data-binary 'text' https://packet.social/post  # post
echo -n 'LIKE:7' | nc -u -w1 packet.social 6969            # like post 7
```

Handy curl flags: `-i` show response headers, `-v` show the whole conversation including the
TLS handshake, `-s` hide the progress meter, `-X DELETE` choose a different method.

---

## 5. Rules and limits

| Rule | Value |
| ---- | ----- |
| Post size | 280 bytes |
| Handle | lowercase letters, digits, underscore, 1 to 20 characters |
| Password | 8 to 128 bytes |
| Requests | 60 per minute per address |
| Posts | 20 per 10 minutes per handle |
| Registration attempts | 5 per hour per address |
| Likes | 1 per post per address |
| Feed | newest 50 posts |
| Edit or delete | not possible |

Go over a limit and the server answers `429 Too Many Requests` with a `Retry-After` header
telling you how many seconds to wait.

---

## 6. Status codes

Every HTTP answer starts with a three digit code. The first digit tells you the family:
2 means success, 4 means you made a mistake, 5 means the server did.

| Code | Name | What it means here |
| ---- | ---- | ------------------ |
| 200 | OK | The request worked. |
| 201 | Created | Your post or account now exists. |
| 400 | Bad Request | You sent something malformed: bad handle, short password, empty or non text post. |
| 401 | Unauthorized | Missing or wrong handle or password. |
| 403 | Forbidden | Wrong registration answer, or the question changed. |
| 404 | Not Found | No such post or profile. |
| 405 | Method Not Allowed | DELETE, PUT and friends do not exist here. |
| 409 | Conflict | That handle is taken. |
| 413 | Payload Too Large | Your post is over 280 bytes. |
| 429 | Too Many Requests | Slow down, then retry after the wait. |

---

## 7. When something goes wrong

**`zsh: command not found: 10.1.2.3`** (or any answer you typed). You typed the answer as its
own command. The answer goes inside the register command, in `-d 'answer=...'`.

**`401 Unauthorized`.** The handle or password is wrong, or you forgot `-u`. Handles are all
lowercase. Wrap the credentials in single quotes so symbols like `!` and `$` are not
interpreted by your shell.

**`403 Forbidden` when registering.** The answer was wrong, or the question changed. Run
`curl https://packet.social/register` again and answer the question you get now.

**`400 Bad Request`.** Read the line under it, the server says what it did not like. Common
causes: a handle with capitals or symbols, a password under 8 characters, an empty post, or a post
containing invisible control characters.

**`409 Conflict`.** Someone has that handle. Choose another.

**`413 Payload Too Large`.** Over 280 bytes. Count with `printf '%s' 'text' | wc -c` and cut
it down. Emoji and accented letters use more than one byte each.

**`429 Too Many Requests`.** You hit a rate limit. Run the failing command with `curl -i` to
see the `Retry-After` header, wait that many seconds, then try again.

**`nc` printed nothing.** That is correct. UDP never replies. Check the like with
`curl https://packet.social/post/ID`.

**The like count did not change.** The post id must exist, you can like each post once per
address, and UDP packets can be lost or blocked. Some networks block outgoing UDP on unusual
ports. Try again from another network, such as a phone hotspot.

**`curl: command not found`.** Install it, or on Windows use `curl.exe` in PowerShell.

**An SSL or certificate error.** Check that your computer's date and time are correct.
Certificates are only valid within a date range.

**`curl: (6) Could not resolve host`.** Your computer cannot look up the name. Check your internet
connection and try `nslookup packet.social`.

---

## 8. Things to try (learn while you use it)

The point of this place is to learn. Try these and read what comes back:

```
curl -i https://packet.social/status      # see the headers the server sends
curl -v https://packet.social/status      # watch the TLS handshake and the whole request
curl -X DELETE https://packet.social/post/1   # get a 405 and read the Allow header
curl -i https://packet.social/post/999999     # a 404
curl -i -u 'nobody:nothing1234' --data-binary 'x' https://packet.social/post   # a 401
nslookup packet.social                    # what address does the name point to
ping packet.social                        # does it answer, how fast
traceroute packet.social                  # every router between you and the server
```

Catch a UDP packet yourself. In one terminal, listen on your own computer:

```
nc -u -l 6969
```

(On some systems it is `nc -u -l -p 6969`.) In a second terminal, send to yourself:

```
echo -n 'LIKE:1' | nc -u -w1 127.0.0.1 6969
```

Your first terminal prints what arrived. That is exactly what the server receives when you like a post.

Look at the raw bytes of a post:

```
printf 'hello' | xxd
```

---

## 9. Glossary

| Word | Plain meaning |
| ---- | ------------- |
| Terminal | A window where you type commands instead of clicking. |
| Command | One line you type and run. |
| `curl` | A tool that makes web requests from the terminal. |
| `nc` / netcat | A tool that sends or receives raw network packets. |
| Address (IP) | The number that identifies a computer on a network, like `46.101.187.251`. |
| Loopback | The address that means "this computer itself", `127.0.0.1`. |
| DNS | The phone book that turns `packet.social` into an IP address. |
| Port | A numbered door on a computer. Different services listen on different doors. |
| HTTP / HTTPS | The language browsers and curl use to ask for pages. The S means encrypted. |
| TLS | The encryption HTTPS uses. The padlock in your browser. |
| TCP | A way to send data with checking and retries. Used for posts. |
| UDP | A way to send data with no checking and no reply. Used for likes. |
| Packet | One small chunk of data on the network. |
| Header | Extra lines of information sent with a request or a reply. |
| Status code | The three digit result of an HTTP request. |
| Basic auth | Sending a handle and password with a request (`curl -u`). Safe over TLS. |
| Byte | The unit of size. A plain letter is one byte. |
| Unix time | Seconds since 1 January 1970, used for timestamps. |
| Rate limit | A cap on how often you may ask. |

---

## 10. Run your own server

This section is for people who want to host a copy. You do not need it to use packet.social.

### What is in this repo

| File | Purpose |
| ---- | ------- |
| `index.html` | The whole website: home, manual and feed, in one file. |
| `server.py` | The server. Python standard library only, no packages to install. |
| `backup.py` | Nightly database backup. |
| `deploy/Caddyfile` | Caddy config: sends API paths to the server, serves the website for the rest. |
| `deploy/packet.service` | systemd unit that runs the server. |
| `deploy/packet-backup.service`, `deploy/packet-backup.timer` | Runs the backup every night at 03:30. |
| `deploy/install.sh` | Installs and restarts everything. |

### How it fits together

```
you ----https 443----> Caddy ----http 127.0.0.1:8080----> server.py ----> SQLite
you ----udp   6969---------------------------------------> server.py
```

- Caddy gets and renews the HTTPS certificate on its own and forwards the API paths
  (`/status`, `/register`, `/feed`, `/post`, `/profile` and their JSON forms) to the Python server.
  Anything else is served from `/var/www/packet`.
- The Python server speaks plain text HTTP on localhost only, and listens for UDP on 6969.
- Data lives in one SQLite file, `/var/lib/packet/packet.db`, in WAL mode.
- The server runs as a locked down `packet` user with no shell and write access only to
  `/var/lib/packet`. Users never get a shell on your machine.

### Try it on your own computer first

You need Python 3.8 or newer.

```
git clone https://github.com/kblssd/packet-social-web.git
cd packet-social-web
PACKET_DB=/tmp/packet.db PACKET_HTTP_PORT=8080 PACKET_UDP_PORT=6969 python3 server.py
```

In another terminal, replace `https://packet.social` with `http://127.0.0.1:8080`:

```
curl http://127.0.0.1:8080/status
curl http://127.0.0.1:8080/register
```

### Settings (environment variables)

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `PACKET_DB` | `/var/lib/packet/packet.db` | Database file. |
| `PACKET_HTTP_HOST` | `127.0.0.1` | HTTP listen address. Keep it on localhost behind Caddy. |
| `PACKET_HTTP_PORT` | `8080` | HTTP listen port. Must match `deploy/Caddyfile`. |
| `PACKET_UDP_HOST` | `0.0.0.0` | UDP listen address. |
| `PACKET_UDP_PORT` | `6969` | UDP port for likes. |
| `PACKET_IP` | `46.101.187.251` | The server's public IP, used for one registration question. Set it to yours in `deploy/packet.service`. |

### Deploy on a small VPS (Ubuntu 24.04)

1. Create a server with an SSH key, 512 MB of RAM is enough (add a swap file). Point your
   domain's `A` records (`@` and `www`) at its IP address.
2. Create a normal sudo user, then open the firewall for exactly these ports:
   ```
   ufw allow OpenSSH
   ufw allow 80/tcp
   ufw allow 443/tcp
   ufw allow 6969/udp
   ufw enable
   ```
3. Install Caddy (see the official Caddy install page for Debian and Ubuntu) and git.
4. Edit the domain in `deploy/Caddyfile` and the IP in `deploy/packet.service`.
5. Clone and install:
   ```
   git clone https://github.com/kblssd/packet-social-web.git /opt/packet-social-web
   bash /opt/packet-social-web/deploy/install.sh
   ```
   The script ends by printing `active` three times and the `/status` reply.

**Update after changing the code:**

```
cd /opt/packet-social-web && git pull && bash deploy/install.sh
```

**Watch the logs:**

```
journalctl -u packet -f
```

### Backups and restore

`backup.py` writes a consistent copy to `/var/lib/packet/backups/` every night at 03:30
(server time) and keeps the newest 14. Copy those files off the machine now and then, a backup on
the same disk does not survive losing the disk.

Restore:

```
systemctl stop packet
cp /var/lib/packet/backups/FILE.db /var/lib/packet/packet.db
rm -f /var/lib/packet/packet.db-wal /var/lib/packet/packet.db-shm
chown packet:packet /var/lib/packet/packet.db
systemctl start packet
```

### Changing the registration questions

The questions are the `QUESTIONS` list near the top of `server.py`, each a pair of question and
answer. Edit the list, keep every answer findable in the manual or one command away, then redeploy.

---

## 11. Security notes and known limits

What is in place:

- **Encryption.** All reading and writing goes over HTTPS. Caddy manages the certificate.
- **Passwords** are stored as scrypt hashes with a random salt. The server never keeps them in
  plain text, and a request for an unknown handle takes about as long as a wrong password.
- **Likes** are stored against a keyed hash of the sender's address, not the address itself.
- **Rate limits** on requests, posts, registration and UDP.
- **Input checks.** Size limits are enforced before the body is read, posts must be valid
  UTF-8 with no control characters, and all database queries use bound parameters.
- **Least privilege.** The service runs as a no shell user with a read only view of the system
  except its data folder.

Honest limits:

- **UDP source addresses can be forged.** A determined person can fake likes. Likes are
  best effort by design.
- **Shared addresses share limits.** People behind one router or mobile carrier share a
  rate limit and a like.
- **No password reset, no account deletion, no edit or delete of posts.** That is deliberate for
  now, but it means mistakes are permanent.
- **No moderation tools.** The operator can remove rows directly in the SQLite file. There is
  no in app way to do it.
- **One server, one database.** There is no clustering and no failover.
- **The IP in one registration question is configured by hand.** If your address changes,
  update `PACKET_IP` and redeploy.
