"""Offline regression for the VPC name-prefix ownership matcher (IB-051).

Wave E sweep skipped ``regrw5vpc6a2d542e`` as "name-mismatch": the VPC sweep
used the narrow ``("regrvpc","zznetvpc")`` prefix list, so a VPC whose name does
not put "vpc" right after "regr" (the wave-5 privatelink/firewall chains name
theirs ``regrw5vpc{unique}``) was never recognised as owned → never reclaimed →
held the 5-VPC account cap → provision-failure cascade.

These tests lock in the broadened ``("regr","zznet")`` family roots: every
``regr*``/``zznet*`` VPC name is owned + deletable, while SCP account built-ins
(name-mismatch, never created by our runs) stay unowned.
"""
from core.registry import is_owned
from cleanup.reconciler import _VPC_NAME_PREFIXES, _is_deletable, _is_candidate


# names a real run stamps on its VPCs (regr/zznet families, tag-less = legacy /
# untagged-by-API-quirk path → identified by name prefix)
OWNED_VPC_NAMES = [
    "regrvpc1234abcd",      # the canonical light networking shape
    "regrvpcb5678ef01",     # peering's 2nd VPC (regrvpcb*)
    "regrw5vpc6a2d542e",    # THE leak — wave-5 privatelink/firewall chain
    "regrw5vpcdeadbeef",
    "zznetvpc0011aabb",     # the zznet family
]

# SCP account BUILT-INS surfaced by the Wave E sweep as name-mismatch skips.
# They are NOT VPCs and never created by us — must stay unowned.
NOT_OWNED_NAMES = [
    "BillingplanFullAccess",
    "Cloud Functions",
    "File Storage",
    "SystemReadOnlyAccess",
]


def test_owned_vpc_names_match():
    for name in OWNED_VPC_NAMES:
        item = {"name": name, "id": f"vpc-{name}"}
        assert is_owned(item, name_prefixes=_VPC_NAME_PREFIXES), name
        # tag-less + prefix match ⇒ legacy orphan ⇒ deletable in a sweep
        assert _is_deletable(item, name_prefixes=_VPC_NAME_PREFIXES), name
        assert _is_candidate(item, name_prefixes=_VPC_NAME_PREFIXES), name


def test_the_specific_leak_is_reclaimable():
    """Exact Wave E artifact: regrw5vpc6a2d542e."""
    leak = {"name": "regrw5vpc6a2d542e", "id": "vpc-leak"}
    assert is_owned(leak, name_prefixes=_VPC_NAME_PREFIXES)
    assert _is_deletable(leak, name_prefixes=_VPC_NAME_PREFIXES)


def test_account_builtins_stay_unowned():
    for name in NOT_OWNED_NAMES:
        item = {"name": name, "id": f"x-{name}"}
        assert not is_owned(item, name_prefixes=_VPC_NAME_PREFIXES), name
        assert not _is_deletable(item, name_prefixes=_VPC_NAME_PREFIXES), name


def test_owner_tag_still_primary_signal():
    """A tagged VPC is owned regardless of name; an unexpired tagged VPC is a
    live concurrent-run resource and must NOT be swept by prefix logic."""
    tagged = {
        "name": "someone-elses-vpc",
        "id": "vpc-tagged",
        "tags": [{"key": "owner", "value": "apitest"}],
    }
    assert is_owned(tagged, name_prefixes=_VPC_NAME_PREFIXES)
