#!/usr/bin/env python3
"""
Instagram account creator — runs inside GitHub Actions (public IP, different from sandbox).
Creates an account using a temp mail.tm inbox + reads the confirmation code automatically.

Usage:
    python ig_signup.py            # create 1 account
    python ig_signup.py 3          # create 3 accounts (with delay between them)

Proxy support:
    Set the PROXY env var to a real residential/ISP proxy:
        PROXY="http://user:pass@host:port" python ig_signup.py
    Without PROXY it falls back to free public proxies (usually already blocked by IG).

Output: each account is printed as JSON and appended to accounts.json
(also uploaded as a workflow artifact).
"""
import logging
import json
import random
import string
import re
import time
import sys
import os
import secrets
import requests

logging.basicConfig(level=logging.WARNING)
logging.getLogger("instagrapi").setLevel(logging.ERROR)

from instagrapi import Client
from instagrapi.mixins.signup import SignUpMixin
from instagrapi.exceptions import (
    ClientError,
    ChallengeRequired,
    FeedbackRequired,
    PleaseWaitFewMinutes,
)

def _check_age(self, year, month, day):
    r = self.private_request(
        "consent/check_age_eligibility/",
        data={"_csrftoken": self.token, "day": day, "year": year, "month": month},
        with_signature=False,
    )
    return r if isinstance(r, dict) else r.json()

SignUpMixin.check_age_eligibility = _check_age

def _create(self, username, password, email="", signup_code="", phone_number="",
            phone_code="", full_name="", year=None, month=None, day=None, **kw):
    import secrets as _s
    import time as _t
    import random as _r

    if not (email or phone_number):
        raise ClientError("Use email or phone_number")

    def _str(v):
        if v is None:
            return ""
        if isinstance(v, bytes):
            return v.decode(errors="ignore")
        return str(v)

    data = {
        "jazoest": str(int(_r.randint(22300, 22399))),
        "tos_version": "row",
        "suggestedUsername": "",
        "sn_result": "",
        "do_not_auto_login_if_credentials_match": "false",
        "phone_id": _str(getattr(self, "phone_id", "")),
        "enc_password": self.password_encrypt(password),
        "username": _str(username),
        "first_name": _str(full_name),
        "adid": _str(getattr(self, "adid", "")),
        "guid": _str(self.uuid),
        "day": day,
        "month": month,
        "year": year,
        "device_id": _str(self.android_device_id),
        "_uuid": _str(self.uuid),
        "waterfall_id": _str(getattr(self, "waterfall_id", "")),
        "one_tap_opt_in": "true",
    }

    if email and not phone_number:
        endpoint = "accounts/create/"
        domain = "www.instagram.com"
        data.update({
            "email": email,
            "force_sign_up_code": signup_code,
            "qs_stamp": "",
            "sn_nonce": f"{email}|{int(_t.time())}|{_s.token_hex(24)}",
        })
    else:
        endpoint = "accounts/create_validated/"
        domain = None
        data.update({
            "phone_number": phone_number,
            "verification_code": phone_code,
            "force_sign_up_code": "",
            "has_sms_consent": "true",
        })

    return self.private_request(endpoint, data, domain=domain)

SignUpMixin.accounts_create = _create

class TempMail:
    def __init__(self):
        self.s = requests.Session()

    def create(self):
        doms = self.s.get("https://api.mail.tm/domains", timeout=20).json().get("hydra:member", [])
        self.domain = doms[0]["domain"]
        local = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        self.addr = f"{local}@{self.domain}"
        self.pw = "Aa1!" + "".join(random.choices(string.ascii_letters + string.digits, k=10))
        for _ in range(5):
            r = self.s.post("https://api.mail.tm/accounts",
                            json={"address": self.addr, "password": self.pw}, timeout=20)
            if r.status_code in (200, 201):
                break
            time.sleep(2)
        self.tok = None
        for _ in range(12):
            try:
                self.tok = self.s.post("https://api.mail.tm/token",
                                       json={"address": self.addr, "password": self.pw},
                                       timeout=20).json().get("token")
            except Exception:
                pass
            if self.tok:
                break
            time.sleep(2)
        self.h = {"Authorization": "Bearer " + self.tok}
        return self.addr

    def code(self):
        try:
            ms = self.s.get("https://api.mail.tm/messages", headers=self.h, timeout=20).json().get("hydra:member", [])
        except Exception:
            return None
        for m in ms:
            try:
                d = self.s.get(f"https://api.mail.tm/messages/{m['id']}", headers=self.h, timeout=20).json()
            except Exception:
                continue
            t = ""
            for k in ("text", "html"):
                v = d.get(k)
                t += (v if isinstance(v, str) else " ".join(map(str, v)) if isinstance(v, list) else str(v or ""))
            c = re.search(r"\b(\d{5,6})\b", t)
            if c:
                return c.group(1)
        return None

def fetch_free_proxies():
    """Scrape free public proxies. NOTE: these are datacenter IPs and are
    almost always already blocked by Instagram. Kept here so it is explicit."""
    out = []
    try:
        r = requests.get("https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
                         timeout=15).text
        for line in r.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append("http://" + line)
    except Exception as e:
        print(f"[!] proxy list fetch failed: {e}", flush=True)
    return out

def get_proxy():
    p = os.environ.get("PROXY") or os.environ.get("IG_PROXY")
    if p:
        return p
    plist = fetch_free_proxies()
    return random.choice(plist) if plist else None

def make_account():
    tm = TempMail()
    addr = tm.create()
    u = "".join(random.choices(string.ascii_lowercase + string.digits, k=9)) + str(random.randint(10, 99))
    p = "".join(random.choices(string.ascii_letters + string.digits + "!@#_", k=15))
    fn = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=7)).capitalize()

    print(f"[+] email: {addr}", flush=True)
    print(f"[*] username: @{u}", flush=True)

    cl = Client()
    cl.delay_range = [2, 4]

    proxy = get_proxy()
    if proxy:
        try:
            before = cl._send_public_request("https://api.ipify.org/")
            cl.set_proxy(proxy)
            after = cl._send_public_request("https://api.ipify.org/")
            print(f"[PROXY] {proxy}", flush=True)
            print(f"[IP] before={before}  after={after}", flush=True)
        except Exception as e:
            print(f"[!] proxy failed, continuing without: {e}", flush=True)

    def h(username, choice=None):
        for _ in range(40):
            c = tm.code()
            if c:
                print(f"[CODE] {c}", flush=True)
                return c
            time.sleep(4)
        return ""

    cl.challenge_code_handler = h

    user = cl.signup(username=u, password=p, email=addr, full_name=fn,
                     year=1992, month=5, day=15)

    rec = {
        "username": u,
        "password": p,
        "email": addr,
        "email_pass": tm.pw,
        "user_id": str(user.pk),
        "status": "created",
    }
    return rec

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    results = []
    for i in range(n):
        print(f"\n===== account {i + 1}/{n} =====", flush=True)
        try:
            rec = make_account()
            results.append(rec)
            print(json.dumps(rec, ensure_ascii=False), flush=True)
        except (FeedbackRequired, PleaseWaitFewMinutes) as e:
            print(f"[-] blocked: {type(e).__name__}: {str(e)[:200]}", flush=True)
        except Exception as e:
            print(f"[-] {type(e).__name__}: {str(e)[:250]}", flush=True)
        if i < n - 1:
            time.sleep(random.randint(30, 60))

    with open("accounts.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n=== DONE ===", flush=True)
    print(json.dumps(results, indent=2, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
