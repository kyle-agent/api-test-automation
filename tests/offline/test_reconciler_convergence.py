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

    def __init__(self, lists=None, delete_status=None, region="kr-west1"):
        # lists: {path_without_query: [items]}
        self.lists = lists or {}
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
        return _Resp(200, {"items": list(self.lists.get(self._key(path), []))})

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
# Ownership guard is NOT weakened (lock the invariant alongside the new logic)
# --------------------------------------------------------------------------- #
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
