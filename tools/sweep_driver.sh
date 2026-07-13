#!/usr/bin/env bash
# Per-service sequential integration sweep driver (2026-07-13 night).
# For each service: dry-run (dep closure) -> live run (provision/exec/teardown)
#   -> verify_clean (leak check) -> reconcile if leaked -> collect 4xx/5xx.
# Records-only policy: NO code fixes tonight. Continues past any single-service failure.
set -u
cd /home/user/api-test-automation

LOGDIR=reports/sweep-logs
mkdir -p "$LOGDIR"
REPORT=reports/per-service-sweep-2026-07-13.md
PROG=$LOGDIR/_progress.tsv        # machine-readable per-service row
BASELINE=1                         # servicewatch log-group, always present

GATES="SCP_DAG_RUNNER=true SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true SCP_RUN_HEAVY=true"

SERVICES="networking/vpc networking/security-group networking/firewall compute/virtualserver compute/baremetal compute/scf compute/multinodegpucluster container/scr container/ske storage/filestorage storage/parallel-filestorage storage/backup storage/archivestorage storage/baremetal-blockstorage database/mysql database/mariadb database/postgresql database/epas database/sqlserver database/cachestore networking/loadbalancer networking/dns networking/gslb networking/cdn networking/vpn networking/direct-connect security/kms security/secretsmanager security/secretvault security/certificatemanager security/configinspection management/iam management/iam-identity-center management/organization management/resourcemanager management/cloudcontrol management/cloudmonitoring management/servicewatch management/loggingaudit management/network-logging management/quota management/support data-analytics/data-flow data-analytics/data-ops data-analytics/eventstreams data-analytics/quick-query data-analytics/searchengine data-analytics/vertica ai-ml/cloud-ml ai-ml/aimlops-platform application-service/apigateway application-service/queueservice devops-tools/devopsservice financial-management/billingplan financial-management/budget platform/sts"

kst(){ TZ=Asia/Seoul date +%H:%M:%S; }

survivors(){  # print TOTAL owned survivors integer (read-only scan)
  timeout 300 python -m cleanup.verify_clean 2>&1 | tee "$LOGDIR/_verify_last.log" \
    | grep -oE 'TOTAL owned survivors across all collections: [0-9]+' | grep -oE '[0-9]+$' | tail -1
}

echo "sweep start $(date -u +%FT%TZ)" > "$LOGDIR/_driver.log"

for S in $SERVICES; do
  SLUG=$(echo "$S" | tr '/' '__')
  LOG=$LOGDIR/$SLUG.log
  echo "" ; echo "########## $S  ($(kst) KST) ##########" | tee -a "$LOGDIR/_driver.log"

  NODES=$(python -c "from regression.scenarios import catalog_planner as cp; g=cp.load_graph(); print(' '.join(n for n,x in g.items() if x.service=='$S'))" 2>>"$LOG")
  if [ -z "$NODES" ]; then
    echo "  no nodes -> SKIP" | tee -a "$LOGDIR/_driver.log"
    printf '%s\t-\t-\tNODELESS\t-\t-\t-\t⏭️\n' "$S" >> "$PROG"; continue
  fi

  # a/b) dry-run: dependency closure + waves
  { echo "=== DRY-RUN $S ($(kst) KST) ==="; python -m regression.scenarios.catalog_run --target $NODES --dry-run; } > "$LOG.dry" 2>&1
  CLOSURE=$(grep -oE 'closure: [0-9]+ resource' "$LOG.dry" | grep -oE '[0-9]+' | head -1)
  NLC=$(grep -oE 'runnable lifecycles \([0-9]+\)' "$LOG.dry" | grep -oE '[0-9]+' | head -1)

  # c) live run
  echo "=== LIVE $S start $(kst) KST ===" > "$LOG"
  eval "$GATES timeout 2400 python -m regression.scenarios.catalog_run --target $NODES" >> "$LOG" 2>&1
  RC=$?
  echo "=== LIVE $S exit=$RC end $(kst) KST ===" >> "$LOG"
  RUNSUM=$(grep -E 'summary:|-- run result --' "$LOG" | tail -1)
  # count non-2xx step outcomes seen in the run log
  ERR4=$(grep -oE '\-> 4[0-9][0-9]' "$LOG" | wc -l | tr -d ' ')
  ERR5=$(grep -oE '\-> 5[0-9][0-9]' "$LOG" | wc -l | tr -d ' ')
  FAILSTEP=$(grep -cE 'failed ->' "$LOG" | tr -d ' ')

  # d) teardown double-check
  SURV=$(survivors); SURV=${SURV:-ERR}
  RECON=no
  if [ "$SURV" != "ERR" ] && [ "$SURV" -gt "$BASELINE" ] 2>/dev/null; then
    echo "  LEAK: survivors=$SURV > baseline=$BASELINE -> reconcile ($(kst) KST)" | tee -a "$LOGDIR/_driver.log"
    SCP_ALLOW_DESTRUCTIVE=true timeout 1200 python -m cleanup.reconciler >> "$LOG.reconcile" 2>&1
    RECON=yes
    SURV=$(survivors); SURV=${SURV:-ERR}
  fi

  # verdict
  if [ "$RC" = "124" ] || [ "$RC" = "137" ]; then V="❌TIMEOUT"
  elif [ "$SURV" != "ERR" ] && [ "$SURV" -gt "$BASELINE" ] 2>/dev/null; then V="❌LEAK"
  elif [ "$RC" != "0" ]; then V="⚠️RC$RC"
  elif [ "${ERR4:-0}" -gt 0 ] || [ "${ERR5:-0}" -gt 0 ] || [ "${FAILSTEP:-0}" -gt 0 ]; then V="⚠️ERRS"
  else V="✅OK"; fi

  printf '%s\tclosure=%s\tLC=%s\trc=%s\t4xx=%s;5xx=%s;failstep=%s\tsurv=%s;recon=%s\t%s\t%s\n' \
    "$S" "${CLOSURE:-?}" "${NLC:-?}" "$RC" "${ERR4:-0}" "${ERR5:-0}" "${FAILSTEP:-0}" "$SURV" "$RECON" "$RUNSUM" "$V" >> "$PROG"
  echo "  DONE $S -> $V  (closure=$CLOSURE LC=$NLC rc=$RC 4xx=$ERR4 5xx=$ERR5 fail=$FAILSTEP surv=$SURV recon=$RECON)" | tee -a "$LOGDIR/_driver.log"
done

echo "" | tee -a "$LOGDIR/_driver.log"
echo "=== FINAL full verify_clean ($(kst) KST) ===" | tee -a "$LOGDIR/_driver.log"
FINAL=$(survivors); echo "FINAL survivors=$FINAL" | tee -a "$LOGDIR/_driver.log"
echo "sweep end $(date -u +%FT%TZ)" | tee -a "$LOGDIR/_driver.log"
