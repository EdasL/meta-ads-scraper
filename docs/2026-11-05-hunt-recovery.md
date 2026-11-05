# Hunt worker recovery — 2026-11-05

Hunt round 48 was queued on October 28 and had never been claimed. The API and
Cloud SQL remained available, but the worker VM was unresponsive over direct SSH
and IAP. Restarting the VM restored processing; round 48 began at 09:01:33 UTC
on November 5 and had persisted five results by 09:03:30 UTC.

## Evidence

- The VM has 3,924 MiB RAM and no swap. Its previous boot began June 26.
- On September 25, the kernel's OOM process snapshot recorded the Google guest
  agent manager at 2,813,204 KiB RSS, with `oom_score_adj=-999`. The kernel killed
  MinIO instead. Earlier OOM events also killed Python and Chromium processes.
- The manager repeatedly failed to restart its plugins with `resource temporarily
  unavailable`. Scheduled processes had accumulated. The manager has no systemd
  memory limit and is strongly protected from the host OOM killer.
- After reboot, the manager service used approximately 99 MB, including its
  children. All nine long-running containers resumed; worker and MinIO health
  checks passed. Cloud SQL connections were available, and Hunt persisted results.
- The exact allocation source inside the guest agent was not profiled. Its memory
  growth and retry failures are confirmed; a particular upstream bug is not.

## Changes and verification

Before restarting, the instance's startup script was changed to preserve an
existing nonempty `.env` and compose file. Previously, every boot rewrote the
environment with the original MinIO defaults and extracted the current `latest`
image's compose file, potentially discarding subsequent production configuration.
The boot journal confirmed that the new preservation path ran successfully.

Verification: `bash -n`, `git diff --check`, a temporary-directory execution probe
that confirmed both existing files remained unchanged, live boot logs, container
health checks, and read-only production queries showing Hunt progress.

The checkout was connected to `https://github.com/EdasL/meta-ads-scraper.git` and
`main` was pushed, including the startup safeguard. A Gitleaks scan of the outgoing
history reported no leaks. Unrelated untracked local files were not included.

## Remaining risk

The reboot cleared the resource exhaustion; it did not establish a permanent fix
for guest-agent memory growth. A scoped follow-up should investigate that growth,
bound the manager's memory with verified restart behavior, and prevent overlapping
cron jobs. No guest-agent upgrade, IAM change, VM resize, or cron change was made
during this recovery. The Hunt was still running at the verification checkpoint.
