# Forge Ticket Drop Design QA

- Source visual truth: `/Users/stg-mba002/Desktop/UI design/锻造券/N.png`
- Runtime N asset: `/Users/stg-mba002/N.E.K.O/static/assets/forge-tickets/forge-ticket-n.png`
- Implementation screenshot: `/private/tmp/neko-forge-ticket-prominent-360-desktop.png`
- Small-window screenshot: `/private/tmp/neko-forge-ticket-prominent-360-small.png`
- Focused comparison: `/private/tmp/neko-forge-ticket-prominent-360-comparison.png`
- Viewports: 960 × 540 and 390 × 500 CSS pixels
- State: N-rarity ticket in the stable hold phase before flying to the community button

**Findings**

- No actionable P0, P1, or P2 differences remain.
- Fonts and typography: `Project`, `Neko`, `MEMORY FORGE`, and rarity lettering remain rasterized directly from the supplied finished artwork. No CSS font approximation, wrapping, or truncation is introduced.
- Spacing and layout rhythm: the stable ticket is 360 × 134 on desktop, centered at the existing 42% viewport anchor. At 390px width it keeps 15px margins on both sides and has no horizontal overflow. The original split-panel proportions, corner radii, perforation gap, barcode, and character crop are preserved.
- Colors and visual tokens: the main ticket is unfiltered and retains the source palette. The new attention layer is a blurred duplicate of the same supplied ticket asset, so its aura inherits the correct rarity colour without inventing a second palette.
- Image quality and asset fidelity: the browser loads the native 1192 × 445 N PNG and renders the sharp main copy at 360 × 134. The aura is isolated behind the main copy, so blur does not soften typography, line art, or the barcode. No placeholder, CSS drawing, inline SVG, emoji, or text-glyph particle is visible.
- Copy and content: all visible ticket copy comes from the supplied artwork. Runtime reason and held-count context remains in the accessible label without modifying the ticket face.

**Full-view comparison evidence**

- The 960 × 540 screenshot shows the 360 × 134 ticket centered with a restrained source-derived aura. The 390 × 500 screenshot confirms the larger ticket still fits with 15px side margins and keeps the community-button target unobstructed.

**Focused region comparison evidence**

- The focused side-by-side image aligns the supplied N ticket and the browser-rendered ticket at the same 360 × 134 size. Typography, separator, character crop, barcode, rounded corners, transparency, and panel proportions match; the only intentional difference is the aura behind the implementation.

**Interaction and runtime checks**

- Triggered the real overlay play path from the browser QA harness.
- Confirmed the cache-busted main and aura images complete at natural dimensions 1192 × 445.
- Confirmed the full hold/fly sequence removes the card and finishes with a visible community badge showing `1`.
- Checked browser console errors after the complete interaction: none.
- Verified reduced-motion rules keep the card visible while disabling attention animations.

**Comparison history**

- Pass 1: P2 image-quality mismatch. The original low-resolution preview plus a transformed, filtered main ticket produced visible blur.
- Fix: replaced all rarity assets with the supplied high-resolution PNGs and removed filtering from the main ticket.
- Pass 2: focused comparison passed source fidelity and image sharpness.
- Pass 3: increased the stable ticket from 256 × 99 to 320 × 120; desktop and 390px viewport captures passed without clipping.
- Pass 4: P2 prominence gap. The 320px ticket was clear but visually quiet against the application background.
- Fix: increased the responsive maximum to 360px, extended the hold phase from 2.8s to 3.2s, strengthened the entrance overshoot, added a source-derived aura behind the sharp main image, and removed text-glyph sparks.
- Pass 5: desktop and 390px captures confirm stronger hierarchy without source drift, clipping, main-image blur, interaction breakage, or console errors.

**Implementation checklist**

- [x] Preserve supplied N, R, SR, and SSR ticket artwork.
- [x] Increase the stable desktop ticket to 360 × 134.
- [x] Keep at least 12px responsive side margins below the desktop maximum.
- [x] Use a source-derived aura while keeping the main artwork unfiltered.
- [x] Preserve drop, hold, fly, badge, and reduced-motion behavior.
- [x] Remove text-glyph particle decoration.
- [x] Verify source fidelity, responsive fit, interaction completion, and console cleanliness.

**Follow-up polish**

- None required for this scope.

final result: passed

---

# Forge Ticket Drop Design QA — UR replacement

- Source visual truth: `/Users/stg-mba002/Desktop/UI design/锻造券/UR.png`
- Runtime UR asset: `/Users/stg-mba002/N.E.K.O/static/assets/forge-tickets/forge-ticket-ur.png`
- Implementation screenshot: `/private/tmp/neko-forge-ticket-ur-desktop.png`
- Small-window screenshot: `/private/tmp/neko-forge-ticket-ur-small.png`
- Focused comparison: `/private/tmp/neko-forge-ticket-ur-comparison.png`
- Viewports: 960 × 540 and 390 × 500 CSS pixels
- State: UR-rarity ticket in the stable hold phase before the fly-out transition

**Findings**

- No actionable P0, P1, or P2 differences remain.
- Fonts and typography: `Project`, `Neko`, `MEMORY FORGE`, and `UR` are rendered directly from the supplied artwork. No substitute web font, wrapping, truncation, or CSS text recreation is introduced.
- Spacing and layout rhythm: the desktop ticket is exactly 360 × 134 and remains centered on the existing 42% viewport anchor. At 390px width it measures 360 × 134 with 15px margins on both sides and no overflow. The split-panel proportions, perforation, radii, barcode, and character crop remain intact.
- Colors and visual tokens: the unfiltered main image preserves the supplied pastel rainbow palette. The existing source-derived aura sits behind the main image and only adds attention around transparent pixels.
- Image quality and asset fidelity: the browser loads the dedicated cache-busted UR asset at its native 1193 × 445 size. The source and runtime asset have the same SHA-256 digest. No placeholder, CSS drawing, inline SVG, emoji, or text-glyph replacement is present.
- Copy and content: all visible copy comes from the supplied UR artwork. Runtime reason and held-count context remain in the accessible label and do not alter the ticket face.

**Full-view comparison evidence**

- The 960 × 540 capture shows the 360 × 134 UR ticket centered in the actual overlay layer during the stable hold phase.
- The 390 × 500 capture confirms the same ticket fits with 15px side margins and remains fully visible without clipping or horizontal scroll.

**Focused region comparison evidence**

- The side-by-side comparison aligns the source and browser-rendered ticket at the same 360 × 134 size. Typography, palette, character silhouette, separator, barcode, rounded corners, and panel proportions match. The only intentional visible difference is the existing blurred duplicate behind the sharp main image.

**Interaction and runtime checks**

- Triggered the production `window.nekoForgeDrop.play()` path with `rarity: 'UR'` from a local browser QA page.
- Confirmed `/static/assets/forge-tickets/forge-ticket-ur.png?v=20260718-hd` loads completely at 1193 × 445.
- Confirmed the card reaches the stable hold state, completes the fly-out transition, and is removed from the overlay.
- Checked browser console warnings and errors on clean desktop and narrow-window runs: none.
- Confirmed the UR mapping contract and all other rarity assets with the focused unit suite.

**Comparison history**

- Pre-build source audit: UR still reused the SSR ticket path, so the visible rarity artwork did not match the newly supplied reference.
- Fix: added the dedicated UR PNG and mapped only UR to the new cache-busted asset path.
- Pass 1: desktop, narrow-window, and focused side-by-side comparisons found no actionable P0/P1/P2 mismatch.

**Implementation checklist**

- [x] Preserve the supplied UR artwork byte-for-byte.
- [x] Give UR its own runtime asset and cache version.
- [x] Preserve the existing drop, hold, aura, fly-out, and accessibility behavior.
- [x] Verify desktop fidelity, narrow-window fit, interaction completion, and console cleanliness.
- [x] Keep N, R, SR, and SSR mappings unchanged.

**Follow-up polish**

- None required for this scope.

final result: passed
