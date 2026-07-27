# Codex host guidance

- `checked_at: 2026-07-26`
- `status: PROVIDER_BASED_STARTING_POINT`
- `atlas_benchmark_status: NOT_YET_ATLAS_BENCHMARKED`

This is a provider-based starting point, not a measured Atlas optimum. Re-run a
representative Atlas eval before changing the default matrix. In Run Economics,
record the selected label and the effective resolved model because host labels,
aliases, and availability can move after `checked_at`.

## Model and reasoning starting point

| Atlas work | Model | Reasoning effort |
| --- | --- | --- |
| QUICK / read-heavy | GPT-5.6 Terra | `medium` |
| STANDARD | GPT-5.6 Sol | `high` |
| FORENSIC / adversarial | GPT-5.6 Sol | `xhigh` |

Use `max` only for the hardest quality-first block after a representative eval
shows a material gain over `xhigh` and the user has seen the block's token
budget. Never set `max` or `ultra` globally.

## Native interaction

Ask 1-3 questions per interaction. There may be unlimited total questions, but
continue only while an answer can materially change scope, safety, evidence
interpretation, output, or the next authorized action. Apply a semantic stop
when the remaining gaps can safely stay `UNKNOWN`.

Every question must show exactly four visible choices. Use plain chat with A-D
unless the native picker can preserve the exact four-choice contract without
adding another control. D is exactly `Другое — напишу сам`.

Accept the literal answer `не знаю`; do not turn it into a guessed decision.

## Usage display

Show an exact weekly remaining percentage only when the host exposes the exact
weekly bucket together with its timestamp and source. If any of those fields is
missing, omit the quota line entirely; never infer quota from token estimates,
message counts, or a different usage window.

## Provider sources

- <https://learn.chatgpt.com/docs/pricing#what-are-the-usage-limits-for-my-plan>
- <https://developers.openai.com/api/docs/guides/latest-model#using-gpt-5-6>
