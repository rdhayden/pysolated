---
name: software-design-research
description: Research and compare several different approaches to implement something the user wants to build, then write a research document into the research/ directory. Iterates with the user asking clarifying questions, searches the web to learn from how others solved similar problems and to validate assumptions. Use when the user wants to explore how to build something, weigh options/libraries/architectures before committing, says "research how to build X", "what are my options for X", or "research approaches for X".
disable-model-invocation: true
---

# Software Design Research

Turn a vague "I want to build X" into a decision-ready research document that lays out
several distinct implementation approaches, what each is good for, and how it would be used.

The deliverable is a Markdown file in the `research/` directory. The work to get there is a
**loop**: clarify → search the web → validate with the user → repeat until the option space
is well understood. Do NOT jump straight to writing the document.

## Process

### 1. Clarify the goal

Before researching anything, understand what the user actually wants to build. Ask focused
questions. Cover, as relevant:

- **What** is being built and the core problem it solves (one sentence, in their words).
- **Constraints** — existing stack, language, must-use/must-avoid tools, hosting, budget, timeline.
- **Scale & quality bar** — prototype vs production, expected load, team familiarity.
- **Success criteria** — what makes one approach "better" here (speed, simplicity, cost, control).

Ask only what you can't infer from the repo or conversation. Explore the codebase first if the
build plugs into existing code. Don't over-interview — 3–6 sharp questions beats a questionnaire.

### 2. Research the web

Search for how others have solved similar problems and to **validate assumptions** — never assert
"X is the standard" from memory; confirm it. Use WebSearch to find candidates and WebFetch to read
the strongest sources. For broad sweeps, spawn parallel `Explore`/`general-purpose` agents.

Aim for **3–5 genuinely distinct approaches** (not minor variants). For each, gather: how it works,
who uses it, maturity/maintenance, known pitfalls, and a real example or write-up. Prefer primary
sources (docs, source, post-mortems) over listicles. Capture URLs — every claim needs one.

### 3. Validate with the user

Surface what you found before committing to the document. Report the candidate approaches in a
short list, name any assumption the research overturned, and confirm the comparison criteria that
matter to them. Narrow or expand the option set based on their reaction, then loop back to step 2
if gaps remain. Move on only when the approaches and trade-offs are clear and agreed.

### 4. Write the research document

Write to `research/<short-kebab-topic>.md`. **If the `research/` directory does not exist, create
it first.** Use the structure in [TEMPLATE.md](TEMPLATE.md). The document must:

- Describe each implementation option, with its trade-offs and **how it is intended to be used**.
- Cite sources inline (every non-obvious claim links to where it came from).
- End with an opinionated recommendation — not just a menu. Say which you'd pick and why, and note
  when you'd choose differently.

Then tell the user the path and give a 2–3 line summary of the recommendation.

## Notes

- This skill is for **deciding how to build** something. For a general fact-finding report on a
  topic, the `deep-research` skill is the better fit.
