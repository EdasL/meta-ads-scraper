"use client";

import { useRef, useState } from "react";
import type { Rating } from "@/lib/api";
import { setRating } from "@/lib/api";

/** Props for RatingControl. `userEmail` is required — the component is a no-op
 * when null (not logged in or session not yet resolved). */
export interface RatingControlProps {
  advertiserId: number;
  userEmail: string | null;
  /** Current rating from the server/parent. */
  rating: Rating | null;
  /** Current note from the server/parent. */
  comment: string;
  /** Called after an optimistic write so the parent can update its store. */
  onChange: (advertiserId: number, rating: Rating | null, comment: string) => void;
}

/** Pending optimistic override — set while the API call is in flight. */
interface Pending {
  rating: Rating | null;
  comment: string;
}

/** Inline like / neutral / dislike control with an optional note.
 *
 * Three buttons in a row. Clicking the active rating clears it (sends "none").
 * A note icon toggles a small textarea; the note is saved on blur or Enter
 * (Shift+Enter inserts a newline). All writes are optimistic — failure reverts
 * and logs to the console.
 */
export function RatingControl({
  advertiserId,
  userEmail,
  rating: propRating,
  comment: propComment,
  onChange,
}: RatingControlProps) {
  // Pending overrides prop values while an API call is in flight.
  const [pending, setPending] = useState<Pending | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  if (!userEmail) return null;

  // Resolved display values: pending wins over props.
  const displayRating = pending !== null ? pending.rating : propRating;
  const displayComment = pending !== null ? pending.comment : propComment;

  async function handleRatingClick(clicked: Rating) {
    if (!userEmail) return;
    const next: Rating = displayRating === clicked ? "none" : clicked;
    const nextRating = next === "none" ? null : next;

    setPending({ rating: nextRating, comment: displayComment });
    onChange(advertiserId, nextRating, displayComment);

    try {
      await setRating(advertiserId, userEmail, next, displayComment);
    } catch (err) {
      console.error("Rating update failed — reverting", err);
      setPending(null);
      onChange(advertiserId, propRating, propComment);
    } finally {
      setPending(null);
    }
  }

  async function saveComment(value: string) {
    if (!userEmail) return;
    const effectiveRating: Rating = displayRating ?? "none";

    setPending({ rating: displayRating, comment: value });
    onChange(advertiserId, displayRating, value);

    try {
      await setRating(advertiserId, userEmail, effectiveRating, value);
    } catch (err) {
      console.error("Comment save failed — reverting", err);
      setPending(null);
      onChange(advertiserId, propRating, propComment);
    } finally {
      setPending(null);
    }
  }

  function handleNoteToggle(e: React.MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    const next = !noteOpen;
    setNoteOpen(next);
    if (next) {
      setTimeout(() => textareaRef.current?.focus(), 0);
    }
  }

  function handleTextareaKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void saveComment(e.currentTarget.value);
      setNoteOpen(false);
    }
    if (e.key === "Escape") {
      setNoteOpen(false);
    }
  }

  const btnBase: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    cursor: "pointer",
    padding: 4,
    border: "1.5px solid var(--ink)",
    background: "transparent",
    color: "var(--ink)",
    fontSize: 13,
    lineHeight: 1,
    userSelect: "none",
  };

  const btnActive: React.CSSProperties = {
    background: "var(--ink)",
    color: "var(--bg)",
  };

  const noteActive = displayComment.trim().length > 0;

  return (
    <span
      style={{ display: "inline-flex", alignItems: "center", gap: 2, position: "relative" }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        aria-pressed={displayRating === "like"}
        aria-label="Like"
        title="Like"
        style={displayRating === "like" ? { ...btnBase, ...btnActive } : btnBase}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          void handleRatingClick("like");
        }}
      >
        👍
      </button>

      <button
        type="button"
        aria-pressed={displayRating === "neutral"}
        aria-label="Neutral"
        title="Neutral"
        style={displayRating === "neutral" ? { ...btnBase, ...btnActive } : btnBase}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          void handleRatingClick("neutral");
        }}
      >
        ➖
      </button>

      <button
        type="button"
        aria-pressed={displayRating === "dislike"}
        aria-label="Dislike"
        title="Dislike"
        style={displayRating === "dislike" ? { ...btnBase, ...btnActive } : btnBase}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          void handleRatingClick("dislike");
        }}
      >
        👎
      </button>

      <button
        type="button"
        aria-label={noteOpen ? "Close note" : "Add note"}
        aria-pressed={noteOpen}
        title={noteActive ? displayComment : "Add note"}
        style={{
          ...btnBase,
          ...(noteOpen || noteActive ? btnActive : {}),
          fontSize: 12,
        }}
        onClick={handleNoteToggle}
      >
        ✏️
      </button>

      {noteOpen && (
        <textarea
          ref={textareaRef}
          defaultValue={displayComment}
          rows={2}
          placeholder="Note…"
          aria-label="Note"
          onBlur={(e) => {
            void saveComment(e.currentTarget.value);
            setNoteOpen(false);
          }}
          onKeyDown={handleTextareaKeyDown}
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            zIndex: 10,
            width: 200,
            minHeight: 52,
            padding: "4px 6px",
            border: "1.5px solid var(--ink)",
            background: "var(--bg)",
            color: "var(--ink)",
            fontSize: 12,
            fontFamily: "inherit",
            resize: "vertical",
          }}
        />
      )}
    </span>
  );
}
