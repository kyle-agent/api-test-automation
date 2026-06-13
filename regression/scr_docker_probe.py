"""SCR docker-auth probe (owner hypothesis 2026-06-12): does docker login to a
Container Registry endpoint work with the EXISTING SCP access/secret keys —
i.e. is the 'scr-auth-key' credential already satisfied by the runner env?

One-shot experiment, NOT a lifecycle: create a registry via the API, wait
Running, docker login/push with the SCP keys, report a clear verdict line,
then delete the registry (500-race retry per the VALIDATED quirk).  Always
exits 0 — the verdict is for the operator/triage, never a CI failure.

Run from the workflow with run-request `docker_probe=true`:
    python -m regression.scr_docker_probe
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

VERDICT = "SCR-DOCKER-PROBE:"


def sh(*cmd: str, timeout: int = 120) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def main() -> int:
    from core.config import Settings
    from core.http_client import ApiClient
    try:
        from core import oplog as _oplog
    except Exception:
        _oplog = None

    def _ev(action, **kw):
        # probe bypasses the engine, so emit resource events itself —
        # otherwise the ops dashboard never shows the probe registry
        # (owner report 2026-06-13)
        if _oplog:
            _oplog.emit_resource(action, service="scr",
                                 lifecycle="scr-docker-probe", **kw)

    cfg = Settings()
    client = ApiClient(cfg)
    suffix = uuid.uuid4().hex[:8]
    name = f"regrdkr{suffix}"
    reg_id = ""
    try:
        # the model's VALIDATED create body (container-registry node)
        # public registry on purpose (run 27444823109 lesson): a private one
        # has empty public_domain, and docker login from a GitHub runner needs
        # the public endpoint. Bonus: public uses the OTHER quota slot
        # (visibility max 1 + non-visibility max 1), so the scr chain's
        # private registry no longer contends with the probe.
        # run 27447899572: public_visible_enabled alone still yields only a
        # .private. domain (runner DNS can't resolve it) — the detail body
        # exposes a separate public_endpoint_enabled flag; send it too.
        resp = client.request("POST", "/v1/container-registries",
                              json={"name": name, "private_acl_enabled": False,
                                    "private_acl_resources": [],
                                    "public_acl_resources": [],
                                    "public_visible_enabled": True,
                                    "public_endpoint_enabled": True},
                              service="scr")
        print(f"{VERDICT} create -> {resp.status} {str(resp.raw_text)[:300]}")
        body = resp.body or {}
        reg_id = str(body.get("id") or body.get("registry_id") or "")
        if reg_id:
            _ev("created", path="/v1/container-registries", name=name,
                res_id=reg_id, status=str(resp.status))
        borrowed = False
        if not reg_id and resp.status == 403 and "quota" in str(resp.raw_text):
            # NON_VISIBILITY max 1 (userguide fact, live-confirmed run
            # 27421363609): a concurrent scr lifecycle holds the slot —
            # borrow an existing Running registry instead of creating one.
            lst = client.request("GET", "/v1/container-registries", service="scr")
            for it in (lst.body or {}).get("contents", []) or                       (lst.body if isinstance(lst.body, list) else []):
                if str(it.get("state", "")).lower() == "running":
                    reg_id = str(it.get("id") or "")
                    borrowed = True
                    print(f"{VERDICT} quota hit — borrowing existing registry "
                          f"{reg_id} ({it.get('name')})")
                    break
        if not reg_id:
            print(f"{VERDICT} INCONCLUSIVE — no registry id in create response "
                  f"(keys: {list(body) if isinstance(body, dict) else type(body)})")
            return 0
        endpoint = ""
        for _ in range(60):  # wait Running + endpoint visible (<=10min)
            r = client.request("GET", f"/v1/container-registries/{reg_id}",
                               service="scr")
            b = r.body or {}
            if isinstance(b.get("registry"), dict):
                # live envelope (run 27428457582): detail is {"registry": {...}}
                b = b["registry"]
            state = str(b.get("state") or b.get("status") or "")
            flat = json.dumps(b)
            # live keys (run 27444823109): public_domain / private_domain are
            # the docker endpoints — prefer public (reachable from the runner)
            for k in ("public_domain", "private_domain"):
                v = b.get(k) if isinstance(b, dict) else None
                if isinstance(v, str) and "." in v:
                    endpoint = v
                    break
            for k, v in (b.items() if isinstance(b, dict) else []):
                if endpoint:
                    break
                if isinstance(v, str) and ("endpoint" in k or "url" in k) \
                        and "." in v:
                    endpoint = v
            if state.lower() == "running" and endpoint:
                break
            time.sleep(10)
        print(f"{VERDICT} registry {reg_id} state={state!r}, endpoint={endpoint!r}")
        if not endpoint:
            print(f"{VERDICT} INCONCLUSIVE — no endpoint field on the registry "
                  f"(detail keys: {sorted(b) if isinstance(b, dict) else '?'})")
            return 0
        host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        # run 27450089575: the .scr.public. domain existed but the runner's
        # resolver had no record yet — newly minted subdomains may lag. Wait
        # for DNS up to ~4 min before concluding NETWORK-UNREACHABLE.
        import socket
        dns_ok = False
        for _ in range(16):
            try:
                socket.getaddrinfo(host, 443)
                dns_ok = True
                break
            except OSError:
                time.sleep(15)
        print(f"{VERDICT} dns({host}) resolvable={dns_ok}")
        ak, sk = os.environ["SCP_ACCESS_KEY"], os.environ["SCP_SECRET_KEY"]
        r = subprocess.run(["docker", "login", host, "-u", ak,
                            "--password-stdin"],
                           input=sk, capture_output=True, text=True, timeout=60)
        out = (r.stdout + r.stderr).strip()
        print(f"{VERDICT} docker login({host}, user=access-key) rc={r.returncode} "
              f"{out[:200]}")
        if r.returncode != 0:
            if "dial tcp" in out or "lookup" in out or "no such host" in out:
                # DNS/dial failure — the endpoint is unreachable from the
                # runner (e.g. .private. domain), NOT a credential rejection
                print(f"{VERDICT} NETWORK-UNREACHABLE — endpoint not resolvable "
                      f"from the runner; credential hypothesis still untested")
            else:
                print(f"{VERDICT} LOGIN-FAILED — SCP keys are NOT the docker "
                      f"credential (separate console auth key still required)")
            return 0
        sh("docker", "pull", "hello-world")
        tag = f"{host}/regrprobe/hello:{suffix}"
        sh("docker", "tag", "hello-world", tag)
        rc, out = sh("docker", "push", tag, timeout=180)
        print(f"{VERDICT} docker push rc={rc} {out[:300]}")
        if rc == 0:
            print(f"{VERDICT} PUSH-OK — scr-auth-key IS the SCP key pair; "
                  f"image/tag surface + cloud-ml unblocked")
        else:
            print(f"{VERDICT} LOGIN-OK-PUSH-FAILED — check repo perms/format")
    except Exception as exc:  # report, never fail CI
        print(f"{VERDICT} ERROR {type(exc).__name__}: {exc}")
    finally:
        if reg_id and not locals().get("borrowed"):
            for _ in range(24):  # 500-race retry ~6min (VALIDATED quirk)
                try:
                    r = client.request("DELETE",
                                       f"/v1/container-registries/{reg_id}",
                                       service="scr")
                    print(f"{VERDICT} delete -> {r.status}")
                    if r.status < 500:
                        _ev("deleted",
                            path=f"/v1/container-registries/{reg_id}",
                            name=name, res_id=reg_id, status=str(r.status))
                        break
                except Exception as exc:
                    print(f"{VERDICT} delete error {exc}")
                time.sleep(15)
    if _oplog:
        try:
            _oplog.flush_resources()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
