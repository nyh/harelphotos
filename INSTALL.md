# Installing on a server

For putting harelphotos on a public server with a hostname and TLS. To run it
on your own machine, ignore all of this and see [MANUAL.md](MANUAL.md) — there
you need nothing but `harelphotos serve`.

Assumed here: Rocky Linux 9, an Apache `httpd` **already running other sites**,
and photos that stay where they are, in a home directory. Nothing below
replaces your httpd or moves your photos.

The layout it produces:

| what | where | why |
|---|---|---|
| the photos | `/home/nyh/pictures` | untouched, never written to |
| the code | `/home/nyh/harelphotos` | a git clone plus a venv |
| the config, accounts, key | `/etc/harelphotos` | root-owned, `users.toml` is 0600 |
| generated images, index | `/var/lib/harelphotos` | outside `$HOME` so Apache can read it |

The photos stay in `$HOME` because nothing but the application ever reads them.
The *generated* tree is the part Apache touches, and it is far easier to put it
somewhere readable than to open up a home directory.


## 0. Check the machine first

Before configuring anything:

```sh
git clone https://github.com/nyh/harelphotos
cd harelphotos
python3.12 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/harelphotos check --env
```

This runs without a config and tells you what the machine is missing. Fix
anything it marks `FAIL` before going on; it is quicker than discovering a
missing `mod_ssl` from certbot, or a Pillow without AVIF an hour into a scan.

Rocky 9's stock python3.9 is too old — it cannot install a maintained Pillow,
which is where AVIF comes from. `dnf install python3.12` if it is not there.

Packages you are likely to need, all from EPEL:

```sh
sudo dnf install epel-release
sudo dnf install mod_ssl certbot python3-certbot-apache mod_xsendfile
sudo systemctl restart httpd
```

`mod_xsendfile` is optional but worth it on a weak machine: it lets Apache send
the image bytes itself once the application has done the access check, instead
of copying every image through Python.


## 1. Configure

```sh
sudo mkdir -p /etc/harelphotos /var/lib/harelphotos
sudo .venv/bin/harelphotos --config /etc/harelphotos/config.toml init \
    --photo-root /home/nyh/pictures \
    --state-dir /var/lib/harelphotos

# After init, not before: it runs as root and creates index.sqlite and
# derived/ as root inside these directories. Chowning first does nothing for
# the files that do not exist yet, and the first scan then fails with
# "attempt to write a readonly database".
sudo chown -R nyh:nyh /etc/harelphotos /var/lib/harelphotos
```

Then edit `/etc/harelphotos/config.toml`. Four lines matter for a server:

```toml
base_url        = "https://photos.example.org"   # exactly, no trailing slash
behind_proxy    = true                           # believe Apache's X-Forwarded-*
sendfile_header = "X-Sendfile"                   # only with mod_xsendfile
```

`behind_proxy` is not cosmetic. Without it every request appears to come from
`127.0.0.1` over plain HTTP: the login throttle counts Apache rather than the
person failing to log in, so one person's bad password throttles everybody. It
defaults to off because believing `X-Forwarded-For` on a directly-reachable
server would let anyone claim any address.

Set `HARELPHOTOS_CONFIG` in your shell so you do not have to pass `--config`
every time:

```sh
echo 'export HARELPHOTOS_CONFIG=/etc/harelphotos/config.toml' >> ~/.bashrc
```

Add yourself an account, and the family:

```sh
harelphotos user add nyh --admin
harelphotos user add someone
```

Optionally fetch the place-name database, so photos with GPS say where they
were taken (about 30 MB, offline afterwards, no per-photo lookups):

```sh
harelphotos init --geonames
```


## 2. Scan

This is the long part. It reads every photo, then encodes four AVIF sizes of
each, and on a CPU-weak machine that is measured in hours or days rather than
minutes. Two things make it survivable:

**Measure before committing to it.** Scan one directory and extrapolate:

```sh
time harelphotos scan --dir 2024/08
```

**Run it where an ssh disconnection cannot kill it.** `tmux` is the least
trouble, and lets you watch the progress line and stop it with Ctrl-C:

```sh
tmux new -s scan
harelphotos scan
#  detach: Ctrl-B then D          reattach: tmux attach -t scan
```

As a transient systemd unit instead, note that `systemd-run --user` fails with
"Failed to connect to bus" over a plain ssh login, which has no user D-Bus
session — either `sudo loginctl enable-linger $USER` once and log back in, or
use a system unit, which needs no session at all:

```sh
sudo systemd-run --unit=hp-scan --uid=nyh --gid=nyh \
    --working-directory=/home/nyh/harelphotos \
    --setenv=HARELPHOTOS_CONFIG=/etc/harelphotos/config.toml \
    /home/nyh/harelphotos/.venv/bin/harelphotos scan
journalctl -u hp-scan -f
```

A scan is interruptible and resumable: Ctrl-C, or a reboot, loses only the
photo in progress. Running it again picks up where it stopped, and does not
re-encode anything already done. It is also safe to run while the site is
serving — the web process only reads the index.

It runs at `nice 10` by default (`[scan] nice` in the config) so it does not
make the site unresponsive while it works. `[scan] jobs = 0` uses every core;
lower it if the machine has other jobs to do.

You can start serving before the scan finishes. Photos with no images
generated yet show as empty placeholder tiles rather than broken ones.


## 3. Run it

```sh
sudo cp contrib/harelphotos.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now harelphotos
systemctl status harelphotos
```

Edit `User=` and the two paths in the unit first if yours differ from the table
above. Check it came up before putting Apache in front of it:

```sh
curl -sI http://127.0.0.1:8099/healthz
```

A bad config stops the unit from starting rather than turning every request
into a 500, so `systemctl status` is where the error will be.


## 4. Apache

Do the plain-HTTP half first, get a certificate, and let certbot write the TLS
vhost — it does that correctly and, more to the point, it renews it.

```sh
sudo mkdir -p /var/www/harelphotos-acme
sudo cp contrib/harelphotos-vhost.conf /etc/httpd/conf.d/harelphotos.conf
sudo vi /etc/httpd/conf.d/harelphotos.conf   # ServerName and the two paths
sudo apachectl configtest && sudo systemctl reload httpd
```

Nothing needs commenting out. The TLS vhost in that file is wrapped in
`<IfFile>` on the certificate, so it is inert until certbot has run and turns
itself on at the next reload. Both states are checked by `configtest`.

Before running certbot, confirm the two things it needs, because its failure
messages are much worse than these:

```sh
dig +short photos.example.org        # must be this machine's public address
sudo firewall-cmd --list-services    # needs http and https
sudo firewall-cmd --permanent --add-service={http,https} && sudo firewall-cmd --reload
```

Then get the certificate. Use `certonly --webroot`, **not** `--apache`: the
vhost file already contains the TLS vhost, and `--apache` would write a second
one for the same name, leaving two and a warning about it.

```sh
sudo certbot certonly --webroot -w /var/www/harelphotos-acme -d photos.example.org
sudo systemctl reload httpd
```

That reload is what activates the TLS vhost and the HTTP→HTTPS redirect, both
of which were waiting on the certificate. Certificates are free, last 90 days
and renew themselves; check the timer with `systemctl list-timers certbot*`.
Renewal keeps working because the `:80` vhost still serves `/.well-known/`
rather than redirecting it.

```sh
harelphotos check --env --url https://photos.example.org
```

That last command checks the live site end to end from outside: TLS, whether
compression is actually on, the security headers, and — the one that matters —
that `/a/` is **not** reachable without logging in.

HSTS is commented out at the bottom of the vhost, and is genuinely optional.
The redirect already sends http to https and the session cookie is `Secure`, so
a session cannot be stolen over plain HTTP either way. What HSTS adds is
protection for the *first* request on a hostile network, where an attacker
could otherwise intercept it and phish a password with a fake login page.

Against that, it is a one-way door: if the certificate later breaks, the site
is unreachable and nobody can click through the warning. If you enable it,
ramp — `max-age=300`, leave it through at least one certbot renewal, then
raise it — rather than starting at a year.


## 5. Google sign-in (optional)

Local accounts in `users.toml` are enough, and nothing below is required. It
exists so relatives can use a Google account they already have instead of
another password.

Your server does not have to be on Google Cloud, and this costs nothing. All
you are doing is registering an OAuth client: an entry that tells Google "this
site may ask me to confirm who someone is". No Google service runs your code
and nothing is billed.

### Register the client

1. <https://console.cloud.google.com/> → create a project (any name).
2. Find the consent screen. Google has moved it around; it is under
   **APIs & Services → OAuth consent screen**, or **Google Auth Platform →
   Branding / Audience** in the newer layout. Choose **External**.
   Fill in the app name, your email, and the two links your own site already
   serves: `https://photos.example.org/privacy` and `/terms`. Edit the text of
   those pages in `/etc/harelphotos/` first — they are template text.
3. Scopes: add **`openid`** and **`email`**, nothing else. This matters; see
   below.
4. **Credentials → Create credentials → OAuth client ID → Web application.**
   Authorised redirect URI, exactly, with no trailing slash:
   `https://photos.example.org/auth/google/callback`
   Google compares this character for character, and a mismatch is by far the
   commonest failure: it shows up as `redirect_uri_mismatch`. It must equal
   `base_url` from your config with `/auth/google/callback` appended.

   The form also demands an **Authorised JavaScript origin**. Nothing here
   needs one — the sign-in button is a plain link, your server does the
   redirect, and the code is exchanged server-to-server, so no script on the
   page ever contacts Google. The field is required by the form rather than by
   the flow; enter the bare origin and ignore it:
   `https://photos.example.org` — scheme and host only, no path, no slash.
5. Put the client ID and secret in `/etc/harelphotos/config.toml` and restart:

```toml
[google]
enabled       = true
client_id     = "....apps.googleusercontent.com"
client_secret = "..."
```

The "Sign in with Google" button appears on the login page only when
`enabled = true`, so nothing changes until you set it.

### Press "Publish app", or you will maintain two lists

This is the part worth understanding, and the answer to "do I have to list
everyone twice?"

A consent screen starts in **Testing**. In that state Google will only let
accounts on its own **Test users** list sign in at all — so every relative has
to be added *both* in the Google console *and* in `users.toml`, and their
sessions expire after seven days. That is the double bookkeeping, and it is
not something you have to live with.

Set the publishing status to **In production** ("Publish app"). Then Google
maintains no list: anyone with a Google account can get as far as Google
confirming who they are, and `users.toml` alone decides who is actually let in.
One list, the one you already keep.

Publishing is immediate and free **because of step 3 above**. Google's
verification review — the one that takes weeks and asks for a demo video —
applies to *sensitive* and *restricted* scopes, such as reading someone's Drive
or their Google Photos. An email address is neither. Ask for nothing but
`openid email` and there is nothing to review.

The unverified-app warning screen is likewise a sensitive-scope thing. With
these scopes your relatives see the ordinary Google account chooser.

### Google authenticates; `users.toml` authorises

Being published does **not** mean anyone can see your photos. All Google does
is hand us a verified email address. That address must already be on an
account here, or the sign-in is refused with "has not been invited":

```sh
harelphotos user add aunt --google aunt@gmail.com          # password too
harelphotos user add cousin --google cousin@gmail.com --google-only
```

`--google-only` means no password exists for that account at all: Google is
the only way in. Use it for relatives you would rather not invent a password
for.

Two details the code insists on, both about not letting the wrong person in:

- The address must be **verified** with Google. An unverified one is just a
  string somebody typed into a signup form, so accepting it would let anyone
  claim a relative's address.
- The token must have been issued to *your* client ID. A genuine Google token
  minted for some other site is not accepted here.


## Afterwards

Rescan when photos change. It only looks at what the filesystem says changed:

```sh
harelphotos scan
```

Reclaim space from photos that were deleted or replaced:

```sh
harelphotos gc
```

Upgrade:

```sh
git pull && .venv/bin/pip install -e . && sudo systemctl restart harelphotos
```

The index is a cache, not data. If it is ever damaged, delete it and rescan;
you lose nothing but the time. The things that are *not* rebuildable are
`/etc/harelphotos/users.toml` and `secret_key` — back those up. Replacing the
key logs everyone out.

Where things go wrong:

| symptom | look at |
|---|---|
| `attempt to write a readonly database` | `sudo chown -R nyh:nyh /var/lib/harelphotos` — `init` ran as root |
| 503 from Apache | `systemctl status harelphotos` — gunicorn is not up |
| every image 404s but pages work | `sendfile_header`/`XSendFilePath` disagree, or Apache cannot read `/var/lib/harelphotos/derived` |
| login always returns to the login page | `base_url` is not exactly what the browser asked for, so the cookie is dropped |
| everyone throttled at once | `behind_proxy` is not `true` |
| grey placeholder tiles | those photos have no images generated yet; finish the scan |
