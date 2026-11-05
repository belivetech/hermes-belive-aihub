# Validation

Recorded 2026-11-05. This package targets Hermes `v0.22.7`, source commit `b200f5bc60e31588ba2cb60e288029c4a54ba5fa`.

## Local proof

- Hermes's canonical `scripts/run_tests.sh` ran this repository's `tests/` with the isolated host environment: **12 passed, 0 failed**.
- `hermes plugins validate <repository> --install-deps --json`: **ok**, 15 checks passed, no warnings, security scan safe, no runtime overrides of core code.
- Ruff against the pinned host's lint configuration: **all checks passed**.
- Independent source/fixture review: no remaining blockers in the changed authentication, context-lookup and endpoint boundaries.
- Fresh isolated-home install through `hermes plugins install file://<repository> --enable`: passed. A subsequent real host lookup discovered `belive-aihub` from the user-plugin layer using `chat_completions`.

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

Staging qualification passed on 2026-11-05 using the installed GitHub source and the real Hermes host: **3 inference requests in 4.58 seconds**, with **USD 0.00069719 total customer cost**. The job covered non-streaming text, a streamed tool call, execution of a safe local fixture-read tool, the tool result returning to the model and a streamed final answer, `17 + 38 = 55`. Every response supplied final usage. Activity and authenticated generation lookups matched the three model/usage/charge records; the prepaid balance decreased from USD 5.00000000 to USD 4.99930281 with no remaining holds. The dedicated test key was revoked afterward.

The campaign used a separately prepaid organisation, enforcement enabled, no auto top-up, a model-restricted key with a USD 5 cap, and USD 5 daily/weekly/monthly guardrail limits. The reviewed runner enforced a persistent 12-request/600-second campaign limit, exact staging origin/key/model, a 1024-token reply cap, fixture-only tools and no redirects or discretionary paid retries. Offline real-agent and guard-regression proof passed first. Independent review found no remaining runner blockers; gateway credit/exposure/concurrency coverage passed 86 tests.

Sanitized request records are in [qualification-2026-11-05.json](qualification-2026-11-05.json). Vision remains unqualified. No production inference was performed. Customer-charge matching does not establish supplier invoice reconciliation. [Catalog PR #149509](https://github.com/NousResearch/hermes-agent/pull/149509) is submitted and awaits maintainer review; catalog acceptance is not claimed.
