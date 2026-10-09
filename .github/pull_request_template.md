## What and why

<!-- What this changes, and the problem it solves. Link the issue if there is one. -->

## How it was checked

<!-- Tests added or changed; anything run by hand (dev network, a real instrument). -->

## Checklist

- [ ] Tests cover the change (and a fix has a test that failed before it)
- [ ] `uv run pytest`, `uv run lint-imports`, and `labnet driver build --check` pass
- [ ] No lab's names, addresses or instruments in `packages/`, `examples/` or `tests/`
- [ ] Contracts stay backward compatible (no renumbered or retyped proto fields)
- [ ] Docs in `docs/` and a line in `CHANGELOG.md` under "unreleased"
- [ ] Leases, limits, approvals and grants are no weaker (or this is discussed above)
