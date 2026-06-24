# Research Document Template

Fill in every section. Keep prose tight. Every non-obvious claim gets an inline source link.

```md
# Research: <what is being built>

**Date:** <YYYY-MM-DD> · **Status:** Draft | Reviewed

## Goal

One paragraph: what we want to build and the problem it solves, in the user's terms.

## Constraints & Criteria

- **Constraints:** stack, language, must-use/avoid, hosting, budget, timeline.
- **Success criteria:** what makes one approach better here (ranked if possible) —
  e.g. simplicity > cost > raw performance.
- **Assumptions validated:** assumptions we checked against sources, and any that were overturned.

## Options

Repeat this block for each of the 3–5 approaches.

### Option N: <name>

- **How it works:** the approach in 2–4 sentences.
- **How it's intended to be used:** the shape of the solution — key components, the
  workflow a developer follows, and a minimal usage sketch if helpful.
- **Good for:** the situations where this is the right call.
- **Trade-offs:** cost, complexity, maturity, lock-in, team familiarity, known pitfalls.
- **In the wild:** who uses it / a real example. [source](url)

## Comparison

A short table scoring each option against the success criteria.

| Option | <criterion 1> | <criterion 2> | <criterion 3> | Notes |
|--------|---------------|---------------|---------------|-------|
| Opt 1  | …             | …             | …             | …     |

## Recommendation

The option to pick and **why**, tied back to the criteria. Note the conditions under which a
different option would win. Be opinionated.

## Open Questions

Anything still unresolved or worth spiking before committing.

## Sources

Bulleted list of the key sources consulted, with URLs.
```
