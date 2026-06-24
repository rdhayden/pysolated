# ISSUES

Here are a set of GitHub issues:

!`gh issue list --state open --json number,title,body,comments`

You will work on one AFK (away from keyboard) issue only, not the HITL (human in the loop) ones.

When the task is complete, output <completion>ISSUE-DONE</completion>
If there are not more AFK issues, output <completion>NO-MORE-ISSUES</completion>
If the all open AFK issues have unresolved dependencies output <completion>AWAITING-DEPENDENCIES</completion>

# TASK SELECTION

Pick the next task. Prioritize tasks in this order:

1. Critical bugfixes
2. Development infrastructure

Getting development infrastructure like tests and types and dev scripts ready is an important precursor to building features.

3. Tracer bullets for new features

Tracer bullets are small slices of functionality that go through all layers of the system, allowing you to test and validate your approach early. This helps in identifying potential issues and ensures that the overall architecture is sound before investing significant time in development.

TL;DR - build a tiny, end-to-end slice of the feature first, then expand it out.

4. Polish and quick wins
5. Refactors

# EXPLORATION

Explore the repo.

# IMPLEMENTATION

Complete the task using the tdd skill

# FEEDBACK LOOPS

Before committing, run the feedback loops:

- run tests for any files that have changed
- run mypy in strict mode for the files that have changed
- run ruff to check formatting and linting for files that have changed

# COMMIT

Make a git commit. The commit message must:

1. Include key decisions made
2. Include files changed
3. Blockers or notes for next iteration

# THE ISSUE

If the task is complete, close the original GitHub issue.

If the task is not complete, leave a comment on the GitHub issue with what was done.

# FINAL RULES

ONLY WORK ON A SINGLE TASK. If you receive a multi-phase plan, only work on a single phase of that plan.