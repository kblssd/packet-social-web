# packet.social

A social network you reach with a terminal, built for people learning networking.

There is no app and no feed to scroll. You connect with one command, and every post you read,
send or like crosses the same network you are trying to learn.

```
ssh -p 2222 packet.social
```

No install and no password. Your SSH key is your account. The first time, you pick a handle.
After that the server recognises your key. [packet.social](https://packet.social) is only a front
door: it shows whether the server is up, the live numbers and how to connect.

Two ways in, same network:

- **Easy mode:** the SSH app. A real terminal with a feed, posting, likes, profiles, a daily
  challenge and a `netinfo` command that shows what the network sees about your own connection.
- **Hard mode:** the raw tools (`curl`, `ssh-keygen`, `nc`). You build the requests by hand and
  sign them yourself. Doing it earns the `hard_mode` badge. This is where the deep learning is.

> The original browser simulator of this idea lives in
> [kblssd/packet-social](https://github.com/kblssd/packet-social). This repo is the real thing.

---

## Contents

0. [Easy mode: the SSH app](#0-easy-mode-the-ssh-app)
1. [What you need for hard mode](#1-what-you-need-for-hard-mode)
2. [Hard mode quick start](#2-hard-mode-quick-start)
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

## 0. Easy mode: the SSH app

Open a terminal and run:

```
ssh -p 2222 packet.social
```

**No SSH key yet?** Make one once and press Enter at every question:

```
ssh-keygen -t ed25519
```

**Want a look first?** `ssh -p 2222 guest@packet.social` needs no key and is read only.

The first time with a new key you pick a handle (lowercase letters, digits and underscore, up to
20). Then you get a prompt and these commands:

| Command | What it does |
| ------- | ------------ |
| `help` | List the commands. |
| `feed [n]` | Newest posts, 10 by default, 30 at most. |
| `read ID` | One post. |
| `post [text]` | Write a post, 280 bytes at most. Without text it asks you. |
| `like ID` | Like a post, once each. |
| `profile [name]` | A person: joined date, posts, likes received, badges. |
| `learn` | Today's challenge and the learning ladder. |
| `netinfo` | Your address and port, the server's, the cipher and your key, as the server sees them. |
| `status` | Users, posts and likes. |
| `exit` | Disconnect. |

You can also run one command and leave: `ssh -p 2222 packet.social feed 5`.

Your private key is your password, and it never leaves your computer. Lose it and that handle is
gone, so back up your `~/.ssh` folder. An account made in the SSH app also works in hard mode,
with the same key, as long as it is an ed25519 key.

Every day a bot called `@packet` posts a networking challenge. Answer it with `post`.

**Badges:** `first_post`, `first_like`, and `hard_mode` for a post sent with a hand signed request.

---

## 1. What you need for hard mode

**A terminal.** This is the text window where you type commands.

| System  | How to open one |
| ------- | --------------- |
| macOS   | Press Cmd + Space, type `Terminal`, press Enter. |
| Windows | Open the Start menu, type `PowerShell` or `Windows Terminal`, press Enter. WSL (Ubuntu inside Windows) is the smoothest option and the commands below work unchanged. |
| Linux   | Press Ctrl + Alt + T, or search for Terminal. |

**Three tools:**

- `curl` sends requests to websites. It is built in on macOS and Linux.
- `ssh-keygen` makes your key pair and signs your requests. It is built in on macOS and Linux
  (OpenSSH 8.0 or newer, which every current system has).
- `nc` (netcat) sends raw network packets. It is built in on macOS. On Debian or Ubuntu install it
  with `sudo apt install netcat-openbsd`.

Check that they exist:

```
curl --version
ssh-keygen -?
nc -h
```

(A help screen or an error about options is fine. "command not found" is not.)

**Quotes matter.** Always wrap text in plain single quotes: `'like this'`. Do not use curly
"smart" quotes. If you paste commands from a chat app or a document and get odd errors, retype the quotes.

**Windows.** The commands are written for a bash shell. Use WSL (Ubuntu inside Windows), which
works unchanged. Git Bash also works for everything except likes, because it has no `nc`.

---

## 2. Hard mode quick start

There are no passwords. You make a **key pair**: a private key that stays on your computer, and a
public key you give to the server. Every request you send is **signed** with the private key, and
the server checks the signature with your public key.

**Each curl command is one line. Copy all of it, including the web address at the end.**
Change only the words in CAPITALS or the part the text tells you to change.

```
# 1. is the server alive?
curl https://packet.social/status

# 2. make your key pair, once (press Enter twice for no passphrase, or choose one)
ssh-keygen -t ed25519 -f ~/.ssh/packet_social

# 3. get the registration question. It also prints the exact commands for step 4.
curl https://packet.social/register

# 4. register: change H= (your handle) and A= (your answer), then copy all three lines
H=yourhandle; A=YOUR_ANSWER; T=$(date +%s)
S=$(printf '%s' "register $H $T" | ssh-keygen -Y sign -n packet.social -f ~/.ssh/packet_social | base64 | tr -d '\n')
curl -H "Handle: $H" -H "Time: $T" -H "Signature: $S" -d "answer=$A" --data-urlencode "pubkey=$(cat ~/.ssh/packet_social.pub)" https://packet.social/register

# 5. read the feed
curl https://packet.social/feed

# 6. post: change H= and B=, then copy all three lines
H=yourhandle; B='hello from the wire'; T=$(date +%s)
S=$(printf '%s' "$H $T $B" | ssh-keygen -Y sign -n packet.social -f ~/.ssh/packet_social | base64 | tr -d '\n')
curl -H "Handle: $H" -H "Time: $T" -H "Signature: $S" --data-binary "$B" https://packet.social/post

# 7. like post number 3 (a UDP packet, no reply will ever come)
echo -n 'LIKE:3' | nc -u -w1 packet.social 6969
```

When you register, the server replies `201 Created` and a welcome message that repeats the posting
commands with your handle already filled in, so you can start without coming back here.

The rest of this page explains each part slowly.

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

**2a. Make your key pair (once).**

```
ssh-keygen -t ed25519 -f ~/.ssh/packet_social
```

It asks for a passphrase. Press Enter twice for none, or type one (you will be asked for it every
time you sign). Two files appear in the hidden folder `~/.ssh`:

- `packet_social` is the **private key**. Never share it. Back it up. If you lose it, nobody,
  including me, can get your handle back.
- `packet_social.pub` is the **public key**. It is safe to share. This is what the server stores.

**2b. Ask for your question.**

```
curl https://packet.social/register
```

You get the question and the exact commands to answer it:

```
question: which port does HTTPS use?

HOW TO ANSWER
Type your answer in the A= part of the first line. It is not case sensitive,
so UDP and udp both work. Change H= to the handle you want ...
```

You will be asked one simple networking question. Every answer is on this page
or one command away. A search engine
is allowed. This is a lesson, not an exam.

**2c. Register.** Pick a **handle** (lowercase letters, digits and underscore, up to 20
characters). Here the question was about HTTPS, so the answer is `443`. Say your handle is `ada`.
Change only the first line, then copy all three lines:

```
H=ada; A=443; T=$(date +%s)
S=$(printf '%s' "register $H $T" | ssh-keygen -Y sign -n packet.social -f ~/.ssh/packet_social | base64 | tr -d '\n')
curl -H "Handle: $H" -H "Time: $T" -H "Signature: $S" -d "answer=$A" --data-urlencode "pubkey=$(cat ~/.ssh/packet_social.pub)" https://packet.social/register
```

What those three lines do:

1. `H=ada; A=443; T=$(date +%s)` stores three values in variables: your handle, your answer, and
   the current time in seconds (`date +%s`).
2. The second line signs the text `register ada 1785961012` with your private key
   (`ssh-keygen -Y sign`) and turns the result into one line (`base64 | tr -d '\n'`).
3. The curl line sends the handle, time and signature as headers (`-H`), the answer as form data
   (`-d`) and your public key (`--data-urlencode`). The server checks the signature against the
   public key you sent. A valid signature proves you own the private key.

On success the server replies `201 Created` with a welcome message. Your account exists.

The question you are asked can be any one of these, so here is where to find each answer:

| Question | Where to look |
| -------- | ------------- |
| What is the IPv4 loopback address? | Run `ping localhost` and read the number in brackets. |
| What is the IP address of packet.social? | Run `nslookup packet.social`. |
| Which port does HTTPS use? / plain HTTP? / SSH? | A web search for "default port HTTPS". |
| What status code means Not Found / Unauthorized / Created? | The [status codes](#6-status-codes) table below. |
| Do likes travel over tcp or udp? | This page, or the home page. |
| Which port do likes use? | This page, or the home page. |
| What is the maximum size of a post, in bytes? | [Rules and limits](#5-rules-and-limits). |
| How many bits are in an IPv4 address? | A web search. |

Answers are not case sensitive. A question stays valid for 10 to 20 minutes and is tied to your
internet address. If the question changes before you answer, just ask again and answer the new one.

> **There is no password and no reset.** Your private key is your account. Keep
> `~/.ssh/packet_social` safe and backed up. If you lose it, that handle is gone for good.

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

Change `H=` to your handle and `B=` to your words, then copy all three lines:

```
H=ada; B='hello from the wire'; T=$(date +%s)
S=$(printf '%s' "$H $T $B" | ssh-keygen -Y sign -n packet.social -f ~/.ssh/packet_social | base64 | tr -d '\n')
curl -H "Handle: $H" -H "Time: $T" -H "Signature: $S" --data-binary "$B" https://packet.social/post
```

You get `201 Created`. To see which id your post got, add `-i` to the curl line to show the
headers, and look for `Location: /post/42`.

Things to know about posting:

- The signature covers your handle, the time and your exact words, so nobody can change a post in
  flight or reuse a captured request. A signed request is valid for 5 minutes, so your computer's
  clock must be right.
- A post is at most **280 bytes**. Bytes, not characters: a plain letter is 1 byte, an accented
  letter is 2, an emoji is usually 4. Check a length with
  `printf '%s' 'your text' | wc -c`.
- Use `--data-binary`, not `-d`. The `-d` option removes newlines, so your post would be mangled.
- Several lines work too: put a real line break inside the quotes of `B='...'`.
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

Open [packet.social](https://packet.social) in a browser. It shows whether the server is up, the
live numbers (users, posts, likes, uptime) and the latest posts. You cannot post, like or delete from
the website. It is a front door, not the app.

---

## 4. Cheat sheet

```
curl https://packet.social/status                          # is it alive, how many users and posts
ssh-keygen -t ed25519 -f ~/.ssh/packet_social              # make your key pair, once
curl https://packet.social/register                        # get a question and the register commands
curl https://packet.social/feed                            # newest 50 posts
curl https://packet.social/feed.json                       # the same, as JSON
curl https://packet.social/post/7                          # one post
curl https://packet.social/profile/ada                     # one person
echo -n 'LIKE:7' | nc -u -w1 packet.social 6969            # like post 7
```

Signing (register and post) always has the same three lines. For a post:

```
H=ada; B='your words'; T=$(date +%s)
S=$(printf '%s' "$H $T $B" | ssh-keygen -Y sign -n packet.social -f ~/.ssh/packet_social | base64 | tr -d '\n')
curl -H "Handle: $H" -H "Time: $T" -H "Signature: $S" --data-binary "$B" https://packet.social/post
```

Handy curl flags: `-i` show response headers, `-v` show the whole conversation including the
TLS handshake, `-s` hide the progress meter, `-X DELETE` choose a different method.

---

## 5. Rules and limits

| Rule | Value |
| ---- | ----- |
| Post size | 280 bytes |
| Handle | lowercase letters, digits, underscore, 1 to 20 characters |
| Key | ed25519 only |
| Signed request lifetime | 5 minutes |
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
| 400 | Bad Request | You sent something malformed: bad handle, a public key that is not ed25519, an empty or non text post. |
| 401 | Unauthorized | Missing or bad signature, wrong key, clock more than 5 minutes off, or a request used twice. |
| 403 | Forbidden | Wrong registration answer, or the question changed. |
| 404 | Not Found | No such post or profile. |
| 405 | Method Not Allowed | DELETE, PUT and friends do not exist here. |
| 409 | Conflict | That handle is taken. |
| 413 | Payload Too Large | Your post is over 280 bytes. |
| 429 | Too Many Requests | Slow down, then retry after the wait. |

---

## 7. When something goes wrong

**`zsh: command not found: 443`** (or any answer you typed on its own). The answer is not a
command. It goes after `A=` on the first line of the register commands.

**`curl: (2) no URL specified`** (or `curl: try 'curl --help'`). You left off the web address at
the end of the command. Every curl command ends with an address such as
`https://packet.social/register`. Copy the whole line.

**`401 Unauthorized`.** The signature did not check out. The usual causes:
you changed the words after signing, the handle in `H=` differs from the one you registered,
you used a different key file, you copied only some of the three lines, or your clock is wrong
(the error then says how many seconds off you are). Run all three lines again from the top.
`this exact request was already used` means the same signed request was sent twice, so just run the
three lines again to make a fresh one.

**`Load key ... No such file or directory`.** You have not made your key yet. Run
`ssh-keygen -t ed25519 -f ~/.ssh/packet_social`.

**`ssh-keygen: illegal option -- Y`.** Your OpenSSH is too old (older than 8.0). Update your system.

**`403 Forbidden` when registering.** The answer was wrong, or the question changed. Run
`curl https://packet.social/register` again and answer the question you get now.

**`400 Bad Request`.** Read the line under it, the server says what it did not like. Common
causes: a handle with capitals or symbols, a public key that is not ed25519, an empty post, or a
post containing invisible control characters.

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

**`curl: command not found`.** Install it. On Windows, use WSL.

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
curl -i --data-binary 'x' https://packet.social/post   # a 401, no signature
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
| Key pair | A private key you keep secret and a public key you share. Made with `ssh-keygen`. |
| Signature | Proof that a message came from the holder of a private key, checked with the public key. |
| Byte | The unit of size. A plain letter is one byte. |
| Unix time | Seconds since 1 January 1970, used for timestamps. |
| Rate limit | A cap on how often you may ask. |

---

## 10. Run your own server

This section is for people who want to host a copy. You do not need it to use packet.social.

### What is in this repo

| File | Purpose |
| ---- | ------- |
| `index.html` | The landing page: status, live numbers and how to connect. One file. |
| `server.py` | The HTTP and UDP server. Python standard library only. |
| `sshapp.py` | The SSH app. Needs the `python3-asyncssh` package. Shares the database with `server.py`. |
| `backup.py` | Nightly database backup. |
| `deploy/Caddyfile` | Caddy config: sends API paths to the server, serves the website for the rest. |
| `deploy/packet.service` | systemd unit that runs the HTTP and UDP server. |
| `deploy/packet-ssh.service` | systemd unit that runs the SSH app. |
| `deploy/packet-backup.service`, `deploy/packet-backup.timer` | Runs the backup every night at 03:30. |
| `deploy/install.sh` | Installs and restarts everything. |

### How it fits together

```
you ----https 443----> Caddy ----http 127.0.0.1:8080----> server.py ----> SQLite
you ----udp   6969---------------------------------------> server.py
you ----ssh   2222---------------------------------------> sshapp.py ----> SQLite
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
| `PACKET_SSH_HOST` | `0.0.0.0` | SSH app listen address. |
| `PACKET_SSH_PORT` | `2222` | SSH app port. Use 22 only after moving your own admin SSH elsewhere. |
| `PACKET_SSH_HOST_KEY` | `/var/lib/packet/ssh_host_key` | The server's own SSH identity. Created on first start. Back it up, or users see a changed host key warning. |
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
   ufw allow 2222/tcp
   ufw enable
   ```
3. Install Caddy (see the official Caddy install page for Debian and Ubuntu), git and
   `openssh-client` (the server uses `ssh-keygen` to check signatures; `deploy/install.sh` installs it).
4. Edit the domain in `deploy/Caddyfile` and the IP in `deploy/packet.service`.
5. Clone and install:
   ```
   git clone https://github.com/kblssd/packet-social-web.git /opt/packet-social-web
   bash /opt/packet-social-web/deploy/install.sh
   ```
   The script installs `python3-asyncssh`, opens port 2222 if the `ufw` firewall is on, and ends by
   printing `active` for each service and the `/status` reply. If your host has a separate cloud
   firewall, allow TCP 2222 there too.

**Moving the SSH app to port 22** (so users type `ssh packet.social` with no `-p`): first move your
own admin SSH to another port, for example 2200. Edit `/etc/ssh/sshd_config` (`Port 2200`), run
`systemctl restart ssh`, and from a second window confirm `ssh -p 2200 root@your-ip` works before
closing anything. Allow 2200 in the firewalls. Then set `Environment=PACKET_SSH_PORT=22` in
`deploy/packet-ssh.service` and redeploy. From then on you log in with `ssh -p 2200`.

**Update after changing the code:**

```
cd /opt/packet-social-web && git pull && bash deploy/install.sh
```

**Watch the logs:**

```
journalctl -u packet -f        # HTTP and UDP server
journalctl -u packet-ssh -f    # SSH app
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
answer. Edit the list, keep every answer findable in the README or one command away, then redeploy.

---

## 11. Security notes and known limits

What is in place:

- **Encryption.** All reading and writing goes over HTTPS or SSH. Caddy manages the web certificate.
- **The SSH app is not a shell.** Connecting gives you the app's own prompt. There is no way to run
  system commands, forward ports or transfer files through it. Guests need no key and are read only.
- **No passwords.** The server stores only your public key. Every post and registration is signed
  with your private key, which never leaves your computer. Nothing secret is ever sent or stored.
- **Replay protection.** A signed request is valid for 5 minutes and can be used once.
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
- **No key recovery, no account deletion, no edit or delete of posts.** Lose your private key and the
  handle is gone. That is deliberate for now, but it means mistakes are permanent.
- **No moderation tools.** The operator can remove rows directly in the SQLite file. There is
  no in app way to do it.
- **One server, one database.** There is no clustering and no failover.
- **The IP in one registration question is configured by hand.** If your address changes,
  update `PACKET_IP` and redeploy.
