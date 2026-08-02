---
name: trip-revise-day
description: >-
  Revises a single day in an existing itinerary while preserving global constraints.
  Use when user asks to change Day N, reduce pace, or adjust one day only. Do NOT
  re-run Phase B parse.
---

# Revise Day

## DO
- Load global POI occupancy + must-visit coverage + adjacent day areas
- Run `revise_day` tool then global verifier

## DO NOT
- Re-parse guide text
- Change locked items unless user requests

## Output
Updated `itinerary.json` with incremented `version`
