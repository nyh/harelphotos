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

from . import config as config_mod
from . import db, initialise, users as users_mod

log = logging.getLogger("harelphotos")

NOT_YET = {
    "scan": "M2/M3",
    "geocode": "M3",
    "gc": "M3",
    "stats": "M3",
    "serve": "M4",
    "check": "M2",
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
    if args.geonames or args.landmarks:
        print(
            "init --geonames/--landmarks is not implemented yet (M3); "
            "see DESIGN.md 9.5.",
            file=sys.stderr,
        )
        return 2
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
            initialise.InitError) as e:
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
