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

**Run it where an ssh disconnection cannot kill it.** Under `tmux`, or:

```sh
systemd-run --user --unit=hp-scan --working-directory=$PWD \
    .venv/bin/harelphotos scan
journalctl --user -u hp-scan -f
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
sudo vi /etc/httpd/conf.d/harelphotos.conf   # ServerName, paths; comment out the :443 block for now
sudo apachectl configtest && sudo systemctl reload httpd
```

Before running certbot, confirm the two things it needs, because its failure
messages are much worse than these:

```sh
dig +short photos.example.org        # must be this machine's public address
sudo firewall-cmd --list-services    # needs http and https
sudo firewall-cmd --permanent --add-service={http,https} && sudo firewall-cmd --reload
```

Then:

```sh
sudo certbot --apache -d photos.example.org
```

Let it add the HTTP→HTTPS redirect when it offers. It writes an `:443` vhost
and a renewal timer; check the timer with `systemctl list-timers certbot*`.
Certificates are free and last 90 days, renewed automatically.

Now copy everything from `ProxyPreserveHost` downwards out of
`contrib/harelphotos-vhost.conf` into the `:443` vhost certbot created, and
reload. Keep the `:80` vhost: renewal serves its challenge from there.

```sh
sudo apachectl configtest && sudo systemctl reload httpd
harelphotos check --env --url https://photos.example.org
```

That last command checks the live site end to end from outside: TLS, whether
compression is actually on, the security headers, and — the one that matters —
that `/a/` is **not** reachable without logging in.

Enable HSTS (it is in the vhost, commented) only once TLS is confirmed working.
Browsers remember it for its full lifetime and you cannot take it back quickly.


## 5. Google sign-in (optional)

Local accounts in `users.toml` are enough, and nothing below is required. It
exists so relatives can use a Google account they already have instead of
another password.

The server does not have to be on Google Cloud. You are only registering an
OAuth client, which is free.

1. <https://console.cloud.google.com/> → create a project.
2. **APIs & Services → OAuth consent screen** → External. Fill in the app name,
   your email, and the two links your own site already serves:
   `https://photos.example.org/privacy` and `/terms`. Edit the text of those
   two pages in `/etc/harelphotos/` first.
3. **Credentials → Create credentials → OAuth client ID → Web application.**
   Authorised redirect URI, exactly:
   `https://photos.example.org/auth/google/callback`
4. Put the client ID and secret in the config:

```toml
[google]
enabled       = true
client_id     = "....apps.googleusercontent.com"
client_secret = "..."
```

While the consent screen is in **Testing**, only accounts you list as test
users can sign in, and their sessions expire after a week. To lift that, press
**Publish app**. For the scopes used here — email address only — publishing is
immediate and free: Google's verification review applies to sensitive scopes,
which this does not request.

Signing in with Google authenticates; it does not authorise. The email address
still has to appear in `users.toml`, so nobody can grant themselves access by
having a Google account:

```sh
harelphotos user add someone@gmail.com --google
```


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
