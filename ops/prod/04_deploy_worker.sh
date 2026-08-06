#!/usr/bin/env bash
# Redeploy worker-image services on the agf-worker VM, then reclaim disk.
#
# Run AFTER ops/prod/02_build_image.sh has pushed :latest. Idempotent.
#
#   bash ops/prod/04_deploy_worker.sh              # every worker-image service
#   bash ops/prod/04_deploy_worker.sh hunt         # just the hunt daemon
#   bash ops/prod/04_deploy_worker.sh worker cron  # a subset
#
# Why this script exists rather than the runbook's two manual lines:
#
# 1. The service list drifts. RUNBOOK.md documented `worker cron` long after
#    enrich/pipeline/keeper/hunt were added, so `pull worker cron && up -d
#    worker cron` silently left the hunt daemon on old code — a deploy that
#    reports success and changes nothing.
# 2. Nothing collected old images. Every pull leaves the previous one behind.
#    On 2026-08-06 the VM hit 100% of its 79 GB disk with 26 images, 70 GB
#    reclaimable, and a deploy failed mid-extract with "no space left on
#    device". Images only accumulate when you pull, so pruning right after the
#    pull is complete coverage — no separate cron needed. The prune cannot live
#    in the `cron` container (ops/cron/prune.sh is DB retention, and that
#    container has no docker socket), and it cannot live in 02_build_image.sh,
#    which runs on Cloud Build and never touches the VM.
set -euo pipefail

PROJECT=peon-489013
ZONE=europe-west1-b
VM=agf-worker
COMPOSE=/opt/agf/docker-compose.yml

# Every service running the worker image (compose services minus the
# infrastructure ones: cloud-sql-proxy, minio, minio-init).
ALL_SERVICES="worker cron enrich pipeline keeper funnel-confirm hunt"
SERVICES="${*:-$ALL_SERVICES}"

echo "==> deploying [$SERVICES] to $VM"

# --force-recreate: without it compose skips containers whose config is
# unchanged, which is every one of them — the image moved, the config did not.
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" --command="
  set -euo pipefail
  echo '--- disk before ---'
  df -h / | tail -1
  sudo docker compose -f $COMPOSE pull $SERVICES
  sudo docker compose -f $COMPOSE up -d --force-recreate $SERVICES
  echo '--- reclaiming superseded images ---'
  sudo docker image prune -af | tail -1
  echo '--- disk after ---'
  df -h / | tail -1
  echo '--- running ---'
  sudo docker compose -f $COMPOSE ps $SERVICES
"

echo "==> done. Verify the change TOOK, not just that it deployed:"
echo "    a container can start clean on a stale image and look identical."
