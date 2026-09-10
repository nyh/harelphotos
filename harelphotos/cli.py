"""Command-line entry point (DESIGN.md 15).

Only the M1 subcommands are implemented; the rest are declared so that
``--help`` describes the whole shape of the tool and so an unimplemented
command says so plainly instead of "unknown command".
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import time
from pathlib import Path

from . import aclcmd
from . import check as check_mod
from . import envcheck
from . import config as config_mod
from . import db, geocode as geocode_mod, geonames, initialise, lock
from . import overrides, queries
from . import maintenance, scanner, sync as sync_mod, users as users_mod

log = logging.getLogger("harelphotos")

def _setup_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    elif verbosity < 0:
        level = logging.ERROR
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s", stream=sys.stderr)


def _load_config(args: argparse.Namespace) -> config_mod.Config:
    return config_mod.load(args.config)


def cmd_init(args: argparse.Namespace) -> int:
    if args.landmarks:
        cfg = _load_config(args)
        dest = cfg.state_dir / "geonames.sqlite"
        print("Landmarks name airports, parks, monuments and the like, on top\n"
              "of the town. This downloads GeoNames' worldwide dump -- about\n"
              "421 MB -- and keeps roughly a tenth of it.\n")
        n = geonames.build_landmarks(dest, progress=lambda m: print(f"  {m}"))
        print(f"kept {n:,} landmarks in {dest} "
              f"({dest.stat().st_size / 1e6:.0f} MB)")
        print("Run 'harelphotos geocode --force' to apply them to photos\n"
              "that already have a place name.")
        print("Data from GeoNames (https://www.geonames.org/), CC BY 4.0.")
        return 0
    if args.geonames:
        cfg = _load_config(args)
        dest = cfg.state_dir / "geonames.sqlite"
        print(f"downloading the place-name dataset (~14 MB) to {dest} ...")
        n = geonames.build(dest, progress=lambda msg: print(f"  {msg}"))
        print(f"built {dest} with {n:,} places "
              f"({dest.stat().st_size / 1e6:.0f} MB)")
        print("Data from GeoNames (https://www.geonames.org/), CC BY 4.0.")
        return 0
    config_path = Path(args.config) if args.config else Path("config.toml")
    photo_root = Path(args.photo_root).expanduser().resolve() if args.photo_root else None
    if photo_root is None:
        print(
            "init needs --photo-root: the directory holding your photos.\n"
            "  harelphotos init --photo-root ~/pictures",
            file=sys.stderr,
        )
        return 2
    if not photo_root.is_dir():
        print(f"--photo-root is not a directory: {photo_root}", file=sys.stderr)
        return 2
    state_dir = (
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else config_path.parent.resolve() / "state"
    )
    for note in initialise.init(
        config_path.resolve(), photo_root=photo_root, state_dir=state_dir
    ):
        print(f"  {note}")
    try:
        from . import public_assets
        cfg = config_mod.load(config_path)
        for name in public_assets.build(cfg) + public_assets.build_icons(cfg):
            print(f"  prepared {name}")
    except config_mod.ConfigError:
        pass
    print(f"\nNext: harelphotos user add <name>, then 'harelphotos scan' (M2).")
    return 0


def cmd_user_add(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    us = users_mod.load(cfg.users_file)
    if us.get(args.name):
        print(f"user {args.name!r} already exists", file=sys.stderr)
        return 1
    pw_hash = None
    if not args.google_only:
        pw = args.password or getpass.getpass(f"Password for {args.name}: ")
        if not pw:
            print("empty password refused", file=sys.stderr)
            return 2
        if not args.password:
            again = getpass.getpass("Again: ")
            if pw != again:
                print("passwords did not match", file=sys.stderr)
                return 2
        pw_hash = users_mod.hash_password(pw)
    table = dict(us.by_token)
    table[args.name] = users_mod.User(
        token=args.name,
        name=args.display_name or args.name,
        password_hash=pw_hash,
        google=args.google,
        admin=args.admin,
    )
    users_mod.save(cfg.users_file, users_mod.Users(by_token=table))
    print(f"added {args.name} to {cfg.users_file}")
    return 0


def cmd_user_list(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    us = users_mod.load(cfg.users_file)
    if not len(us):
        print(f"no accounts in {cfg.users_file}")
        return 0
    print(f"{'token':24} {'name':20} {'local':6} {'google':28} {'admin':5} epoch")
    for u in sorted(us, key=lambda x: x.token):
        print(
            f"{u.token:24} {u.name:20} {'yes' if u.can_login_locally else 'no':6} "
            f"{u.google or '-':28} {'yes' if u.admin else 'no':5} {u.epoch}"
        )
    return 0


def cmd_user_passwd(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    us = users_mod.load(cfg.users_file)
    u = us.get(args.name)
    if u is None:
        print(f"no such user: {args.name}", file=sys.stderr)
        return 1
    pw = getpass.getpass(f"New password for {args.name}: ")
    if pw != getpass.getpass("Again: "):
        print("passwords did not match", file=sys.stderr)
        return 2
    table = dict(us.by_token)
    table[args.name] = users_mod.User(
        token=u.token,
        name=u.name,
        password_hash=users_mod.hash_password(pw),
        google=u.google,
        admin=u.admin,
        epoch=u.epoch,
    )
    users_mod.save(cfg.users_file, users_mod.Users(by_token=table))
    print(f"password updated for {args.name}")
    return 0


def cmd_user_del(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    us = users_mod.load(cfg.users_file)
    if us.get(args.name) is None:
        print(f"no such user: {args.name}", file=sys.stderr)
        return 1
    table = {k: v for k, v in us.by_token.items() if k != args.name}
    users_mod.save(cfg.users_file, users_mod.Users(by_token=table))
    print(f"removed {args.name}; their sessions stop working immediately")
    return 0


def cmd_user_revoke(args: argparse.Namespace) -> int:
    """Bump a user's epoch, invalidating every existing session cookie."""
    cfg = _load_config(args)
    us = users_mod.load(cfg.users_file)
    u = us.get(args.name)
    if u is None:
        print(f"no such user: {args.name}", file=sys.stderr)
        return 1
    table = dict(us.by_token)
    table[args.name] = users_mod.User(
        token=u.token,
        name=u.name,
        password_hash=u.password_hash,
        google=u.google,
        admin=u.admin,
        epoch=u.epoch + 1,
    )
    users_mod.save(cfg.users_file, users_mod.Users(by_token=table))
    print(f"revoked all sessions for {args.name} (epoch {u.epoch} -> {u.epoch + 1})")
    return 0


def cmd_config_show(args: argparse.Namespace) -> int:
    """Print the effective configuration — the quickest way to see what loaded."""
    cfg = _load_config(args)
    print(f"config file      {cfg.source}")
    print(f"photo_root       {cfg.photo_root}")
    print(f"derived_root     {cfg.derived_root}")
    print(f"index_db         {cfg.index_db}")
    print(f"users_file       {cfg.users_file}")
    print(f"secret_key_file  {cfg.secret_key_file}")
    print(f"base_url         {cfg.base_url}")
    print(f"tiers            {', '.join(str(t) for t in cfg.sizes.tiers)}")
    print(f"                 thumb {list(cfg.sizes.thumb)}  view {list(cfg.sizes.view)}")
    print(f"encode           {cfg.encode.format} speed={cfg.encode.speed} "
          f"fallback={cfg.encode.fallback} recipe={cfg.encode.recipe_version}")
    print(f"quality          " + ", ".join(
        f"{t}:{cfg.encode.quality_for(t)}" for t in cfg.sizes.tiers))
    print(f"scan jobs        {cfg.scan.effective_jobs} (nice {cfg.scan.nice})")
    print(f"groups           {', '.join(sorted(cfg.groups)) or '-'}")
    print(f"google           {'enabled' if cfg.google.enabled else 'disabled'}")
    idx = "present" if cfg.index_db.exists() else "absent (run scan)"
    print(f"index            {idx}")
    if cfg.index_db.exists():
        try:
            conn = db.open_index(cfg.index_db, read_only=True)
            conn.close()
            print(f"schema           v{db.SCHEMA_VERSION}, ok")
        except db.SchemaMismatch as e:
            print(f"schema           MISMATCH: {e}")
    us = users_mod.load(cfg.users_file)
    print(f"accounts         {len(us)}")
    return 0


def _fmt_date(ts: int | None) -> str:
    if ts is None:
        return "-"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _eta(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def _bar(label: str, done: int, total: int, elapsed: float) -> None:
    rate = done / elapsed if elapsed > 0 else 0
    eta = (total - done) / rate if rate > 0 else 0
    sys.stderr.write(
        f"\r\033[K  {label}: {done:,}/{total:,} · {rate:,.1f}/s · ETA {_eta(eta)}"
    )
    sys.stderr.flush()


def _progress(done: int, total: int, elapsed: float) -> None:
    _bar("reading metadata", done, total, elapsed)


def _walk_progress(dirs: int, photos: int, elapsed: float, final: bool) -> None:
    """Phase 1 has no total to count towards, so show what it has found."""
    rate = photos / elapsed if elapsed > 0 else 0
    sys.stderr.write(
        f"\r\033[K  looking for photos: {dirs:,} director{'y' if dirs == 1 else 'ies'}, "
        f"{photos:,} photos"
        + (f" · {rate:,.0f}/s" if elapsed > 1 else "")
    )
    sys.stderr.flush()


def _derive_progress(done: int, total: int, elapsed: float) -> None:
    _bar("generating images", done, total, elapsed)


def _geocode_progress():
    """A live line for `geocode`, drawn on a clock rather than a row count.

    It ran silently before. Most of its work is looking up distinct
    coordinates, so the rate climbs sharply once a trip's photos start
    repeating locations -- which is worth watching rather than guessing at.
    """
    started = time.monotonic()
    last = [0.0]

    def report(done: int, total: int) -> None:
        now = time.monotonic()
        if done < total and now - last[0] < scanner.PROGRESS_INTERVAL:
            return
        last[0] = now
        _bar("naming places", done, total, now - started)

    return report


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    if not cfg.photo_root.is_dir():
        print(f"photo_root is not a directory: {cfg.photo_root}", file=sys.stderr)
        return 1
    lock_path = cfg.state_dir / "scan.lock"
    if args.force_unlock and lock.break_lock(lock_path):
        print(f"removed stale lock {lock_path}")

    if not args.quiet:
        target = cfg.photo_root / args.dir if args.dir else cfg.photo_root
        print(f"scanning {target}", file=sys.stderr)

    if args.dry_run:
        # A dry run must not touch the real index, so point the scanner at a
        # throwaway copy of the schema in memory.
        import sqlite3 as _sq
        conn = _sq.connect(":memory:")
        conn.row_factory = _sq.Row
        db.initialise(conn)
        print("dry run: counting what a scan would find, writing nothing")
    else:
        conn = db.open_index(cfg.index_db)

    try:
        with lock.ScanLock(lock_path):
            stats = scanner.scan(
                cfg,
                conn,
                subpath=args.dir or "",
                jobs=args.jobs,
                limit=args.limit,
                full=args.full,
                repair=args.repair,
                headers_only=args.headers_only,
                walk_progress=None if args.quiet else _walk_progress,
                progress=None if args.quiet else _progress,
                derive_progress=None if args.quiet else _derive_progress,
            )
    except lock.LockBusy as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if not args.dry_run:
            conn.commit()

    if not args.quiet:
        sys.stderr.write("\r\033[K")      # clear the progress line
        sys.stderr.flush()
    print(stats.summary())
    for path in stats.skipped_names[:10]:
        print(f"  skipped (not valid UTF-8): {path}", file=sys.stderr)
    if len(stats.skipped_names) > 10:
        print(f"  ... and {len(stats.skipped_names) - 10} more", file=sys.stderr)
    for msg in stats.config_errors[:10]:
        print(f"  {msg}", file=sys.stderr)
    if stats.photos_failed:
        print(f"  {stats.photos_failed} photos could not be read; see 'harelphotos check'",
              file=sys.stderr)
    conn.close()
    return 0


def cmd_geocode(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    conn = db.open_index(cfg.index_db)
    lock_path = cfg.state_dir / "scan.lock"
    try:
        with lock.ScanLock(lock_path):
            stats = geocode_mod.geocode(
                cfg, conn, force=args.force,
                progress=None if args.quiet else _geocode_progress(),
            )
    except geonames.GeonamesError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not args.quiet:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()
    print(stats.summary())
    if stats.landmarks_stale:
        # The table is filtered when it is built, so a code added since then
        # was never stored and no amount of re-geocoding will find it.
        print("\nNOTE: the landmark table was built with an older list of\n"
              "      feature codes. Re-run 'harelphotos init --landmarks' to\n"
              "      rebuild it (the download is cached), then geocode again.",
              file=sys.stderr)
    conn.close()
    return 0


def cmd_acl(args: argparse.Namespace) -> int:
    cfg = _load_config(args)

    if args.list:
        conn = db.open_index(cfg.index_db, read_only=True)
        rows = aclcmd.restricted_dirs(conn)
        conn.close()
        if not rows:
            print("no directory is restricted")
            return 0
        print(f"{len(rows)} restricted director{'y' if len(rows) == 1 else 'ies'}:")
        for path, chain in rows:
            allow = " AND ".join("{" + ", ".join(link) + "}" for link in chain)
            print(f"  /{path}  {allow}")
        return 0

    relpath = (args.dir or "").strip("/")
    d = cfg.photo_root / relpath if relpath else cfg.photo_root
    if not d.is_dir():
        print(f"error: {d} is not a directory", file=sys.stderr)
        return 1

    if args.allow is not None or args.clear:
        names = None if args.clear else [
            a.strip() for a in args.allow.split(",") if a.strip()
        ]
        if names == []:
            print("error: --allow needs at least one name; use --clear to remove "
                  "the restriction", file=sys.stderr)
            return 1
        try:
            path = aclcmd.write_allow(cfg, relpath, names, replace=args.replace)
        except aclcmd.AclError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"wrote {path}")

        # A restriction that is not in the index is not in force: the web
        # process reads the chain computed at scan time, so an edit does
        # nothing until the subtree is walked again.
        conn = db.open_index(cfg.index_db)
        try:
            scanner.scan(cfg, conn, subpath=relpath, headers_only=True)
        finally:
            conn.close()
        print("rescanned the subtree, so the change is in force now")

    return _print_acl(cfg, relpath)


def _print_acl(cfg: config_mod.Config, relpath: str) -> int:
    links = aclcmd.chain_with_sources(cfg, relpath)
    where = "/" + relpath if relpath else "/ (the whole collection)"
    print(f"\n{where}")
    if not links:
        print("  unrestricted — every account can see it")
        return 0

    print("  restrictions, all of which must admit you:")
    for link in links:
        src = "/" + link.dir_path if link.dir_path != "." else "/"
        note = "  (replaces everything above it)" if link.replaced else ""
        print(f"    from {src:<30} {', '.join(link.allow)}{note}")

    can, unknown = aclcmd.who_can_view(cfg, links)
    print(f"  can see it: {', '.join(sorted(can)) if can else 'NOBODY'}")
    if unknown:
        # Silent and always in the restrictive direction, so worth shouting
        # about: nobody ever complains about photos they cannot see.
        print(f"  WARNING: matches no account or group: {', '.join(sorted(unknown))}")
    return 0


def cmd_hide(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    relpath = (args.dir or "").strip("/")
    if not relpath:
        print("error: refusing to hide the whole collection", file=sys.stderr)
        return 1
    d = cfg.photo_root / relpath
    if not d.is_dir():
        print(f"error: {d} is not a directory", file=sys.stderr)
        return 1

    overrides.set_for(cfg, relpath, hidden=None if args.show else True)
    # Hiding is resolved at scan time, like the ACL chain, so that a request
    # can decide from one column instead of walking ancestors. That means the
    # change is not in force until the subtree has been walked again.
    conn = db.open_index(cfg.index_db)
    try:
        scanner.scan(cfg, conn, subpath=relpath, headers_only=True)
    finally:
        conn.close()
    if args.show:
        print(f"/{relpath} is visible again")
    else:
        print(f"/{relpath} and everything beneath it is hidden:\n"
              f"  not listed, and not reachable by URL either.\n"
              f"  The photos are untouched on disk. This is tidiness, not\n"
              f"  privacy -- use 'harelphotos acl' to control who may see something.")
    return 0


def cmd_cover(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    relpath = (args.dir or "").strip("/")
    d = cfg.photo_root / relpath if relpath else cfg.photo_root
    if not d.is_dir():
        print(f"error: {d} is not a directory", file=sys.stderr)
        return 1

    if args.clear:
        overrides.set_for(cfg, relpath, cover=None)
        print(f"cleared the cover pick for /{relpath}")
    elif args.photo:
        # Checked against the index rather than the filesystem: a name that is
        # not an indexed photo would leave the album showing nothing, and the
        # mistake would only surface as a blank card.
        #
        # A path is allowed as well as a bare name, and for a directory holding
        # nothing but subdirectories it is the only possibility -- there is no
        # photo of its own to name.
        pick = args.photo.strip("/")
        if ".." in pick.split("/"):
            print("error: the photo must be inside this album", file=sys.stderr)
            return 1
        conn = db.open_index(cfg.index_db, read_only=True)
        index = queries.Index(conn, cfg)
        full = f"{relpath}/{pick}" if relpath else pick
        found = index.photo(full, queries.Viewer(token=None, name="", is_admin=True))
        conn.close()
        if found is None:
            print(f"error: {pick!r} is not a photo under /{relpath}\n"
                  f"  give a file name, or a path relative to this album such as\n"
                  f"  '2003a/IMG_0123.JPG' for a photo in a subdirectory",
                  file=sys.stderr)
            return 1
        overrides.set_for(cfg, relpath, cover=pick)
        print(f"cover for /{relpath} is now {pick}")

    conn = db.open_index(cfg.index_db, read_only=True)
    index = queries.Index(conn, cfg)
    alb = index.album(relpath, queries.Viewer(token=None, name="", is_admin=True))
    cover = index.cover_photo(alb) if alb else None
    conn.close()
    picked = overrides.get(cfg, relpath).cover
    print(f"showing: {cover.name if cover else '(nothing)'}"
          + ("  (picked)" if picked else "  (chosen automatically)"))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    dest = sync_mod.Destination.parse(args.dest)
    r = sync_mod.run(
        cfg,
        dest,
        dry_run=args.dry_run,
        geonames=args.geonames,
        index_only=args.index_only,
    )
    print()
    for what, detail in r.steps:
        print(f"{what:16} {detail}")
    if args.dry_run:
        print("\n(dry run — nothing was copied)")
    else:
        print(f"\nsynced to {dest}")
        print("run 'harelphotos gc' there to reclaim images this scan dropped")
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    conn = db.open_index(cfg.index_db, read_only=True)
    r = maintenance.collect(cfg, conn, deep=args.deep, dry_run=args.dry_run)
    what = "would remove" if args.dry_run else "removed"
    if r.stale_tiers:
        print(f"{what} tier directories no longer configured: {', '.join(r.stale_tiers)}")
    if r.orphan_files:
        print(f"{what} {len(r.orphan_files):,} orphaned files")
        for f in r.orphan_files[:10]:
            print(f"  {f}")
        if len(r.orphan_files) > 10:
            print(f"  ... and {len(r.orphan_files) - 10:,} more")
    if r.dirs_removed:
        print(f"removed {r.dirs_removed} empty directories")
    if not (r.stale_tiers or r.orphan_files or r.dirs_removed):
        print("nothing to collect")
    else:
        print(f"{r.bytes_freed / 1e6:.1f} MB {'reclaimable' if args.dry_run else 'freed'}")
    if not args.deep:
        print("(use --deep to also look for files with no matching photo)")
    conn.close()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    conn = db.open_index(cfg.index_db, read_only=True)
    s = maintenance.gather(cfg, conn)
    print(f"directories      {s.dirs:,}")
    print(f"photos           {s.photos:,}")
    print(f"  derived        {s.derived_photos:,}")
    if s.pending:
        print(f"  pending        {s.pending:,}   (run 'harelphotos scan')")
    if s.failed:
        print(f"  failed         {s.failed:,}   (see 'harelphotos check')")
    print(f"  with a place   {s.with_place:,}")
    if s.per_tier:
        print(f"\nderived tree     {s.derived_bytes / 1e9:.2f} GB in {s.derived_files:,} files")
        for label, n, size in s.per_tier:
            avg = size / n if n else 0
            print(f"  {label:10} {n:8,} files  {size / 1e9:6.2f} GB  avg {avg / 1024:6.1f} KB")
        if s.derived_photos:
            print(f"  per photo  {s.derived_bytes / s.derived_photos / 1024:.0f} KB")
    if s.biggest:
        print("\nbiggest directories:")
        for path, n in s.biggest:
            print(f"  {n:7,}  {path}")
    conn.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    if not cfg.index_db.exists():
        print("no index yet — run 'harelphotos scan' first", file=sys.stderr)
        return 1
    conn = db.open_index(cfg.index_db, read_only=True)
    total = conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"]
    ready = conn.execute(
        "SELECT count(*) AS n FROM photos WHERE deriv_key IS NOT NULL"
    ).fetchone()["n"]
    conn.close()
    if total == 0:
        print("the index is empty — run 'harelphotos scan' first", file=sys.stderr)
        return 1
    if ready < total:
        # Otherwise the pages render with every thumbnail broken and no clue why.
        print(
            f"NOTE: {total - ready:,} of {total:,} photos have no images generated yet, "
            f"so they will not display.\n"
            f"      Run 'harelphotos scan' to generate them.",
            file=sys.stderr,
        )

    from . import public_assets
    from .web import create_app

    host = args.bind or "127.0.0.1"
    loopback = host in ("127.0.0.1", "localhost", "::1")

    # No login by default: this is the development server, and typing a
    # password to look at your own photos on your own machine is friction for
    # nothing. --login turns the real gate on so it can be exercised.
    require_login = bool(args.login)
    if not loopback and not require_login and not args.insecure:
        print(
            f"refusing to serve {host} without a login: that would hand the whole\n"
            f"collection to anyone who can reach this machine.\n"
            f"  Use --login to require one (recommended), or --insecure to mean it.",
            file=sys.stderr,
        )
        return 2

    made = public_assets.build(cfg) + public_assets.build_icons(cfg)
    if made:
        print(f"prepared the landing image and icons ({', '.join(made)})")

    app = create_app(cfg, require_login=require_login)
    print(f"serving {cfg.photo_root} at http://{host}:{args.port}/  (Ctrl-C to stop)")
    if require_login:
        from . import users as u
        n = len(u.load(cfg.users_file))
        print(f"login required — {n} account{'' if n == 1 else 's'} in {cfg.users_file}")
        if n == 0:
            print("  no accounts yet: harelphotos user add <name>", file=sys.stderr)
    else:
        print("NO LOGIN REQUIRED (use --login to turn authentication on)")
    app.run(host=host, port=args.port, debug=False, threaded=True)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    if args.env:
        # Runnable before anything is configured: this is the first thing to
        # run on a new machine, and the python/apache/selinux checks are
        # exactly what you want at that point.
        try:
            cfg = _load_config(args)
        except config_mod.ConfigError as e:
            cfg = None
            print(f"note: {e}\n", file=sys.stderr)
        r = envcheck.run(cfg, url=args.url)
        print(r.render())
        print()
        if r.failures:
            print(f"{r.failures} failure(s), {r.warnings} warning(s)")
        elif r.warnings:
            print(f"no failures, {r.warnings} warning(s)")
        else:
            print("everything checks out")
        return 1 if r.failures else 0
    cfg = _load_config(args)
    conn = db.open_index(cfg.index_db, read_only=True)
    r = check_mod.run(cfg, conn, verify_files=args.verify_files)
    print(f"index          {cfg.index_db}")
    print(f"directories    {r.dirs:,}")
    print(f"photos         {r.photos:,}")
    pct = (100 * r.photos_with_headers / r.photos) if r.photos else 0
    print(f"  metadata read  {r.photos_with_headers:,} ({pct:.0f}%)")
    pct = (100 * r.photos_with_dates / r.photos) if r.photos else 0
    print(f"  EXIF date      {r.photos_with_dates:,} ({pct:.0f}%)")
    pct = (100 * r.photos_with_gps / r.photos) if r.photos else 0
    print(f"  GPS            {r.photos_with_gps:,} ({pct:.0f}%)")
    print(f"date range     {_fmt_date(r.date_range[0])} .. {_fmt_date(r.date_range[1])}")

    if r.biggest:
        print("\nbiggest directories:")
        for path, n in r.biggest:
            print(f"  {n:7,}  {path}")
    if r.restricted:
        print(f"\nrestricted directories ({len(r.restricted)}):")
        for path, chain in r.restricted[:20]:
            print(f"  {path}  {chain}")
    if r.empty_dirs:
        print(f"\ndirectories with no photos anywhere ({len(r.empty_dirs)}):")
        for path in r.empty_dirs[:20]:
            print(f"  {path}")
        if len(r.empty_dirs) > 20:
            print(f"  ... and {len(r.empty_dirs) - 20} more")

    if r.problems:
        print(f"\nPROBLEMS ({r.problems}):")
        for path, err in r.config_errors[:20]:
            print(f"  .album.toml  {path}: {err}")
        for path, err in r.photo_errors[:20]:
            print(f"  photo        {path}: {err}")
        for path in r.missing_covers[:20]:
            print(f"  cover        {path}: 'cover' names a photo that does not exist")
        if r.missing_derivatives:
            print(f"  derivatives  {len(r.missing_derivatives):,} photos have recorded "
                  f"images that are not on disk — run 'harelphotos scan --repair'")
            for path in r.missing_derivatives[:5]:
                print(f"                 {path}")
    else:
        print("\nno problems found")
    conn.close()
    return 1 if r.problems else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harelphotos",
        description="A self-hosted photo gallery over a directory tree of JPEGs.",
    )
    p.add_argument("-c", "--config", help="path to config.toml")
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.add_argument("-q", "--quiet", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("init", help="create config, state directories and secret key")
    pi.add_argument("--photo-root", help="directory holding your photos")
    pi.add_argument("--state-dir", help="where index.sqlite and derived/ go")
    pi.add_argument("--geonames", action="store_true", help="download the city dataset (M3)")
    pi.add_argument("--landmarks", action="store_true", help="also name airports, parks and monuments (a 421 MB download)")
    pi.set_defaults(func=cmd_init)

    pc = sub.add_parser("config", help="inspect configuration")
    csub = pc.add_subparsers(dest="subcommand", required=True)
    pcs = csub.add_parser("show", help="print the effective configuration")
    pcs.set_defaults(func=cmd_config_show)

    ps = sub.add_parser("scan", help="index the photo tree (phases 1-2)")
    ps.add_argument("--dir", help="rescan only this subdirectory")
    ps.add_argument("--jobs", type=int, help="parallel workers (default: all cores)")
    ps.add_argument("--limit", type=int, help="stop after N header reads")
    ps.add_argument("--full", action="store_true",
                    help="re-read all metadata and regenerate all images")
    ps.add_argument("--repair", action="store_true",
                    help="rebuild derivatives whose files have gone missing")
    ps.add_argument("--headers-only", "--no-images", dest="headers_only",
                    action="store_true",
                    help="index metadata only, generate no images")
    ps.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ps.add_argument("--force-unlock", action="store_true", help="remove a stale lock file")
    ps.set_defaults(func=cmd_scan)

    pk = sub.add_parser("check", help="report index contents and problems")
    pk.add_argument("--verify-files", action="store_true",
                    help="also check that every recorded derivative is on disk")
    pk.add_argument("--env", action="store_true",
                    help="check this machine instead: python, permissions, apache, selinux")
    pk.add_argument("--url", help="with --env, also check a running site over the network")
    pk.set_defaults(func=cmd_check)

    pg = sub.add_parser("geocode", help="resolve place names from GPS already indexed")
    pg.add_argument("--force", action="store_true", help="re-resolve photos that have a place")
    pg.set_defaults(func=cmd_geocode)

    ph = sub.add_parser("hide", help="keep a directory out of the album listing")
    ph.add_argument("dir", help="directory, relative to photo_root")
    ph.add_argument("--show", action="store_true", help="undo it")
    ph.set_defaults(func=cmd_hide)

    pc = sub.add_parser("cover", help="choose the photo shown on an album's card")
    pc.add_argument("dir", help="directory, relative to photo_root")
    pc.add_argument("photo", nargs="?", help="file name of a photo in it")
    pc.add_argument("--clear", action="store_true",
                    help="go back to choosing one automatically")
    pc.set_defaults(func=cmd_cover)

    pa = sub.add_parser(
        "acl", help="inspect or set who may see a directory"
    )
    pa.add_argument("dir", nargs="?", default="",
                    help="directory, relative to photo_root (default: the root)")
    pa.add_argument("--allow", metavar="NAMES",
                    help="comma-separated accounts and @groups; replaces this "
                         "directory's own list")
    pa.add_argument("--replace", action="store_true",
                    help="with --allow, ignore restrictions inherited from above")
    pa.add_argument("--clear", action="store_true",
                    help="remove this directory's own restriction")
    pa.add_argument("--list", action="store_true",
                    help="list every restricted directory in the index")
    pa.set_defaults(func=cmd_acl)

    psy = sub.add_parser(
        "sync", help="copy the generated images and index to the serving machine"
    )
    psy.add_argument("dest", metavar="[user@]host:/path",
                     help="the far side's state directory, e.g. "
                          "nyh@harel.org.il:/var/lib/harelphotos")
    psy.add_argument("--dry-run", action="store_true", help="report, copy nothing")
    psy.add_argument("--index-only", action="store_true",
                     help="skip the images (use when only metadata changed)")
    psy.add_argument("--geonames", action="store_true",
                     help="also send the place-name database (once, ~30 MB)")
    psy.set_defaults(func=cmd_sync)

    pgc = sub.add_parser("gc", help="remove derivatives with no matching photo")
    pgc.add_argument("--deep", action="store_true", help="walk the whole derived tree")
    pgc.add_argument("--dry-run", action="store_true", help="report, delete nothing")
    pgc.set_defaults(func=cmd_gc)

    pst = sub.add_parser("stats", help="counts and derived-tree size")
    pst.set_defaults(func=cmd_stats)

    pv = sub.add_parser("serve", help="run the web interface (development server)")
    pv.add_argument("--bind", help="address to listen on (default: 127.0.0.1)")
    pv.add_argument("--port", type=int, default=5000)
    pv.add_argument("--login", action="store_true",
                    help="require logging in (off by default on this server)")
    pv.add_argument("--insecure", action="store_true",
                    help="allow a non-local address with no login")
    pv.set_defaults(func=cmd_serve)

    pu = sub.add_parser("user", help="manage accounts in users.toml")
    usub = pu.add_subparsers(dest="subcommand", required=True)

    ua = usub.add_parser("add", help="add an account")
    ua.add_argument("name", help="username, or the email for a Google-only account")
    ua.add_argument("--display-name", help="name shown in the UI (default: the username)")
    ua.add_argument("--google", help="Google email this account may also sign in with")
    ua.add_argument("--google-only", action="store_true", help="no password; Google sign-in only")
    ua.add_argument("--admin", action="store_true", help="bypass all ACLs")
    ua.add_argument("--password", help="set non-interactively (avoid: it lands in your shell history)")
    ua.set_defaults(func=cmd_user_add)

    ul = usub.add_parser("list", help="list accounts")
    ul.set_defaults(func=cmd_user_list)
    up = usub.add_parser("passwd", help="change a password")
    up.add_argument("name")
    up.set_defaults(func=cmd_user_passwd)
    ud = usub.add_parser("del", help="remove an account")
    ud.add_argument("name")
    ud.set_defaults(func=cmd_user_del)
    ur = usub.add_parser("revoke", help="invalidate a user's existing sessions")
    ur.add_argument("name")
    ur.set_defaults(func=cmd_user_revoke)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(-1 if args.quiet else args.verbose)
    try:
        return args.func(args)
    except (config_mod.ConfigError, users_mod.UsersError, db.SchemaMismatch,
            db.NotWritable, initialise.InitError, lock.LockBusy,
            geonames.GeonamesError, sync_mod.SyncError,
            NotADirectoryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
