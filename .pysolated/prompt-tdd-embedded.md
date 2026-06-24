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

Complete the task using test driven development (TDD)

## TDD Philosophy

**Core principle**: Tests should verify behavior through public interfaces, not implementation details. 
Code can change entirely; tests shouldn't.

**Good tests** are integration-style: they exercise real code paths through public APIs. 
They describe _what_ the system does, not _how_ it does it. 
A good test reads like a specification - "user can checkout with valid cart" tells you exactly what capability exists. 
These tests survive refactors because they don't care about internal structure.

**Bad tests** are coupled to implementation. 
They mock internal collaborators, test private methods, or verify through external means (like querying a database directly instead of using the interface). 
The warning sign: your test breaks when you refactor, but behavior hasn't changed. 
If you rename an internal function and tests fail, those tests were testing implementation, not behavior.

### Test examples

#### Good Tests

**Integration-style**: Test through real interfaces, not mocks of internal parts.

```python
# GOOD: Tests observable behavior
async def test_user_can_checkout_with_valid_cart() -> None:
    cart = create_cart()
    cart.add(product)
    result = await checkout(cart, payment_method)
    assert result.status == "confirmed"
```

Characteristics:

- Tests behavior users/callers care about
- Uses public API only
- Survives internal refactors
- Describes WHAT, not HOW
- One logical assertion per test

#### Bad Tests

**Implementation-detail tests**: Coupled to internal structure.

```python
# BAD: Tests implementation details
async def test_checkout_calls_payment_service_process() -> None:
    with patch("myapp.checkout.payment_service") as mock_payment:
        await checkout(cart, payment)
    mock_payment.process.assert_called_once_with(cart.total)
```

#### Red flags:

- Mocking internal collaborators
- Testing private methods
- Asserting on call counts/order
- Test breaks when refactoring without behavior change
- Test name describes HOW not WHAT
- Verifying through external means instead of interface

```python
# BAD: Bypasses interface to verify
async def test_create_user_saves_to_database() -> None:
    await create_user({"name": "Alice"})
    row = await db.query("SELECT * FROM users WHERE name = ?", ["Alice"])
    assert row is not None

# GOOD: Verifies through interface
async def test_create_user_makes_user_retrievable() -> None:
    user = await create_user({"name": "Alice"})
    retrieved = await get_user(user.id)
    assert retrieved.name == "Alice"
```
### Mocking guidelines

#### When to Mock

Mock at **system boundaries** only:

- External APIs (payment, email, etc.)
- Databases (sometimes - prefer test DB)
- Time/randomness
- File system (sometimes)

Don't mock:

- Your own classes/modules
- Internal collaborators
- Anything you control

#### Designing for Mockability

At system boundaries, design interfaces that are easy to mock:

**1. Use dependency injection**

Pass external dependencies in rather than creating them internally:

```python
# Easy to mock
def process_payment(order, payment_client):
    return payment_client.charge(order.total)

# Hard to mock
def process_payment(order):
    client = StripeClient(os.environ["STRIPE_KEY"])
    return client.charge(order.total)
```

**2. Prefer SDK-style interfaces over generic fetchers**

Create specific functions for each external operation instead of one generic function with conditional logic:

```python
# GOOD: Each method is independently mockable
class Api:
    def get_user(self, id):
        return fetch(f"/users/{id}")

    def get_orders(self, user_id):
        return fetch(f"/users/{user_id}/orders")

    def create_order(self, data):
        return fetch("/orders", method="POST", body=data)

# BAD: Mocking requires conditional logic inside the mock
class Api:
    def fetch(self, endpoint, options):
        return fetch(endpoint, options)
```

The SDK approach means:
- Each mock returns one specific shape
- No conditional logic in test setup
- Easier to see which endpoints a test exercises
- Type safety per endpoint

## TDD Anti-Pattern: Horizontal Slices

**DO NOT write all tests first, then all implementation.** This is "horizontal slicing" - treating RED as "write al
l tests" and GREEN as "write all code."

This produces **crap tests**:

- Tests written in bulk test _imagined_ behavior, not _actual_ behavior
- You end up testing the _shape_ of things (data structures, function signatures) rather than user-facing behavior
- Tests become insensitive to real changes - they pass when behavior breaks, fail when behavior is fine
- You outrun your headlights, committing to test structure before understanding the implementation

**Correct approach**: Vertical slices via tracer bullets. One test → one implementation → repeat. Each test respond
s to what you learned from the previous cycle. Because you just wrote the code, you know exactly what behavior matt
ers and how to verify it.

```
WRONG (horizontal):
  RED:   test1, test2, test3, test4, test5
  GREEN: impl1, impl2, impl3, impl4, impl5

RIGHT (vertical):
  RED→GREEN: test1→impl1
  RED→GREEN: test2→impl2
  RED→GREEN: test3→impl3
  ...
```

## Definition of deep modules

From "A Philosophy of Software Design":

**Deep module** = small interface + lots of implementation
This is good

```
┌─────────────────────┐
│   Small Interface   │  ← Few methods, simple params
├─────────────────────┤
│                     │
│                     │
│  Deep Implementation│  ← Complex logic hidden
│                     │
│                     │
└─────────────────────┘
```

**Shallow module** = large interface + little implementation (avoid)
This is bad

```
┌─────────────────────────────────┐
│       Large Interface           │  ← Many methods, complex params
├─────────────────────────────────┤
│  Thin Implementation            │  ← Just passes through
└─────────────────────────────────┘
```

When designing interfaces, ask:

- Can I reduce the number of methods?
- Can I simplify the parameters?
- Can I hide more complexity inside?

## Interface design for testability

Good interfaces make testing natural:

1. **Accept dependencies, don't create them**

```python
# Testable
def process_order(order, payment_gateway):
    ...

# Hard to test
def process_order(order):
    gateway = StripeGateway()
    ...
```

2. **Return results, don't produce side effects**

```python
# Testable
def calculate_discount(cart) -> Discount:
    ...

# Hard to test
def apply_discount(cart) -> None:
    cart.total -= discount
```

3. **Small surface area**
   - Fewer methods = fewer tests needed
   - Fewer params = simpler test setup

## TDD Workflow

### 1. Planning

When exploring the codebase, use the project's domain glossary so that test names and interface vocabulary match the project's language, 
and respect ADRs in the area you're touching.

Before writing any code:

- [ ] Identify opportunities for deep modules
- [ ] Design interfaces for testability
- [ ] List the behaviors to test (not implementation steps)
- [ ] Get user approval on the plan

Consider what the public interface should look like and which behaviors are most important to test

**You can't test everything.** Focus testing effort on critical paths and complex logic, not every possible edge case.

### 2. Tracer Bullet

Write ONE test that confirms ONE thing about the system:

```
RED:   Write test for first behavior → test fails
GREEN: Write minimal code to pass → test passes
```

This is your tracer bullet - proves the path works end-to-end.

### 3. Incremental Loop

For each remaining behavior:

```
RED:   Write next test → fails
GREEN: Minimal code to pass → passes
```

Rules:

- One test at a time
- Only enough code to pass current test
- Don't anticipate future tests
- Keep tests focused on observable behavior

### 4. Refactor

After all tests pass, look for [refactor candidates](refactoring.md):

- [ ] Extract duplication
- [ ] Break long methods into private helpers (keep tests on public interface)
- [ ] Deepen modules (move complexity behind simple interfaces)
- [ ] Move logic to where data lives
- [ ] Apply SOLID principles where natural
- [ ] Consider what new code reveals about existing code
- [ ] Run tests after each refactor step

**Never refactor while RED.** Get to GREEN first.

## Checklist Per Cycle

```
[ ] Test describes behavior, not implementation
[ ] Test uses public interface only
[ ] Test would survive internal refactor
[ ] Code is minimal for this test
[ ] No speculative features added

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