---
name: trip-intake
description: >-
  Handles intent routing, slot filling, and writing trip-meta/session for the
  trip planner agent. Use when the user starts a new plan, provides trip
  constraints, or when session/trip-meta files need to be created or updated.
  Do NOT use for POI extraction or itinerary scheduling.
---

# Trip Intake

## DO
- Write `trip-meta.json` and `session.json`
- Route intents: `new_plan`, `parse_only`, `chitchat_reject`
- Ask at most 2 rounds of clarifying questions

## DO NOT
- Parse guide text into POIs
- Build or revise itinerary days

## Output
`context/{sessionId}/trip-meta.json` + `session.json`
