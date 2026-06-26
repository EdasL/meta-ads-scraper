# ops/prod — Field Desk production (peon-489013)

Provisioning scripts + the VM compose payload for the scraping + DB deploy.
See `docs/superpowers/plans/2026-06-26-peon-scraping-db-prod.md` for the full
runbook and `ops/RUNBOOK.md` for day-2 ops. Scripts are idempotent.

Run them in **dependency order** (NOT the numeric filename order — the prefixes
are misleading):

1. `02_secrets.sh`       — creates the Secret Manager entries the rest consume
2. `00_provision_sql.sh` — reads `agf-db-password` / `agf-postgres-password`, so needs step 1
3. `02_build_image.sh`   — builds `worker:latest` into Artifact Registry
4. `01_provision_vm.sh`  — the VM boot (`startup.sh`) `docker pull`s `worker:latest`, so needs step 3

i.e. `02_secrets → 00_provision_sql → 02_build_image → 01_provision_vm`.
