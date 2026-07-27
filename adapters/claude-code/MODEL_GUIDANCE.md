# Claude Code host guidance

- `checked_at: 2026-07-26`
- `status: PROVIDER_BASED_STARTING_POINT`
- `atlas_benchmark_status: NOT_YET_ATLAS_BENCHMARKED`

This is a provider-based starting point, not a measured Atlas optimum. Re-run a
representative Atlas eval before changing the default matrix. In Run Economics,
record the selected alias, effective resolved model, provider, and Claude Code
version because aliases, provider routing, organization access, and availability
can move after `checked_at`.

## Model and reasoning starting point

| Atlas work | Model alias | Effort |
| --- | --- | --- |
| QUICK | `sonnet` | `high` |
| STANDARD | `opus` | `high` |
| FORENSIC / adversarial | `best` | `xhigh` |

At `checked_at`, `best` uses Fable 5 when the organization has access to it;
otherwise the latest Opus model. On the Anthropic API and Claude Platform on
AWS, `opus` currently resolves to Opus 5. Other providers differ. Opus 5
requires Claude Code v2.1.219 or later. A recorded Claude Code v2.1.207 run is
historical evidence, not the current alias contract: at that version, `opus`
resolved to Opus 4.8 on the Anthropic API and Claude Platform on AWS. Aliases
vary by provider and update over time, so treat them as moving provider labels
rather than pinned model identities.

Provider guidance treats each model's default effort as the general starting
point. Move to a larger model for a genuinely harder or more ambiguous problem;
raise effort when the model skipped files, tests, or verification rather than
when it lacked the capability to solve the problem.
Use `max` only for an exceptional quality-first session after a representative
eval shows a material gain over `xhigh`; provider guidance warns about
diminishing returns and overthinking. Do not make `max` a global Atlas default.
`ultracode` is a session-only Claude Code setting, not an effort level: it uses
`xhigh` plus dynamic workflows for substantive tasks. Use it only for a hardest
substantive block when workflows are available and the extra usage is explicitly
budgeted; it is not a default for Atlas or a global setting. The
`--effort ultracode` form requires Claude Code v2.1.203 or later. Codex remains
the primary adapter.

For one-off deeper reasoning on one turn, use the exact `ultrathink` keyword; it
does not change API effort. `haiku` is allowed only for bounded extraction or
classification, never for a whole Atlas run.

## Native interaction

Ask 1-3 questions per interaction. There may be unlimited total questions, but
continue only while an answer can materially change scope, safety, evidence
interpretation, output, or the next authorized action. Apply a semantic stop
when the remaining gaps can safely stay `UNKNOWN`.

Every question must show exactly four visible choices. Do not claim a native
question picker for this adapter. Use plain-chat A-D, where D is
`Другое — напишу сам`, and accept free text. Accept the literal answer
`не знаю`; do not turn it into a guessed decision.

## Usage display

Show an exact weekly remaining percentage only when the host exposes the exact
weekly bucket together with its timestamp and source. If any of those fields is
missing, omit the quota line entirely; never infer quota from token estimates,
message counts, or a different usage window.

## Provider sources

- <https://code.claude.com/docs/en/model-config#model-aliases>
- <https://code.claude.com/docs/en/model-config#choose-an-effort-level>
- <https://claude.com/blog/claude-model-and-effort-level-in-claude-code>
- <https://claude.com/blog/introducing-dynamic-workflows-in-claude-code>
