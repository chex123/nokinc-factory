# GPT-6 Luna provider evidence capture

This capture records the first-party evidence used by the GPT-6 Luna rate card.
It records repository verification time separately from any provider-effective
date. It is a compact factual extract, not a reproduction of either full page.

## Capture metadata

Retrieved directly over HTTPS as Markdown on 2026-09-28. Both responses returned
HTTP 200 with `Content-Type: text/markdown; charset=utf-8`.

| Source | Response date | Last modified | ETag | Decoded-body SHA-256 (UTF-8) |
| --- | --- | --- | --- | --- |
| [GPT-6 Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna.md) | 2026-09-28 18:48:23 UTC | Not supplied | Not supplied | `561a86af72eced9a76a4e3a96c45ae7fff7782732514a8429b55ebddb4404945` <!-- pragma: allowlist secret --> |
| [API pricing page](https://developers.openai.com/api/docs/pricing.md) | 2026-09-28 18:48:23 UTC | 2026-09-28 18:23:20 UTC | `W/"fe49a5be045824662f1cdafca856143b"` | `0de899a93b1d9a8c7cf7b3a01ed3553337636c7ff374153821f218a203a203c8` <!-- pragma: allowlist secret --> |

The digests are SHA-256 over each complete decoded Markdown response re-encoded
as UTF-8. The relevant facts below are from those responses; the full pages are
not copied into this repository.

## Captured model facts

The model page identifies the default snapshot and API model ID as `gpt-6-luna`.
It lists a 1,050,000-token context window, 922,000 maximum input tokens, and
128,000 maximum output tokens.

The model page's pricing section lists Standard text rates per 1 million tokens:

| Metric | Standard price |
| --- | ---: |
| Input | $0.10 |
| Cached input | $0.01 |
| Cache writes | $0.125 |
| Output | $0.50 |

It states that requests above 272,000 input tokens use 2x input and cache rates
and 1.5x output for the full request.

The pricing page's Standard pricing data row for `gpt-6-luna`, per 1 million
tokens, is:

| Context | Input | Cached input | Cache writes | Output |
| --- | ---: | ---: | ---: | ---: |
| Short | $0.10 | $0.01 | $0.125 | $0.50 |
| Long | $0.20 | $0.02 | $0.25 | $0.75 |

The long-context row is consistent with the model page's published multipliers.
These are Standard rates, not Batch, Flex, Fast, or regional-processing rates.

## Effective-date finding

A case-insensitive line scan of both complete decoded responses for `effective`
or `billing begins` returned no matches. The model page's pricing section and the
pricing page's GPT-6 Luna Standard row contain no model-specific effective-from
date. This is a finding about the captured responses only; it does not establish
when OpenAI first made the rates effective or claim that no other provider
source exists.

The rate card therefore uses 2026-09-28 as this repository's first verification
date (`known_from`), not as an OpenAI-announced effective date. No live provider
model probe was performed as part of this capture.