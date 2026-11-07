# Validation

Recorded 2026-11-05; media backends added 2026-11-07 (last section). This package targets Hermes `v0.22.7`, source commit `b200f5bc60e31588ba2cb60e288029c4a54ba5fa`.

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

Sanitized request records are in [qualification-2026-11-05.json](qualification-2026-11-05.json). Vision remains unqualified. No production inference was performed. Customer-charge matching does not establish supplier invoice reconciliation. The catalog entry was accepted on 2026-11-05: maintainers landed it through [NousResearch/hermes-agent#149641](https://github.com/NousResearch/hermes-agent/pull/149641) (merge `3987a6bfc3`, which closed [#149509](https://github.com/NousResearch/hermes-agent/pull/149509)), as `plugin-catalog/belive-aihub.yaml`, tier `community`, pinned to `5d022b5a7190c673162965f4593b5a86f14e2c84`. A change to this repository reaches catalog installs only through a new reviewed SHA-bump PR.

## Hermes v0.23.0 (2026-11-06)

Re-checked against Hermes `v0.23.0` (`2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`) in an isolated environment and home:

- `scripts/run_tests.sh` on this repository's `tests/`: **12 passed, 0 failed**.
- `hermes plugins validate <repository> --install-deps --json`: **ok**, 15 of 15 checks, no warnings.
- `hermes plugins search belive` lists the entry, and `hermes plugins install belive-aihub --enable` installs it by catalog name at the pinned SHA `5d022b5` (`hermes plugins info`: version 0.1.0). The catalog is read live: the entry is not in the `v0.23.0` tag, which predates the merge.
- The installed plugin registers `belive-aihub` (BeLive AI Hub, `chat_completions`, `BELIVE_TOKENHUB_API_KEY`); Hermes's own `tencent-tokenhub` still reads `TOKENHUB_API_KEY`.

No inference was run for this re-check.

## Native media backends (2026-11-07)

Adds `media/`, the `belive-aihub-media` plugin (version 0.2.0, with the provider's manifest bumped to match). It registers backends for four Hermes tools through the host's own provider ABCs, `register_image_gen_provider`, `register_video_gen_provider`, `register_tts_provider` and `register_transcription_provider`; no core code is touched. It is a separate plugin because Hermes imports a `kind: model-provider` plugin only through its provider loader, which does not call `register(ctx)`; the provider keeps its root install path and the media plugin installs from the `media` subdirectory (`hermes plugins install belivetech/hermes-belive-aihub#media`).

**Hermes surfaces, v0.23.0 (`2bd378e`):** image generation (`agent/image_gen_provider.py`, `image_generate`), video generation (`agent/video_gen_provider.py`, `video_generate`), text-to-speech (`agent/tts_provider.py`, `text_to_speech`) and speech-to-text (`agent/transcription_provider.py`); the registrar table is `hermes_cli/plugins_registrars.py`. **No media-specific surface:** music and other audio generation, 3D generation, asynchronous speech jobs, chat-route speech models, reference-video inputs, live transcription (the ABC has a streaming session but AI Hub has no matching route) and `n > 1` images. Image and video understanding is not a plugin surface: `vision_analyze` and `video_analyze` (`tools/vision_tools.py`) call the auxiliary client, which the existing `belive-aihub` model provider already serves. Only the general `register_tool` could add a music tool; it was not used.

**Local proof.** Hermes's canonical `scripts/run_tests.sh` on this repository's `tests/`, run against v0.23.0 and against v0.22.7 (`b200f5b`): **66 passed, 0 failed** on each (12 existing, 54 new). Ruff with the host's lint configuration: all checks passed. `hermes plugins validate <repository> --install-deps --json` and the same for `<repository>/media`, on both hosts: **ok**, no warnings, security scan safe, no runtime overrides of core code, isolation verdict `host`. The new tests cover: request construction and routes for every adapter; the live catalogues and refusal of an unlisted model before any request; size, duration, resolution and aspect clamping; edit uploads and refusal of non-image sources; video submit with one stable `Idempotency-Key`, resume without a second submit, reconciliation by key after an uncertain submit, failed and missing jobs, bounded poll failures, storage-link download without the key and refusal of a same-origin redirect, non-MP4 downloads; the six speech formats and `ogg`; transcription of MP3 and WAV, job polling and the never-raise envelope; official-base enforcement, profile-scoped key resolution, per-key catalogue caching, sanitized network errors with one attempt, and a real local HTTP server proving a redirect is not followed; and the plugin installed into an isolated home and driven through Hermes's own `image_generate`, `video_generate`, `text_to_speech` and `transcribe_audio`. Fixture transports prove request construction; the live proof below proves the gateway.

**Live proof (staging only).** Hermes v0.23.0, a fresh isolated `HERMES_HOME`, both plugins installed from this repository at `d5d755c` with `hermes plugins install file://<repo>[#media] --ref <sha>`, the staging base and an existing staging inference key held only in the process environment (never printed, written or committed). Every call went through the real host tool; a transport guard allowed only the staging host and counted paid requests. The gateway's own generation records were read back for every paid request.

| Tool | Model | Result | Generation id | USD |
| --- | --- | --- | --- | --- |
| `image_generate` | `qwen/qwen-image-2.1-turbo` | PNG 1024 x 1024 | `req_qi5wbgcaws1e5fp6g3ry89vnmq` | 0.016 |
| `image_generate` with a source image | `qwen/qwen-image-3.0-edit` via `/images/edits` | PNG 1024 x 1024; the red apple became green | `req_274kpcgjufjnd70jru9k52won9` | 0.03135 |
| `video_generate`, 2 s, 480p, 16:9 | `alibaba/wan3.0-video` | job `job_sg8d19j8z7glyg326b20fzly21`; MP4 h264 854x480 2.00 s + aac; 92.9 s; content served directly | `req_kzjop72gf9qkul7j3xpx8iegd2` | 0.095 |
| `text_to_speech`, seven formats | `qwen/qwen-audio-3.0-tts-flash`, voice `loongjohn` | wav, mp3, opus, ogg, aac, flac and pcm all returned and decoded; `opus` and `ogg` are Ogg Opus in `.ogg` | seven ids in the record | 0.000375 each |
| speech-to-text of the WAV clip | `qwen/qwen-audio-3.0-asr-flash` | "Hello from Believe AI Hub." | `req_vaeukismtegs0unnopkyg4uysu` | 0.000105 |

Recorded charges total **USD 0.14508** over 11 paid requests, each with `cost_complete` true and matching the price table. A first image request made before the generation id was reported cost another USD 0.016 and its id was not captured. `vision_analyze` and `video_analyze` also passed through the `belive-aihub` provider on `qwen/qwen3.8-flash` (this closes the earlier "vision remains unqualified" for this provider); their ids and charges were not captured, estimated under USD 0.01. Total spend is well under the USD 2 allowance. No retries, no production calls and no supplier-invoice claim. Sanitized records are in [qualification-2026-11-07-media.json](qualification-2026-11-07-media.json).

**Not exercised live:** reference-image video input, first-frame video input, an interrupted-and-resumed video job and a long (job-answered) transcription, all of which are covered by fixtures only; a storage-link video download (the gateway served the bytes directly); production; Hermes Desktop pickers.

**Catalog.** The accepted entry `belive-aihub` pins `5d022b5` (provider only). Reaching catalog installs needs a reviewed SHA bump for it and a new `belive-aihub-media` entry with `subdir: media`; neither is opened.

## Request ids in results and failures (0.2.1, 2026-11-07)

Each failure that reached AI Hub now ends with `(AI Hub request <id>)`, from the gateway's `X-Generation-Id` header; a header that is not a plain id is ignored. Transcriptions also return the upload's id as `generation_id`, like image results and video jobs already did. A transcription job that fails is named by the upload's id.

- Tests on Hermes v0.23.0: **68 passed, 0 failed** (`scripts/run_tests.sh`, as in Reproduce).
- Staging, 2026-11-07, the backends called directly with only the staging key: a transcription of the WAV clip with `qwen/qwen-audio-3.0-asr-flash` returned `generation_id` `req_rgbz018jwtwrfv7o5f43rltnbp`, and `GET /generation?id=` on it returned the model, `alibaba-model-studio` and USD 0.000245. A speech request with an unknown voice failed with the gateway's voice list and `(AI Hub request req_35fu3m40riygvhu7oevi31mv1s)`; that refusal is uncharged and `GET /generation` answers 404 for it.
