# External evidence: why a reusable project map can help

These results are **not Project Atlas measurements**. They test adjacent
techniques: repository graphs, compact reuse of prior repository context, and
leaner agent prompts. They justify measuring Atlas; they do not prove that Atlas
will reproduce the same percentages.

## Repository graph: quality improved, token cost increased

The peer-reviewed RepoGraph paper (ICLR 2025) added a repository-level code
graph to several software-engineering agents on SWE-bench Lite.

| Comparison | Without graph | With graph | Change |
| --- | ---: | ---: | ---: |
| SWE-agent average turns | 21.47 | 19.12 | **−10.9%** |
| Agentless + GPT-4o resolved | 27.33% | 29.67% | **+2.34 points / +8.6% relative** |
| Agentless + GPT-4o tokens | 42,376 | 47,323 | **+11.7%** |
| Agentless + Claude 3.5 Sonnet resolved | 27.67% | 30.33% | **+2.66 points / +9.6% relative** |
| Agentless + Claude 3.5 Sonnet tokens | 40,984 | 46,238 | **+12.8%** |

This is the useful uncomfortable result: structure can improve task success
without reducing tokens. Atlas therefore reports quality, tokens, and time
separately instead of promising that every map is cheaper.

Source: [RepoGraph, ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/file/4a4a3c197deac042461c677219efd36c-Paper-Conference.pdf),
especially Tables 2 and 6 and the turn analysis in Appendix A.

## Compact relevant context: fewer tokens and usually better results

The SWE-ContextBench preprint, arXiv v3 dated 2026-05-06, evaluates `n=376`
related repository tasks with and without a compact summary of prior work. Its
oracle summary averaged 217.1 tokens versus 25,633.7 tokens for a full
trajectory: a **99.2%** smaller reusable representation.

| Agent | Average tokens per evaluated task | Runtime | Resolved tasks |
| --- | ---: | ---: | ---: |
| Claude Sonnet 4.5, baseline | 1,701,548 | 344.47 s | 19.68% |
| Claude Sonnet 4.5, compact context | 1,352,830 (**−20.5%**) | 280.62 s (**−18.5%**) | 23.40% (**+3.72 points / +18.9% relative**) |
| GPT-5.3 Codex, baseline | 858,253 | 349.41 s | 22.60% |
| GPT-5.3 Codex, compact context | 718,920 (**−16.2%**) | 362.39 s (**+3.7%**, slower) | 23.94% (**+1.34 points / +5.9% relative**) |

These token values are averages per evaluated related task, not campaign
totals. The oracle-summary condition is also not a deployable retrieval result:
it gives the agent a gold relevant summary selected from known task
relationships and deliberately removes retrieval errors. Treat the comparison
as an upper-bound context-reuse experiment, not as measured performance of an
automatic Atlas retriever.

The same paper warns that irrelevant or unfiltered context can provide little
benefit or hurt performance. A useful map must therefore be compact,
traceable, and selectively retrieved; “more context” is not itself a win.

Source: [SWE-ContextBench v3](https://arxiv.org/pdf/2602.08316v3),
Table 3 and Sections 3.2–3.3. This is a v3 preprint dated 2026-05-06, not a
peer-reviewed Atlas benchmark.

## Leaner agent instructions: strong directional evidence, not an Atlas result

OpenAI reports that, in a sample of internal coding-agent evaluations, leaner
system prompts improved scores by roughly **10–15%**, reduced total tokens by
**41–66%**, and reduced cost by **33–67%**. OpenAI explicitly says the result
varies by workload and should be validated on representative tasks.

Source: [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model#favor-leaner-prompts).

## Calculation

Every percentage above uses one of these formulas:

```text
reduction % = (before - after) / before × 100
relative improvement % = (after - before) / before × 100
percentage-point change = after % - before %
```

Calculated relative changes are rounded to one decimal place. Source values
keep the precision shown in their tables so the arithmetic can be independently
checked.
