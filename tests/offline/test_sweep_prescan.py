"""스윕 병렬 프리스캔 오프라인 검증 (오너 2026-07-15: "클린업이 너무너무
느리다 — 전체 리소스 리스트 조회(병렬)하고 있는놈만 역방향 순서 생각해서
지우면 될 것 같은데").

프리스캔은 라운드 시작 시 스윕이 나열할 컬렉션 전체를 병렬 LIST해서 1회용
캐시에 담고, 각 패스의 첫 ``_list_all``이 이를 소비한다 — 직렬 나열 합계가
max(지연) 하나로 줄어든다. 삭제 순서(역방향 패스 순서)·소유권 게이트는
불변이다. FakeClient 하네스는 test_reconciler_convergence의 것을 재사용.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

import cleanup.reconciler as recon
from tests.offline.test_reconciler_convergence import FakeClient, _owned


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """test_reconciler_convergence와 동일한 오프라인 무대기 배선 — FakeClient의
    GET은 절대 404가 안 되므로 실제 _wait_gone/_wait_all_gone은 타임아웃까지
    돈다 (fixture 없이 run_sweep을 부르면 라운드가 실시간 수백 초를 태운다)."""
    monkeypatch.setattr(recon.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(recon, "_wait_gone", lambda *a, **k: True)
    monkeypatch.setattr(recon, "_wait_all_gone", lambda *a, **k: True)
    yield


def setup_function(_):
    recon._reset_campaign_state()


def test_registry_covers_every_select_literal():
    """소스의 `_select(c, "<svc>", "<path>")` / `_list_all(c, "<svc>", "<path>")`
    리터럴 컬렉션은 전부 _SWEEP_COLLECTIONS에 있어야 한다 — 신규 패스를
    레지스트리에 안 올리면 프리스캔이 그 컬렉션만 못 데우는 드리프트를 여기서
    잡는다 (빠져도 동작은 라이브 나열로 안전하지만, 낙관 최적화가 조용히
    삭아가는 것을 막는다)."""
    src = Path(recon.__file__).read_text()
    pat = re.compile(
        r'_(?:select|list_all)\(c(?:lient)?,\s*"([a-z-]+)",\s*"([^"]+)"')
    literals = {(svc, path) for svc, path in pat.findall(src)}
    registry = set(recon._SWEEP_COLLECTIONS)
    missing = literals - registry
    assert not missing, f"프리스캔 레지스트리에 없는 패스 컬렉션: {missing}"


def test_prescan_warms_cache_and_pass_consumes_without_relisting():
    img = _owned("regrimg-x", id="img-1")
    client = FakeClient(lists={"/v1/images": [img]})
    recon._prescan(client)
    n_prescan_gets = sum(1 for m, p in client.calls
                         if m == "GET" and p == "/v1/images")
    assert n_prescan_gets == 1, "프리스캔이 컬렉션당 1회 나열"
    picked = recon._select(client, "virtualserver", "/v1/images",
                           name_prefixes=("regrimg",))
    assert [it["id"] for it in picked] == ["img-1"], "캐시 경유에도 픽 동일"
    n_total_gets = sum(1 for m, p in client.calls
                       if m == "GET" and p == "/v1/images")
    assert n_total_gets == 1, "패스는 캐시를 소비 — 재나열 없음"
    # 1회용: 같은 컬렉션의 두 번째 나열(패스 중간 재나열)은 라이브로 간다
    recon._select(client, "virtualserver", "/v1/images",
                  name_prefixes=("regrimg",))
    n_after = sum(1 for m, p in client.calls
                  if m == "GET" and p == "/v1/images")
    assert n_after == 2, "캐시는 pop — 재나열은 종전대로 라이브"


def test_prescan_cache_ttl_discards_stale_entries(monkeypatch):
    img = _owned("regrimg-y", id="img-2")
    client = FakeClient(lists={"/v1/images": [img]})
    key = (id(client), "virtualserver", "/v1/images")
    recon._LIST_CACHE[key] = (time.monotonic() - recon._PRESCAN_TTL_S - 1,
                              [])   # 기한 지난 (그리고 틀린) 스냅샷
    picked = recon._select(client, "virtualserver", "/v1/images",
                           name_prefixes=("regrimg",))
    assert [it["id"] for it in picked] == ["img-2"], \
        "TTL 지난 캐시는 버리고 라이브 나열"
    assert key not in recon._LIST_CACHE, "만료 엔트리는 소비(제거)된다"


def test_prescan_disabled_by_env(monkeypatch):
    monkeypatch.setenv("SCP_SWEEP_NO_PRESCAN", "true")
    client = FakeClient(lists={"/v1/images": [_owned("regrimg-z", id="i3")]})
    recon._prescan(client)
    assert not recon._LIST_CACHE and not client.calls, \
        "opt-out이면 프리스캔은 완전 no-op"


def test_prescan_skips_converged_collections(monkeypatch):
    client = FakeClient(lists={})
    recon._CONVERGED.add(("virtualserver", "/v1/images"))
    recon._prescan(client)
    assert ("GET", "/v1/images") not in client.calls, \
        "수렴된 컬렉션은 프리스캔도 건너뛴다 (라운드 2+ 최적화와 결합)"


def test_run_sweep_results_identical_with_and_without_prescan(monkeypatch):
    """프리스캔은 순수 지연 최적화 — 같은 계정 상태에서 삭제 집합/순서가
    프리스캔 on/off와 무관해야 한다 (images→volumes 역방향 순서 포함)."""
    def _mk():
        return FakeClient(lists={
            "/v1/images": [_owned("regrimg-a", id="im-1", state="ACTIVE")],
            "/v1/volumes": [_owned("regrvol-a", id="vo-1", state="ACTIVE")],
        })

    c_on = _mk()
    recon.run_sweep(c_on)
    dels_on = [p for m, p in c_on.calls if m == "DELETE"]

    recon._reset_campaign_state()
    monkeypatch.setenv("SCP_SWEEP_NO_PRESCAN", "true")
    c_off = _mk()
    recon.run_sweep(c_off)
    dels_off = [p for m, p in c_off.calls if m == "DELETE"]

    assert dels_on == dels_off, (dels_on, dels_off)
    assert "/v1/images/im-1" in dels_on


# --------------------------------------------------------------------------- #
# 태그 인벤토리 스코프 축소 (오너 2026-07-15: listresources로 범위를 좁히고
# 부산물만 종류별 일괄 조회)
# --------------------------------------------------------------------------- #
def _inv_item(service, rtype, name, tags=None):
    return {"service": service, "resource_type": rtype, "resource_name": name,
            "id": f"id-{name}",
            "tags": tags if tags is not None else
            [{"key": "owner", "value": "apitest"}]}


def test_tag_scope_narrows_to_owned_plus_derivative_collections():
    """인벤토리가 vpc 잔존만 보이면 — vpc 컬렉션 + 부산물 컬렉션만 나열하고
    나머지(예: apigateway/cdn/queues)는 converged 마킹으로 패스째 스킵."""
    client = FakeClient(lists={
        "/v1/resources": [_inv_item("vpc", "vpc", "regrvpcx")],
        "/v1/vpcs": [_owned("regrvpcx", id="vpc-1")],
    })
    recon._prescan(client)
    listed = {p for m, p in client.calls if m == "GET"}
    assert "/v1/vpcs" in listed, "잔존이 있는 컬렉션은 나열"
    assert "/v1/log-groups" in listed, "부산물 컬렉션은 무조건 나열"
    assert "/v1/apis" not in listed and "/v1/cdns" not in listed, \
        "잔존 없는 비-부산물 컬렉션은 나열 자체를 스킵"
    assert ("apigateway", "/v1/apis") in recon._CONVERGED, \
        "스킵 컬렉션은 converged 마킹 → 패스도 스킵"


def test_tag_scope_unknown_type_falls_back_to_full_listing():
    """매핑에 없는 (service, type)의 소유 잔존이 보이면 축소를 통째로 포기 —
    미지 타입이 자기 컬렉션 스킵을 유발하면 안 된다 (SAFETY 2)."""
    client = FakeClient(lists={
        "/v1/resources": [_inv_item("newservice", "widget", "regrwidget1")],
    })
    recon._prescan(client)
    listed = {p for m, p in client.calls if m == "GET"}
    assert "/v1/apis" in listed, "미지 타입 → 전체 나열 폴백"
    assert ("apigateway", "/v1/apis") not in recon._CONVERGED


def test_tag_scope_empty_inventory_means_full_final_verification():
    """소유 0건이어도 축소하지 않는다 — 태그 미노출 파생물의 최종 확인은
    전체 나열이 해야 한다 (SAFETY 3)."""
    client = FakeClient(lists={"/v1/resources": []})
    recon._prescan(client)
    listed = {p for m, p in client.calls if m == "GET"}
    assert "/v1/apis" in listed and "/v1/vpcs" in listed


def test_tag_scope_inventory_error_falls_back(monkeypatch):
    class Err503Client(FakeClient):
        def get(self, path, service=None, **kw):
            self.calls.append(("GET", path))
            if path.startswith("/v1/resources"):
                from tests.offline.test_reconciler_convergence import _Resp
                return _Resp(503, {})
            return super().get(path, service=service, **kw)

    client = Err503Client(lists={})
    recon._prescan(client)
    listed = {p for m, p in client.calls if m == "GET"}
    assert "/v1/apis" in listed, "listresources 실패 → 전체 나열 폴백"


def test_tag_scope_opt_out_env(monkeypatch):
    monkeypatch.setenv("SCP_SWEEP_TAG_SCOPE", "false")
    client = FakeClient(lists={})
    recon._prescan(client)
    assert not any(p.startswith("/v1/resources")
                   for m, p in client.calls if m == "GET"), \
        "opt-out이면 인벤토리 호출 자체가 없다"


def test_tag_scope_stringified_tags_and_name_prefix_fallback():
    """listresources의 tags가 문자열화된 JSON이어도 파싱하고, 태그가 없어도
    regr* 이름이면 소유로 인정한다 (스윕의 이중 판정과 동일 원리)."""
    client = FakeClient(lists={
        "/v1/resources": [
            _inv_item("vpc", "subnet", "sub-x",
                      tags='[{"key": "owner", "value": "apitest"}]'),
            _inv_item("vpc", "vpc", "regrvpcy", tags=[]),
        ],
        "/v1/vpcs": [_owned("regrvpcy", id="vpc-2")],
    })
    recon._prescan(client)
    listed = {p for m, p in client.calls if m == "GET"}
    assert "/v1/vpcs" in listed and "/v1/subnets" in listed
    assert "/v1/apis" not in listed


def test_rm_ghost_report_separates_ghosts_from_real_leftovers(monkeypatch, capsys):
    """오너 2026-07-16: /v1/resources엔 남아 있는데 실자원은 없는 유령 레코드가
    있다 — 잔존과 분리해 보고하고 conformance finding으로 기록, 삭제 시도 금지."""
    from core import results as res
    recorded = []
    monkeypatch.setattr(res, "record_finding", lambda f: recorded.append(f))
    client = FakeClient(lists={
        "/v1/resources": [
            _inv_item("vpc", "vpc", "regrvpc-ghost"),      # 인덱스엔 있고
            _inv_item("vpc", "vpc", "regrvpc-real"),
        ],
        "/v1/vpcs": [_owned("regrvpc-real", id="id-regrvpc-real")],  # 실자원엔 real만
    })
    recon._rm_ghost_report(client)
    out = capsys.readouterr().out
    assert "유령 레코드 1건" in out and "regrvpc-ghost" in out
    assert "regrvpc-real" not in out.split("유령")[1].split("미확인")[0] \
        if "미확인" in out else "regrvpc-real" not in out
    assert len(recorded) == 1
    assert recorded[0].rule_id == "resourcemanager.stale-index-entry"
    assert "regrvpc-ghost" in recorded[0].detail
    assert not any(m == "DELETE" for m, p in client.calls), "유령에 삭제 시도 금지"


def test_rm_ghost_report_unknown_type_is_not_declared_ghost(monkeypatch, capsys):
    from core import results as res
    monkeypatch.setattr(res, "record_finding",
                        lambda f: (_ for _ in ()).throw(AssertionError("no finding")))
    client = FakeClient(lists={
        "/v1/resources": [_inv_item("newsvc", "widget", "regrwidget")],
    })
    recon._rm_ghost_report(client)
    out = capsys.readouterr().out
    assert "미확인 1건" in out and "유령 레코드" not in out
