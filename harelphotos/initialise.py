"""``harelphotos init`` — create the config, the state directories and the key.

Everything here is idempotent and refuses to overwrite: running it twice must
never clobber a config you have edited or, far worse, rotate the secret key and
log the whole family out.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import secrets
from pathlib import Path

from . import db

CONFIG_TEMPLATE = """\
# harelphotos configuration. See DESIGN.md 5.1.

photo_root      = "{photo_root}"
derived_root    = "{derived_root}"
index_db        = "{index_db}"
users_file      = "{users_file}"
secret_key_file = "{secret_key_file}"

base_url        = "http://127.0.0.1:5000"   # production: https://your.host
session_days    = 30                        # how long a login lasts
sendfile_header = "auto"                    # "auto" | "X-Sendfile" | "none"
# log_file      = "{state}/harelphotos.log" # unset = stderr / journal

[ui]
site_title      = "Photo Album"
heading         = "Photo Album"
tagline         = "By invitation only. Please login to continue."
# landing_image = "{config_dir}/landing.jpg"
hero_width      = 640                       # width of the login page picture
# app_icon      = "{config_dir}/icon.jpg"   # home-screen icon; defaults to
                                            # landing_image. Any image; it is
                                            # cropped to a square.
show_gps        = true
map_link        = "osm"                     # "osm" | "google" | "none"
album_page_size = 5000
dirsort         = "name"                    # "-name" = newest first
dir_card_aspect = "4/3"                     # "native" = do not crop covers
dir_card_dates  = true                      # dates under a subdirectory's card

[sizes]
thumb = [256, 512]                          # album grid
view  = [1280, 1600]                        # viewing one photo

[encode]
format         = "avif"
fallback       = "auto"                     # "none" = serve AVIF to everyone
speed          = 6
subsampling    = "4:2:0"
recipe_version = 1
# Do not add entries here casually: this table is part of the recipe
# fingerprint, so touching it re-encodes everything, whereas changing `view`
# above only makes the sizes that changed. A size with no entry here uses
# quality_default, which is what makes trying a new size cheap.
quality         = {{ 256 = 52, 512 = 48, 1280 = 46, 2048 = 45 }}
quality_default = 46

[scan]
jobs    = 0                                 # 0 = all cores
nice    = 10
exclude = [".*", "@eaDir", "Thumbs.db"]

[groups]
# family = ["nyh", "someone@gmail.com"]

[google]
enabled = false
# client_id     = "....apps.googleusercontent.com"
# client_secret = "..."
"""

PRIVACY_TEMPLATE = """\
# Privacy

This is a private, invitation-only photo album run on a personal server.

- Photos are visible only to people who have been given an account.
- We do not use analytics, advertising, tracking pixels or third-party scripts.
- We do not share anything with third parties.
- If you sign in with Google, Google tells us only your email address, so that
  we can check it against the invitation list. Nothing else is requested, and
  nothing is sent to Google about what you view.
- Server logs record errors and failed sign-in attempts. They do not record
  which photos anyone looked at.
- Cookies are used only to keep you signed in.

To have your account or any photo of you removed, contact the site owner.
"""

TERMS_TEMPLATE = """\
# Terms

This is a private family photo album, provided as-is, for the personal use of
invited people.

- Do not share your account or the photos with anyone who has not been invited.
- There is no warranty of any kind, and no guarantee of availability.
- The site owner may withdraw access at any time.
"""


class InitError(Exception):
    pass


def _write_new(path: Path, text: str, mode: int = 0o644) -> bool:
    """Write a file only if absent. Returns True if it was created."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return True


def generate_secret_key(path: Path) -> bool:
    """Create a 32-byte secret key, 0600. Never overwrites.

    Rotating this logs everyone out, so an accidental second 'init' must not
    do it.
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with the right mode from the start, rather than chmod'ing after
    # the bytes are already on disk and briefly world-readable. O_EXCL also
    # closes the race against a concurrent init.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, secrets.token_bytes(32))
    finally:
        os.close(fd)
    return True


def init(
    config_path: Path,
    *,
    photo_root: Path,
    state_dir: Path,
    force_config: bool = False,
) -> list[str]:
    """Create config, state directories, secret key, users file and legal text.

    Returns a list of human-readable notes about what happened.
    """
    notes: list[str] = []
    config_dir = config_path.parent
    derived_root = state_dir / "derived"
    index_db = state_dir / "index.sqlite"
    users_file = config_dir / "users.toml"
    secret_key_file = config_dir / "secret_key"

    for d in (config_dir, state_dir, derived_root):
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            notes.append(f"created directory {d}")

    if config_path.exists() and not force_config:
        notes.append(f"kept existing {config_path} (not overwritten)")
    else:
        config_path.write_text(
            CONFIG_TEMPLATE.format(
                photo_root=photo_root,
                derived_root=derived_root,
                index_db=index_db,
                users_file=users_file,
                secret_key_file=secret_key_file,
                config_dir=config_dir,
                state=state_dir,
            ),
            encoding="utf-8",
        )
        notes.append(f"wrote {config_path}")

    if generate_secret_key(secret_key_file):
        notes.append(f"generated {secret_key_file} (mode 0600)")
    else:
        notes.append(f"kept existing {secret_key_file} — rotating it would log everyone out")

    if _write_new(users_file, "# harelphotos accounts. See DESIGN.md 5.2.\n", 0o600):
        notes.append(f"wrote empty {users_file} — add accounts with 'harelphotos user add'")
    else:
        notes.append(f"kept existing {users_file}")

    for name, text in (("privacy.md", PRIVACY_TEMPLATE), ("terms.md", TERMS_TEMPLATE)):
        if _write_new(config_dir / name, text):
            notes.append(f"wrote {config_dir / name} — edit before publishing")

    if index_db.exists():
        notes.append(f"kept existing {index_db}")
    else:
        conn = db.create_index(index_db)
        conn.close()
        notes.append(f"created {index_db} (schema v{db.SCHEMA_VERSION})")

    return notes
