# BeLive AI Hub for Hermes Agent

Use [BeLive AI Hub](https://aihub.belive.sg/) as a named inference provider in Hermes Agent. Requires Hermes **0.22.7 or later**. BeLive maintains this MIT-licensed plugin; support and source are in this repository's issues. Owner contact: Hassan / BeLive Technology.

## Install and configure

Install with Hermes's supported plugin installer:

```sh
hermes plugins install https://github.com/belivetech/hermes-belive-aihub --enable
```

Create a workspace inference key in BeLive AI Hub. Make it available as `BELIVE_TOKENHUB_API_KEY` through Hermes's supported secret configuration, such as the active profile's private `.env` file. Keep the key out of shell history, source control and logs. Do not use a console login or management key.

Run `hermes model`, select **BeLive AI Hub**, and choose a model. For explicit configuration, merge into the active profile's `config.yaml`:

```yaml
model:
  provider: belive-aihub
  default: qwen/qwen3.8-flash
auxiliary:
  compression:
    provider: belive-aihub
  vision:
    provider: belive-aihub
    model: qwen/qwen3.8-flash
```

Auxiliary requests resolve the same active profile's key. Do not add `api_key` overrides to auxiliary blocks. Restart any background gateway after changing secrets.

## Without the plugin

Hermes older than 0.22.7, or a host where you do not want a plugin, can call BeLive AI Hub as a custom OpenAI-compatible provider. Hermes reads this shape from 0.18.0 through 0.23.0. It was run against production on Hermes 0.22.5 on 2026-11-09 (`qwen/qwen3.8-flash` main, `google/gemini-3.8-flash` with `-m`).

Put the key in the profile's private `.env` as `BELIVE_TOKENHUB_API_KEY=sk-tokenhub-…`. Do not use `TOKENHUB_API_KEY`: Hermes's Tencent provider reads that name. Then merge into `config.yaml`:

```yaml
providers:
  belive:                      # not "belive-aihub", which is the plugin's provider name
    base_url: https://tokenhub-api.belive.sg/api/v1
    key_env: BELIVE_TOKENHUB_API_KEY
    transport: chat_completions
    default_model: qwen/qwen3.8-flash
model:
  provider: custom:belive
  default: qwen/qwen3.8-flash
fallback_providers:
  - provider: custom
    model: z-ai/glm-5.3-flash
    base_url: https://tokenhub-api.belive.sg/api/v1
    key_env: BELIVE_TOKENHUB_API_KEY
auxiliary:
  vision:      { provider: main, model: google/gemini-3.8-flash }
  compression: { provider: main, model: qwen/qwen3.8-flash }
agent:
  reasoning_effort: low
```

`base_url` is needed on the named provider and on each `fallback_providers` entry. Auxiliary blocks use `provider: main`, which reuses the main provider's address and key. `agent.reasoning_effort` is sent as a top-level `reasoning_effort` from Hermes 0.19.0; from 0.21.4 Hermes sends `medium` when it is unset.

What you do not get without the plugin:

| Feature | Plugin | Custom provider |
|---|---|---|
| Chat, tools, fallback, vision, compression | Yes | Yes |
| Model list | Ready chat-with-tools models from the live catalogue | Every catalogue entry, media models included |
| `provider_routing` sent as AI Hub's `provider` object | Yes | No |
| Discovery address guard | Production and staging AI Hub only | None |
| Image, video, speech and transcription tools | `belive-aihub-media` | None; needs the plugins |
| Setup | `hermes model` → BeLive AI Hub | Config edit as above |

To move to the plugin later, keep the key name, set `model.provider: belive-aihub`, and remove the `providers.belive` block and the `custom` fallback entries in the same change.

## Models and billing

The plugin discovers models from `https://tokenhub-api.belive.sg/api/v1/models` and lists entries whose catalog explicitly advertises ready Chat Completions with tools and text output. It preserves the gateway's exact `author/slug` IDs. Non-chat models are excluded. If discovery fails, Hermes uses the small curated model list; that list is not a guarantee that every account may access every model. A successful empty response remains an empty list.

Hermes owns context-length and other metadata lookup. The plugin's public `live_context_length` hook reads the same live catalog so a named provider receives the correct context window; it does not introduce a second metadata or pricing registry. Check the AI Hub Models page for current availability and prices, and Activity for actual usage and charges. Local estimates are not an invoice.

`agent.reasoning_effort` is sent as AI Hub's `reasoning` object (`{"enabled": true, "effort": "medium"}`), and `provider_routing` (`order`, `only`, `ignore`, `sort`, `require_parameters`) as its `provider` object, from version 0.2.3; earlier versions sent neither. AI Hub refuses a `provider` field it does not serve, such as `data_collection`, with HTTP 400 instead of ignoring it.

**Broken streams can be resent.** Each chat request Hermes resends is a separate charge. Hermes v0.23.0 has more than one retry layer, and `agent.api_max_retries` caps only one of them. As a **partial mitigation**, set the auto-recovery layer to zero as well, so a broken stream is not resent for minutes:

```yaml
agent:
  api_max_retries: 0
  auto_recovery_cycles: 0   # default 5: waits 15 to 120 s and resends the whole request each cycle
```

With both at zero, a stream that ends in an error frame sends 2 requests (the stream and one non-streaming check). A stream that closes without `[DONE]` still sends 4: Hermes asks the model to continue three times, and no setting turns that off. This is not a no-retry guarantee: one failure can still mean two or four requests, each charged. A tool call cut off mid-arguments is not run. Measurements and the reproduction are in [VALIDATION.md](VALIDATION.md).

Use Hermes's existing `fallback_providers` settings to opt into fallback. This plugin does not enable fallback to another service, create keys, change account limits or alter other providers. Review old auxiliary/fallback overrides when migrating.

## Media: images, video, speech and transcription

Hermes loads model providers and media backends through different plugin mechanisms, so media is a second plugin in this repository, `belive-aihub-media`, installed separately:

```sh
hermes plugins install belivetech/hermes-belive-aihub#media --enable
```

It makes AI Hub the backend for `image_generate` (generation and edits), `video_generate` (resumable, idempotent jobs), `text_to_speech` (mp3, opus, aac, flac, wav, pcm) and speech-to-text, with models taken from the live AI Hub catalogues and the same `BELIVE_TOKENHUB_API_KEY`. Image and video understanding needs no media plugin: `vision_analyze` and `video_analyze` use the auxiliary vision configuration above with this provider. See [media/README.md](media/README.md) for configuration, behaviour and what Hermes has no plugin surface for.

## Migration and endpoints

This provider is **BeLive AI Hub**, not Tencent TokenHub. It does not register the `tokenhub` alias or read `TOKENHUB_API_KEY`.

To migrate an existing BeLive `custom:tokenhub` configuration, keep the BeLive secret under `BELIVE_TOKENHUB_API_KEY` and deliberately change the provider references to `belive-aihub`. Existing custom connections keep working without installing this plugin. Preserve Tencent's environment variables and settings if you use that service too.

Staging qualification uses an isolated profile and a staging inference key. Set `base_url: https://api-stg-tokenhub.belive.sg/api/v1` under **each** of `model`, `auxiliary.compression` and `auxiliary.vision`. A main-model override alone does not redirect explicit auxiliary-provider blocks; without their own endpoint they use the production profile default. No auxiliary key override is needed. Discovery accepts only the official production and staging HTTPS bases and refuses redirects. The host controls completion transport and explicit base-URL overrides: configure only an endpoint you intend to trust with your key. Proxy endpoints are not supported by this plugin's discovery.

## Update, remove and development

Use Hermes's supported plugin update/remove commands for `belive-aihub`. Catalog installations are pinned to the catalog's reviewed commit; a new catalog SHA requires upstream review. Disable/remove the plugin before restoring a custom-provider configuration. No key is deleted or revoked by plugin removal.

The plugin makes authenticated model-list requests and normal host-managed inference calls to BeLive. It reads only its declared key through the host's profile-aware API or a credential passed to discovery. It has no telemetry, self-updater, credential-file reader or background service.

Behavioral tests use isolated Hermes checkouts at `v0.22.7` and `v0.23.0`, the canonical host test runner and this repository's `tests/` directory. They exercise catalog filtering, failures/redirect refusal, host discovery, real host compression/vision credential resolution across two profiles, and the media backends driven through Hermes's own tools. See `VALIDATION.md` for recorded commands and scope before relying on a release claim.
