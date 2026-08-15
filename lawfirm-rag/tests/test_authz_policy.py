"""Unit tests for authz/policy.py — pure authorization logic, no DB."""
from __future__ import annotations

import pytest

from authz.models import AccessDenied, MatterGrant, UserProfile
from authz.policy import (
    can_administer_any_matter,
    classify_upload,
    effective_read_ceiling,
    is_admin,
    requires_admin,
    sensitivity_floor,
)


def _profile(**overrides) -> UserProfile:
    defaults = dict(user_id="u1", tenant_id="t1", role="associate", access_level=1, firm_wide=False, admin=False)
    defaults.update(overrides)
    return UserProfile(**defaults)


def _grant(matter_id="m-1", access_level=1, can_administer=False) -> MatterGrant:
    return MatterGrant(matter_id=matter_id, access_level=access_level, can_administer=can_administer)


# ---------------------------------------------------------------- sensitivity


def test_sensitivity_floor_defaults_to_one():
    assert sensitivity_floor("") == 1
    assert sensitivity_floor("plain text about a contract") == 1


def test_sensitivity_floor_levels():
    assert sensitivity_floor("This document is confidential.") == 3
    assert sensitivity_floor("Attorney work product — do not distribute.") == 4
    assert sensitivity_floor("Privileged and confidential. Without prejudice.") == 5


def test_sensitivity_floor_is_conservative_highest_wins():
    assert sensitivity_floor("Confidential internal memo.") == 3
    assert sensitivity_floor("Highly confidential and legally privileged.") == 5


# ---------------------------------------------------------------- admin


def test_admin_requires_explicit_flag():
    assert is_admin(_profile(admin=True)) is True
    assert is_admin(_profile(admin=False)) is False
    # an access level is never administration
    assert is_admin(_profile(access_level=5, admin=False)) is False


def test_requires_admin_raises_for_non_admin():
    requires_admin(_profile(admin=True))
    with pytest.raises(AccessDenied):
        requires_admin(_profile(admin=False, access_level=5))


def test_can_administer_any_matter():
    assert can_administer_any_matter([_grant(can_administer=True)], _profile()) is True
    assert can_administer_any_matter([_grant(can_administer=False)], _profile()) is False
    assert can_administer_any_matter([], _profile(firm_wide=True)) is True


# ---------------------------------------------------------------- read ceiling


def test_effective_read_ceiling_scope():
    profile = _profile(access_level=4, firm_wide=False)
    grants = [_grant("m-1", access_level=3, can_administer=False)]
    assert effective_read_ceiling(profile, grants, "m-1") == 3
    # no matching matter grant -> caller's profile ceiling governs (unbounded scope)
    assert effective_read_ceiling(profile, grants, "other") == 4
    # firm-wide users are not capped by grants
    assert effective_read_ceiling(_profile(access_level=5, firm_wide=True), [], "m-1") == 5


# ---------------------------------------------------------------- classify_upload


def test_classify_upload_firm_wide_admin():
    profile = _profile(role="managing_partner", access_level=5, firm_wide=True, admin=True)
    decision = classify_upload(profile, [], "ordinary text", requested_access_level=5)
    assert decision.access_level == 1  # floor default 1
    assert decision.firm_wide is True
    assert decision.sensitivity_floor == 1
    assert decision.ceiling == 5


def test_classify_upload_floor_never_below_content_markers():
    profile = _profile(role="partner", access_level=4, firm_wide=False, admin=False)
    grants = [_grant("m-1", access_level=4, can_administer=True)]
    decision = classify_upload(
        profile, grants, "This memo is confidential and legally privileged.",
        requested_access_level=1, requested_matter_id="m-1",
    )
    # content says 5, caller ceiling is 4 -> capped at 4 (cannot exceed authority)
    assert decision.access_level == 4
    assert decision.sensitivity_floor == 5
    assert decision.ceiling == 4


def test_classify_upload_cannot_lower_sensitive_file():
    profile = _profile(role="partner", access_level=4, firm_wide=False, admin=False)
    grants = [_grant("m-1", access_level=4, can_administer=True)]
    # requested level 1 but content is confidential -> floor raises to 3
    decision = classify_upload(
        profile, grants, "This document contains confidential client information.",
        requested_access_level=1, requested_matter_id="m-1",
    )
    assert decision.access_level == 3
    assert decision.requested_access_level == 1  # recorded as a hint only


def test_classify_upload_matter_scoped_requires_admin_grant():
    profile = _profile(role="senior_associate", access_level=3, firm_wide=False)
    # no can_administer grant for any matter -> denied
    with pytest.raises(AccessDenied):
        classify_upload(profile, [_grant("m-1", access_level=3, can_administer=False)], "text", requested_matter_id="m-1")


def test_classify_upload_firm_pool_requires_firm_wide():
    profile = _profile(role="associate", access_level=2, firm_wide=False)
    with pytest.raises(AccessDenied):
        classify_upload(profile, [], "text")


def test_classify_upload_requested_level_is_never_an_authority_fact():
    profile = _profile(role="partner", access_level=4, firm_wide=False)
    grants = [_grant("m-1", access_level=4, can_administer=True)]
    # client asks for level 5; the requested level is only recorded as a hint.
    # plain text has floor 1, so the effective level is 1 — never inflated by
    # the request, never above the caller's ceiling (4).
    decision = classify_upload(profile, grants, "text", requested_access_level=5, requested_matter_id="m-1")
    assert decision.access_level == 1
    assert decision.ceiling == 4
    assert decision.requested_access_level == 5