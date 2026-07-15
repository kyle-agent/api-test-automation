"""Offline regression for the tag-scoped cleanup reconciler convergence + leak
fixes (field-discovered 2026-06-22; the live sweep looped 8 rounds and leaked
billable resources). All hermetic — a FakeClient stands in for the SCP API
(``cleanup/verify_clean.py`` uses the same stub-the-client idea), so no network.

Covers the three bug classes the fix targets:

  Bug 1 — ``/v1/images`` is swept, and BEFORE ``/v1/volumes`` (a virtualserver
          custom image pins its source volume; image must be reaped first).
  Bug 2 — a 4xx DELETE is NOT counted as deleted, and the filestorage volume
          teardown pauses+deletes its replication from the REPLICA side using
          the ``?volume_id=`` query; an extra region (SCP_SWEEP_REGIONS) is also
          swept so the kr-east1 replica is reaped.
  Bug 3 — persistent-after-delete (stuck) detection: an owned id we deleted in a
          prior round that STILL lists is marked stuck and not retried, so the
          sweep converges instead of looping; a no-progress round ends the sweep.

Ownership is NEVER weakened here: every item these tests delete carries either an
owner tag or a ``regr*`` prefix; un-owned items must survive.
"""
from __future__ import annotations

import dataclasses
import re

import pytest

import cleanup.reconciler as recon


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Make every test fully offline + fast: no real waits, and _wait_gone is a
    no-op (the FakeClient's GET never 404s, so the real poll would block). Mirrors
    how cleanup/verify_clean.py neutralises waits for its read-only sweep."""
    monkeypatch.setattr(recon.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(recon, "_wait_gone", lambda *a, **k: True)
    monkeypatch.setattr(recon, "_wait_all_gone", lambda *a, **k: True)
    yield


# --------------------------------------------------------------------------- #
# A minimal fake SCP client that records every call and serves canned list data.
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class _FakeCfg:
    region: str = "kr-west1"


class _Resp:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else {}
        self.raw_text = ""

    @property
    def ok(self):
        return 200 <= self.status < 300


class FakeClient:
    """Serves GET lists from ``self.lists`` (keyed by path, ``?query`` stripped
    for matching but recorded verbatim), records DELETE/PUT, and lets a test
    delete an item from a list to model real teardown across rounds."""

    def __init__(self, lists=None, delete_status=None, region="kr-west1",
                 objects=None):
        # lists: {path_without_query: [items]}
        self.lists = lists or {}
        # objects: {path_without_query: raw_body_dict} — single-object GETs
        # (e.g. the LB static-nats show endpoint, which returns
        # {"static_nat": {...}} rather than a collection) take priority over
        # the items-list wrapping below.
        self.objects = objects or {}
        # delete_status: {path_prefix: status} — first match wins; default 204
        self.delete_status = delete_status or {}
        self.calls: list[tuple[str, str]] = []   # (METHOD, full_path)
        # cfg must be a real dataclass so dataclasses.replace(cfg, region=...)
        # works the way the reconciler does it against core.config.Settings.
        self.cfg = _FakeCfg(region=region)

    # -- helpers ------------------------------------------------------------
    def _key(self, path):
        return path.split("?", 1)[0]

    def remove_from_list(self, path, pred):
        key = self._key(path)
        self.lists[key] = [it for it in self.lists.get(key, []) if not pred(it)]

    # -- verbs --------------------------------------------------------------
    def get(self, path, service=None, **kw):
        self.calls.append(("GET", path))
        key = self._key(path)
        if key in self.objects:
            return _Resp(200, self.objects[key])
        return _Resp(200, {"items": list(self.lists.get(key, []))})

    def delete(self, path, service=None, json=None, **kw):
        self.calls.append(("DELETE", path))
        key = self._key(path)
        for pref, st in self.delete_status.items():
            if key.startswith(pref):
                return _Resp(st)
        return _Resp(204)

    def put(self, path, service=None, json=None, **kw):
        self.calls.append(("PUT", path))
        return _Resp(202)

    def post(self, path, service=None, json=None, **kw):
        self.calls.append(("POST", path))
        return _Resp(200)


def _owned(name, **extra):
    """A leaked-orphan item the sweep recognises as ours and DELETABLE.

    Modelled on how the live leaks actually appeared: tag-LESS resources whose
    name carries a ``regr*`` family prefix (platform-derived volumes/images, or
    legacy resources). ``_is_deletable`` treats a tag-less prefix match as a
    legacy orphan with no TTL -> always deletable. (A tagged-but-unexpired item
    would instead be a LIVE concurrent-run resource and correctly skipped — that
    path is covered by test_reconciler_vpc_prefix.py, not exercised here.)"""
    it = {"name": name, "id": f"id-{name}"}
    it.update(extra)
    return it


def _delete_paths(client):
    return [p for (m, p) in client.calls if m == "DELETE"]


def _ordered_index(paths, needle):
    for i, p in enumerate(paths):
        if needle in p:
            return i
    return -1


def setup_function(_):
    # Each test is an independent campaign — clear the module-level convergence
    # and stuck caches so state never leaks between tests.
    recon._reset_campaign_state()


# --------------------------------------------------------------------------- #
# Bug 1: /v1/images is swept, and ordered BEFORE /v1/volumes
# --------------------------------------------------------------------------- #
def test_images_pass_exists_and_precedes_volumes():
    client = FakeClient(lists={
        "/v1/images": [_owned("regrimg-custom1")],
        "/v1/volumes": [_owned("regrvol-src1")],
    })
    recon.run_sweep(client)
    paths = _delete_paths(client)
    img_i = _ordered_index(paths, "/v1/images/")
    vol_i = _ordered_index(paths, "/v1/volumes/")
    assert img_i >= 0, "an owned /v1/images delete must be issued (Bug 1)"
    assert vol_i >= 0, "the owned /v1/volumes delete must still be issued"
    assert img_i < vol_i, (
        "the custom-image pass must run BEFORE the volume pass (image pins "
        f"volume): images@{img_i} volumes@{vol_i}")


def test_images_pass_is_owned_only():
    """A platform base image (no regr name, no owner tag) must NOT be deleted."""
    client = FakeClient(lists={
        "/v1/images": [
            _owned("regrimg-custom1"),
            {"name": "Ubuntu 22.04 LTS", "id": "id-ubuntu"},  # platform base
        ],
    })
    recon.run_sweep(client)
    deleted = _delete_paths(client)
    assert any("regrimg-custom1" in p for p in deleted)
    assert not any("ubuntu" in p.lower() for p in deleted), \
        "platform base image must never be reaped (ownership intact)"


# --------------------------------------------------------------------------- #
# Bug 2a: a 4xx DELETE is NOT counted as deleted
# --------------------------------------------------------------------------- #
def test_4xx_delete_not_counted_as_deleted():
    recon._PROGRESS_THIS_ROUND[0] = 0
    it = _owned("regrfs-vol1")
    assert recon._note_progress(400, it) is False, "400 is not teardown"
    assert recon._note_progress(409, it) is False, "409 is not teardown"
    assert recon._note_progress(403, it) is False, "403 is not teardown"
    assert recon._PROGRESS_THIS_ROUND[0] == 0, "no genuine progress for any 4xx"
    # a clean 2xx / 404 IS progress
    assert recon._note_progress(204, it) is True
    assert recon._note_progress(404, it) is True
    assert recon._PROGRESS_THIS_ROUND[0] == 2


def test_4xx_is_2xx_or_gone_predicate():
    assert recon._is_2xx_or_gone(200)
    assert recon._is_2xx_or_gone(204)
    assert recon._is_2xx_or_gone(404)
    assert not recon._is_2xx_or_gone(400)
    assert not recon._is_2xx_or_gone(409)
    assert not recon._is_2xx_or_gone(500)
    assert not recon._is_2xx_or_gone(None)
    assert not recon._is_2xx_or_gone(0)


def test_filestorage_volume_400_not_counted():
    """A filestorage volume whose delete 400s (replication in use) must not be
    tallied as deleted, and the volume must still be listed (not reaped)."""
    client = FakeClient(
        lists={"/v1/volumes": [_owned("regrfs-src1")]},
        delete_status={"/v1/volumes": 400},
    )
    n = recon._sweep_filestorage_volumes(client)
    assert n == 0, "a 400 volume delete must not count as deleted (Bug 2a)"


# --------------------------------------------------------------------------- #
# Bug 2b: replication is paused + deleted from the REPLICA side (?volume_id=)
# --------------------------------------------------------------------------- #
def test_filestorage_replication_teardown_uses_replica_side_query():
    rep = {"replication_id": "rep-1", "replica_volume_id": "vol-replica"}
    client = FakeClient(lists={"/v1/replications": [rep]})
    issued = recon._teardown_filestorage_replication(client, "vol-source")
    puts = [p for (m, p) in client.calls if m == "PUT"]
    dels = [p for (m, p) in client.calls if m == "DELETE"]
    # the destructive calls must target the replication id AND carry the replica
    # volume id in ?volume_id= (the source side 400s "Check the volume purpose").
    assert any("/v1/replications/rep-1" in p and "volume_id=vol-replica" in p
               for p in puts), f"PUT must pause from replica side: {puts}"
    assert any("/v1/replications/rep-1" in p and "volume_id=vol-replica" in p
               for p in dels), f"DELETE must remove from replica side: {dels}"
    assert issued is True


def test_filestorage_volume_pass_tears_replication_before_volume():
    """The replication delete must be issued BEFORE the volume delete."""
    rep = {"replication_id": "rep-9", "replica_volume_id": "vol-rep9"}
    client = FakeClient(lists={
        "/v1/volumes": [_owned("regrfs-src9", id="vol-src9")],
        "/v1/replications": [rep],
    })
    recon._sweep_filestorage_volumes(client)
    seq = [p for (m, p) in client.calls if m == "DELETE"]
    rep_i = _ordered_index(seq, "/v1/replications/rep-9")
    vol_i = _ordered_index(seq, "/v1/volumes/vol-src9")
    assert rep_i >= 0 and vol_i >= 0
    assert rep_i < vol_i, (
        f"replication teardown must precede the volume delete: "
        f"replication@{rep_i} volume@{vol_i}")


# --------------------------------------------------------------------------- #
# Bug 2c: multi-region — SCP_SWEEP_REGIONS builds a region-overridden client
# --------------------------------------------------------------------------- #
def test_extra_region_clients_override_region(monkeypatch):
    monkeypatch.setenv("SCP_SWEEP_REGIONS", "kr-east1")
    built = {}

    class _StubApiClient:
        def __init__(self, cfg):
            self.cfg = cfg
            built["region"] = cfg.region

    monkeypatch.setattr(recon.core, "ApiClient", _StubApiClient)
    primary = FakeClient(region="kr-west1")
    extras = recon._extra_region_clients(primary)
    assert len(extras) == 1, "one extra region client for kr-east1"
    assert built["region"] == "kr-east1", \
        "extra client must override region to kr-east1 (replica region)"
    # primary region is never duplicated
    monkeypatch.setenv("SCP_SWEEP_REGIONS", "kr-west1")
    recon._REGION_CLIENTS.clear()
    assert recon._extra_region_clients(primary) == [], \
        "the primary region must not be swept twice"


def test_no_extra_regions_by_default(monkeypatch):
    monkeypatch.delenv("SCP_SWEEP_REGIONS", raising=False)
    primary = FakeClient(region="kr-west1")
    assert recon._extra_region_clients(primary) == [], \
        "no extra-region sweep unless SCP_SWEEP_REGIONS is set (no behaviour change)"


# --------------------------------------------------------------------------- #
# Bug 3: persistent-after-delete (stuck) detection + no-progress convergence
# --------------------------------------------------------------------------- #
def test_persistent_item_marked_stuck_and_skipped():
    """An owned id deleted in a prior round that still lists is marked stuck and
    dropped from the next round's deletable set (so the sweep can converge)."""
    it = _owned("regrfs-stuck", id="vol-stuck")
    client = FakeClient(lists={"/v1/volumes": [it]})

    # round 1: it's selected (deletable), and we simulate the failing delete by
    # recording the issued id the way _note_progress(400, it) would.
    picked1 = recon._select(client, "filestorage", "/v1/volumes",
                            name_prefixes=("regrfs",))
    assert any(recon._item_id(x) == "vol-stuck" for x in picked1)
    recon._note_progress(400, it)      # failed delete -> records issued id

    # round 2: same id still lists -> _select must mark it stuck and exclude it.
    picked2 = recon._select(client, "filestorage", "/v1/volumes",
                            name_prefixes=("regrfs",))
    assert all(recon._item_id(x) != "vol-stuck" for x in picked2), \
        "a persistent (deleted-but-still-listed) id must be dropped (Bug 3)"
    assert "vol-stuck" in recon._STUCK, "the id must be recorded as stuck"


def test_409_is_retryable_not_stuck():
    """A 409 (child/dependency still present) must NOT mark the item stuck — the
    sweep is actively clearing the dependency, so the item must keep retrying
    across rounds. Only HARD rejections (400/403) feed stuck-tracking."""
    it = _owned("regrvol-dep", id="vol-dep")
    assert recon._note_progress(409, it) is False
    assert "vol-dep" not in recon._DELETE_ISSUED, \
        "a 409 (retryable dependency) must not be recorded as stuck-eligible"
    # but a hard 400/403 does record it
    recon._note_progress(400, _owned("hard", id="hard1"))
    recon._note_progress(403, _owned("hard", id="hard2"))
    assert "hard1" in recon._DELETE_ISSUED and "hard2" in recon._DELETE_ISSUED


def test_clean_async_delete_not_marked_stuck():
    """A genuine 2xx delete must NOT be recorded as issued-failed, so a resource
    that legitimately deletes async (gone next round) is never called stuck."""
    it = _owned("regrfs-ok", id="vol-ok")
    recon._note_progress(204, it)      # clean delete -> not recorded
    assert "vol-ok" not in recon._DELETE_ISSUED
    assert "vol-ok" not in recon._STUCK


def test_stuck_id_skipped_silently_on_third_round():
    it = _owned("regrfs-x", id="vol-x")
    client = FakeClient(lists={"/v1/volumes": [it]})
    recon._note_progress(400, it)                       # round 1 fail
    recon._select(client, "filestorage", "/v1/volumes",
                  name_prefixes=("regrfs",))            # round 2 -> stuck
    picked3 = recon._select(client, "filestorage", "/v1/volumes",
                            name_prefixes=("regrfs",))  # round 3 -> silent skip
    assert picked3 == [], "known-stuck id stays excluded on later rounds"


def test_log_group_persistent_after_200_delete_is_stuck():
    """The IAM-gated SKE log-group: bulk DELETE returns 200 but it persists.
    run_sweep must mark it stuck on the SECOND round (it re-lists) and stop
    re-attempting it — the convergence fix for the 8-round loop."""
    lg = {"log_group_name": "/scp/ske/regrske-abc", "id": "lg-iam"}
    client = FakeClient(
        lists={"/v1/log-groups": [lg], "/v1/log-groups/lg-iam/log-streams": []},
        delete_status={"/v1/log-groups": 200},   # deceptive 200, item stays
    )
    # round 1: delete issued (200), id recorded
    recon.run_sweep(client)
    assert "lg-iam" in recon._DELETE_ISSUED
    # round 2: still listed -> stuck, and no further log-group delete attempted
    n_del_before = sum(1 for (m, p) in client.calls
                       if m == "DELETE" and "/v1/log-groups" == p.split("?")[0])
    recon.run_sweep(client)
    assert "lg-iam" in recon._STUCK, "persistent log-group must be marked stuck"
    n_del_after = sum(1 for (m, p) in client.calls
                      if m == "DELETE" and "/v1/log-groups" == p.split("?")[0])
    assert n_del_after == n_del_before, \
        "no further bulk log-group delete once stuck (no re-attempt)"


def test_stuck_reason_mentions_iam_for_log_group():
    lg = {"log_group_name": "/scp/ske/regrske-z", "id": "lg-z"}
    client = FakeClient(
        lists={"/v1/log-groups": [lg], "/v1/log-groups/lg-z/log-streams": []},
        delete_status={"/v1/log-groups": 200},
    )
    recon.run_sweep(client)   # round 1
    recon.run_sweep(client)   # round 2 -> stuck
    assert "lg-z" in recon._STUCK
    assert re.search(r"iam", recon._STUCK["lg-z"], re.I), \
        "the stuck reason should name the IAM-gated child stream"


def test_no_progress_predicate_drives_convergence():
    """_PROGRESS_THIS_ROUND counts only genuine teardown; a round of only-stuck
    items yields 0 -> main()'s loop would stop. Exercise the counter directly."""
    recon._PROGRESS_THIS_ROUND[0] = 0
    # two failed deletes -> zero genuine progress
    recon._note_progress(400, _owned("a", id="a"))
    recon._note_progress(409, _owned("b", id="b"))
    assert recon._PROGRESS_THIS_ROUND[0] == 0
    # one good delete -> progress registers
    recon._note_progress(204, _owned("c", id="c"))
    assert recon._PROGRESS_THIS_ROUND[0] == 1


# --------------------------------------------------------------------------- #
# Async-deletion in-progress handling (2026-07-03 TGW incident, run 28648339307)
# — a 202-accepted transit-gateway lists as DELETING for minutes and 409-blocks
# its VPC; the sweep must GRANT another bounded round, not converge. The PF-09
# scheduled-deletion behaviour (KMS/secrets pending their waiting window still
# converge) is locked alongside.
# --------------------------------------------------------------------------- #
def test_async_deleting_item_counts_in_progress_and_blocks_converge_cache():
    tgw = _owned("regrtgw-drain", id="tgw-1", state="DELETING")
    client = FakeClient(lists={"/v1/transit-gateways": [tgw]})
    recon._INPROGRESS_THIS_ROUND[0] = 0
    picked = recon._select(client, "vpc", "/v1/transit-gateways",
                           name_prefixes=("regrtgw",))
    assert picked, "the DELETING TGW is still owned + listed"
    assert recon._INPROGRESS_THIS_ROUND[0] == 1, \
        "a transitional DELETING item must count as in-progress"
    assert ("vpc", "/v1/transit-gateways") not in recon._CONVERGED, \
        "a collection with an async-deleting item must NOT converge-cache " \
        "(it will change once the delete lands)"


def test_scheduled_deletion_still_converges_pf09():
    """A KMS key pending its scheduled-deletion window must NOT grant rounds —
    the PF-09 convergence the existing sweep relies on stays intact."""
    kms = _owned("regrkms-x", id="kms-1", state="To_Be_Terminated")
    client = FakeClient(lists={"/v1/kms/transit": [kms]})
    recon._INPROGRESS_THIS_ROUND[0] = 0
    recon._select(client, "kms", "/v1/kms/transit", name_prefixes=("regr",))
    assert recon._INPROGRESS_THIS_ROUND[0] == 0, \
        "scheduled (PF-09) deletion is NOT async-in-progress"
    assert ("kms", "/v1/kms/transit") in recon._CONVERGED, \
        "a pending-deletion-only collection still converge-caches"


# --------------------------------------------------------------------------- #
# Terminal policy (owner 2026-07-14): once no owned VPC remains, an async-
# deleting LEAF (dbaas cluster in late internal drain / orphan TGW) blocks
# nothing and must NOT buy the remaining full rounds — the sweep stops and
# REPORTS it instead of holding the run open for a ~90-min drain ("vpc 모두
# 삭제되고 부산물 정리되면 끝내는게 맞고 .. 이슈로 남은 자원 리포트"). A still-
# present owned VPC keeps the grant (the 2026-07-03 TGW incident, where the
# in-progress item IS what 409-blocks the VPC).
# --------------------------------------------------------------------------- #
def test_owned_vpcs_present_counts_owned_only():
    client = FakeClient(lists={"/v1/vpcs": [
        _owned("regrvpc-a", id="v1"),
        {"name": "someone-elses-vpc", "id": "v2"},   # neither tag nor regr* name
    ]})
    assert recon._owned_vpcs_present(client) == 1, \
        "only the owned regr* VPC counts toward the cap-clear check"
    assert recon._owned_vpcs_present(FakeClient(lists={"/v1/vpcs": []})) == 0


def test_owned_vpc_probe_failure_assumes_present():
    """A list error must fail SAFE (assume a VPC is present → keep granting),
    never declare the cap clear on a transient failure."""
    class BoomClient(FakeClient):
        def get(self, path, service=None, **kw):
            raise RuntimeError("list host unreachable")
    assert recon._owned_vpcs_present(BoomClient()) == 1


class _StubSettings:
    allow_destructive = True

    def require_credentials(self):
        pass


def _stub_main_env(monkeypatch, vpcs_present, calls):
    """Wire main() to a fake sweep that always presents a draining leaf
    (genuine=0, inprog=1, reported=1 = the grant-inprog shape) and a stubbed
    owned-VPC probe / leftover report, so the loop's terminal decision is what's
    under test — not the real network machinery."""
    def fake_sweep(client):
        calls["sweep"] += 1
        recon._INPROGRESS_THIS_ROUND[0] = 1   # a leaf is mid-async-deletion
        recon._PROGRESS_THIS_ROUND[0] = 0     # nothing genuinely reaped
        return 1                               # reported (deceptive)
    monkeypatch.setattr(recon, "run_sweep", fake_sweep)
    monkeypatch.setattr(recon, "_owned_vpcs_present", lambda c: vpcs_present)
    monkeypatch.setattr(recon, "_leftover_report",
                        lambda c: calls.__setitem__("report", calls["report"] + 1))
    monkeypatch.setattr(recon.core, "settings", _StubSettings())
    monkeypatch.setattr(recon.core, "ApiClient", lambda cfg: object())
    monkeypatch.delenv("SCP_SWEEP_NOWAIT", raising=False)
    monkeypatch.setenv("SCP_SWEEP_ROUNDS", "8")


def test_main_leaf_drain_stops_and_reports_when_no_vpc(monkeypatch):
    calls = {"sweep": 0, "report": 0}
    _stub_main_env(monkeypatch, vpcs_present=0, calls=calls)
    recon.main()
    assert calls["sweep"] == 1, \
        "no owned VPC + only a draining leaf → stop after ONE round, not all 8"
    assert calls["report"] == 1, "the surviving leaf must be reported"


def test_main_keeps_granting_while_a_vpc_is_present(monkeypatch):
    """The negative control: a still-present owned VPC means the in-progress
    item may be blocking it — keep granting rounds up to the cap (preserves the
    2026-07-03 TGW-mid-deletion behaviour)."""
    calls = {"sweep": 0, "report": 0}
    _stub_main_env(monkeypatch, vpcs_present=1, calls=calls)
    recon.main()
    assert calls["sweep"] == 8, \
        "a present owned VPC keeps granting rounds to the cap (blocker may drain)"
    assert calls["report"] == 1, "the cap-stop still reports what is left"


def test_round_verdict_grants_round_for_inprogress_only():
    """The exact incident shape: genuine=0, reported=1 (inflated by a truthy
    non-2xx), one item mid-async-deletion → the loop must CONTINUE. Without
    in-progress items the same shape converges (PF-09 re-delete unaffected)."""
    assert recon._round_verdict(1, 5, 0) == "continue"
    assert recon._round_verdict(0, 1, 1) == "grant-inprog"   # the incident
    assert recon._round_verdict(0, 1, 0) == "stop"           # PF-09 converges
    assert recon._round_verdict(0, 0, 0) == "stop"
    assert recon._round_verdict(2, 2, 3) == "continue"       # progress wins


def test_tgw_vpc_connections_deleted_before_tgw():
    """The TGW pass must enumerate the NESTED per-TGW connection list (the flat
    one is 403 live) and delete connections BEFORE the TGW — a TGW delete does
    not reliably cascade its connection (live 2026-07-04: connection DELETING
    for hours while the TGW sat EDITING, pinning the shared VPC)."""
    tgw = _owned("regrtgw-a", id="tgw-a", state="ACTIVE")
    conn = {"id": "conn-1", "vpc_id": "vpc-9", "state": "ACTIVE"}
    client = FakeClient(lists={
        "/v1/transit-gateways": [tgw],
        "/v1/transit-gateways/tgw-a/vpc-connections": [conn],
    })
    recon.run_sweep(client)
    seq = _delete_paths(client)
    ci = next((i for i, p in enumerate(seq)
               if p == "/v1/transit-gateways/tgw-a/vpc-connections/conn-1"), -1)
    ti = next((i for i, p in enumerate(seq)
               if p == "/v1/transit-gateways/tgw-a"), -1)
    assert ci >= 0, "the TGW's vpc-connection must be deleted"
    assert ti >= 0, "the TGW itself must still be deleted"
    assert ci < ti, f"connection must go before the TGW: conn@{ci} tgw@{ti}"


def test_deleting_tgw_not_redeleted_and_counts_in_progress():
    """A TGW (and its connection) already in DELETING must not be re-DELETEd
    (no-op noise) and must count as in-progress so the round loop waits."""
    tgw = _owned("regrtgw-d", id="tgw-d", state="DELETING")
    conn = {"id": "conn-2", "vpc_id": "vpc-9", "state": "DELETING"}
    client = FakeClient(lists={
        "/v1/transit-gateways": [tgw],
        "/v1/transit-gateways/tgw-d/vpc-connections": [conn],
    })
    recon._INPROGRESS_THIS_ROUND[0] = 0
    recon.run_sweep(client)
    seq = _delete_paths(client)
    assert "/v1/transit-gateways/tgw-d" not in seq, \
        "a DELETING TGW must not be re-deleted"
    assert not any("conn-2" in p for p in seq), \
        "a DELETING connection must not be re-deleted"
    assert recon._INPROGRESS_THIS_ROUND[0] >= 2, \
        "both the TGW and its connection count as in-progress"


def test_is_tgw_settling_predicate():
    """CREATING/EDITING are transitional-not-yet-deletable; ACTIVE/ERROR are
    the only DELETE-acceptable states (live error string: 'Transit Gateway
    state is not deletable state(Active, Error)'); an already-DELETING item is
    left to _is_async_deleting, not double-counted here."""
    assert recon._is_tgw_settling({"state": "EDITING"})
    assert recon._is_tgw_settling({"state": "CREATING"})
    assert not recon._is_tgw_settling({"state": "ACTIVE"})
    assert not recon._is_tgw_settling({"state": "ERROR"})
    assert not recon._is_tgw_settling({"state": "DELETING"})  # _is_async_deleting's turn
    assert not recon._is_tgw_settling({})


def test_editing_tgw_delete_skipped_and_counts_in_progress():
    """REPAIR 2026-07-07 (HB4b-2 item 5): a TGW settling in EDITING (e.g. right
    after its own create, or after a vpc-connection create/delete flips it back
    from ACTIVE) must NOT have its DELETE attempted this round — it would just
    400 'not deletable state(Active, Error)', and unlike DELETING that 400 was
    never counted in-progress, so a sweep whose only remaining owned item was
    such a TGW converged ('stop') one round before it would have settled
    (2026-07-06 HB4b run 28827996068: final sweep left the TGW+VPC pair for a
    human FORCE re-sweep hours later)."""
    tgw = _owned("regrtgw-e", id="tgw-e", state="EDITING")
    client = FakeClient(lists={
        "/v1/transit-gateways": [tgw],
        "/v1/transit-gateways/tgw-e/vpc-connections": [],
    })
    recon._INPROGRESS_THIS_ROUND[0] = 0
    recon.run_sweep(client)
    seq = _delete_paths(client)
    assert "/v1/transit-gateways/tgw-e" not in seq, \
        "an EDITING TGW's doomed-to-400 DELETE must not even be attempted"
    assert recon._INPROGRESS_THIS_ROUND[0] >= 1, \
        "the settling TGW must count as in-progress so the round loop waits " \
        "instead of converging early"


def test_vpc_409_with_detectable_holder_single_attempt():
    """A VPC whose delete 409s while an owned TGW's vpc-connection still points
    at it must be attempted ONCE (with a blocked-by line + in-progress defer),
    not 6 times — the noisy-409 shape from the 2026-07-03 FORCE cleanup log."""
    vpc = _owned("regrvpcsh-x", id="vpc-9")
    tgw = _owned("regrtgw-h", id="tgw-h", state="DELETING")
    conn = {"id": "conn-3", "vpc_id": "vpc-9", "state": "DELETING"}
    client = FakeClient(
        lists={"/v1/vpcs": [vpc], "/v1/transit-gateways": [tgw],
               "/v1/transit-gateways/tgw-h/vpc-connections": [conn]},
        delete_status={"/v1/vpcs": 409},
    )
    recon._INPROGRESS_THIS_ROUND[0] = 0
    recon.run_sweep(client)
    vpc_deletes = [p for p in _delete_paths(client) if p == "/v1/vpcs/vpc-9"]
    assert len(vpc_deletes) == 1, \
        f"one attempt then defer when the holder is detectable: {vpc_deletes}"
    assert recon._INPROGRESS_THIS_ROUND[0] >= 1, \
        "the deferred VPC must arm the in-progress round grant"


def test_vpc_409_without_holder_keeps_purge_retry():
    """No detectable holder → the existing purge-children + retry loop is kept
    (un-prefixed child leaks still get reaped the old way)."""
    vpc = _owned("regrvpc-nh", id="vpc-nh")
    client = FakeClient(
        lists={"/v1/vpcs": [vpc]},
        delete_status={"/v1/vpcs": 409},
    )
    recon.run_sweep(client)
    vpc_deletes = [p for p in _delete_paths(client) if p == "/v1/vpcs/vpc-nh"]
    assert len(vpc_deletes) > 1, \
        "without a detectable holder the retry loop must still run"


# --------------------------------------------------------------------------- #
# Ownership guard is NOT weakened (lock the invariant alongside the new logic)
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# HB4d item 2 — LB static-NAT reaped BEFORE the load balancer itself. Live
# incident (run 28835929967): static-nat-create (201) fires, then an
# immediate delete 400s `StaticNatNotDeletableState` (CREATING) — an
# interrupted run leaks the NAT, and the account-wide sweep used to delete
# the LB directly, which 409s "associated" while the NAT is still attached,
# stranding the LB + its publicip (ATTACHED) + the shared VPC.
# --------------------------------------------------------------------------- #
def test_lb_static_nat_deleted_before_loadbalancer():
    lb = _owned("regrlb-a", id="lb-a", vpc_id="id-regrvpcsh-a")
    vpc = _owned("regrvpcsh-a", id="id-regrvpcsh-a")
    client = FakeClient(
        lists={"/v1/loadbalancers": [lb], "/v1/vpcs": [vpc]},
        objects={"/v1/loadbalancers/lb-a/static-nats":
                 {"static_nat": {"state": "ACTIVE", "publicip_id": "pip-1",
                                 "external_ip_address": "1.2.3.4"}}},
    )
    recon.run_sweep(client)
    seq = _delete_paths(client)
    nat_i = next((i for i, p in enumerate(seq)
                  if p == "/v1/loadbalancers/lb-a/static-nats"), -1)
    lb_i = next((i for i, p in enumerate(seq)
                 if p == "/v1/loadbalancers/lb-a"), -1)
    assert nat_i >= 0, "an attached static-NAT must be reaped"
    assert lb_i >= 0, "the load balancer delete must still be issued"
    assert nat_i < lb_i, (
        f"static-nat delete must precede the LB delete: nat@{nat_i} lb@{lb_i}")


def test_lb_static_nat_skipped_when_none_attached():
    """An LB reporting an empty static_nat state must NOT get a no-op DELETE
    against the static-nats collection."""
    lb = _owned("regrlb-b", id="lb-b", vpc_id="id-regrvpcsh-b")
    vpc = _owned("regrvpcsh-b", id="id-regrvpcsh-b")
    client = FakeClient(
        lists={"/v1/loadbalancers": [lb], "/v1/vpcs": [vpc]},
        objects={"/v1/loadbalancers/lb-b/static-nats":
                 {"static_nat": {"state": "", "publicip_id": None,
                                 "external_ip_address": ""}}},
    )
    recon.run_sweep(client)
    seq = _delete_paths(client)
    assert not any("static-nats" in p for p in seq), \
        "no static-NAT attached -> no DELETE against the static-nats endpoint"
    assert any(p == "/v1/loadbalancers/lb-b" for p in seq), \
        "the load balancer delete must still be issued"


def test_lb_static_nat_retries_on_400_then_gives_up():
    """A static-NAT stuck CREATING across every retry must not block the sweep
    forever — after retries are exhausted, reaping gives up (returns False) and
    the caller still attempts the LB delete this round (next round retries)."""
    client = FakeClient(
        objects={"/v1/loadbalancers/lb-c/static-nats":
                 {"static_nat": {"state": "CREATING"}}},
        delete_status={"/v1/loadbalancers/lb-c/static-nats": 400},
    )
    ok = recon._reap_lb_static_nat(client, "lb-c")
    assert ok is False
    dels = [p for (m, p) in client.calls
            if m == "DELETE" and p == "/v1/loadbalancers/lb-c/static-nats"]
    assert len(dels) == 6, f"expected the bounded retry budget, got {dels}"


def test_items_skips_pagination_links_before_items_key():
    """PF 2026-07-11: SKE nodepools returns {"count":1,"links":[],"nodepools":
    [...]}; the old first-list rule returned the empty links list, the sweep saw
    0 nodepools, skipped nodepool teardown, and the cluster delete 409-looped."""
    body = {"count": 1, "links": [],
            "nodepools": [{"id": "np-1", "name": "regrnp1"}]}
    assert recon._items(body) == [{"id": "np-1", "name": "regrnp1"}]


def test_items_empty_collection_still_returns_empty():
    assert recon._items({"count": 0, "links": [], "contents": []}) == []
    assert recon._items({"items": []}) == []
    assert recon._items({"links": []}) == []          # links alone ≠ items
    assert recon._items({"count": 0}) == []
    assert recon._items(None) == []


def test_items_prefers_first_nonempty_dict_list():
    body = {"sort": [], "contents": [{"id": "a"}], "extra": [{"id": "b"}]}
    assert recon._items(body) == [{"id": "a"}]


def test_endpoint_type_subnet_swept_via_type_query():
    """PF-47: an owned VPC_ENDPOINT-type subnet lists ONLY under
    /v1/subnets?type=VPC_ENDPOINT (the bare list hides it) — the sweep must
    still find and delete it, and a subnet appearing in both collections must
    be deleted once (dedup)."""
    ep = _owned("regrsubc86cfbf3", id="sub-ep1")
    both = _owned("regrsubboth", id="sub-both")

    class QueryFakeClient(FakeClient):
        def _key(self, path):
            # query-SENSITIVE keying: the type= query addresses a different
            # collection view (the real API hides endpoint subnets from the
            # bare list), unlike the base class which strips queries.
            return path if "type=VPC_ENDPOINT" in path else path.split("?", 1)[0]

    client = QueryFakeClient(lists={
        "/v1/subnets": [both],
        "/v1/subnets?type=VPC_ENDPOINT": [ep, both],
    })
    recon.run_sweep(client)
    dels = [p for p in _delete_paths(client) if p.startswith("/v1/subnets/")]
    assert "/v1/subnets/sub-ep1" in dels, \
        "endpoint-type subnet must be swept via the ?type= collection (PF-47)"
    assert dels.count("/v1/subnets/sub-both") == 1, \
        "a subnet visible in both collections is deleted exactly once"


def test_vpc_409_srn_holder_purged_directly():
    """run-892a: VPC DELETE 409 본문의 related_resources SRN이 direct-connect
    홀더를 명시 — 목록/탐지에 안 잡히는 홀더라도 SRN을 파싱해 (routing-rules
    자식 먼저) 직접 삭제하고 VPC를 즉시 재시도해야 한다."""
    vpc = _owned("regrvpcsh-dc", id="vpc-dc")
    srn = ("srn:e::acct1:kr-west1::direct-connect:direct-connect/"
           "0784758f4c96419e93df2650aba592b0")

    class DC409Client(FakeClient):
        def delete(self, path, service=None, json=None, **kw):
            self.calls.append(("DELETE", path))
            if path == "/v1/vpcs/vpc-dc":
                # DC가 살아 있는 동안 409 + SRN, 지워진 뒤 204
                if any(p.startswith("/v1/direct-connects/0784758f") and "/routing-rules/" not in p
                       for (m, p) in self.calls if m == "DELETE"):
                    return _Resp(204)
                return _Resp(409, {"errors": [{
                    "code": "scp-network.vpc.related-resource",
                    "related_resources": [srn]}]})
            return _Resp(204)

    client = DC409Client(lists={
        "/v1/vpcs": [vpc],
        "/v1/direct-connects/0784758f4c96419e93df2650aba592b0/routing-rules":
            [{"id": "rule-1"}],
    })
    recon.run_sweep(client)
    seq = _delete_paths(client)
    rule_i = _ordered_index(seq, "/routing-rules/rule-1")
    dc_i = next((i for i, p in enumerate(seq)
                 if p == "/v1/direct-connects/0784758f4c96419e93df2650aba592b0"), -1)
    assert rule_i >= 0 and dc_i >= 0 and rule_i < dc_i, \
        f"routing-rule이 DC보다 먼저: rule@{rule_i} dc@{dc_i}"
    assert seq.count("/v1/vpcs/vpc-dc") >= 2, "홀더 회수 후 VPC 즉시 재시도"
    assert any(p == "/v1/vpcs/vpc-dc" for p in seq[dc_i:]), \
        "DC 소멸 후 VPC 삭제가 이어져야 함"


def test_direct_connect_pass_rules_before_dc():
    """신설 DC 패스: 소유 regrdc*의 routing-rules를 먼저 비우고 DC를 삭제."""
    dc = _owned("regrdcmhjehckc", id="dc-1")
    client = FakeClient(lists={
        "/v1/direct-connects": [dc],
        "/v1/direct-connects/dc-1/routing-rules": [{"id": "rr-9"}],
    })
    recon.run_sweep(client)
    seq = _delete_paths(client)
    rr_i = _ordered_index(seq, "/v1/direct-connects/dc-1/routing-rules/rr-9")
    dc_i = next((i for i, p in enumerate(seq)
                 if p == "/v1/direct-connects/dc-1"), -1)
    assert rr_i >= 0 and dc_i >= 0 and rr_i < dc_i


def test_unowned_items_never_selected():
    """Items with neither owner tag nor a regr* name must never be selected for
    deletion by any of the new passes."""
    client = FakeClient(lists={
        "/v1/images": [{"name": "windows-2019", "id": "win"}],
        "/v1/volumes": [{"name": "someones-volume", "id": "x"}],
    })
    recon.run_sweep(client)
    deleted = _delete_paths(client)
    assert not any("win" in p or "someones-volume" in p or "/x" in p
                   for p in deleted), \
        "un-owned resources must never be deleted (ownership guard intact)"


def test_dbaas_deleting_cluster_not_redeleted_no_barrier_hostage():
    """teardown 최소화(2026-07-15): 이미 DELETING인 클러스터(라이프사이클이
    방금 삭제, drain ~90분)에 재-DELETE를 발행하면 902s dbaas 배리어가 그
    drain을 인질로 잡는다 — 스킵하고 in-progress 집계(_select)에만 맡긴다."""
    cl = _owned("regr-maria", id="cl-1", state="DELETING")
    client = FakeClient(lists={"/v1/clusters": [cl]})
    recon._INPROGRESS_THIS_ROUND[0] = 0
    recon.run_sweep(client)
    assert not any(p == "/v1/clusters/cl-1" for p in _delete_paths(client)), \
        "DELETING 클러스터 재-DELETE 금지 (배리어 인질 방지)"
    assert recon._INPROGRESS_THIS_ROUND[0] >= 1, "in-progress 집계는 유지"


def test_platform_named_igw_reclaimed_via_token_match():
    """IGW는 create 바디에 name이 없어 플랫폼이 IGW_<vpc이름>으로 자동 명명 —
    'IGW_regrvpcnb…'가 regr* 프리픽스에 안 걸려 영구 스킵되던 갭(2026-07-15
    신규계정 첫 런 실측; 구 계정에도 동일 잔존). 토큰 매칭으로 회수하되 남의
    IGW(IGW_othervpc)는 절대 안 지운다."""
    ours = {"name": "IGW_regrvpcnb6a5774bd", "id": "igw-ours"}
    theirs = {"name": "IGW_customer-prod-vpc", "id": "igw-theirs"}
    client = FakeClient(lists={"/v1/internet-gateways": [ours, theirs]})
    recon.run_sweep(client)
    dels = _delete_paths(client)
    assert "/v1/internet-gateways/igw-ours" in dels, \
        "플랫폼 자동명명 IGW_regr* 는 토큰 매칭으로 회수"
    assert "/v1/internet-gateways/igw-theirs" not in dels, \
        "남의 IGW는 토큰이 regr*가 아니므로 안전"
