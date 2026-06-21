#!/usr/bin/env bash
# Autonomous: wait for the account to be clean (VPC->0, reconcile-until-clean), then
# trigger the full heavy chat-heavy run (unified pytest-xdist + shared optimizations).
set -uo pipefail
cd /home/user/api-test-automation
url=$(git remote get-url origin)

vpc_count() {
  timeout 25 python -c "from core.config import settings; from core.http_client import ApiClient; c=ApiClient(settings); print(len((c.request('GET','/v1/vpcs',service='vpc',timeout=15,retry=False).body or {}).get('vpcs',[])))" 2>/dev/null
}

# let any in-flight reconciler finish first
for _ in $(seq 1 30); do pgrep -f cleanup.reconciler >/dev/null || break; sleep 10; done

for pass in $(seq 1 12); do
  V=$(vpc_count); V="${V:-?}"
  echo "[rerun] pass $pass: VPC=$V"
  [ "$V" = "0" ] && { echo "[rerun] account CLEAN"; break; }
  SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true SCP_SWEEP_NOWAIT=true \
    timeout 320 python -m cleanup.reconciler >> reports/reconcile-rerun.log 2>&1 || true
  sleep 15
done

# trigger the full heavy run
printf 'action=run\nmutations=true\ndestructive=true\nheavy=true\ncrud_filter=\nparallel=10\n' > .github/chat-heavy-request
git add .github/chat-heavy-request
git commit -q -m "ci(chat-heavy): RUN full heavy — unified pytest-xdist + shared longest-first/warm pool (n=10)

Re-run after the SHARED-code optimization fix: pytest-xdist now gets longest-first
ordering (tests/crud/conftest.py) so the big DB/K8s clusters lead the distribution
(start at t=0, not late), and the warm per-host pool (core.http_client.ApiClient).
Same engine as api-test.yml — no divergent driver.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AHoEtuVqYFeupmYk53WrsT"
for i in 1 2 3 4; do
  git push -u origin claude/start-here-review-5z8jt2 2>&1 | tail -1 && { echo "[rerun] TRIGGERED full heavy run"; break; }
  sleep $((2**i))
done
