"""Global configuration: loading, defaults and validation (DESIGN.md 5.1)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_ENV = "HARELPHOTOS_CONFIG"

# Search order, first match wins (DESIGN.md 5.1).
CONFIG_SEARCH = (
    Path("config.toml"),
    Path.home() / ".config" / "harelphotos" / "config.toml",
    Path("/etc/harelphotos/config.toml"),
)

DEFAULT_TIERS_THUMB = [256, 512]

# 1600 rather than 2048 as the top view size. Measured in a real browser: the
# photo page letterboxes the image to fit inside the window, so the binding
# constraint is the available HEIGHT, not the window width. A 4:3 photo in a
# 1920x1080 window renders 1079 px wide, and the most demanding case tested --
# a retina laptop -- needs 1676. Nothing needed 2048, and it was more than half
# the derived tree.
DEFAULT_TIERS_VIEW = [1280, 1600]

# Per-tier AVIF quality. Smaller tiers get a higher Q because downscaling
# concentrates detail, so low-Q artefacts show more (DESIGN.md 5.1).
#
# **Do not add entries here casually.** This table is part of the recipe
# fingerprint, so touching it re-encodes the whole collection, whereas changing
# which sizes are generated costs only the sizes that changed. A size with no
# entry uses `quality_default`, which is what makes trying a new one cheap — so
# a new size is added to `[sizes]` and left out of here.
DEFAULT_QUALITY = {256: 52, 512: 48, 1280: 46, 2048: 45}


class ConfigError(Exception):
    """A configuration file is missing, malformed or self-contradictory.

    Always fatal: unlike a bad .album.toml (which degrades to defaults for one
    directory), a bad global config means we do not know where the photos are.
    """


@dataclass(frozen=True)
class Sizes:
    thumb: tuple[int, ...] = tuple(DEFAULT_TIERS_THUMB)
    view: tuple[int, ...] = tuple(DEFAULT_TIERS_VIEW)

    @property
    def tiers(self) -> tuple[int, ...]:
        """All configured tiers, descending — the order the cascade runs in."""
        return tuple(sorted(set(self.thumb) | set(self.view), reverse=True))


@dataclass(frozen=True)
class Encode:
    format: str = "avif"
    fallback: str = "auto"          # "auto" = Accept negotiation; "none" = one format
    speed: int = 6
    subsampling: str = "4:2:0"
    recipe_version: int = 1
    quality: dict[int, int] = field(default_factory=lambda: dict(DEFAULT_QUALITY))
    quality_default: int = 46

    def quality_for(self, px: int) -> int:
        return self.quality.get(px, self.quality_default)


@dataclass(frozen=True)
class Ui:
    site_title: str = "Photo Album"
    heading: str = "Photo Album"
    tagline: str = "By invitation only. Please login to continue."
    landing_image: Path | None = None
    show_gps: bool = True
    map_link: str = "osm"           # "osm" | "google" | "none"
    album_page_size: int = 5000
    dirsort: str = "name"
    dir_card_aspect: str = "4/3"


@dataclass(frozen=True)
class Scan:
    jobs: int = 0                   # 0 = os.cpu_count()
    nice: int = 10
    exclude: tuple[str, ...] = (".*", "@eaDir", "Thumbs.db")

    @property
    def effective_jobs(self) -> int:
        return self.jobs if self.jobs > 0 else (os.cpu_count() or 1)


@dataclass(frozen=True)
class Google:
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""


@dataclass(frozen=True)
class Config:
    photo_root: Path
    derived_root: Path
    index_db: Path
    users_file: Path
    secret_key_file: Path
    base_url: str = "http://127.0.0.1:5000"
    sendfile_header: str = "auto"   # "auto" | "X-Sendfile" | "none"
    # Set only when a reverse proxy on this machine terminates TLS. It makes
    # the application believe X-Forwarded-For and X-Forwarded-Proto, which a
    # directly-reachable server must never do: anyone could then claim any
    # address and defeat the login throttle.
    behind_proxy: bool = False
    # How long a login lasts, in days. Counted from the moment of logging in,
    # not from last use: the cookie is deliberately not re-sent on every
    # response, because its value is part of the browser's image cache key and
    # changing it threw away every cached thumbnail (see web.py).
    session_days: int = 30
    log_file: Path | None = None
    sizes: Sizes = field(default_factory=Sizes)
    encode: Encode = field(default_factory=Encode)
    ui: Ui = field(default_factory=Ui)
    scan: Scan = field(default_factory=Scan)
    groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    google: Google = field(default_factory=Google)
    source: Path | None = None      # which file this came from, for error messages

    @property
    def state_dir(self) -> Path:
        """Where geonames.sqlite and the scan lock live: beside the index."""
        return self.index_db.parent


def find_config(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate config.toml, honouring $HARELPHOTOS_CONFIG and the search order."""
    if explicit is not None:
        p = Path(explicit)
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        return p
    env = os.environ.get(CONFIG_ENV)
    if env:
        p = Path(env)
        if not p.is_file():
            raise ConfigError(f"{CONFIG_ENV} points at a missing file: {p}")
        return p
    for cand in CONFIG_SEARCH:
        if cand.is_file():
            return cand
    searched = ", ".join(str(c) for c in CONFIG_SEARCH)
    raise ConfigError(
        f"no config.toml found (tried ${CONFIG_ENV}, then {searched}). "
        f"Run 'harelphotos init' to create one."
    )


def _require(raw: dict, key: str, src: Path) -> str:
    val = raw.get(key)
    if not val:
        raise ConfigError(f"{src}: missing required key '{key}'")
    if not isinstance(val, str):
        raise ConfigError(f"{src}: '{key}' must be a string, got {type(val).__name__}")
    return val


def _tier_list(raw: object, key: str, src: Path, default: list[int]) -> tuple[int, ...]:
    if raw is None:
        return tuple(default)
    # A bare integer is an easy mistake to make and harmless to accept.
    if isinstance(raw, int) and not isinstance(raw, bool):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{src}: [sizes] {key} must be a non-empty list of pixel sizes")
    out = []
    for v in raw:
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            raise ConfigError(f"{src}: [sizes] {key} contains a non-positive-integer: {v!r}")
        out.append(v)
    return tuple(sorted(set(out)))


def _enum(value: object, allowed: tuple[str, ...], what: str, src: Path, default: str) -> str:
    if value is None:
        return default
    if value not in allowed:
        raise ConfigError(
            f"{src}: {what} must be one of {', '.join(allowed)}, got {value!r}"
        )
    return str(value)


def _positive_int(value: object, what: str, src: Path, default: int) -> int:
    if value is None:
        return default
    # bool is an int subclass, and `session_days = true` is a mistake, not a 1.
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{src}: {what} must be a positive integer, got {value!r}")
    return value


def load(path: str | os.PathLike[str] | None = None) -> Config:
    """Load and validate config.toml."""
    src = find_config(path)
    try:
        raw = tomllib.loads(src.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{src}: invalid TOML: {e}") from e
    except OSError as e:
        raise ConfigError(f"{src}: cannot read: {e}") from e
    return from_dict(raw, src)


def from_dict(raw: dict, src: Path) -> Config:
    """Build a Config from already-parsed TOML. Separated out for testing."""
    photo_root = Path(_require(raw, "photo_root", src)).expanduser()
    derived_root = Path(_require(raw, "derived_root", src)).expanduser()
    index_db = Path(_require(raw, "index_db", src)).expanduser()
    users_file = Path(_require(raw, "users_file", src)).expanduser()
    secret_key_file = Path(_require(raw, "secret_key_file", src)).expanduser()

    sz = raw.get("sizes") or {}
    sizes = Sizes(
        thumb=_tier_list(sz.get("thumb"), "thumb", src, DEFAULT_TIERS_THUMB),
        view=_tier_list(sz.get("view"), "view", src, DEFAULT_TIERS_VIEW),
    )

    en = raw.get("encode") or {}
    quality_raw = en.get("quality") or {}
    quality: dict[int, int] = {}
    for k, v in quality_raw.items():
        # TOML inline-table keys are strings; the design writes them as bare
        # integers (quality = { 256 = 52 }), which tomllib also hands back as
        # strings. Accept both.
        try:
            quality[int(k)] = int(v)
        except (TypeError, ValueError):
            raise ConfigError(f"{src}: [encode] quality has a non-integer entry {k!r} = {v!r}")
    speed = en.get("speed", 6)
    if not isinstance(speed, int) or isinstance(speed, bool) or not 0 <= speed <= 10:
        raise ConfigError(f"{src}: [encode] speed must be an integer 0-10, got {speed!r}")
    encode = Encode(
        format=_enum(en.get("format"), ("avif", "webp", "jpeg"), "[encode] format", src, "avif"),
        fallback=_enum(en.get("fallback"), ("auto", "none"), "[encode] fallback", src, "auto"),
        speed=speed,
        subsampling=str(en.get("subsampling", "4:2:0")),
        recipe_version=int(en.get("recipe_version", 1)),
        quality=quality or dict(DEFAULT_QUALITY),
        quality_default=int(en.get("quality_default", 46)),
    )

    u = raw.get("ui") or {}
    landing = u.get("landing_image")
    ui = Ui(
        site_title=str(u.get("site_title", "Photo Album")),
        heading=str(u.get("heading", "Photo Album")),
        tagline=str(u.get("tagline", Ui.tagline)),
        landing_image=Path(landing).expanduser() if landing else None,
        show_gps=bool(u.get("show_gps", True)),
        map_link=_enum(u.get("map_link"), ("osm", "google", "none"), "[ui] map_link", src, "osm"),
        album_page_size=int(u.get("album_page_size", 5000)),
        dirsort=_enum(
            u.get("dirsort"), ("name", "-name", "date", "-date"), "[ui] dirsort", src, "name"
        ),
        dir_card_aspect=str(u.get("dir_card_aspect", "4/3")),
    )

    s = raw.get("scan") or {}
    exclude = s.get("exclude")
    if exclude is not None and (
        not isinstance(exclude, list) or not all(isinstance(x, str) for x in exclude)
    ):
        raise ConfigError(f"{src}: [scan] exclude must be a list of strings")
    scan = Scan(
        jobs=int(s.get("jobs", 0)),
        nice=int(s.get("nice", 10)),
        exclude=tuple(exclude) if exclude is not None else Scan.exclude,
    )

    groups_raw = raw.get("groups") or {}
    groups: dict[str, tuple[str, ...]] = {}
    for name, members in groups_raw.items():
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            raise ConfigError(f"{src}: [groups] {name} must be a list of strings")
        groups[name] = tuple(members)

    g = raw.get("google") or {}
    secret = g.get("client_secret", "")
    secret_file = g.get("client_secret_file")
    if secret_file:
        try:
            secret = Path(secret_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ConfigError(f"{src}: [google] cannot read client_secret_file: {e}") from e
    google = Google(
        enabled=bool(g.get("enabled", False)),
        client_id=str(g.get("client_id", "")),
        client_secret=str(secret),
    )
    if google.enabled and not (google.client_id and google.client_secret):
        raise ConfigError(
            f"{src}: [google] enabled = true but client_id/client_secret are not both set"
        )

    log_file = raw.get("log_file")
    return Config(
        photo_root=photo_root,
        derived_root=derived_root,
        index_db=index_db,
        users_file=users_file,
        secret_key_file=secret_key_file,
        base_url=str(raw.get("base_url", "http://127.0.0.1:5000")).rstrip("/"),
        behind_proxy=bool(raw.get("behind_proxy", False)),
        session_days=_positive_int(raw.get("session_days"), "session_days", src, 30),
        sendfile_header=_enum(
            raw.get("sendfile_header"),
            ("auto", "X-Sendfile", "none"),
            "sendfile_header",
            src,
            "auto",
        ),
        log_file=Path(log_file).expanduser() if log_file else None,
        sizes=sizes,
        encode=encode,
        ui=ui,
        scan=scan,
        groups=groups,
        google=google,
        source=src,
    )
