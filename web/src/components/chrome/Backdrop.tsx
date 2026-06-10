/**
 * Page backdrop, two variants of the same fingerprint texture:
 *
 * - "dark" (signed-out: landing, login) — white ridges on dark charcoal,
 *   the forensic brand moment.
 * - "light" (logged-in product) — the print inverted and faded to a pale
 *   watermark on paper, so content stays readable.
 *
 * Fixed to the viewport, behind all content. Pure decoration.
 */
export function Backdrop({ variant = "dark" }: { variant?: "dark" | "light" }) {
  if (variant === "light") {
    return (
      <div aria-hidden style={{ position: "fixed", inset: 0, zIndex: -1, backgroundColor: "var(--bg)" }}>
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage: "url('/field-desk-fingerprint.png')",
            backgroundSize: "cover",
            backgroundPosition: "center",
            backgroundRepeat: "no-repeat",
            // Invert the white-on-charcoal art into dark ridges on paper,
            // then fade it to a watermark.
            filter: "grayscale(1) invert(1)",
            opacity: 0.16,
          }}
        />
      </div>
    );
  }

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
