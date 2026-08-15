"""Acceptance matrix for server-side upload classification.

Covers: uploader cannot write into a non-administered matter; cannot make a
sensitive file broadly accessible; level-5 non-admin denied.
"""
from __future__ import annotations

import pytest

from authz.models import AccessDenied, MatterGrant, UserProfile
from authz.policy import classify_upload

ADMIN_GRANT = MatterGrant(matter_id="m-1", access_level=4, can_administer=True)
READ_ONLY_GRANT = MatterGrant(matter_id="m-1", access_level=4, can_administer=False)


def _profile(**overrides) -> UserProfile:
    defaults = dict(user_id="u1", tenant_id="t1", role="associate", access_level=1, firm_wide=False, admin=False)
    defaults.update(overrides)
    return UserProfile(**defaults)


def test_uploader_cannot_write_into_non_administered_matter():
    profile = _profile(role="senior_associate", access_level=3, firm_wide=False)
    # The user holds a READ grant on m-2 but no administer grant anywhere.
    grants = [MatterGrant(matter_id="m-2", access_level=3, can_administer=False)]
    with pytest.raises(AccessDenied):
        classify_upload(profile, grants, "ordinary text", requested_access_level=3, requested_matter_id="m-2")


def test_uploader_cannot_write_into_unrelated_matter_even_with_admin_grant_elsewhere():
    profile = _profile(role="senior_associate", access_level=3, firm_wide=False)
    grants = [ADMIN_GRANT, MatterGrant(matter_id="m-9", access_level=3, can_administer=False)]
    # The authoritative auth_can_administer_matter_ref RPC returns False for m-9
    # (the caller administers m-1 only). The server passes that verdict down, so
    # ingest into the unrelated matter is denied regardless of grants elsewhere.
    with pytest.raises(AccessDenied):
        classify_upload(
            profile, grants, "text",
            requested_access_level=3, requested_matter_id="m-9", administered=False,
        )


def test_administered_matter_with_grant_is_allowed():
    profile = _profile(role="senior_associate", access_level=3, firm_wide=False)
    grants = [ADMIN_GRANT]
    decision = classify_upload(
        profile, grants, "text",
        requested_access_level=3, requested_matter_id="m-1", administered=True,
    )
    assert decision.access_level == 1
    assert decision.matter_id == "m-1"


def test_uploader_cannot_make_sensitive_file_broadly_accessible():
    profile = _profile(role="partner", access_level=4, firm_wide=False)
    grants = [ADMIN_GRANT]
    # Client tries to stamp a privileged document at level 1 (broadly readable).
    decision = classify_upload(
        profile, grants, "This document is privileged and confidential.",
        requested_access_level=1, requested_matter_id="m-1",
    )
    # Floor = 5 (privileged), ceiling = 4 -> cannot go below 4.
    assert decision.access_level == 4
    assert decision.access_level >= decision.sensitivity_floor or decision.sensitivity_floor > decision.ceiling


def test_level5_non_admin_cannot_ingest_into_firm_pool():
    profile = _profile(role="associate", access_level=5, firm_wide=False, admin=False)
    # access_level 5 but NOT firm-wide and not admin: no firm-pool authority.
    with pytest.raises(AccessDenied):
        classify_upload(profile, [], "ordinary text")


def test_level5_non_admin_matter_scoped_still_needs_admin_grant():
    profile = _profile(role="associate", access_level=5, firm_wide=False, admin=False)
    grants = [MatterGrant(matter_id="m-1", access_level=5, can_administer=False)]
    with pytest.raises(AccessDenied):
        classify_upload(profile, grants, "text", requested_access_level=5, requested_matter_id="m-1")


def test_firm_wide_partner_can_ingest_into_specific_matter():
    profile = _profile(role="managing_partner", access_level=5, firm_wide=True, admin=True)
    decision = classify_upload(profile, [], "text", requested_access_level=5, requested_matter_id="m-3")
    assert decision.access_level == 1  # floor default 1, ceiling 5
    assert decision.matter_id == "m-3"
    assert decision.firm_wide is False