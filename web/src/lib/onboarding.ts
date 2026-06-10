/**
 * onboarding.ts — tiny, dismissible "learn by doing" state for the activation
 * checklist (spec §5.5). Guides a first-timer through the core loop without a
 * tour: find a gem → see why it climbs → save it → write why. Two flags live in
 * localStorage; save/note completion is derived from the saved store.
 *
 * Pure `checklistSteps` is unit-tested; the storage helpers are browser glue.
 */
const KEY = "fielddesk.onboarding";

export interface OnboardingFlags {
  visitedGem?: boolean;
  expandedWhy?: boolean;
  dismissed?: boolean;
}

export function readFlags(): OnboardingFlags {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(KEY) ?? "{}") as OnboardingFlags;
  } catch {
    return {};
  }
}

function write(flags: OnboardingFlags): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(flags));
  } catch {
    /* private mode / quota — ignore */
  }
}

export function markVisitedGem(): void {
  const f = readFlags();
  if (!f.visitedGem) write({ ...f, visitedGem: true });
}

export function markExpandedWhy(): void {
  const f = readFlags();
  if (!f.expandedWhy) write({ ...f, expandedWhy: true });
}

export function dismissChecklist(): void {
  write({ ...readFlags(), dismissed: true });
}

export interface ChecklistStep {
  id: string;
  label: string;
  done: boolean;
}

/** Derive the four activation steps from flags + saved-store context. */
export function checklistSteps(
  flags: OnboardingFlags,
  ctx: { savedCount: number; hasNote: boolean },
): { steps: ChecklistStep[]; allDone: boolean } {
  const steps: ChecklistStep[] = [
    { id: "find", label: "Open a gem", done: !!flags.visitedGem },
    { id: "why", label: "Read why it might be working", done: !!flags.expandedWhy },
    { id: "save", label: "Save one", done: ctx.savedCount > 0 },
    { id: "note", label: "Note why you like it", done: ctx.hasNote },
  ];
  return { steps, allDone: steps.every((s) => s.done) };
}
