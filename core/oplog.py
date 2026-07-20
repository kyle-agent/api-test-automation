"""Operations log — persistent, cross-run workflow progress on Object Storage.

A single NEVER-DELETED S3-compatible bucket (default ``apitest-oplog-permanent``
— named so no sweep matcher can ever touch it and the intent is self-evident) on the
test account accumulates one small JSON object per workflow milestone, so a
static viewer (``dashboard/ops.html``) can show the LIVE progress of the
current run and the history of every past run — independent of GitHub.

Layout (all keys under the bucket):
  runs/<run_id>/run.json                  run manifest (sha, branch, options)
  runs/<run_id>/events/<ms>-<stage>.json  one object per milestone (unique key
                                          per event -> no write races between
                                          the parallel A/B jobs)
  runs/<run_id>/summary.json              final summary (history row)
  index.json                              [{run summary}, ...] newest-first —
                                          read-modify-written ONLY by the
                                          dashboard job (single writer at the
                                          end of a run, so no race)

SCP Object Storage is Amazon-S3 compatible (userguide "Amazon S3 활용 가이드")
and accepts the SAME access/secret key pair as the Open APIs (owner-confirmed
2026-06-11). SDK region for kr-west1 is "kr-west"; the real region is resolved
from the endpoint URL.

Credentials (owner principle 2026-07-15): the oplog bucket lives in the
CURRENT TEST ACCOUNT by default (SCP_ACCESS_KEY/SECRET_KEY) and is
auto-created on first use if missing (same contract as the logsink bucket).
SCP_OPLOG_ACCESS_KEY/SECRET_KEY — only when BOTH are set — act as an explicit
override for legacy split configurations. See ``_cfg`` for the exact priority.

Everything here is BEST-EFFORT and self-disabling: missing boto3, missing
credentials, or an unreachable endpoint prints one notice and no-ops — a
broken oplog must never fail a test run.

When APITEST_PLATFORM_URL is set, every event is ALSO mirrored to the platform
control plane (POST <url>/api/ingest/events, optional Bearer
APITEST_PLATFORM_TOKEN) so the M1 server can show a live run view without
polling the bucket (docs/PLATFORM-PLAN.md §2.5). Same fire-and-forget rule:
unset by default, failures are swallowed silently.

CLI:
  python -m core.oplog ensure                      # create bucket + CORS + ACL
  python -m core.oplog emit --stage smoke --status done [--detail '...']
  python -m core.oplog finalize --history dashboard/history.jsonl
  python -m core.oplog plan-manifest               # build+emit runs/<id>/plan.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_NOTICE_SHOWN = False


def _cfg(keys: str = "oplog"):
    """Resolve endpoint/bucket/credentials from env (None = disabled).

    ``keys`` — 자격 선택 (2026-07-15 오너 원칙 전환: "oplog도 테스트 계정에
    있는 걸 원칙으로, 없으면 최초 한 번은 만들자" — logsink와 동일 원칙):
      * "oplog"(기본) — **미러/자동수리 버킷**(apitest-oplog-permanent)용.
        우선순위:
          ① SCP_OPLOG_ACCESS_KEY/SECRET_KEY가 **둘 다** 설정돼 있으면 명시적
             오버라이드 — 레거시 분리 구성 하위호환(예: 구 계정 미러를 한시적
             으로 유지). 한쪽만 설정된 경우 키쌍이 갈라져 서명 오류가 되므로
             오버라이드를 무시하고 ②로 간다.
          ② 기본값: SCP_ACCESS_KEY/SECRET_KEY (현재 테스트 계정). 새 계정에
             버킷이 없으면 최초 사용 시 1회 자동 ensure(_ensure_oplog_once,
             best-effort — logsink 규약과 동일).
      * "test": 항상 SCP_ACCESS_KEY/SECRET_KEY만 — **logsink 버킷**
        (apitest-logsink)처럼 시나리오가 '테스트 계정 안에서' 참조하는 고정
        픽스처용. 오버라이드가 다른 계정을 가리켜도 절대 따라가지 않는다
        (다른 계정에 ensure되는 오배치 방지).
    """
    bucket = os.getenv("SCP_OPLOG_BUCKET", "apitest-oplog-permanent").strip()
    o_access = (os.getenv("SCP_OPLOG_ACCESS_KEY") or "").strip()
    o_secret = (os.getenv("SCP_OPLOG_SECRET_KEY") or "").strip()
    if keys != "test" and o_access and o_secret:
        access, secret = o_access, o_secret          # ① 명시적 오버라이드(쌍)
    else:
        access = (os.getenv("SCP_ACCESS_KEY") or "").strip()   # ② 테스트 계정
        secret = (os.getenv("SCP_SECRET_KEY") or "").strip()
    endpoint = os.getenv("SCP_OPLOG_S3_ENDPOINT", "").strip()
    if not endpoint:
        # per-service host convention; override via SCP_OPLOG_S3_ENDPOINT with
        # the Public URL from the Object Storage detail page if this guess is
        # wrong for the account.
        region = os.getenv("SCP_REGION", "kr-west1").strip()
        env = os.getenv("SCP_ENV", "e").strip()
        # live-verified 2026-06-11: the S3 endpoint host is object-store.<region>.<env>
        endpoint = f"https://object-store.{region}.{env}.samsungsdscloud.com"
    # SDK region: kr-west1 -> kr-west, kr-south1/2/3 -> kr-south (userguide)
    region = os.getenv("SCP_REGION", "kr-west1").strip()
    sdk_region = "kr-south" if region.startswith("kr-south") else "kr-west"
    if not (bucket and access and secret):
        return None
    return {"bucket": bucket, "endpoint": endpoint, "region": sdk_region,
            "access": access, "secret": secret}


_OPLOG_ENSURED = [False]  # 프로세스당 1회 auto-ensure 가드 (mutable holder)


def _ensure_oplog_once(c, cfg) -> None:
    """oplog 버킷 최초-1회 자동 ensure — ensure_logsink()와 동일 규약.

    2026-07-15 원칙 전환으로 oplog 버킷도 테스트 계정이 기본이라, 새 계정
    첫 사용 시 버킷이 없다. OBS 버킷 생성은 S3 프로토콜(boto3) 전용(SCP REST
    카탈로그에 없음 — logsink에서 확인된 제약과 동일)이므로 여기서 만든다.
    존재하면 no-op(head 1회, 프로세스당 최대 1번); 없으면 ensure_bucket()
    (create + CORS + public-read — 정적 ops 뷰어 계약 포함)을 재사용. 모든
    실패는 삼킨다 — 깨진 oplog가 런을 죽여서는 절대 안 된다."""
    if _OPLOG_ENSURED[0]:
        return
    _OPLOG_ENSURED[0] = True   # 실패해도 재시도 폭주 방지: 프로세스당 1회만
    try:
        c.head_bucket(Bucket=cfg["bucket"])
        return                                   # 있으면 무음 no-op (멱등)
    except Exception:
        pass
    try:
        ensure_bucket()
    except Exception as exc:  # noqa: BLE001 — best-effort bootstrap
        print(f"[oplog] auto-ensure failed (continuing): {exc}")


def _client(keys: str = "oplog"):
    global _NOTICE_SHOWN
    cfg = _cfg(keys)
    if not cfg:
        if not _NOTICE_SHOWN:
            print("[oplog] disabled (no credentials/bucket configured)")
            _NOTICE_SHOWN = True
        return None, None
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        if not _NOTICE_SHOWN:
            print("[oplog] disabled (boto3 not installed)")
            _NOTICE_SHOWN = True
        return None, None
    c = boto3.client(
        "s3", endpoint_url=cfg["endpoint"], region_name=cfg["region"],
        aws_access_key_id=cfg["access"], aws_secret_access_key=cfg["secret"],
        config=Config(connect_timeout=10, read_timeout=20,
                      retries={"max_attempts": 2}))
    if keys == "oplog":
        # oplog를 처음 쓰는 모든 경로(emit/put_text/snapshot 미러 업로드 등)가
        # _client를 지나므로 여기서 best-effort 부트스트랩 — 실패해도 계속.
        try:
            _ensure_oplog_once(c, cfg)
        except Exception:  # noqa: BLE001
            pass
    return c, cfg


def _run_id() -> str:
    return os.getenv("APITEST_RUN_ID") or os.getenv("GITHUB_RUN_ID") or "local"


def _post_platform(kind: str, payload: dict) -> None:
    """Mirror one event to the platform control plane (fire-and-forget).

    Disabled unless APITEST_PLATFORM_URL is set; a slow/unreachable server
    must never stall a test run, so the timeout is short and every failure
    is swallowed. Env is read per call so jobs can set it via $GITHUB_ENV."""
    url = os.getenv("APITEST_PLATFORM_URL", "").strip()
    if not url:
        return
    try:
        import urllib.request
        body = json.dumps({"kind": kind, "run_id": _run_id(), **payload},
                          ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"}
        token = os.getenv("APITEST_PLATFORM_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url.rstrip("/") + "/api/ingest/events",
                                     data=body, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def _put(c, cfg, key, payload: dict) -> bool:
    body = json.dumps(payload, ensure_ascii=False).encode()
    # public-read PER OBJECT: live test 2026-06-11 showed bucket-level
    # public-read grants anonymous LIST but object GETs still 403 without an
    # object ACL (RGW semantics). Fall back to a private put if ACL is rejected.
    try:
        c.put_object(Bucket=cfg["bucket"], Key=key, Body=body,
                     ContentType="application/json", ACL="public-read")
        return True
    except Exception:
        pass
    try:
        c.put_object(Bucket=cfg["bucket"], Key=key, Body=body,
                     ContentType="application/json")
        return True
    except Exception as exc:
        print(f"[oplog] put {key} failed: {exc}")
        return False


def put_text(key: str, text: str, content_type: str = "text/plain") -> bool:
    """임의 텍스트 객체 업로드 (run-end 자동수리 루프의 events 미러용).
    _put과 같은 ACL 폴백; 실패는 조용히 False (런을 절대 방해하지 않음)."""
    c, cfg = _client()
    if not c:
        return False
    body = text.encode("utf-8")
    for kw in ({"ACL": "public-read"}, {}):
        try:
            c.put_object(Bucket=cfg["bucket"], Key=key, Body=body,
                         ContentType=content_type, **kw)
            return True
        except Exception:
            continue
    print(f"[oplog] put_text {key} failed")
    return False


IMAGE_ASSET_KEY = "assets/regr-minimal.qcow2"
_IMAGE_ASSET_URL: list = [None]   # 프로세스당 1회 계산 캐시


def ensure_image_asset() -> str | None:
    """createimage/importimage용 상비 qcow2 자산을 보장하고 public URL을 반환.

    오너 2026-07-15: "테스트용 이미지는 git에 넣어두고, 이것도 최초 1회는
    obj(버킷) 만들고 넣는 걸로." 원본은 repo의 ``assets/regr-minimal.qcow2``
    (수제 qcow2 v3 헤더+refcount+L1, 262,144B — tests/offline이 구조 검증).
    최초 1회: 버킷 ensure(아래 head/create) → head_object → 없으면 git
    원본을 public-read로 업로드(put_text와 같은 ACL 폴백).

    자격/URL 형식 (2026-07-16 라이브 확정, soft-4xx 원인규명 캠페인):
    **항상 테스트 계정 키(keys="test")** — createimage는 URL의 계정이
    **호출자 자신의 계정일 때만** 통과한다. run a690에서 SCP_OPLOG_* 오버라이드
    (구 계정 ec11538a…)의 버킷 URL로 400 Image.InvalidObjectStorageUrl,
    같은 객체를 신 계정(81eccb26…) 버킷에 두고 호출하면 200(queued, 즉시
    DELETE 204)을 실측. URL 형식 자체는 RGW tenant-path
    (``<account_id>:<bucket>``, 콜론)가 정답 — 문서 request_example의 슬래시
    형식(``/<account>/<bucket>/…``)은 RGW anon GET 400 + createimage 400
    둘 다 실측 (문서 예시가 이 환경과 불일치, conformance 후보). account_id는
    SCP_ACCOUNT_ID env 우선, 없으면 get_bucket_acl Owner ID에서 유도.
    모든 실패는 None (best-effort — 해당 시나리오 스텝이 4xx로 표면화)."""
    if _IMAGE_ASSET_URL[0]:
        return _IMAGE_ASSET_URL[0]
    c, cfg = _client(keys="test")
    if not c:
        return None
    try:
        # keys="test"는 _ensure_oplog_once를 타지 않으므로 여기서 직접 ensure
        # (신 계정 첫 사용 시 버킷 부재 — 이름은 계정 간 충돌하지 않는다).
        c.head_bucket(Bucket=cfg["bucket"])
    except Exception:
        try:
            c.create_bucket(Bucket=cfg["bucket"])
        except Exception as exc:  # noqa: BLE001 — best-effort
            print(f"[oplog] image asset bucket ensure failed ({exc})")
            return None
    try:
        c.head_object(Bucket=cfg["bucket"], Key=IMAGE_ASSET_KEY)
    except Exception:
        src = Path(__file__).resolve().parent.parent / IMAGE_ASSET_KEY
        try:
            body = src.read_bytes()
        except OSError as exc:
            print(f"[oplog] image asset source missing ({exc})")
            return None
        for kw in ({"ACL": "public-read"}, {}):
            try:
                c.put_object(Bucket=cfg["bucket"], Key=IMAGE_ASSET_KEY,
                             Body=body,
                             ContentType="application/octet-stream", **kw)
                break
            except Exception:
                continue
        else:
            print(f"[oplog] image asset upload failed ({IMAGE_ASSET_KEY})")
            return None
    account = (os.getenv("SCP_ACCOUNT_ID") or "").strip()
    if not account:
        try:
            owner = c.get_bucket_acl(Bucket=cfg["bucket"])["Owner"]["ID"]
            account = str(owner).split("$")[0].strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[oplog] image asset: account id unresolved ({exc}) — "
                  "SCP_ACCOUNT_ID로 지정 가능")
            return None
    if not account:
        return None
    url = f"{cfg['endpoint'].rstrip('/')}/{account}:{cfg['bucket']}/{IMAGE_ASSET_KEY}"
    _IMAGE_ASSET_URL[0] = url
    return url


LOGSINK_BUCKET = "apitest-logsink"  # shared pre-existing OBS sink for
# network-logging storages and loggingaudit trails (owner 2026-06-13: both
# need a pre-defined Object Storage bucket). Plain private bucket — no CORS/
# public ACL, never swept.


def ensure_logsink() -> bool:
    # logsink는 테스트-계정 픽스처 — 항상 테스트 키로. SCP_OPLOG_* 오버라이드가
    # 다른 계정을 가리키는 구성에서도 절대 따라가지 않는다 (오배치 방지,
    # 2026-07-15).
    c, cfg = _client(keys="test")
    if not c:
        return False
    try:
        c.head_bucket(Bucket=LOGSINK_BUCKET)
        print(f"[oplog] logsink bucket {LOGSINK_BUCKET} exists")
        return True
    except Exception:
        pass
    try:
        c.create_bucket(Bucket=LOGSINK_BUCKET)
        print(f"[oplog] logsink bucket {LOGSINK_BUCKET} created (PERSISTENT)")
        return True
    except Exception as exc:
        print(f"[oplog] logsink create_bucket failed: {exc}")
        return False


def ensure_bucket() -> bool:
    """Create the bucket if missing; apply CORS + public-read so the static
    ops viewer (GitHub Pages) can fetch/list it from the browser. Each step is
    independent best-effort (SCP may reject some ACL/CORS shapes)."""
    c, cfg = _client()
    if not c:
        return False
    try:
        c.head_bucket(Bucket=cfg["bucket"])
        print(f"[oplog] bucket {cfg['bucket']} exists")
    except Exception:
        try:
            c.create_bucket(Bucket=cfg["bucket"])
            print(f"[oplog] bucket {cfg['bucket']} created (PERSISTENT — never swept)")
        except Exception as exc:
            print(f"[oplog] create_bucket failed: {exc}")
            return False
    try:
        c.put_bucket_cors(Bucket=cfg["bucket"], CORSConfiguration={
            "CORSRules": [{"AllowedMethods": ["GET", "HEAD"],
                           "AllowedOrigins": ["*"],
                           "AllowedHeaders": ["*"], "MaxAgeSeconds": 300}]})
    except Exception as exc:
        print(f"[oplog] put_bucket_cors failed (viewer may need a proxy): {exc}")
    try:
        c.put_bucket_acl(Bucket=cfg["bucket"], ACL="public-read")
    except Exception as exc:
        print(f"[oplog] put_bucket_acl public-read failed (viewer reads may 403): {exc}")
    return True


def emit(stage: str, status: str, detail: str = "", job: str = "") -> bool:
    """Write one milestone event (unique key — race-free across jobs)."""
    rid = _run_id()
    now_ms = int(time.time() * 1000)
    job = job or os.getenv("GITHUB_JOB", "")
    ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "run_id": rid, "job": job, "stage": stage, "status": status,
          "detail": detail[:2000]}
    # platform mirror is independent of the S3 oplog being configured
    _post_platform("milestone", ev)
    c, cfg = _client()
    if not c:
        return False
    ok = _put(c, cfg, f"runs/{rid}/events/{now_ms}-{stage}.json", ev)
    # first emit of a run also drops the manifest (idempotent overwrite)
    manifest = {"run_id": rid,
                "sha": os.getenv("GITHUB_SHA", "")[:7],
                "branch": os.getenv("GITHUB_REF_NAME", ""),
                "event": os.getenv("GITHUB_EVENT_NAME", ""),
                "url": (f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/"
                        f"{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{rid}"),
                "started": ev["ts"]}
    if stage == "run-start":
        _put(c, cfg, f"runs/{rid}/run.json", manifest)
    return ok


# ---------------------------------------------------------------------------
# Resource-level events (engine hooks) — BUFFERED so a heavy run's hundreds of
# create/delete events become a handful of batch objects, not per-event PUTs.
# Keys are unique per process (pid+ms) -> race-free across xdist workers and
# the parallel A/B jobs. The ops viewer folds the batches into a per-resource
# created→testing→deleted timeline (간트).
# ---------------------------------------------------------------------------
_RES_BUF: list = []
_RES_FIRST_TS = [0.0]
_RES_SEQ = [0]
# Flush IMMEDIATELY by default (one object per event): a run only produces a
# few hundred resource events, and buffering hid events during long polls
# (a 30-min cluster wait emits nothing, so the age check never ran and the
# viewer saw the create up to 30min late). Raise via env if PUT volume ever
# becomes a concern.
_FLUSH_EVERY = int(os.getenv("SCP_OPLOG_FLUSH_EVERY", "1"))
_FLUSH_MAX_AGE = 30.0      # seconds


def _kind_of(path: str) -> str:
    """'/v1/vpcs/{id}' -> 'vpcs'; '/v1/subnets' -> 'subnets' (raw segment —
    the viewer prettifies; service qualifies colliding roots like clusters)."""
    segs = [s for s in (path or "").split("?")[0].split("/") if s]
    return segs[1] if len(segs) > 1 else (segs[0] if segs else "")


def emit_resource(action: str, *, path: str = "", service: str = "",
                  name: str = "", res_id: str = "", lifecycle: str = "",
                  status: str = "", parent: str = "") -> None:
    """Buffer one resource/lifecycle event (best-effort, never raises)."""
    try:
        if _cfg() is None and not os.getenv("APITEST_PLATFORM_URL", "").strip():
            return
        # delete events carry the live path (/v1/vpcs/<id>) — recover the id so
        # the viewer can pair created→deleted bars without guessing.
        if not res_id and path:
            last = [s for s in path.split("?")[0].split("/") if s][-1:]
            if last and "{" not in last[0] and last[0] != _kind_of(path):
                res_id = last[0]
        now = time.time()
        _RES_BUF.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "t": int(now * 1000), "action": action, "kind": _kind_of(path),
            "service": service or "", "name": str(name or "")[:120],
            "res_id": str(res_id or "")[:80], "lifecycle": lifecycle or "",
            "status": str(status or "")[:40], "parent": str(parent or "")[:80]})
        if not _RES_FIRST_TS[0]:
            _RES_FIRST_TS[0] = now
        if (len(_RES_BUF) >= _FLUSH_EVERY
                or now - _RES_FIRST_TS[0] >= _FLUSH_MAX_AGE):
            flush_resources()
    except Exception:
        pass


def flush_resources() -> None:
    """PUT the buffered events as one batch object (best-effort)."""
    global _RES_BUF
    if not _RES_BUF:
        return
    try:
        batch, _RES_BUF = _RES_BUF, []
        _RES_FIRST_TS[0] = 0.0
        # platform mirror is independent of the S3 oplog being configured
        _post_platform("resources", {"events": batch})
        c, cfg = _client()
        if not c:
            return
        _RES_SEQ[0] += 1
        # ms+pid alone collides when two flushes land in the same millisecond
        # (caught by the offline test) — the per-process sequence disambiguates.
        key = (f"runs/{_run_id()}/res/{int(time.time()*1000)}"
               f"-{os.getpid()}-{_RES_SEQ[0]}.json")
        _put(c, cfg, key, {"events": batch})
    except Exception:
        _RES_BUF = []


import atexit
atexit.register(flush_resources)


def finalize(history_path: str = "dashboard/history.jsonl") -> bool:
    """Called once by the dashboard job (single writer): write summary.json for
    this run and fold it into the newest-first index.json (kept ≤ 200 rows)."""
    c, cfg = _client()
    if not c:
        return False
    rid = _run_id()
    row = {}
    try:
        with open(history_path) as fh:
            lines = [l for l in fh if l.strip()]
        if lines:
            row = json.loads(lines[-1])
    except (OSError, ValueError):
        pass
    summary = {"run_id": rid, "sha": os.getenv("GITHUB_SHA", "")[:7],
               "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "history": row}
    _put(c, cfg, f"runs/{rid}/summary.json", summary)
    index = []
    try:
        obj = c.get_object(Bucket=cfg["bucket"], Key="index.json")
        index = json.loads(obj["Body"].read())
        if not isinstance(index, list):
            index = []
    except Exception:
        pass
    index = [summary] + [r for r in index if r.get("run_id") != rid]
    return _put(c, cfg, "index.json", index[:200])


# ---------------------------------------------------------------------------
# Plan-manifest (M6d) — the INTENDED dependency chain, uploaded ONCE at run
# start as runs/<run_id>/plan.json (parallel to runs/<id>/res/*.json). The ops
# viewer pre-draws plan.order as grey placeholders and lights them up as the
# matching `created` resource events arrive (docs/archive/M6-DESIGN.md §C).
# ---------------------------------------------------------------------------
import re as _re


def _plan_kind_of(endpoint: str) -> str:
    """First path segment after /v1 of a 'METHOD /path' endpoint — same logic
    as dashboard/gen_dep_map.py kind_of (replicated to avoid a dashboard import
    from core)."""
    path = (endpoint or "").partition(" ")[2]
    segs = [s for s in path.split("?")[0].split("/") if s]
    return segs[1] if len(segs) > 1 else (segs[0] if segs else "")


def _recover_targets(lifecycle: dict, model: dict) -> list:
    """Recover the original compose targets from a composed lifecycle's steps.

    The loader hands us lifecycle JSON (steps), not the compose targets. A
    create-<node> step names a node; strip the prefix and keep it if it is a
    real model node. Some node names legitimately end in a digit
    (``epas-engine-version-16``), so try the full name first and only strip a
    trailing ``-<N>`` instance suffix if the full name is not a model node.
    Sub-resource steps that resolve to neither (apigateway ``create-method``)
    are dropped — plan()'s closure rebuilds everything else."""
    out: list = []
    seen: set = set()
    for s in lifecycle.get("steps") or []:
        name = s.get("name", "")
        if not name.startswith("create-"):
            continue
        node = name[len("create-"):]
        cand = None
        if node in model:
            cand = node
        else:
            stripped = _re.sub(r"-\d+$", "", node)
            if stripped in model:
                cand = stripped
        if cand and cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


def build_plan_manifest() -> dict:
    """Serialize composer.plan() for every ENABLED composed lifecycle.

    Composed lifecycles are those whose id starts with ``gen-`` or ``bundle-``
    (loader.load_lifecycles()). For each we recover the compose targets from
    the create-<node> steps and call plan(). A lifecycle that yields no
    recoverable target or whose plan() raises is recorded as ``{id, error}``
    — never fatal. Returns a serializable dict (no S3 dependency)."""
    from regression.scenarios.loader import load_lifecycles
    from regression.scenarios import composer

    model = composer.load_model()
    entries: list = []
    for lc in load_lifecycles():
        lid = str(lc.get("id", ""))
        if not (lid.startswith("gen-") or lid.startswith("bundle-")):
            continue
        if not lc.get("enabled"):
            continue
        targets = _recover_targets(lc, model)
        if not targets:
            entries.append({"id": lid, "error": "no recoverable compose targets"})
            continue
        try:
            p = composer.plan(targets, model=model)
        except Exception as exc:                      # gated/invalid — skip
            entries.append({"id": lid, "error": f"{type(exc).__name__}: {exc}"})
            continue
        kinds = {}
        for node in p.get("order", []):
            base = node.partition("#")[0]             # drop #N instance suffix
            ep = ((model.get(base) or {}).get("create") or {}).get("endpoint") or ""
            k = _plan_kind_of(ep)
            if k:
                kinds[node] = k
        entries.append({
            "id": lid,
            "service": lc.get("service", ""),
            "order": p.get("order", []),
            "teardown": p.get("teardown", []),
            "dedup": p.get("dedup", {}),
            "peak_quota": p.get("peak_quota", {}),
            "kinds": kinds,
        })
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lifecycles": entries}


def emit_plan(plan_doc: dict) -> bool:
    """PUT the plan-manifest to runs/<run_id>/plan.json (single object, not
    batched), public-read, best-effort. Also mirrors to the platform control
    plane on a "plan" channel when APITEST_PLATFORM_URL is set. Never raises;
    returns False when the oplog is disabled (no creds) or the PUT fails."""
    try:
        # platform mirror is independent of the S3 oplog being configured
        _post_platform("plan", {"plan": plan_doc})
        c, cfg = _client()
        if not c:
            return False
        return _put(c, cfg, f"runs/{_run_id()}/plan.json", plan_doc)
    except Exception:
        return False


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="workflow oplog -> object storage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ensure")
    sub.add_parser("ensure-logsink")
    em = sub.add_parser("emit")
    em.add_argument("--stage", required=True)
    em.add_argument("--status", required=True)
    em.add_argument("--detail", default="")
    em.add_argument("--job", default="")
    fin = sub.add_parser("finalize")
    fin.add_argument("--history", default="dashboard/history.jsonl")
    sub.add_parser("plan-manifest")
    a = ap.parse_args(argv)
    if a.cmd == "ensure":
        ensure_bucket()
    elif a.cmd == "ensure-logsink":
        ensure_logsink()
    elif a.cmd == "emit":
        emit(a.stage, a.status, a.detail, a.job)
    elif a.cmd == "finalize":
        finalize(a.history)
    elif a.cmd == "plan-manifest":
        doc = build_plan_manifest()
        ok = emit_plan(doc)
        n = len(doc.get("lifecycles", []))
        if ok:
            print(f"[oplog] plan-manifest emitted runs/{_run_id()}/plan.json "
                  f"({n} lifecycles)")
        else:
            # disabled-or-failed: still print the manifest so a local/dry run
            # is useful (oplog already printed its '[oplog] disabled' notice)
            print(f"[oplog] plan-manifest NOT uploaded ({n} lifecycles built)")
            print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0  # never fail the calling step


if __name__ == "__main__":
    sys.exit(main())
