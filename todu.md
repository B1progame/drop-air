# todu

## Goal

Create a second code flow that also provides a website UI, but uses cable transfer when possible.

## What We Will Build

- A second protected route in the app (example: `/cable`).
- A second code (PIN) for this cable mode.
- A dedicated cable-mode webpage with clear steps for iPhone/iPad + Windows.
- Automatic preference for cable workflow first, then wireless fallback.

## Cable-First Behavior

- If USB/cable transfer path is available, guide user to use it first.
- Show this as the primary option for speed and stability.
- If cable path is not available, show fallback options (LocalSend / SMB / browser upload).

## Security Rules

- Cable mode requires its own separate code.
- Code must be different from the normal upload page code.
- Route should reject access if cable-mode code is missing or invalid.

## Website Requirements

- Add a dedicated cable-mode page with:
- "Enter second code" step.
- "Connect cable" step.
- "Use Apple Devices / native transfer" instruction section.
- "Fallback to wireless" section.

## Technical Note

Browser APIs cannot directly unlock native USB transfer speed on iOS.
So the website should act as a secure guide + control flow, while real high-speed cable transfer is handled by native Apple/OS paths.

## Done When

- Second code exists and is validated server-side.
- `/cable` route exists and is protected.
- Cable-mode webpage exists and is reachable.
- Cable-first instructions are shown before fallback options.
- User can still complete transfer via fallback if cable path is unavailable.
