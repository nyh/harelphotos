"""`.album.toml` parsing (DESIGN.md 5.3).

The recurring theme: a mistake in one file degrades that directory to defaults
and is reported, never raised.
"""

from harelphotos import album


def test_absent_file_gives_defaults(tmp_path):
    cfg = album.load(tmp_path)
    assert cfg.ok
    assert cfg.title is None
    assert cfg.cover == "auto"
    assert cfg.allow is None
    assert not cfg.hidden


def test_full_file(tmp_path):
    (tmp_path / album.ALBUM_FILE).write_text(
        """
        title       = "Summer in Greece"
        description = "Two weeks on Naxos."
        cover       = "IMG_1234.jpg"
        sort        = "-date"
        dirsort     = "-name"
        order       = ["passover", "summer"]
        sort_key    = "1975"
        group_by    = "day"
        hidden      = true
        allow       = ["dad@gmail.com", "@family"]
        allow_replace = true
        location    = "Naxos, Greece"

        [photos."IMG_1234.jpg"]
        title  = "Nadav on the beach"
        hidden = true
        """,
        encoding="utf-8",
    )
    cfg = album.load(tmp_path)
    assert cfg.ok, cfg.errors
    assert cfg.title == "Summer in Greece"
    assert cfg.cover == "IMG_1234.jpg"
    assert (cfg.sort, cfg.reverse_sort) == ("date", True)
    assert (cfg.dirsort, cfg.reverse_dirsort) == ("name", True)
    assert cfg.order == ("passover", "summer")
    assert cfg.sort_key == "1975"
    assert cfg.group_by == "day"
    assert cfg.hidden
    assert cfg.allow == ("dad@gmail.com", "@family")
    assert cfg.allow_replace
    assert cfg.location == "Naxos, Greece"
    assert cfg.photos["IMG_1234.jpg"].title == "Nadav on the beach"
    assert cfg.photos["IMG_1234.jpg"].hidden


def test_malformed_toml_is_reported_not_raised(tmp_path):
    (tmp_path / album.ALBUM_FILE).write_text('title = "unclosed', encoding="utf-8")
    cfg = album.load(tmp_path)
    assert not cfg.ok
    assert "invalid TOML" in cfg.error_text
    # ...and everything falls back to defaults, so the directory still works.
    assert cfg.title is None
    assert cfg.cover == "auto"


def test_wrong_types_are_reported_per_key(tmp_path):
    (tmp_path / album.ALBUM_FILE).write_text(
        """
        title  = 42
        hidden = "yes"
        allow  = 5
        """,
        encoding="utf-8",
    )
    cfg = album.load(tmp_path)
    assert not cfg.ok
    assert len(cfg.errors) == 3
    assert cfg.title is None
    assert cfg.hidden is False
    assert cfg.allow is None


def test_bad_enum_values_fall_back(tmp_path):
    (tmp_path / album.ALBUM_FILE).write_text(
        """
        sort     = "sideways"
        dirsort  = "upwards"
        group_by = "fortnight"
        cover    = "auto:telepathy"
        """,
        encoding="utf-8",
    )
    cfg = album.load(tmp_path)
    assert not cfg.ok
    assert len(cfg.errors) == 4
    assert cfg.sort is None
    assert cfg.dirsort is None
    assert cfg.group_by == "none"
    assert cfg.cover == "auto"


def test_single_string_accepted_where_a_list_belongs():
    # A common slip and unambiguous, so accepting it beats dropping the key.
    cfg = album.parse('allow = "nyh"')
    assert cfg.ok
    assert cfg.allow == ("nyh",)


def test_allow_empty_list_is_meaningful_not_an_error():
    # An explicit empty allow-list means "nobody", which is a legitimate thing
    # to write and must not be confused with an absent key ("no restriction").
    cfg = album.parse("allow = []")
    assert cfg.ok
    assert cfg.allow == ()


def test_auto_cover_variants():
    for value in ("auto", "auto:first", "auto:middle", "auto:hash"):
        cfg = album.parse(f'cover = "{value}"')
        assert cfg.ok, cfg.errors
        assert cfg.cover == value


def test_cover_may_be_a_path_into_a_subdirectory():
    cfg = album.parse('cover = "kids/IMG_9.jpg"')
    assert cfg.ok
    assert cfg.cover == "kids/IMG_9.jpg"


def test_photos_table_must_be_tables():
    cfg = album.parse('photos = "nope"')
    assert not cfg.ok
    assert cfg.photos == {}


def test_bad_per_photo_key_is_reported_with_the_photo_name():
    cfg = album.parse('[photos."a.jpg"]\ntitle = 5\n')
    assert not cfg.ok
    assert "a.jpg" in cfg.error_text


def test_non_utf8_file_is_reported(tmp_path):
    (tmp_path / album.ALBUM_FILE).write_bytes(b'title = "\xff\xfe not utf8"')
    cfg = album.load(tmp_path)
    assert not cfg.ok
    assert "UTF-8" in cfg.error_text


def test_unreadable_file_is_reported(tmp_path):
    # A directory where a file is expected is the easiest portable way to make
    # read_text fail with OSError rather than FileNotFoundError.
    (tmp_path / album.ALBUM_FILE).mkdir()
    cfg = album.load(tmp_path)
    assert not cfg.ok
    assert "cannot read" in cfg.error_text
