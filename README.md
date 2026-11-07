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

## Models and billing

The plugin discovers models from `https://tokenhub-api.belive.sg/api/v1/models` and lists entries whose catalog explicitly advertises ready Chat Completions with tools and text output. It preserves the gateway's exact `author/slug` IDs. Non-chat models are excluded. If discovery fails, Hermes uses the small curated model list; that list is not a guarantee that every account may access every model. A successful empty response remains an empty list.

Hermes owns context-length and other metadata lookup. The plugin's public `live_context_length` hook reads the same live catalog so a named provider receives the correct context window; it does not introduce a second metadata or pricing registry. Check the AI Hub Models page for current availability and prices, and Activity for actual usage and charges. Local estimates are not an invoice.

`agent.reasoning_effort` is sent as AI Hub's `reasoning` object (`{"enabled": true, "effort": "medium"}`), and `provider_routing` (`order`, `only`, `ignore`, `sort`, `require_parameters`) as its `provider` object, from version 0.2.3; earlier versions sent neither. AI Hub refuses a `provider` field it does not serve, such as `data_collection`, with HTTP 400 instead of ignoring it.

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
