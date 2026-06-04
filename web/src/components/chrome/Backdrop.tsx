/**
 * Page backdrop: an original generated texture (gray paper, film grain, faint
 * cropped letterforms, a ghosted duotone silhouette) in the nrthview technique.
 * Fixed to the viewport, behind all content. Pure decoration.
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
        backgroundImage: "url('/field-desk-bg.png')",
        backgroundSize: "cover",
        backgroundPosition: "center",
        backgroundRepeat: "no-repeat",
      }}
    />
  );
}
