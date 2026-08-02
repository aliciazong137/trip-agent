---
name: trip-parse-guide
description: >-
  Parses pasted travel guide text into structured poi-list.json for a single city.
  Use when guide text is provided or when extracting POIs from guides. Do NOT use
  for day scheduling, map routing, or budget optimization.
---

# Parse Guide (Phase B)

## DO
- Output `poi-list.json` matching `schemas/poi-list.schema.json`
- Every POI must have `source_refs`
- Enrich from `data/pois/{city}.json` whitelist when possible

## DO NOT
- Schedule Day1/Day2
- Invent POI names not in guide or whitelist

## Output
`context/{sessionId}/poi-list.json`
