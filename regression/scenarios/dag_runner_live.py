"""dag_runner_live — the LIVE adapters that wire the credential-free
``dag_runner`` orchestration core (ADR 1.0-c) onto the real CRUD engine
(``regression.scenarios.engine``) and the real shared-VPC provisioner
(``regression.scenarios.shared_infra`` / ``engine.provision_shared_vpc``).

``dag_runner`` is execution-agnostic: it drives a ``dag_planner.Plan`` given an
``executor`` (run ONE lifecycle by id -> LifecycleOutcome) and an optional
``provisioner`` (stand the shared roots up before the waves / tear them down
after). This module supplies BOTH, built by :func:`build`.

Why this lives in its own module (not in ``dag_runner``): everything here can
touch credentials and mutate live cloud resources, so ``dag_runner`` imports it
*lazily* on its live path only — its ``--dry-run`` and the offline unit tests
never import this file and so never need creds.

Safety (CLAUDE.md Hard Rule 1): :func:`build` is the live path. It REFUSES to
construct the adapters unless ``SCP_ALLOW_MUTATIONS=true`` (a deliberate opt-in,
never flipped "to make a test pass"). Merely *importing* this module is always
safe (no client is built, no gate is read at import time), so
``python -c "from regression.scenarios import dag_runner_live"`` works without
creds — matching ``shared_infra``'s import-safe contract.

Thread-safety decision (executor runs concurrently within a wave):
``core.http_client.ApiClient`` wraps a single ``requests.Session``. A Session's
connection pool is safe to share, but a Session is NOT documented thread-safe
for concurrent ``request()`` calls (shared cookie jar / per-redirect state), and
``run_plan`` fans the executor across a ``ThreadPoolExecutor``. So this adapter
builds a FRESH ``ApiClient`` per executor call (per lifecycle / per thread) and
shares ONLY the immutable, frozen ``Settings`` (``cfg``) across calls. The
``Budget`` and ``ResourceRegistry`` are likewise per-call (the engine already
expects a per-lifecycle registry; ``ResourceRegistry`` even shards its manifest
file per id-stamped path). Nothing mutable is shared between threads.
"""
from __future__ import annotations

import os
import threading
import time

from regression.scenarios import dag_planner, engine
from regression.scenarios.dag_runner import LifecycleOutcome


# Index the engine's lifecycle list by id once, for O(1) executor lookup. The
# engine loads LIFECYCLES at import; we mirror that single source of truth.
_LIFECYCLE_BY_ID: dict[str, dict] = {lc["id"]: lc for lc in engine.LIFECYCLES}


def _require_mutation_gate(what: str) -> None:
    """Refuse the live path unless the mutation safety gate is explicitly set.

    Names the missing gate so the operator knows exactly which deliberate opt-in
    is absent (CLAUDE.md Hard Rule 1 — gates are never flipped implicitly)."""
    cfg = engine_settings()
    if not getattr(cfg, "allow_mutations", False):
        raise RuntimeError(
            f"dag_runner_live: refusing to {what} — SCP_ALLOW_MUTATIONS is not "
            f"true. The live DAG runner stands up / runs real cloud resources; "
            f"set SCP_ALLOW_MUTATIONS=true (and SCP_ALLOW_DESTRUCTIVE=true for "
            f"teardown, SCP_RUN_HEAVY=true for heavy lifecycles) to opt in. "
            f"This gate is a deliberate safety opt-in and must never be flipped "
            f"just to make a run proceed (CLAUDE.md Hard Rule 1).")


def engine_settings():
    """The shared, frozen Settings instance (one per process). Re-read live so a
    test that toggles SCP_ALLOW_MUTATIONS in-process sees the current value."""
    from core.config import Settings
    return Settings()


def _build_client(cfg, pool_size: int | None = None):
    """Build a live ApiClient bound to the shared cfg. When ``pool_size`` is given,
    size the underlying urllib3 connection pool to it so a SHARED client can serve
    that many concurrent threads without exhausting connections (the default
    requests pool is 10). Credentials are required only here.

    Connection REUSE is the lever against the transparent egress proxy's
    ``upstream connect error … connection timeout`` 503s (2026-06-20 finding): every
    NEW connection through the proxy is a fresh upstream connect that can fail under
    burst. urllib3 caches ``pool_connections`` HOST pools (LRU); we hit ~60 SCP
    service hosts, so a small value evicts host pools and a re-hit host REOPENS a
    connection. Keeping a warm pool PER host (pool_connections >> #hosts) + headroom
    per host (pool_maxsize) maximises reuse and cuts the proxy's cold upstream
    connects. Both env-tunable (SCP_POOL_CONNECTIONS / SCP_POOL_MAXSIZE)."""
    from core.http_client import ApiClient
    cfg.require_credentials()
    client = ApiClient(cfg)
    if pool_size:
        import os
        from requests.adapters import HTTPAdapter
        per_host = max(int(pool_size), 10)
        # keep ALL ~60 service host pools warm (default 96 >> #hosts) so a re-hit
        # host reuses its connection instead of a fresh proxy upstream connect.
        pool_connections = max(int(os.environ.get("SCP_POOL_CONNECTIONS", "96")), per_host)
        pool_maxsize = max(int(os.environ.get("SCP_POOL_MAXSIZE", str(per_host * 2))), per_host)
        adapter = HTTPAdapter(pool_connections=pool_connections, pool_maxsize=pool_maxsize)
        client.session.mount("http://", adapter)
        client.session.mount("https://", adapter)
    return client


def _shared_ctx_from_env() -> dict:
    """Build the shared-VPC ctx the engine adopts, mirroring the pytest
    ``shared_vpc`` fixture's ENV-adoption mode (conftest.py): when
    ``SCP_SHARED_VPC_ID`` (+ optional SUBNET / DB_SUBNET) are present in the
    environment, every executor call adopts the SAME already-live ids instead of
    self-creating. Absent -> ``{}`` and the lifecycle self-creates (or, per the
    engine's IB-049 guard, skips) exactly as before.

    This deliberately reads ENV rather than re-calling ``provision_shared_vpc``:
    the Provisioner below stands the roots up ONCE and publishes their ids into
    ``os.environ``, so reading the env here is the cross-call adoption channel —
    the same channel ``engine.provision_shared_vpc`` uses for xdist workers."""
    ctx: dict[str, str] = {}
    vpc = os.environ.get(engine._ENV_SHARED_VPC, "").strip()
    if vpc:
        ctx["shared_vpc_id"] = vpc
    sub = os.environ.get(engine._ENV_SHARED_SUBNET, "").strip()
    if sub:
        ctx["shared_subnet_id"] = sub
    db_sub = os.environ.get(engine._ENV_SHARED_DB_SUBNET, "").strip()
    if db_sub:
        ctx["shared_db_subnet_id"] = db_sub
    return ctx


class SharedInfraProvisioner:
    """Provisioner adapter (dag_runner.Provisioner protocol) over the real shared
    VPC+subnets.

    ``provision()`` stands the shared roots up IFF the plan needs any
    (``plan.shared_roots`` non-empty) AND publishes the resulting ids into
    ``os.environ`` (SCP_SHARED_VPC_ID / SCP_SHARED_SUBNET_ID /
    SCP_SHARED_DB_SUBNET_ID) so every concurrent executor call adopts the SAME
    live infra via :func:`_shared_ctx_from_env`. ``teardown()`` deletes them
    (subnets before VPC). When ``plan.shared_roots`` is empty BOTH are no-ops.

    Safety: provisioning is gated on SCP_ALLOW_MUTATIONS (checked in
    :func:`build` before this is even constructed); teardown additionally honors
    SCP_ALLOW_DESTRUCTIVE (the underlying ``engine.provision_shared_vpc``
    teardown / DELETE calls are themselves gate-guarded by the client)."""

    def __init__(self, plan: dag_planner.Plan, cfg):
        self._plan = plan
        self._cfg = cfg
        self._teardown_fn = None
        self._provisioned_keys: list[str] = []

    @property
    def _needs_shared(self) -> bool:
        return bool(self._plan.shared_roots)

    def provision(self) -> None:
        if not self._needs_shared:
            return  # no shared roots in this leaf set — nothing to stand up
        # If the ids are ALREADY in the env (e.g. provisioned out-of-band by
        # shared_infra --provision), adopt them and own no teardown — exactly the
        # env-adoption branch the conftest fixture and provision_shared_vpc take.
        if os.environ.get(engine._ENV_SHARED_VPC, "").strip():
            self._teardown_fn = lambda: None
            return
        client = _build_client(self._cfg)
        shared_ctx, teardown = engine.provision_shared_vpc(client, self._cfg)
        self._teardown_fn = teardown
        # Publish the freshly-created ids so executor calls adopt them. Mirrors
        # shared_infra --provision printing SCP_SHARED_*= for $GITHUB_ENV, but
        # here we set the process env directly (in-process scheduler).
        vpc_id = shared_ctx.get("shared_vpc_id")
        subnet_id = shared_ctx.get("shared_subnet_id")
        db_subnet_id = shared_ctx.get("shared_db_subnet_id")
        if vpc_id:
            os.environ[engine._ENV_SHARED_VPC] = str(vpc_id)
            self._provisioned_keys.append(engine._ENV_SHARED_VPC)
        if subnet_id:
            os.environ[engine._ENV_SHARED_SUBNET] = str(subnet_id)
            self._provisioned_keys.append(engine._ENV_SHARED_SUBNET)
        if db_subnet_id:
            os.environ[engine._ENV_SHARED_DB_SUBNET] = str(db_subnet_id)
            self._provisioned_keys.append(engine._ENV_SHARED_DB_SUBNET)
        if not vpc_id:
            # Provision produced nothing (cap loss / async create without id):
            # leave the env clean so adopters self-create / IB-049-skip rather
            # than adopt a phantom id.
            print("dag_runner_live: shared provision returned no VPC id; "
                  "adopters will self-create / skip (no SCP_SHARED_* published)")

    def teardown(self) -> None:
        if not self._needs_shared:
            return
        try:
            if self._teardown_fn is not None:
                self._teardown_fn()
        finally:
            # Remove only the env keys WE published, so a subsequent run/plan in
            # the same process doesn't adopt torn-down ids.
            for k in self._provisioned_keys:
                os.environ.pop(k, None)
            self._provisioned_keys = []


class _SharedBudget:
    """ONE process-wide, thread-safe Budget shared by every concurrent executor
    thread, so the engine's per-create reservations of CAPPED kinds (vpc,
    private-dns, …) COORDINATE across lifecycles instead of each thread seeing its
    own empty budget. An over-cap create then skips environmentally (reserve ->
    False) rather than erroring with e.g. scp-network.private-dns.max-count-exceed.
    Delegates everything else to the wrapped Budget under the lock."""

    def __init__(self):
        from core import budgets
        object.__setattr__(self, "_b", budgets.Budget())
        object.__setattr__(self, "_lock", __import__("threading").Lock())

    def reserve(self, kind, n: int = 1) -> bool:
        with self._lock:
            return self._b.reserve(kind, n)

    def release(self, kind, n: int = 1) -> None:
        with self._lock:
            self._b.release(kind, n)

    def available(self, kind) -> int:
        with self._lock:
            return self._b.available(kind)

    def __getattr__(self, name):  # limits, etc.
        return getattr(object.__getattribute__(self, "_b"), name)


class AdaptiveLimiter:
    """AIMD concurrency limiter that self-tunes to the backend's sustainable level.

    Every ``interval`` seconds: if any NEW transient gateway response (502/503/504,
    via core.http_client.retry_status_count) appeared, MULTIPLICATIVELY decrease the
    limit (halve, floor ``lo``); otherwise ADDITIVELY probe up (+1, ceil ``hi``).
    Lifecycles acquire a slot before running, so live concurrency == the current
    limit and converges to the max the gateway tolerates — which is also how we
    *find* the right concurrency (watch where ``limit`` settles)."""

    def __init__(self, start, lo, hi, err_source, interval=20.0):
        self._limit = float(max(lo, min(hi, start)))
        self._lo, self._hi = lo, hi
        self._active = 0
        self._cv = threading.Condition()
        self._errs = err_source
        self._last_err = err_source()
        self._last_adj = time.monotonic()
        self._interval = interval
        self.history = []   # (t, limit, active, err_delta) for observability

    def _adjust(self):   # caller holds self._cv
        now = time.monotonic()
        if now - self._last_adj < self._interval:
            return
        cur = self._errs()
        delta = cur - self._last_err
        old = self._limit
        if delta > 0:
            self._limit = max(self._lo, self._limit / 2.0)
        else:
            self._limit = min(self._hi, self._limit + 1.0)
        self._last_err, self._last_adj = cur, now
        self.history.append((round(now, 1), round(self._limit, 1), self._active, delta))
        if self._limit > old:
            self._cv.notify_all()

    def acquire(self):
        with self._cv:
            self._adjust()
            while self._active >= self._limit:
                self._cv.wait(timeout=2.0)
                self._adjust()
            self._active += 1

    def release(self):
        with self._cv:
            self._active -= 1
            self._cv.notify()

    @property
    def limit(self) -> float:
        return self._limit


def _make_executor(cfg, max_workers: int | None = None):
    """Build the executor closure bound to the shared cfg. It shares ONE pooled
    ApiClient AND one thread-safe Budget across all concurrent lifecycle threads:

    * the client's urllib3 connection pool is sized to the fan-out, so concurrent
      lifecycles REUSE keep-alive connections instead of each opening a fresh
      requests.Session — the per-call-client version exhausted sockets under the
      overlap, which delayed sends past the HMAC signature's timestamp window
      (401 AuthNFailed) and timed connections out, breaking the heavy adopters.
      The client is safe to share: HmacSigner is stateless and request() never
      mutates shared session state (only the urllib3 pool, which is thread-safe).
    * the Budget coordinates capped-kind quotas (vpc/private-dns) across threads.

    Per-call only the ResourceRegistry (cheap, isolates created-resource tracking).
    Converts the engine's result dict OR a raised error into a LifecycleOutcome.
    NEVER raises (dag_runner.Executor contract)."""
    from core.registry import ResourceRegistry

    shared_budget = _SharedBudget()   # coordinates vpc/private-dns quota across threads
    client = _build_client(cfg, pool_size=max_workers or 8)   # ONE pooled client, reused

    # Optional adaptive concurrency: when SCP_ADAPTIVE=true, gate live concurrency
    # by an AIMD limiter that backs off on gateway 502/503/504 and probes up when
    # healthy — so the run self-tunes to the sustainable concurrency (and reveals
    # the sweet spot). Ceiling = max_workers (run_plan's thread pool); floor/start
    # via env. When unset, no gating (the pool bound alone applies).
    limiter = None
    if os.environ.get("SCP_ADAPTIVE") == "true":
        from core import http_client
        limiter = AdaptiveLimiter(
            start=int(os.environ.get("SCP_ADAPTIVE_START", "8")),
            lo=int(os.environ.get("SCP_ADAPTIVE_MIN", "4")),
            hi=max_workers or 8,
            err_source=http_client.retry_status_count,
            interval=float(os.environ.get("SCP_ADAPTIVE_INTERVAL", "20")))

    def executor(lifecycle_id: str) -> LifecycleOutcome:
        t0 = time.monotonic()
        lifecycle = _LIFECYCLE_BY_ID.get(lifecycle_id)
        if lifecycle is None:
            return LifecycleOutcome(
                lifecycle_id, "failed",
                reason=f"unknown lifecycle id '{lifecycle_id}' (not in "
                       f"engine.LIFECYCLES)",
                duration_s=time.monotonic() - t0)
        if limiter is not None:
            limiter.acquire()
        try:
            result = engine.run_lifecycle(
                lifecycle, client, cfg,
                budget=shared_budget,
                resource_registry=ResourceRegistry(),
                shared_ctx=_shared_ctx_from_env(),
            )
            status = result.get("status", "failed")
            reason = result.get("reason")
            return LifecycleOutcome(lifecycle_id, status, reason=reason,
                                    duration_s=time.monotonic() - t0)
        except Exception as exc:  # genuine failure — engine MAY raise (per spec)
            return LifecycleOutcome(
                lifecycle_id, "failed",
                reason=f"{type(exc).__name__}: {exc}",
                duration_s=time.monotonic() - t0)
        finally:
            if limiter is not None:
                limiter.release()

    executor.limiter = limiter   # exposed for live observability (dashboard/report)
    return executor


def build(plan: dag_planner.Plan, max_workers: int | None = None):
    """Build the (executor, provisioner) pair for a live run of ``plan``.

    LIVE PATH — refuses to proceed unless SCP_ALLOW_MUTATIONS=true (raises a
    clear RuntimeError naming the gate; CLAUDE.md Hard Rule 1). ``max_workers``
    sizes the shared client's connection pool to the run's fan-out (run_plan
    applies the same cap to its thread pool), so concurrent lifecycles reuse
    keep-alive connections rather than exhausting sockets.

    Returns ``(executor, provisioner)`` ready to pass to
    ``dag_runner.run_plan(plan, executor, provisioner=provisioner,
    max_workers=max_workers)``.
    """
    _require_mutation_gate("build live DAG-runner adapters")
    cfg = engine_settings()
    executor = _make_executor(cfg, max_workers=max_workers)
    provisioner = SharedInfraProvisioner(plan, cfg)
    return executor, provisioner
