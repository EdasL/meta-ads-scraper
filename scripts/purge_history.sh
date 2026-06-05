#!/usr/bin/env bash
# Purge the ~70 blobs a stray `git add -A` left in history (HANDOFF §6 item 4):
# web/, .firecrawl/, .cursor/, AGENTS.md, CLAUDE.md exist in history ONLY in the
# accident commits 05843df (added) + 5724019 (removed); the current tip is already
# clean and the working-tree copies are UNTRACKED, so this rewrite does not delete
# them from disk — it only strips them from history. Local repo, no remotes → safe.
#
# This was PROVEN in a throwaway clone (accident blobs gone, legit Plan-6 history +
# the current 219-file tree intact, fsck clean) but is NOT run automatically because
# it rewrites shared history + resets the working tree — only run it when NO other
# agent/editor is working in this repo and the tree has no uncommitted tracked work.
#
#   bash scripts/purge_history.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# 1) refuse to run with uncommitted TRACKED changes (untracked files are fine and survive)
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ABORT: commit or stash tracked changes first (e.g. the .gitignore hardening)." >&2
  echo "       Untracked files (web/, ops/seed_demo.py, …) are safe and will survive." >&2
  exit 1
fi

# 2) tool check
FR="$(command -v git-filter-repo || echo .venv/bin/git-filter-repo)"
[ -x "$FR" ] || { echo "ABORT: git-filter-repo not found (pip install git-filter-repo)." >&2; exit 1; }

# 3) fresh backup (in addition to any pre-made ones)
STAMP="$(git rev-parse --short HEAD)"
git bundle create "../emerging-app-finder-PREFILTER-${STAMP}.bundle" --all
echo "backup bundle: ../emerging-app-finder-PREFILTER-${STAMP}.bundle"

# 4) rewrite: drop the 5 accident roots from all of history
PATHS="$(mktemp)"; printf 'web/\n.firecrawl/\n.cursor/\nAGENTS.md\nCLAUDE.md\n' > "$PATHS"
"$FR" --invert-paths --paths-from-file "$PATHS" --force
rm -f "$PATHS"
git reflog expire --expire=now --all && git gc --prune=now --aggressive

# 5) verify
echo "--- verify (all must be empty / intact) ---"
echo "accident paths in history (want empty):"; git log --oneline --all -- web/ .firecrawl/ .cursor/ AGENTS.md CLAUDE.md
echo "big blobs reachable (want empty):"; git rev-list --objects --all | grep -E 'field-desk-bg.png|appfigures-pricing.md' || true
echo "legit Plan-6 history (want commits):"; git log --oneline -- src/agf/enrich_full/ | head -1
git fsck --full >/dev/null && echo "fsck clean"
echo "DONE — history purged. Working-tree web/, .cursor/, AGENTS.md, CLAUDE.md remain (untracked)."
