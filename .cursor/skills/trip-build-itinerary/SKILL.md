---
name: trip-build-itinerary
description: >-
  Builds day-by-day itinerary from structured poi-list using deterministic tools.
  Use when poi-list.json exists and user wants scheduling. Do NOT re-parse raw
  guide text. May read source_refs evidence slices when verifier flags weak evidence.
---

# Build Itinerary (Phase A)

## DO
- Read `trip-meta.json` + `poi-list.json` only
- Run `src/tools/build-itinerary.ts`
- Run verifier after build

## DO NOT
- Load `guides/raw.md` in full
- Call LLM for scheduling (use code)

## Output
`context/{sessionId}/itinerary.json`
