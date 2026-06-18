"""Audit-log harvest + scenario-optimization (post-run analysis).

Pulls the SCP loggingaudit event log (`GET /v1/logs`) for a run's time window
and derives time/cost optimization signals for the scenario suite. Read-only.
"""
