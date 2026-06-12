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
    from core.config import load_config
    from core.http_client import Client

    cfg = load_config()
    client = Client(cfg)
    suffix = uuid.uuid4().hex[:8]
    name = f"regrdkr{suffix}"
    reg_id = ""
    try:
        # the model's VALIDATED create body (container-registry node)
        resp = client.request("POST", "/v1/container-registries",
                              json={"name": name, "private_acl_enabled": False,
                                    "private_acl_resources": [],
                                    "public_acl_resources": [],
                                    "public_visible_enabled": False},
                              service="scr")
        print(f"{VERDICT} create -> {resp.status} {str(resp.raw_text)[:300]}")
        body = resp.body or {}
        reg_id = str(body.get("id") or body.get("registry_id") or "")
        if not reg_id:
            print(f"{VERDICT} INCONCLUSIVE — no registry id in create response "
                  f"(keys: {list(body) if isinstance(body, dict) else type(body)})")
            return 0
        endpoint = ""
        for _ in range(60):  # wait Running + endpoint visible (<=10min)
            r = client.request("GET", f"/v1/container-registries/{reg_id}",
                               service="scr")
            b = r.body or {}
            state = str(b.get("state") or b.get("status") or "")
            flat = json.dumps(b)
            for k, v in (b.items() if isinstance(b, dict) else []):
                if isinstance(v, str) and ("endpoint" in k or "url" in k) \
                        and "." in v:
                    endpoint = v
            if state.lower() == "running" and endpoint:
                break
            time.sleep(10)
        print(f"{VERDICT} registry {reg_id} state ready, endpoint={endpoint!r}")
        if not endpoint:
            print(f"{VERDICT} INCONCLUSIVE — no endpoint field on the registry "
                  f"(detail keys: {sorted(b) if isinstance(b, dict) else '?'})")
            return 0
        host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        ak, sk = os.environ["SCP_ACCESS_KEY"], os.environ["SCP_SECRET_KEY"]
        r = subprocess.run(["docker", "login", host, "-u", ak,
                            "--password-stdin"],
                           input=sk, capture_output=True, text=True, timeout=60)
        print(f"{VERDICT} docker login({host}, user=access-key) rc={r.returncode} "
              f"{(r.stdout + r.stderr).strip()[:200]}")
        if r.returncode != 0:
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
        if reg_id:
            for _ in range(24):  # 500-race retry ~6min (VALIDATED quirk)
                try:
                    r = client.request("DELETE",
                                       f"/v1/container-registries/{reg_id}",
                                       service="scr")
                    print(f"{VERDICT} delete -> {r.status}")
                    if r.status < 500:
                        break
                except Exception as exc:
                    print(f"{VERDICT} delete error {exc}")
                time.sleep(15)
    return 0


if __name__ == "__main__":
    sys.exit(main())
