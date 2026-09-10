"""ACL model tests (DESIGN.md 6).

The accumulate rule and its escape hatch are the security core of the project,
so these are deliberately thorough.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from harelphotos import acl

GROUPS = {
    "family": ("nyh", "dad@gmail.com", "sis"),
    "cousins": ("@family", "cousin1@gmail.com"),
    "loop_a": ("@loop_b", "alice"),
    "loop_b": ("@loop_a", "bob"),
}


def chain_for(*levels):
    """Build a chain by walking down a tree of (allow, allow_replace) pairs."""
    chain: acl.Chain = ()
    for allow, replace in levels:
        chain = acl.extend_chain(chain, allow, replace)
    return chain


def test_no_restriction_anywhere_admits_everyone():
    chain = chain_for((None, False), (None, False))
    assert chain == ()
    assert acl.can_view(chain, "anyone", GROUPS)


def test_restriction_admits_only_listed_users():
    chain = chain_for((None, False), (["nyh"], False))
    assert acl.can_view(chain, "nyh", GROUPS)
    assert not acl.can_view(chain, "sis", GROUPS)


def test_restrictions_accumulate_down_the_tree():
    # /private allows nyh+sis; /private/inner allows sis+dad.
    # Only sis is in BOTH, so only sis may see the inner directory.
    chain = chain_for((None, False), (["nyh", "sis"], False), (["sis", "dad@gmail.com"], False))
    assert acl.can_view(chain, "sis", GROUPS)
    assert not acl.can_view(chain, "nyh", GROUPS)
    assert not acl.can_view(chain, "dad@gmail.com", GROUPS)


def test_a_subdirectory_cannot_widen_access_by_accident():
    # This is the property the accumulate rule exists for: listing everyone in
    # a child must NOT re-open a private parent.
    chain = chain_for((["nyh"], False), (["nyh", "sis", "dad@gmail.com"], False))
    assert acl.can_view(chain, "nyh", GROUPS)
    assert not acl.can_view(chain, "sis", GROUPS)


def test_allow_replace_is_the_deliberate_escape_hatch():
    chain = chain_for((["nyh"], False), (["cousin1@gmail.com"], True))
    assert acl.can_view(chain, "cousin1@gmail.com", GROUPS)
    assert not acl.can_view(chain, "nyh", GROUPS)


def test_allow_replace_with_no_allow_clears_the_restriction():
    chain = chain_for((["nyh"], False), (None, True))
    assert chain == ()
    assert acl.can_view(chain, "anyone", GROUPS)


def test_groups_expand():
    chain = chain_for((["@family"], False))
    assert acl.can_view(chain, "dad@gmail.com", GROUPS)
    assert not acl.can_view(chain, "stranger", GROUPS)


def test_groups_nest():
    chain = chain_for((["@cousins"], False))
    assert acl.can_view(chain, "cousin1@gmail.com", GROUPS)
    assert acl.can_view(chain, "sis", GROUPS)          # via @family inside @cousins
    assert not acl.can_view(chain, "stranger", GROUPS)


def test_group_cycle_terminates():
    # A cycle in the config must not hang every request.
    chain = chain_for((["@loop_a"], False))
    assert acl.can_view(chain, "alice", GROUPS)
    assert acl.can_view(chain, "bob", GROUPS)
    assert not acl.can_view(chain, "stranger", GROUPS)


def test_unknown_group_matches_nobody():
    chain = chain_for((["@no_such_group"], False))
    assert not acl.can_view(chain, "nyh", GROUPS)


def test_admin_bypasses_everything():
    chain = chain_for((["nobody"], False), (["also-nobody"], False))
    assert acl.can_view(chain, "nyh", GROUPS, is_admin=True)


def test_anonymous_never_passes_a_restriction():
    assert not acl.can_view(chain_for((["nyh"], False)), None, GROUPS)
    # ...but an unrestricted directory is not itself a login gate; that lives
    # in the before_request hook (DESIGN.md 12.3).
    assert acl.can_view((), None, GROUPS)


def test_empty_allow_list_matches_nobody():
    chain = chain_for(([], False))
    assert chain == ((),)
    assert not acl.can_view(chain, "nyh", GROUPS)


def test_chain_round_trips_through_json():
    chain = chain_for((["nyh"], False), (["@family", "x"], False))
    assert acl.loads(acl.dumps(chain)) == chain


def test_empty_chain_round_trips():
    assert acl.loads(acl.dumps(())) == ()
    assert acl.loads(None) == ()
    assert acl.loads("") == ()


def test_corrupt_chain_fails_closed():
    # If we cannot tell what the restriction was, the safe reading is "nobody".
    for bad in ("not json", "{}", "[1,2]", '[["ok"], 5]', '"x"'):
        chain = acl.loads(bad)
        assert not acl.can_view(chain, "nyh", GROUPS), bad
        # ...but an admin still gets in, so a corrupt row is recoverable.
        assert acl.can_view(chain, "nyh", GROUPS, is_admin=True), bad
