"""공유 VPC 프로비저닝 게이트 (2026-07-10 run-adfd 실측).

종전: heavy 선택에서만 provision_shared → non-heavy 선택에 섞인 adopt:vpc
라이프사이클(gen-private-nat, gen-wave5-apigw-privatelink)이 'no shared VPC
and running under xdist worker'로 전부 IB-049 스킵. 이제 선택에 adopt:vpc가
하나라도 있으면 프로비저닝한다.
"""
from __future__ import annotations

from regression.scenarios.local_run import selection_needs_shared_vpc


def test_adopters_trigger_shared_vpc():
    assert selection_needs_shared_vpc(["gen-private-nat"]) is True
    assert selection_needs_shared_vpc(["gen-wave5-apigw-privatelink"]) is True


def test_pure_iam_selection_does_not():
    assert selection_needs_shared_vpc(["iam-role-full", "iam-user-full"]) is False
    assert selection_needs_shared_vpc([]) is False
