# todu-ui

## Goal

Update the UI so it stays compact and clean on iPhone, especially in the Incoming + Share area.

## Main Idea (iPhone)

- Keep Incoming + Share at a fixed 3-block width.
- Allow horizontal scrolling inside that tab/section only.
- Do not let the full interface/page get longer because of many items.
- Only the Incoming section becomes scrollable, not the whole layout.

## Layout Ideas

- Use a fixed-height card for Incoming items.
- Inside that card, show a 3-column mini grid.
- Add `overflow-x: auto` for horizontal scroll.
- Keep header, controls, and send button pinned/visible.
- Add subtle fade/gradient on left/right edges to hint scroll.

## Behavior Ideas

- New incoming item appears as first card with small highlight animation.
- Keep card size consistent (no stretching based on filename length).
- Long names should truncate with ellipsis.
- Tap card to open details/actions.
- Optional: quick action row (Accept, Preview, Save) on card tap.

## Responsive Rules

- iPhone: 3 blocks visible width, horizontal scroll.
- Tablet: 4-6 blocks visible, still scroll inside section.
- Desktop: full grid or wider row with same component behavior.
- Keep spacing and typography consistent across breakpoints.

## Visual Polish

- Keep section borders clear so user sees scroll area boundaries.
- Add item counter (example: `Incoming (12)`).
- Show tiny status dot (waiting/transferring/done).
- Use smooth snap scrolling (`scroll-snap-type: x mandatory`).

## Technical Notes

- Recommended container strategy:
- Outer layout: no growth from incoming list.
- Incoming container: `overflow-x: auto; overflow-y: hidden;`.
- Row/grid width can exceed viewport; parent layout must stay fixed.
- Consider virtualization later if many items are shown.

## Done When

- On iPhone, Incoming + Share visually stays 3 blocks wide.
- User can scroll horizontally through items.
- Whole page height/length does not expand because of incoming item count.
- Interaction remains smooth and readable.
