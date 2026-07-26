# Depth Selection

Select the cheapest mode that still contains the consequence of being wrong.

## Signals

Assess repository size only after assessing:

- production exposure and cost of error;
- personal, regulated, security-sensitive, or financial data;
- runtime, state-store, and state-writer counts;
- automatic decisions and authority conflicts;
- retries, non-idempotent effects, rollback, recovery, and partial states;
- deprecated, duplicated, or overlapping implementations;
- maintainer and agent count;
- expected project lifetime and migration scope.

## Routing

Choose QUICK when the project is low-risk, has one dominant runtime, has a small state surface, and needs a concise continuation note.

Choose STANDARD when the project is active or long-lived, has several meaningful contours, crosses runtime or authority boundaries, or needs current and target architecture separated into routed documents.

Choose FORENSIC when one strong signal or several combined signals make omissions expensive: critical failure impact, production handling of sensitive or financial data, legacy overlap in a high-consequence path, four or more runtimes sharing state, complex authority over automation, or a large unknown migration surface.

Do not lower the mode because the repository has few files. A tiny payment webhook can require FORENSIC depth. Do not raise the mode because generated or vendored files inflate the count.

Automatic selection keeps the complete safe inventory, but derives topology and size thresholds only from the product contour. Tests, fixtures, examples, templates, generated documentation, nested documentation, and conventional root-level test or support filenames remain available evidence without becoming runtime, writer, authority, structural-size, or high-impact semantic signals by vocabulary alone. A byte-identical adapter Skill copy may be collapsed only when its canonical core counterpart is present; unrelated identical services remain distinct.

High-impact semantic signals come from bounded high-confidence project-declaration units or explicit operator inputs. Eligible repository units are root README paragraphs, Python module/class/function docstrings, leading source comments, and explicit allowlisted config keys; arbitrary string literals and regular-expression bodies are not declaration evidence. Do not merge unrelated units into one compound risk claim, and do not interpret a standalone storage `transaction` as financial activity. An inferred automatic-decision signal requires one declaration unit to name both an automatic decision or state-changing action and the authority or override that governs it; filenames, automatic formatting, and a merely documented authority boundary are insufficient. A compound FORENSIC reason such as production financial data, high-consequence legacy overlap, or shared state across several runtimes requires co-evidence in one declaration unit or explicit supplied signals.

## Explicit override

Honor an explicit mode. Every completed mode records exactly these fields in its canonical scope section:

- `Selected by`: the actor or selection source;
- `Conflicting automatic signals`: the recommended mode and conflicting signals, or an explained matching recommendation;
- `Intentionally omitted coverage`: the coverage omitted by this decision, or an explained absence of a lower-depth override;
- `Escalation condition`: an observable trigger that requires a deeper mode or a stop for new authority.

Each field appears once and contains a substantive value. Empty values, `UNKNOWN`, and bare placeholders such as `NONE` or `N/A` do not complete the record.

Escalate during investigation if newly confirmed evidence crosses a higher mode's risk boundary. Do not silently downgrade.
