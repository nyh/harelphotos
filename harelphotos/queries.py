"""Reading the index for the web application (DESIGN.md 10, 11).

Every lookup here takes a *path string from a request* and resolves it by exact
match against the database. Filesystem paths are only ever built from values
that came back out of SQLite, which were produced by ``os.scandir`` — so
``..``, symlink games and NUL bytes cannot reach the filesystem at all
(DESIGN.md 10.2).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import quote

from . import acl, overrides
from .config import Config
from .util import prettify_name


def url_path(relpath: str) -> str:
    """Percent-encode a path for use in a URL, keeping the separators.

    Necessary rather than cosmetic. A filename containing a space breaks
    `srcset` outright, because whitespace is what separates a candidate URL
    from its width descriptor there — so one photo called "zPic 4.jpg"
    silently rendered as a grey box. `#`, `?` and `%` would each break
    something too.
    """
    return quote(relpath, safe="/")


@dataclass(frozen=True)
class Viewer:
    """Who is asking. M5 populates this from the session."""

    token: str | None = None
    name: str = ""
    is_admin: bool = False


@dataclass
class Album:
    id: int
    path: str
    name: str
    title: str
    description: str | None
    location: str | None
    n_photos: int
    n_photos_rec: int
    n_subdirs: int
    date_min: int | None
    date_max: int | None
    cover: "Photo | None" = None
    group_by: str = "none"

    @property
    def url(self) -> str:
        return f"/a/{url_path(self.path)}/" if self.path else "/a/"


@dataclass
class Photo:
    id: int
    dir_path: str
    name: str
    title: str | None
    width: int | None
    height: int | None
    taken: int | None
    mtime_ns: int
    colour: str
    place: str | None
    place_dist: int | None
    tiers: list[int]
    deriv_key: str | None
    size: int
    exif: dict

    @property
    def relpath(self) -> str:
        return f"{self.dir_path}/{self.name}" if self.dir_path else self.name

    @property
    def url_relpath(self) -> str:
        """The relative path, percent-encoded for a URL."""
        return url_path(self.relpath)

    @property
    def page_url(self) -> str:
        return f"/p/{self.url_relpath}"

    @property
    def pending(self) -> bool:
        """True when this photo's images have not been generated yet.

        Distinct from having no derivatives *legitimately*, which happens when
        a photo is smaller than every configured size: there, `deriv_key` is
        set and the original is genuinely small, so serving it is right. Here
        the scan simply has not reached it, and the original may be 29 MB —
        putting that in a grid of thumbnails would be catastrophic.
        """
        return self.deriv_key is None

    @property
    def aspect(self) -> float:
        if self.width and self.height:
            return self.width / self.height
        return 1.0

    @property
    def when(self) -> int:
        """The timestamp this photo sorts and displays by."""
        return self.taken if self.taken is not None else self.mtime_ns // 1_000_000_000

    def tier_for(self, want: int) -> int | None:
        """The smallest generated tier at least `want` px, else the largest."""
        if not self.tiers:
            return None
        big = [t for t in self.tiers if t >= want]
        return min(big) if big else max(self.tiers)

    def size_at(self, tier: int) -> tuple[int, int]:
        """Pixel dimensions of one tier, for the srcset `w` descriptor.

        Must be the real per-photo width: a portrait photo in the 512 tier is
        384 wide, and advertising 512 would make the browser's DPR arithmetic
        wrong for half the collection (DESIGN.md 10.3).
        """
        w, h = self.width or tier, self.height or tier
        longest = max(w, h)
        if longest <= tier:
            return w, h
        scale = tier / longest
        return max(1, round(w * scale)), max(1, round(h * scale))


def _photo_from_row(r: sqlite3.Row) -> Photo:
    try:
        tiers = sorted(json.loads(r["deriv_tiers"] or "[]"))
    except ValueError:
        tiers = []
    try:
        exif = json.loads(r["exif_json"] or "{}")
    except ValueError:
        exif = {}
    return Photo(
        id=r["id"],
        dir_path=r["dir_path"],
        name=r["name"],
        title=r["title"],
        width=r["width"],
        height=r["height"],
        taken=r["taken"],
        mtime_ns=r["mtime_ns"],
        colour=r["color"] or "#888888",
        place=r["place"],
        place_dist=r["place_dist"],
        tiers=tiers,
        deriv_key=r["deriv_key"],
        size=r["size"],
        exif=exif,
    )


def _album_from_row(r: sqlite3.Row) -> Album:
    return Album(
        id=r["id"],
        path=r["path"],
        name=r["name"],
        title=r["title"] or (prettify_name(r["name"]) if r["name"] else "Home"),
        description=r["description"],
        location=r["location"],
        n_photos=r["n_photos"],
        n_photos_rec=r["n_photos_rec"],
        n_subdirs=r["n_subdirs"],
        date_min=r["date_min"],
        date_max=r["date_max"],
        group_by=r["group_by"] or "none",
    )


PHOTO_COLUMNS = (
    "p.id, p.name, p.title, p.width, p.height, p.taken, p.mtime_ns, p.color, "
    "p.place, p.place_dist, p.deriv_tiers, p.deriv_key, p.size, p.exif_json, "
    "d.path AS dir_path"
)


class Index:
    """Read-only access to the index, with access control applied."""

    def __init__(self, conn: sqlite3.Connection, cfg: Config) -> None:
        self.conn = conn
        self.cfg = cfg

    # ------------------------------------------------------------------ ACL

    def _may_view(self, chain_json: str, viewer: Viewer) -> bool:
        return acl.can_view(
            acl.loads(chain_json), viewer.token, self.cfg.groups, viewer.is_admin
        )

    # --------------------------------------------------------------- albums

    def album(self, path: str, viewer: Viewer) -> Album | None:
        """Look up one album. Returns None if absent *or* not permitted.

        Deliberately conflated: the caller turns None into a 404, so a private
        album is indistinguishable from one that does not exist (DESIGN.md 6).
        """
        r = self.conn.execute("SELECT * FROM dirs WHERE path = ?", (path,)).fetchone()
        if r is None or not self._may_view(r["acl_chain"], viewer):
            return None
        # Hidden means gone, not merely unlisted. It used to leave the album
        # reachable by typing its URL, which makes a setting called "hidden" a
        # trap. The flag is propagated down the tree at scan time, so this
        # covers everything beneath a hidden directory too.
        if r["hidden"]:
            return None
        return _album_from_row(r)

    def subalbums(self, album: Album, viewer: Viewer) -> list[Album]:
        rows = self.conn.execute(
            "SELECT * FROM dirs WHERE parent_id = ? AND hidden = 0", (album.id,)
        ).fetchall()
        out = []
        for r in rows:
            if not self._may_view(r["acl_chain"], viewer):
                continue
            # A directory with no photographs anywhere beneath it is not an
            # album — it is a directory of videos, scripts or scratch files
            # that happens to live in the photo tree.
            if r["n_photos_rec"] == 0:
                continue
            out.append(_album_from_row(r))

        order = self._explicit_order(album)
        dirsort = album_dirsort(self.conn, album, self.cfg)
        out = sort_albums(out, order, dirsort, self._natkeys(album))
        for a in out:
            a.cover = self.cover_photo(a, viewer)
        return out

    def _explicit_order(self, album: Album) -> list[str]:
        r = self.conn.execute(
            "SELECT order_json FROM dirs WHERE id = ?", (album.id,)
        ).fetchone()
        try:
            return json.loads(r["order_json"] or "[]")
        except ValueError:
            return []

    def _natkeys(self, album: Album) -> dict[str, str]:
        return {
            r["name"]: (r["natkey"] or r["name"])
            for r in self.conn.execute(
                "SELECT name, natkey FROM dirs WHERE parent_id = ?", (album.id,)
            )
        }

    def cover_photo(self, album: Album, viewer: Viewer | None = None) -> Photo | None:
        """The album's cover: a pick made in the interface, else what the scan
        resolved -- and never a photo this viewer may not see.

        Resolved at request time rather than baked into the index at scan time,
        so choosing a cover takes effect on the next page rather than the next
        scan. Having the web process write `dirs.cover_photo` instead would
        mean opening the index for writing; it is read-only here on purpose,
        which is what stopped a running scan from breaking logins.
        """
        # No viewer means a command-line caller, which has already decided it
        # is entitled to look; the web always passes one.
        if viewer is None:
            viewer = Viewer(token=None, name="", is_admin=True)

        picked = overrides.get(self.cfg, album.path).cover
        if picked and not picked.startswith("auto"):
            # A bare name means a photo of this album; a path reaches into a
            # descendant, which is the only way to give a cover to a directory
            # that holds nothing but subdirectories.
            rel = f"{album.path}/{picked}" if album.path else picked
            found = self.photo(rel, viewer)
            if found is not None:
                return found
            # A pick naming a photo that is gone, or that this viewer may not
            # see, falls through rather than leaving the album blank.

        r = self.conn.execute(
            f"SELECT {PHOTO_COLUMNS}, d.acl_chain, d.hidden AS dir_hidden FROM dirs a "
            f"JOIN photos p ON p.id = a.cover_photo "
            f"JOIN dirs d ON d.id = p.dir_id WHERE a.id = ?",
            (album.id,),
        ).fetchone()
        if r is not None and not r["dir_hidden"] and self._may_view(r["acl_chain"], viewer):
            return _photo_from_row(r)

        # The scanned cover is one this viewer may not see. It was still being
        # put in the page: the image itself came back 404, but the file name
        # and the existence of a restricted album leaked into the HTML and the
        # card rendered blank. Find one they may see instead.
        return self._first_visible_photo(album, viewer)

    # How far to look for a cover the viewer may see. An album whose first
    # hundred photos are all restricted gets no card image, which is a great
    # deal better than reading the whole tree on every page.
    COVER_SEARCH_LIMIT = 100

    def _first_visible_photo(self, album: Album, viewer: Viewer | None) -> Photo | None:
        prefix = f"{album.path}/" if album.path else ""
        rows = self.conn.execute(
            f"SELECT {PHOTO_COLUMNS}, d.acl_chain FROM photos p "
            f"JOIN dirs d ON d.id = p.dir_id "
            f"WHERE (d.path = ? OR d.path LIKE ?) AND p.hidden = 0 AND d.hidden = 0 "
            # A photo whose image has been generated first -- one that has not
            # shows as a placeholder. Preferred rather than required: during a
            # long first scan almost nothing is derived yet, and an album with
            # a placeholder cover is better than one with no card image at all.
            f"ORDER BY (p.deriv_key IS NULL), d.path, p.name LIMIT ?",
            (album.path, f"{prefix}%", self.COVER_SEARCH_LIMIT),
        ).fetchall()
        for r in rows:
            if self._may_view(r["acl_chain"], viewer):
                return _photo_from_row(r)
        return None

    def breadcrumbs(self, album: Album, viewer: Viewer) -> list[Album]:
        """Ancestors from the root down to (not including) this album."""
        crumbs: list[Album] = []
        parts = album.path.split("/") if album.path else []
        for i in range(len(parts)):
            path = "/".join(parts[:i])
            a = self.album(path, viewer)
            if a is not None:
                crumbs.append(a)
        return crumbs

    # --------------------------------------------------------------- photos

    def photos(self, album: Album, viewer: Viewer) -> list[Photo]:
        rows = self.conn.execute(
            f"SELECT {PHOTO_COLUMNS} FROM photos p JOIN dirs d ON d.id = p.dir_id "
            f"WHERE p.dir_id = ? AND p.hidden = 0",
            (album.id,),
        ).fetchall()
        out = [_photo_from_row(r) for r in rows]
        return sort_photos(out, album_sort(self.conn, album))

    def photo(self, relpath: str, viewer: Viewer) -> Photo | None:
        dir_path, _, name = relpath.rpartition("/")
        r = self.conn.execute(
            f"SELECT {PHOTO_COLUMNS}, d.acl_chain FROM photos p "
            f"JOIN dirs d ON d.id = p.dir_id "
            f"WHERE d.path = ? AND p.name = ? AND p.hidden = 0 AND d.hidden = 0",
            (dir_path, name),
        ).fetchone()
        if r is None or not self._may_view(r["acl_chain"], viewer):
            return None
        return _photo_from_row(r)

    def neighbours(self, photo: Photo, viewer: Viewer) -> tuple[Photo | None, Photo | None]:
        """The previous and next photo in the album's own order."""
        album = self.album(photo.dir_path, viewer)
        if album is None:
            return None, None
        siblings = self.photos(album, viewer)
        for i, p in enumerate(siblings):
            if p.id == photo.id:
                return (siblings[i - 1] if i else None,
                        siblings[i + 1] if i + 1 < len(siblings) else None)
        return None, None


# ------------------------------------------------------------------ sorting

def album_sort(conn: sqlite3.Connection, album: Album) -> str:
    r = conn.execute("SELECT sort FROM dirs WHERE id = ?", (album.id,)).fetchone()
    return (r["sort"] if r and r["sort"] else "date")


def album_dirsort(conn: sqlite3.Connection, album: Album, cfg: Config) -> str:
    r = conn.execute("SELECT dirsort FROM dirs WHERE id = ?", (album.id,)).fetchone()
    return (r["dirsort"] if r and r["dirsort"] else cfg.ui.dirsort)


def sort_photos(photos: Sequence[Photo], spec: str) -> list[Photo]:
    reverse = spec.startswith("-")
    key = spec.lstrip("-")
    if key == "name":
        ordered = sorted(photos, key=lambda p: p.name.casefold())
    elif key == "mtime":
        ordered = sorted(photos, key=lambda p: (p.mtime_ns, p.name.casefold()))
    elif key == "exif":
        # Photos with no EXIF date sort last rather than being mixed in.
        ordered = sorted(
            photos, key=lambda p: (p.taken is None, p.taken or 0, p.name.casefold())
        )
    else:   # "date": EXIF when present, file date otherwise (DESIGN.md 5.3)
        ordered = sorted(photos, key=lambda p: (p.when, p.name.casefold()))
    return list(reversed(ordered)) if reverse else ordered


def sort_albums(
    albums: Sequence[Album], order: Sequence[str], spec: str, natkeys: dict[str, str]
) -> list[Album]:
    """Explicit `order` first, then everything else by `dirsort`."""
    pinned = {name: i for i, name in enumerate(order)}
    first = sorted((a for a in albums if a.name in pinned), key=lambda a: pinned[a.name])
    rest = [a for a in albums if a.name not in pinned]

    reverse = spec.startswith("-")
    key = spec.lstrip("-")
    if key == "date":
        rest.sort(key=lambda a: (a.date_max or 0, natkeys.get(a.name, a.name)))
    else:
        rest.sort(key=lambda a: (natkeys.get(a.name, a.name), a.name))
    if reverse:
        rest.reverse()
    return first + rest
