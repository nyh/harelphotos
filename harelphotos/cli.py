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
from pathlib import Path

from . import check as check_mod
from . import config as config_mod
from . import db, geocode as geocode_mod, geonames, initialise, lock
from . import maintenance, scanner, users as users_mod

log = logging.getLogger("harelphotos")

NOT_YET = {
    "serve": "M4",
    "cover": "M9",
    "acl": "M5",
    "sync": "M8",
}


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
        print("init --landmarks is not implemented yet (M9); see DESIGN.md 9.5.",
              file=sys.stderr)
        return 2
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


def _derive_progress(done: int, total: int, elapsed: float) -> None:
    _bar("generating images", done, total, elapsed)


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    if not cfg.photo_root.is_dir():
        print(f"photo_root is not a directory: {cfg.photo_root}", file=sys.stderr)
        return 1
    lock_path = cfg.state_dir / "scan.lock"
    if args.force_unlock and lock.break_lock(lock_path):
        print(f"removed stale lock {lock_path}")

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
            stats = geocode_mod.geocode(cfg, conn, force=args.force)
    except geonames.GeonamesError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(stats.summary())
    conn.close()
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


def cmd_check(args: argparse.Namespace) -> int:
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
    pi.add_argument("--landmarks", action="store_true", help="download landmarks too (M3)")
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
    pk.set_defaults(func=cmd_check)

    pg = sub.add_parser("geocode", help="resolve place names from GPS already indexed")
    pg.add_argument("--force", action="store_true", help="re-resolve photos that have a place")
    pg.set_defaults(func=cmd_geocode)

    pgc = sub.add_parser("gc", help="remove derivatives with no matching photo")
    pgc.add_argument("--deep", action="store_true", help="walk the whole derived tree")
    pgc.add_argument("--dry-run", action="store_true", help="report, delete nothing")
    pgc.set_defaults(func=cmd_gc)

    pst = sub.add_parser("stats", help="counts and derived-tree size")
    pst.set_defaults(func=cmd_stats)

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

    for name, milestone in sorted(NOT_YET.items()):
        sp = sub.add_parser(name, help=f"(not implemented yet — {milestone})")
        sp.add_argument("rest", nargs=argparse.REMAINDER)
        sp.set_defaults(func=_unimplemented, _milestone=milestone, _name=name)

    return p


def _unimplemented(args: argparse.Namespace) -> int:
    print(
        f"'harelphotos {args._name}' is not implemented yet — planned for "
        f"{args._milestone}. See DESIGN.md 17.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(-1 if args.quiet else args.verbose)
    try:
        return args.func(args)
    except (config_mod.ConfigError, users_mod.UsersError, db.SchemaMismatch,
            initialise.InitError, lock.LockBusy, geonames.GeonamesError,
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
