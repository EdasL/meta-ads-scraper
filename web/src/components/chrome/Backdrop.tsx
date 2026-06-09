/**
 * Page backdrop: an original generated full-bleed fingerprint texture (white
 * ridges glowing on dark charcoal grey, forensic evidence vibe). Fixed to the
 * viewport, behind all content. Pure decoration.
 */
export function Backdrop() {
  return (
    <div
      aria-hidden
      style={{
        position: "fixed",
        inset: 0,
        zIndex: -1,
        backgroundColor: "var(--bg)",
        backgroundImage: "url('/field-desk-fingerprint.png')",
        backgroundSize: "cover",
        backgroundPosition: "center",
        backgroundRepeat: "no-repeat",
        // Kill the warm/cream cast from the generated art — force neutral gray.
        filter: "grayscale(1) contrast(1.02)",
      }}
    />
  );
}
