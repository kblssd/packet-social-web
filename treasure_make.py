#!/usr/bin/env python3
"""Make one week of the packet.social treasure hunt. Run it on YOUR computer, not the server.

    python3 treasure_make.py keys.txt --week 1 --prize "0.01 ETH"

keys.txt is a text file holding the wallet's keys (seed phrase or private key).
It writes treasure.json, which holds only public things: the locked file, clues 1 to 3
and a salted hash of the password. The keys and the password never go to the server.

Clue 4 is printed on screen. You put it in DNS yourself.
Needs python3 and the openssl command (built in on macOS and Linux).
"""
import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys

ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
HINTS = [
    "Look at how the server talks before the page begins (the headers).",
    "@packet posted something that looks like nonsense. Decode it.",
    "Ask the page for something it refuses to give you.",
    "Ask the phone book (DNS) for the TXT record of treasure.packet.social.",
]


def token():
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def openssl(args, data, password):
    env = dict(os.environ, CHEST_PW=password)
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-a",
                        "-pass", "env:CHEST_PW"] + args, input=data, capture_output=True, env=env)
    if r.returncode != 0:
        sys.exit("openssl failed: " + r.stderr.decode(errors="replace"))
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", help="text file with the wallet keys")
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--prize", default="")
    ap.add_argument("--out", default="treasure.json")
    a = ap.parse_args()

    keys = open(a.keys, "rb").read()
    if not keys.strip():
        sys.exit("the keys file is empty")

    clues = [token() for _ in range(4)]
    password = "".join(clues)
    chest = openssl(["-salt"], keys, password).decode()

    # prove the locked file opens with the password before anything is published
    back = openssl(["-d"], chest.encode(), password)
    if back != keys:
        sys.exit("round trip failed, nothing written")

    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    data = {"week": a.week, "prize": a.prize, "hints": HINTS, "clues": clues[:3],
            "chest": chest, "salt": salt.hex(), "hash": digest.hex()}
    with open(a.out, "w") as f:
        json.dump(data, f, indent=1)

    print("wrote %s (public data only)\n" % a.out)
    print("DO THIS NEXT")
    print("1. In your domain's DNS, add a TXT record:")
    print("     name:  treasure     (that makes treasure.packet.social)")
    print("     value: %s" % clues[3])
    print("2. Copy the file to the server and lock down who can read it:")
    print("     scp %s root@46.101.187.251:/var/lib/packet/treasure.json" % a.out)
    print("     ssh root@46.101.187.251 chown packet:packet /var/lib/packet/treasure.json")
    print("   The hunt starts by itself within 30 minutes (@packet announces it).")
    print("3. Delete %s from this computer. Fund the wallet only after the hunt is live.\n" % a.keys)
    print("Your own copy of the password (keep it private, test with it): %s" % password)


if __name__ == "__main__":
    main()
