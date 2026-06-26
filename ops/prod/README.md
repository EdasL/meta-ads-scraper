# ops/prod — Field Desk production (peon-489013)

Provisioning scripts + the VM compose payload for the scraping + DB deploy.
See `docs/superpowers/plans/2026-06-26-peon-scraping-db-prod.md` for the full
runbook and `ops/RUNBOOK.md` for day-2 ops. Scripts are idempotent; run them in
numeric order: `00_provision_sql.sh`, `02_secrets.sh`, `01_provision_vm.sh`.
