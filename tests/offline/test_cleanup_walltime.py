"""런 종료 자원정리 wall-time 최적화 오프라인 검증 (오너 지시 2026-07-14:
"별도 agent로 자원정리 시간을 최대한 단축").

실측: 테스트 ~50분 뒤 정리가 15~80분을 더 끌었다. 네 갈래 최적화의 의미
보존을 잠근다 — 소유권 게이트 약화 금지 · 자식→부모 순서 보존 · leaf-drain
종료 정책 / 2026-07-03 TGW 인시던트 보호(grant-inprog) 불변:

  1. engine.provision_shared_vpc의 teardown() — 직렬 사다리(서브넷 gone-wait
     → TGW ACTIVE-wait+사다리 → IGW → 메인 VPC → net-A → net-B)를 독립 체인
     4개([main]=서브넷→IGW→VPC, [tgw], [net-a], [net-b])로 병렬화. 체인
     내부의 자식→부모 순서는 유지.
  2. cleanup.run_scoped.reap_run_leftovers — per-item 직렬 _wait_gone(150s×N)
     을 버킷당 공유 배리어(_wait_all_gone) 1회로; leaf 꼬리는 배리어 스킵.
  3. cleanup.reconciler._leftover_report — full dry-scan(scan_owned, 전 컬렉션
     재나열) 대신 스윕 자신의 마지막 라운드 픽(_LAST_PICKED)을 요약(LIST 0회);
     픽 정보가 비면 기존 scan_owned 폴백.
  4. grant-inprog 라운드 간 대기 — 고정 30s를 30→60→120s 지수 backoff로
     (상한 SCP_SWEEP_INPROGRESS_SLEEP_MAX_S, 기본 120).

FakeClient 패턴은 tests/offline/test_reconciler_convergence.py ·
test_run_scoped_reap.py의 것을 재사용한다.
"""
from __future__ import annotations

import json
import threading
import types

import pytest

import cleanup.reconciler as recon
import cleanup.run_scoped as rs
import regression.scenarios.engine as engine
from tests.offline.test_reconciler_convergence import FakeClient, _owned
from tests.offline.test_run_scoped_reap import _FakeClient, _Resp


# =========================================================================== #
# 1. engine.provision_shared_vpc teardown() — 병렬 체인
# =========================================================================== #
class _TDResp:
    def __init__(self, status, body=None):
        self.status = status
        self.body = body or {}
        self.raw_text = ""


class _VirtualTime:
    """teardown의 time.time/sleep을 가상 시계로 — 대기 사다리가 실시간을
    태우지 않게 한다 (sleep은 가상 시계만 전진)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._t = 1_000_000.0

    def time(self):
        with self._lock:
            return self._t

    def monotonic(self):
        return self.time()

    def sleep(self, s):
        with self._lock:
            self._t += max(float(s), 0.01)


class _ChainClient:
    """체인 독립성/순서를 판별하는 fake:
      * 서브넷 GET은 공유 TGW DELETE가 발행된 뒤에만 404 — 직렬 구현(서브넷
        gone-wait가 TGW보다 먼저 완료)에서는 절대 404를 못 본다 → 병렬 증명.
      * 메인 VPC DELETE는 공유 IGW DELETE 이후에만 204 (자식→부모 보존 증명;
        순서가 깨지면 409 사다리를 다 태우고도 못 지운다).
    호출은 (METHOD, path, thread_name)으로 기록."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str, str]] = []
        self._tgw_deleted = threading.Event()
        self._first_sub_get_done = False

    def _rec(self, m, p):
        with self._lock:
            self.calls.append((m, p, threading.current_thread().name))

    def _deleted(self, prefix):
        with self._lock:
            return any(m == "DELETE" and p.startswith(prefix)
                       for m, p, _ in self.calls)

    def request(self, method, path, *, json=None, service=None, **kw):
        m = method.upper()
        self._rec(m, path)
        if m == "DELETE" and path.startswith("/v1/transit-gateways/"):
            self._tgw_deleted.set()
            return _TDResp(202)
        if m == "DELETE" and path == "/v1/vpcs/vpc-main":
            ok = self._deleted("/v1/internet-gateways/igw-1")
            return _TDResp(204 if ok else 409)
        return _TDResp(202 if m == "DELETE" else 200)

    def get(self, path, service=None, **kw):
        self._rec("GET", path)
        if path.startswith("/v1/subnets/"):
            with self._lock:
                first = not self._first_sub_get_done
                self._first_sub_get_done = True
            if first:
                # 병렬 구현이면 TGW 체인이 곧(≪2s) DELETE를 발행한다; 직렬
                # 구현이면 타임아웃 후 200 → gone-wait가 헛돌다 만료된다.
                self._tgw_deleted.wait(2.0)
            return _TDResp(404 if self._tgw_deleted.is_set() else 200)
        if path.startswith("/v1/transit-gateways/"):
            return _TDResp(200, {"transit_gateway": {"id": "tgw-1",
                                                     "state": "ACTIVE"}})
        return _TDResp(200)


def _provision(monkeypatch, client, **kw):
    """_run_step만 스텁해 provision을 통과시키고 (ctx, teardown)을 얻는다 —
    teardown은 client의 request/get을 직접 쓰므로 fake가 그대로 관측한다."""
    bodies = {
        "create-shared-vpc": {"vpc": {"id": "vpc-main"}},
        "create-shared-net-vpc-a": {"vpc": {"id": "vpc-na"}},
        "create-shared-net-vpc-b": {"vpc": {"id": "vpc-nb"}},
        "create-shared-tgw": {"transit_gateway": {"id": "tgw-1"}},
        "create-shared-igw": {"internet_gateway": {"id": "igw-1"}},
        "create-shared-subnet": {"subnet": {"id": "sub-1"}},
        "create-shared-db-subnet": {"subnet": {"id": "sub-db"}},
    }

    def fake_run_step(cl, step, path, body, service, ctx, **kws):
        return types.SimpleNamespace(status=201,
                                     body=bodies.get(step.get("name"), {}),
                                     raw_text="")

    monkeypatch.setattr(engine, "_run_step", fake_run_step)
    monkeypatch.delenv(engine._ENV_SHARED_VPC, raising=False)
    monkeypatch.setattr(engine, "time", _VirtualTime())
    cfg = types.SimpleNamespace(allow_mutations=True, allow_destructive=True)
    reg = types.SimpleNamespace(track=lambda rec: None)
    return engine.provision_shared_vpc(
        client, cfg, resource_registry=reg, need_db_subnet=True,
        wait_subnets_active=False, **kw)


def test_teardown_chains_run_in_parallel_and_keep_child_parent_order(monkeypatch):
    client = _ChainClient()
    ctx, teardown = _provision(monkeypatch, client, need_net_vpcs=True,
                               need_tgw=True, need_igw=True)
    assert ctx["shared_vpc_id"] == "vpc-main" and ctx["shared_tgw_id"] == "tgw-1"
    teardown()
    seq = [(m, p) for m, p, _t in client.calls]
    dels = [p for m, p in seq if m == "DELETE"]

    # 모든 자원의 delete가 발행됐다 (subnets, igw, tgw, main + net-A/B VPC)
    for want in ("/v1/subnets/sub-1", "/v1/subnets/sub-db",
                 "/v1/internet-gateways/igw-1", "/v1/transit-gateways/tgw-1",
                 "/v1/vpcs/vpc-main", "/v1/vpcs/vpc-na", "/v1/vpcs/vpc-nb"):
        assert want in dels, f"missing delete {want}: {dels}"

    # [병렬 증명 1] 서브넷 gone-wait가 TGW 체인과 겹쳤다: TGW DELETE 이후에
    # 서브넷 GET이 있었고(404 관측) — 직렬 사다리라면 서브넷 대기는 TGW
    # 삭제 전에 이미 끝났어야 한다.
    tgw_del_i = seq.index(("DELETE", "/v1/transit-gateways/tgw-1"))
    assert any(m == "GET" and p.startswith("/v1/subnets/")
               for m, p in seq[tgw_del_i + 1:]), \
        "subnet gone-wait must overlap the TGW chain (parallel teardown)"

    # [병렬 증명 2] TGW 체인과 main 체인은 서로 다른 워커 스레드에서 돌았다.
    thr = {(m, p): t for m, p, t in client.calls if m == "DELETE"}
    assert thr[("DELETE", "/v1/transit-gateways/tgw-1")] != \
        thr[("DELETE", "/v1/vpcs/vpc-main")], "TGW/main 체인은 별도 워커"

    # [자식→부모 보존] main 체인 내부 순서: 서브넷들 → IGW → 메인 VPC.
    sub_is = [i for i, c in enumerate(seq)
              if c[0] == "DELETE" and c[1].startswith("/v1/subnets/")]
    igw_i = seq.index(("DELETE", "/v1/internet-gateways/igw-1"))
    vpc_i = seq.index(("DELETE", "/v1/vpcs/vpc-main"))
    assert max(sub_is) < igw_i < vpc_i, \
        f"main 체인 자식→부모 순서 붕괴: subnets@{sub_is} igw@{igw_i} vpc@{vpc_i}"
    # IGW가 먼저 지워졌으므로 메인 VPC DELETE는 첫 시도에 204 — 사다리 1회.
    assert dels.count("/v1/vpcs/vpc-main") == 1


def test_teardown_single_chain_still_serial_and_ordered(monkeypatch):
    """TGW/net-VPC 없는 최소 구성(main 체인 1개)은 스레드 없이 종전과 동일:
    서브넷 → (gone-wait) → VPC. IGW 없음."""

    class _SoloClient(_ChainClient):
        def get(self, path, service=None, **kw):
            self._rec("GET", path)
            if path.startswith("/v1/subnets/"):
                return _TDResp(404)     # 즉시 gone
            return _TDResp(200)

        def request(self, method, path, *, json=None, service=None, **kw):
            m = method.upper()
            self._rec(m, path)
            return _TDResp(202 if m == "DELETE" else 200)

    client = _SoloClient()
    ctx, teardown = _provision(monkeypatch, client, need_net_vpcs=False,
                               need_tgw=False, need_igw=False)
    teardown()
    dels = [p for m, p, _t in client.calls if m == "DELETE"]
    assert dels[-1] == "/v1/vpcs/vpc-main", dels
    assert set(dels[:-1]) == {"/v1/subnets/sub-1", "/v1/subnets/sub-db"}


def test_teardown_noop_without_destructive_gate(monkeypatch):
    """안전 게이트 보존: allow_destructive=False면 teardown은 아무 호출도
    발행하지 않는다 (Hard Rule 1)."""
    client = _ChainClient()
    bodies = {"create-shared-vpc": {"vpc": {"id": "vpc-main"}},
              "create-shared-subnet": {"subnet": {"id": "sub-1"}}}
    monkeypatch.setattr(
        engine, "_run_step",
        lambda cl, step, path, body, service, ctx, **kw: types.SimpleNamespace(
            status=201, body=bodies.get(step.get("name"), {}), raw_text=""))
    monkeypatch.delenv(engine._ENV_SHARED_VPC, raising=False)
    cfg = types.SimpleNamespace(allow_mutations=True, allow_destructive=False)
    reg = types.SimpleNamespace(track=lambda rec: None)
    ctx, teardown = engine.provision_shared_vpc(
        client, cfg, resource_registry=reg, need_db_subnet=False)
    client.calls.clear()
    teardown()
    assert client.calls == []


# =========================================================================== #
# 2. run_scoped.reap_run_leftovers — 버킷 공유 배리어
# =========================================================================== #
def _events(tmp_path, rows):
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def _wire_reap(monkeypatch, cli, barriers, waits):
    def fake_barrier(c, pairs, *a, **k):
        barriers.append(sorted(pairs))
        c.calls.append(("BARRIER", str(sorted(pairs))))
        return True

    monkeypatch.setattr(rs.r, "_wait_all_gone", fake_barrier)
    monkeypatch.setattr(rs.r, "_wait_gone",
                        lambda *a, **k: waits.append(a) or True)
    monkeypatch.setattr(rs.core, "ApiClient", lambda *a, **k: cli)
    monkeypatch.setattr(rs.time, "sleep", lambda s: None)


def test_reap_batches_bucket_deletes_behind_one_barrier(tmp_path, monkeypatch):
    """같은 버킷(vpc-endpoints)의 delete N개는 전부 발행된 뒤 공유 배리어
    1회로 대기한다 — 종전 per-item _wait_gone(150s×N) 제거. 자식→부모는
    보존: 배리어는 endpoint 발행 후 · VPC(부모) 시도 전에 선다."""
    p = _events(tmp_path, [
        {"kind": "resource-tracked", "service": "vpc",
         "path": "/v1/vpc-endpoints/e1", "lifecycle": "x"},
        {"kind": "resource-tracked", "service": "vpc",
         "path": "/v1/vpc-endpoints/e2", "lifecycle": "x"},
        {"kind": "resource-tracked", "service": "vpc",
         "path": "/v1/vpcs/aaa", "lifecycle": "x"},
    ])
    cli = _FakeClient({("DELETE", "/v1/"): _Resp(202)})
    barriers, waits = [], []
    _wire_reap(monkeypatch, cli, barriers, waits)
    issued = rs.reap_run_leftovers(p, log=lambda m: None)
    assert issued == 3
    assert len(barriers) == 1, f"버킷당 배리어 1회여야: {barriers}"
    assert barriers[0] == [("vpc", "/v1/vpc-endpoints/e1"),
                           ("vpc", "/v1/vpc-endpoints/e2")]
    assert not waits, "reap 루프의 per-item 직렬 _wait_gone은 제거돼야"
    seq = cli.calls
    b_i = next(i for i, c in enumerate(seq) if c[0] == "BARRIER")
    ep_is = [i for i, c in enumerate(seq)
             if c[0] == "DELETE" and "/vpc-endpoints/" in c[1]]
    vpc_i = next(i for i, c in enumerate(seq)
                 if c[0] == "DELETE" and c[1] == "/v1/vpcs/aaa")
    assert max(ep_is) < b_i < vpc_i, \
        f"자식 발행 → 배리어 → 부모 시도 순서: ep@{ep_is} barrier@{b_i} vpc@{vpc_i}"


def test_reap_leaf_tail_skips_barrier(tmp_path, monkeypatch):
    """마지막(leaf) 버킷 뒤에는 기다릴 부모가 없다 — 배리어 스킵."""
    p = _events(tmp_path, [
        {"kind": "resource-tracked", "service": "vpc",
         "path": "/v1/vpcs/aaa", "lifecycle": "x"},
        {"kind": "resource-tracked", "service": "vpc",
         "path": "/v1/vpcs/bbb", "lifecycle": "x"},
    ])
    cli = _FakeClient({("DELETE", "/v1/"): _Resp(202)})
    barriers, waits = [], []
    _wire_reap(monkeypatch, cli, barriers, waits)
    issued = rs.reap_run_leftovers(p, log=lambda m: None)
    assert issued == 2
    assert barriers == [] and not waits, \
        "leaf 꼬리 버킷은 배리어/직렬 대기 없이 즉시 끝나야"


# =========================================================================== #
# 3. reconciler._leftover_report — pick-기반 (LIST 0회) + scan 폴백
# =========================================================================== #
class _NoListClient:
    def get(self, *a, **k):
        raise AssertionError("leftover report must NOT issue any LIST")


def test_leftover_report_uses_last_round_picks_without_relisting(capsys):
    recon._reset_campaign_state()
    tgw = _owned("regrtgw-drain", id="tgw-9", state="DELETING")
    client = FakeClient(lists={"/v1/transit-gateways": [tgw]})
    picked = recon._select(client, "vpc", "/v1/transit-gateways",
                           name_prefixes=("regrtgw",))
    assert picked, "sanity: 스윕 라운드가 픽을 관측했다"
    capsys.readouterr()
    recon._leftover_report(_NoListClient())   # LIST가 나가면 AssertionError
    out = capsys.readouterr().out
    assert "leftover report" in out
    assert "1 owned resource(s) STILL" in out
    assert "regrtgw-drain" in out, "생존자 이름이 리포트에 나와야"
    assert "0 extra LIST" in out
    recon._reset_campaign_state()


def test_leftover_report_falls_back_to_scan_when_no_picks(monkeypatch, capsys):
    recon._reset_campaign_state()
    import cleanup.verify_clean as vc
    calls = []
    monkeypatch.setattr(
        vc, "scan_owned",
        lambda client=None: calls.append(1) or
        [{"service": "vpc", "path": "/v1/vpcs/leftover-x"}])
    recon._leftover_report(object())
    out = capsys.readouterr().out
    assert calls == [1], "픽 정보가 비면 기존 scan_owned 폴백"
    assert "1 owned resource(s) STILL" in out and "vpc" in out


def test_last_picked_reset_and_refreshed_per_observation():
    recon._reset_campaign_state()
    assert recon._LAST_PICKED == {}
    it = _owned("regrvol-a", id="vol-a")
    client = FakeClient(lists={"/v1/volumes": [it]})
    recon._select(client, "virtualserver", "/v1/volumes",
                  name_prefixes=("regr",))
    assert recon._LAST_PICKED[("virtualserver", "/v1/volumes")] == \
        [("vol-a", "regrvol-a")]
    # 다음 관측에서 사라졌으면 픽도 갱신 (덮어쓰기)
    client.remove_from_list("/v1/volumes", lambda x: True)
    recon._CONVERGED.clear()   # 재나열 강제 (converged 스킵 우회)
    recon._select(client, "virtualserver", "/v1/volumes",
                  name_prefixes=("regr",))
    assert recon._LAST_PICKED[("virtualserver", "/v1/volumes")] == []
    recon._reset_campaign_state()


def test_leftover_report_never_weakens_ownership(capsys):
    """pick 기록은 _is_deletable 게이트 통과분만 담는다 — 미소유 아이템은
    리포트에도 등장하지 않는다 (소유권 게이트 불변의 관측 버전)."""
    recon._reset_campaign_state()
    client = FakeClient(lists={"/v1/volumes": [
        _owned("regrvol-mine", id="vol-mine"),
        {"name": "someones-volume", "id": "vol-theirs"},
    ]})
    recon._select(client, "virtualserver", "/v1/volumes",
                  name_prefixes=("regr",))
    capsys.readouterr()
    recon._leftover_report(_NoListClient())
    out = capsys.readouterr().out
    assert "regrvol-mine" in out
    assert "someones-volume" not in out and "vol-theirs" not in out
    recon._reset_campaign_state()


# =========================================================================== #
# 4. grant-inprog 라운드 간 지수 backoff (30→60→120, env 상한)
# =========================================================================== #
class _StubSettings:
    allow_destructive = True

    def require_credentials(self):
        pass


def _wire_main(monkeypatch, sleeps, rounds):
    """test_reconciler_convergence._stub_main_env와 같은 배선: 매 라운드
    genuine=0 / inprog=1 (grant-inprog 형태) + 소유 VPC 잔존(=라운드 계속
    부여, 2026-07-03 TGW 인시던트 보호 유지)."""
    def fake_sweep(client):
        recon._INPROGRESS_THIS_ROUND[0] = 1
        recon._PROGRESS_THIS_ROUND[0] = 0
        return 1
    monkeypatch.setattr(recon, "run_sweep", fake_sweep)
    monkeypatch.setattr(recon, "_owned_vpcs_present", lambda c: 1)
    monkeypatch.setattr(recon, "_leftover_report", lambda c: None)
    monkeypatch.setattr(recon.core, "settings", _StubSettings())
    monkeypatch.setattr(recon.core, "ApiClient", lambda cfg: object())
    monkeypatch.setattr(recon.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.delenv("SCP_SWEEP_NOWAIT", raising=False)
    monkeypatch.delenv("SCP_SWEEP_INPROGRESS_SLEEP_S", raising=False)
    monkeypatch.delenv("SCP_SWEEP_INPROGRESS_SLEEP_MAX_S", raising=False)
    monkeypatch.setenv("SCP_SWEEP_ROUNDS", str(rounds))


def test_grant_inprog_backoff_escalates_then_caps(monkeypatch):
    sleeps: list = []
    _wire_main(monkeypatch, sleeps, rounds=6)
    recon.main()
    # 라운드 1..5가 grant-inprog 대기 (라운드 6은 캡 도달 → 대기 없이 종료).
    assert sleeps == [30, 60, 120, 120, 120], sleeps


def test_grant_inprog_backoff_honours_env_base_and_cap(monkeypatch):
    sleeps: list = []
    _wire_main(monkeypatch, sleeps, rounds=4)
    monkeypatch.setenv("SCP_SWEEP_INPROGRESS_SLEEP_S", "10")
    monkeypatch.setenv("SCP_SWEEP_INPROGRESS_SLEEP_MAX_S", "25")
    recon.main()
    assert sleeps == [10, 20, 25], sleeps


def test_grant_inprog_leaf_drain_stop_still_no_extra_wait(monkeypatch):
    """leaf-drain 종료 정책 보존: 소유 VPC가 0이면 backoff고 뭐고 라운드를
    더 주지 않고 즉시 STOP + 리포트 (기존 정책 그대로)."""
    sleeps: list = []
    _wire_main(monkeypatch, sleeps, rounds=6)
    reports = []
    monkeypatch.setattr(recon, "_owned_vpcs_present", lambda c: 0)
    monkeypatch.setattr(recon, "_leftover_report",
                        lambda c: reports.append(1))
    recon.main()
    assert sleeps == [], "VPC 캡이 비면 어떤 라운드 대기도 없이 종료"
    assert reports == [1], "남은 leaf는 리포트로 남긴다"
