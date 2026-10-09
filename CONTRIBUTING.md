# Contributing to LabNet

Thanks for helping. LabNet drives real instruments in real labs, so changes
are held to two rules: every change comes with tests, and nothing may weaken
a safety check (leases, limits, approvals, grants) without a discussion
first.

## Setting up

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/mill0851/labnet && cd labnet
uv sync                  # every package, editable, with the test tools
uv run labnet dev up     # a whole simulated lab on 127.0.0.1
```

[Development](docs/development.md) describes the dev network, the change
loop and the repository layout.

## Before you open a pull request

CI runs these; run them first:

```bash
uv run pytest
uv run lint-imports
uv run labnet driver build --core --check --import-check
uv run labnet driver build --check --project packages/labnet-sim
uv run python .github/scripts/no_site_names.py
uv run --group docs mkdocs build --strict      # if you changed docs/
```

- **Tests.** A bug fix comes with a test that failed before it; a feature
  with tests for what it does and what it refuses.
- **Generated code.** Never edit `_generated/` or `grpc/generated/` by hand:
  change the `.proto` and run `labnet driver build`.
- **Contracts.** A deployed `.proto` stays backward compatible: never
  renumber or retype a field, add new fields as optional, and reserve
  removed numbers ([contract compatibility](docs/drivers.md#contract-compatibility)).
- **No lab in the code.** A lab's names, addresses and instruments belong in
  its site folder (`sites/<lab>/`) and its docs, not in `packages/`,
  `examples/` or `tests/`. `no_site_names.py` checks.
- **Docs.** Update the page in `docs/` that describes what you changed, and
  add a line to [CHANGELOG.md](CHANGELOG.md) under "unreleased".
- **Style.** Match the code around your change: its naming, comment density
  and error messages, which say what went wrong and what to do.

## New instruments

A new instrument family is a **driver package** of its own, in its own
repository: `labnet driver new acme-thing --service AcmeThing` starts one
that builds and passes its tests ([Writing a driver](docs/drivers.md)).
This repository keeps only LabNet itself and `labnet-sim`. Changes to
LabNet that a driver needs are welcome here.

## Reporting bugs and asking for features

Open an issue with the templates. For anything that could let someone use an
instrument without a lease, outside its limits, or without an approval, **do
not open an issue**: follow [SECURITY.md](SECURITY.md).

## Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
