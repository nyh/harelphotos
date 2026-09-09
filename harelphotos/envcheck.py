"""``harelphotos check --env`` — verify the machine, not the photos.

Written to be pasted. Every line is one assertion with a verdict, so the whole
output can be sent to someone who cannot log in to the machine and they can
tell what is wrong from it alone.

Deliberately checks things that fail *late and confusingly* if left unchecked:
a Pillow without AVIF only complains at the first encode, an hour into a scan;
a missing SELinux boolean shows up as a 503 with a correct-looking config; a
`mod_deflate` that is not actually compressing costs 30x the bandwidth and says
nothing at all.
"""

from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
import urllib.request
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

OK, WARN, FAIL, INFO = "ok", "warn", "FAIL", "--"


@dataclass
class Report:
    lines: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, verdict: str, what: str, detail: str = "") -> None:
        self.lines.append((verdict, what, detail))

    @property
    def failures(self) -> int:
        return sum(1 for v, _, _ in self.lines if v == FAIL)

    @property
    def warnings(self) -> int:
        return sum(1 for v, _, _ in self.lines if v == WARN)

    def render(self) -> str:
        width = max((len(w) for _, w, _ in self.lines), default=0)
        out = []
        for verdict, what, detail in self.lines:
            mark = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL ", INFO: "      "}[verdict]
            out.append(f"[{mark}] {what.ljust(width)}  {detail}")
        return "\n".join(out)


def _mode(path: Path) -> str:
    try:
        return oct(path.stat().st_mode & 0o777)
    except OSError:
        return "?"


def run(cfg: Config | None, url: str | None = None) -> Report:
    """Check the machine. `cfg` may be None: this is meant to be the first
    thing run on a new server, before anything is configured, and the
    machine-level checks do not need a config to be useful."""
    r = Report()
    _python(r)
    _apache(r, cfg)
    _selinux(r, cfg)
    if cfg is None:
        r.add(INFO, "configuration", "none yet — run 'harelphotos init' for the rest")
    else:
        _paths(r, cfg)
        _secrets(r, cfg)
        _deployment(r, cfg)
        _index(r, cfg)
    if url:
        _live(r, url)
    return r


def _python(r: Report) -> None:
    v = sys.version_info
    verdict = OK if v >= (3, 11) else FAIL
    r.add(verdict, "python", f"{v.major}.{v.minor}.{v.micro} at {sys.executable}")

    try:
        import PIL
        from PIL import features

        avif = features.check("avif")
        # Fails at the first encode otherwise — an hour into a bulk scan.
        r.add(OK if avif else FAIL, "Pillow AVIF support",
              f"Pillow {PIL.__version__}, avif={'yes' if avif else 'NO'}"
              + ("" if avif else "  (use the PyPI wheel, not a distro python3-pillow)"))
    except Exception as e:
        r.add(FAIL, "Pillow", str(e))

    for mod in ("flask", "requests"):
        try:
            __import__(mod)
            r.add(OK, mod, "importable")
        except Exception as e:
            r.add(FAIL, mod, str(e))
    r.add(OK if shutil.which("gunicorn") or _importable("gunicorn") else WARN,
          "gunicorn", "present" if _importable("gunicorn") else "not installed (needed to serve)")


def _importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _paths(r: Report, cfg: Config) -> None:
    root = cfg.photo_root
    if not root.is_dir():
        r.add(FAIL, "photo_root", f"{root} is not a directory")
    elif not os.access(root, os.R_OK | os.X_OK):
        r.add(FAIL, "photo_root", f"{root} is not readable by {_whoami()}")
    else:
        # Traversal is the thing that actually breaks when photos sit under a
        # home directory: every parent needs +x, not just the directory itself.
        blocked = [str(p) for p in list(root.parents)[:-1] if not os.access(p, os.X_OK)]
        if blocked:
            r.add(FAIL, "photo_root traversal",
                  f"{_whoami()} cannot pass through {', '.join(blocked)}")
        else:
            r.add(OK, "photo_root", f"{root} readable by {_whoami()}")
        sample = _sample_photo(root)
        if sample is None:
            r.add(WARN, "photo_root contents", "no .jpg found in the first few directories")
        else:
            try:
                with open(sample, "rb") as f:
                    f.read(16)
                r.add(OK, "sample photo", f"opened {sample.name}")
            except OSError as e:
                r.add(FAIL, "sample photo", f"cannot open {sample}: {e}")

    for label, path, need_write in (
        ("derived_root", cfg.derived_root, True),
        ("state dir", cfg.state_dir, True),
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            r.add(FAIL, label, f"cannot create {path}: {e}")
            continue
        writable = os.access(path, os.W_OK)
        r.add(OK if writable or not need_write else FAIL, label,
              f"{path} {'writable' if writable else 'NOT writable'}")

    free = shutil.disk_usage(cfg.derived_root).free if cfg.derived_root.exists() else 0
    r.add(OK if free > 5e9 else WARN, "free space",
          f"{free / 1e9:.1f} GB where the generated images go")


def _sample_photo(root: Path) -> Path | None:
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")][:5]
        for name in filenames:
            if name.lower().endswith((".jpg", ".jpeg")):
                return Path(dirpath) / name
        seen += 1
        if seen > 20:
            break
    return None


def _whoami() -> str:
    import getpass

    try:
        return f"{getpass.getuser()}(uid {os.getuid()})"
    except Exception:
        return f"uid {os.getuid()}"


def _secrets(r: Report, cfg: Config) -> None:
    key = cfg.secret_key_file
    if not key.is_file():
        r.add(FAIL, "secret_key", f"{key} missing — run 'harelphotos init'")
    else:
        size = key.stat().st_size
        world = key.stat().st_mode & 0o077
        r.add(FAIL if size < 16 else OK, "secret_key", f"{size} bytes")
        r.add(FAIL if world else OK, "secret_key mode",
              f"{_mode(key)}" + (" — readable by others!" if world else ""))

    users = cfg.users_file
    if not users.is_file():
        r.add(WARN, "users.toml", f"{users} missing — no one can log in")
    else:
        from . import users as users_mod

        try:
            n = len(users_mod.load(users))
            r.add(OK if n else WARN, "accounts", f"{n} in {users}")
        except Exception as e:
            r.add(FAIL, "users.toml", str(e))
        world = users.stat().st_mode & 0o077
        r.add(FAIL if world else OK, "users.toml mode",
              f"{_mode(users)}" + (" — holds password hashes!" if world else ""))

    if cfg.google.enabled:
        ok = bool(cfg.google.client_id and cfg.google.client_secret)
        r.add(OK if ok else FAIL, "google sign-in", "configured" if ok else "enabled but incomplete")
        r.add(OK if cfg.base_url.startswith("https://") else FAIL, "base_url for OAuth",
              cfg.base_url + ("" if cfg.base_url.startswith("https://") else
                              " — Google will not redirect to plain HTTP"))
    else:
        r.add(INFO, "google sign-in", "disabled")


def _deployment(r: Report, cfg: Config) -> None:
    """Config that is only wrong once it is behind a real web server."""
    https = cfg.base_url.startswith("https://")
    r.add(INFO, "base_url", cfg.base_url)
    if https and not cfg.behind_proxy:
        # The symptom is subtle rather than loud: everything works, but every
        # client looks like 127.0.0.1, so one person failing to log in throttles
        # everyone, and absolute URLs come out as http://.
        r.add(WARN, "behind_proxy",
              "false, but base_url is https — set behind_proxy = true so "
              "X-Forwarded-For/Proto from Apache are believed")
    elif cfg.behind_proxy and not https:
        r.add(WARN, "behind_proxy",
              "true with a plain-http base_url — only set it when a proxy on "
              "this machine really is in front")
    else:
        r.add(OK, "behind_proxy", str(cfg.behind_proxy).lower())
    if not https:
        r.add(WARN, "TLS", "base_url is plain http — sessions are not marked Secure")


def _index(r: Report, cfg: Config) -> None:
    from . import db

    if not cfg.index_db.exists():
        r.add(WARN, "index", f"{cfg.index_db} missing — run 'harelphotos scan'")
        return
    try:
        conn = db.open_index(cfg.index_db, read_only=True)
    except Exception as e:
        r.add(FAIL, "index", str(e))
        return
    photos = conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"]
    ready = conn.execute(
        "SELECT count(*) AS n FROM photos WHERE deriv_key IS NOT NULL"
    ).fetchone()["n"]
    conn.close()
    r.add(OK, "index", f"schema v{db.SCHEMA_VERSION}, {photos:,} photos")
    r.add(OK if ready == photos else WARN, "images generated",
          f"{ready:,}/{photos:,}" + ("" if ready == photos else " — run 'harelphotos scan'"))

    geo = cfg.state_dir / "geonames.sqlite"
    r.add(OK if geo.exists() else INFO, "place names",
          f"{geo.stat().st_size / 1e6:.0f} MB" if geo.exists()
          else "not installed (harelphotos init --geonames)")


def _apache(r: Report, cfg: Config | None) -> None:
    httpd = shutil.which("httpd") or shutil.which("apache2")
    if not httpd:
        r.add(INFO, "apache", "not installed here (fine on a workstation)")
        return
    try:
        out = subprocess.run([httpd, "-M"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        r.add(WARN, "apache modules", f"cannot run {httpd} -M: {e}")
        return
    for mod, needed, why in (
        ("proxy_module", True, "reverse proxy to gunicorn"),
        ("proxy_http_module", True, "same"),
        ("deflate_module", True, "album HTML is 30x smaller compressed"),
        # AddOutputFilterByType is mod_filter's directive, not mod_deflate's,
        # so compression needs both and a missing one fails configtest.
        ("filter_module", True, "provides AddOutputFilterByType"),
        ("headers_module", True, "cache and security headers"),
        ("ssl_module", True, "TLS"),
        ("xsendfile_module", False, "optional: lets Apache send image bytes itself"),
    ):
        present = mod in out
        verdict = OK if present else (FAIL if needed else INFO)
        r.add(verdict, f"mod_{mod.replace('_module', '')}",
              ("loaded" if present else "not loaded") + f" — {why}")

    if cfg and cfg.sendfile_header == "X-Sendfile" and "xsendfile_module" not in out:
        r.add(FAIL, "sendfile_header", "set to X-Sendfile but mod_xsendfile is not loaded")


def _selinux(r: Report, cfg: Config | None) -> None:
    if not shutil.which("getenforce"):
        return
    try:
        mode = subprocess.run(["getenforce"], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:
        return
    r.add(INFO, "selinux", mode)
    if mode != "Enforcing":
        return
    for boolean, why in (
        ("httpd_can_network_connect", "Apache -> gunicorn socket; a 503 without it"),
    ):
        try:
            out = subprocess.run(["getsebool", boolean], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except Exception:
            continue
        on = out.endswith("on")
        r.add(OK if on else FAIL, boolean, f"{'on' if on else 'off'} — {why}")

    if cfg and cfg.sendfile_header == "X-Sendfile":
        for label, path in (("photos", cfg.photo_root), ("derived", cfg.derived_root)):
            ctx = _selinux_context(path)
            fine = ctx and ("httpd_sys_content_t" in ctx or "public_content" in ctx)
            r.add(OK if fine else WARN, f"selinux label ({label})",
                  f"{ctx or 'unknown'}" + ("" if fine else " — Apache may not be able to read it"))


def _selinux_context(path: Path) -> str | None:
    try:
        out = subprocess.run(["ls", "-Zd", str(path)], capture_output=True, text=True,
                             timeout=5).stdout.split()
        return out[0] if out else None
    except Exception:
        return None


def _live(r: Report, url: str) -> None:
    """Check a running site end to end, over the network."""
    import urllib.error
    import urllib.request

    base = url.rstrip("/")
    r.add(INFO, "live check", base)

    req = urllib.request.Request(base + "/", headers={"Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            code = resp.status
    except urllib.error.HTTPError as e:
        code, headers, body = e.code, {k.lower(): v for k, v in e.headers.items()}, b""
    except ssl.SSLCertVerificationError as e:
        r.add(FAIL, "TLS certificate", _explain_cert_error(e))
        return
    except Exception as e:
        r.add(FAIL, "reachable", f"{base}: {e}")
        return

    r.add(OK if code == 200 else FAIL, "front page", f"HTTP {code}")
    r.add(OK if base.startswith("https://") else WARN, "TLS",
          "https" if base.startswith("https://") else "plain http — cookies and OAuth need TLS")
    gz = headers.get("content-encoding", "")
    r.add(OK if "gzip" in gz or "br" in gz else WARN, "compression",
          f"content-encoding: {gz or 'none'} — album HTML is ~30x smaller compressed")
    for header, want in (("referrer-policy", "no-referrer"),
                         ("x-content-type-options", "nosniff"),
                         ("x-frame-options", "DENY")):
        got = headers.get(header, "")
        r.add(OK if want.lower() in got.lower() else WARN, header, got or "missing")
    if base.startswith("https://"):
        hsts = headers.get("strict-transport-security", "")
        r.add(OK if hsts else WARN, "strict-transport-security", hsts or "missing")

    # The album must NOT be reachable without logging in.
    #
    # Redirects are deliberately NOT followed. urlopen follows them by
    # default, which made a correct 302-to-the-login-page look like a 200 and
    # reported a properly locked-down site as PUBLIC -- a false alarm on the
    # one check here that really matters.
    for path, what in (("/a/", "album"), ("/api/a/", "album JSON")):
        code = _status_no_redirect(base + path)
        # A redirect to the login page, or a refusal, are both correct.
        locked = code in (301, 302, 303, 307, 308, 401, 403)
        r.add(OK if locked else FAIL, f"login required ({what})",
              f"{path} answered {code}"
              + ("" if locked else " — THIS IS READABLE WITHOUT LOGGING IN"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _status_no_redirect(url: str) -> int:
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _explain_cert_error(e: Exception) -> str:
    """Name the cause, because the raw message names only the symptom.

    Something answering on 443 with a self-signed certificate is nearly always
    the distribution's stock ssl.conf vhost rather than ours: it ships a
    `_default_:443` using /etc/pki/tls/certs/localhost.crt, and it answers
    whenever our own TLS vhost is not active -- which, with the <IfFile> guard,
    means certbot has not run yet.
    """
    text = str(e)
    if "self-signed" in text or "self signed" in text:
        return (
            "a self-signed certificate is being served — certbot has probably "
            "not run yet, so the stock /etc/httpd/conf.d/ssl.conf vhost is "
            "answering on 443 instead of this site's"
        )
    if "hostname mismatch" in text.lower() or "doesn't match" in text:
        return f"{text} — another vhost is answering on 443"
    if "expired" in text:
        return f"{text} — check 'systemctl list-timers certbot*'"
    return text


def socket_reachable(path: str) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()
