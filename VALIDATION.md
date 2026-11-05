# Validation

Recorded 2026-11-05. This package targets Hermes `v0.22.7`, source commit `b200f5bc60e31588ba2cb60e288029c4a54ba5fa`.

## Local proof

- Hermes's canonical `scripts/run_tests.sh` ran this repository's `tests/` with the isolated host environment: **12 passed, 0 failed**.
- `hermes plugins validate <repository> --install-deps --json`: **ok**, 15 checks passed, no warnings, security scan safe, no runtime overrides of core code.
- Ruff against the pinned host's lint configuration: **all checks passed**.
- Independent source/fixture review: no remaining blockers in the changed authentication, context-lookup and endpoint boundaries.

Tests cover protocol/tool eligibility, malformed/empty catalogs, bounded discovery errors, credential-redacted logging, redirect/origin refusal, missing keys, Tencent identity separation, live context lookup through Hermes's actual named-provider resolver, and actual host compression/vision client requests for two profile-scoped keys on production/staging endpoints. HTTP fixture transports prove request construction and routing; they do not prove a live model invocation.

## Reproduce

Clone the official Hermes repository at the source commit above. Follow that version's PM developer workflow to build a separate environment with the `dev` and `test` groups. From that checkout:

```sh
HERMES_PYTHON=/absolute/path/to/hermes-test-environment/bin/python \
HERMES_HOME=/absolute/path/to/disposable-hermes-home \
scripts/run_tests.sh /absolute/path/to/hermes-belive-aihub/tests -q \
  --override-ini=pythonpath=/absolute/path/to/hermes-checkout
```

Use that isolated host's CLI to validate the package:

```sh
hermes plugins validate /absolute/path/to/hermes-belive-aihub --install-deps --json
```

## Qualification status

No paid qualification has run. The approved campaign has a maximum USD 5 allocation for Hermes, using a separately prepaid staging organisation, verified admission enforcement and bounded request/time limits. Staging signup requires normal human CAPTCHA and email verification. No production inference call is authorized by this campaign.

Catalog submission and a qualified release remain pending live acceptance. Do not describe this source publication or the fixture tests as upstream acceptance, live qualification or supplier invoice reconciliation.
