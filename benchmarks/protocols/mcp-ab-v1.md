# Protocol: Universal Research MCP Paired A/B Benchmark v1

## Status

`proposed`; preparation only. Live execution requires a separate human approval
record that binds the final configuration and artifact fingerprints.

## Research question

For research-operation tasks over the same immutable evidence corpus, does the
Universal Research MCP workflow improve evidence-grounded quality and policy
compliance relative to ordinary read-only filesystem retrieval, and what are
the associated changes in tokens, model/tool calls, latency, and cost?

## Hypotheses

- Primary quality hypothesis: MCP increases the preregistered composite quality
  score without increasing unsupported claims or policy violations.
- Resource hypothesis: MCP may increase input tokens, calls, and latency; these
  costs are reported rather than treated as failure by themselves.
- Integrity hypothesis: MCP increases valid source-line/hash verification and
  detection of changed artifacts.
- No `better`, `faster`, `optimal`, or causal claim is permitted before the
  confirmatory paired run and blinded review are complete.

## Experimental conditions

### A — filesystem

The agent receives the frozen task prompt and read-only access to the exact
source snapshot available to condition B. It may use bounded ordinary file
search and line fetch. It receives no research MCP, MCP tool schema, or
MCP-specific workflow instruction.

### B — mcp

The agent retains the same bounded filesystem tools and additionally receives
the installed Universal Research MCP. Candidate search is not evidence;
material claims that use MCP retrieval must use evidence fetch and integrity
status. The MCP remains read-only. Runs stay in this intention-to-treat arm even
when the agent chooses not to call MCP.

### Optional diagnostics

A sham MCP arm can expose the same tool-schema/prompt overhead but return no
useful evidence. An MCP-only arm can replace filesystem retrieval to measure the
interface rather than the deployment effect. Both are excluded from the primary
A/B inference unless registered before data collection.

## Controlled variables

Every A/B pair must have the same:

- task, task version, and source-bundle SHA-256
- model provider, model ID/revision, endpoint, decoding settings, and seed
- system/user prompt content other than the condition-specific tool contract
- maximum turns, model calls, tool calls, output tokens, wall time, and retries
- runtime/OS/Python class and dependency lock
- pricing snapshot and evaluator rubric
- source visibility; neither condition receives hidden answer keys

The condition order is seeded and randomized within each task/repetition pair.
Each trial starts in a fresh process/context with a unique cache namespace.

## Task taxonomy

The frozen holdout set must cover:

1. evidence retrieval with exact line citation
2. claim support/refutation judgment
3. approval and execution-scope governance
4. artifact mutation and hash-integrity detection
5. negative-result and stopped-work preservation
6. human/AI/external contribution attribution
7. amendment history without canonical rewrite
8. uncertainty and Expected/Observed/Interpretation separation

Task authors and evaluators may inspect answer keys. Benchmark agents may not.
Fixtures must be new synthetic or separately licensed material and cannot be
copied from the reference project or implementation test prompts.

## Phases and sample size

### Instrumentation pilot

- Minimum 18 development tasks spanning all registered types, 1 repetition per
  condition.
- Used only to find harness, scoring, timeout, and task-ambiguity defects.
- Pilot outcomes cannot support product-quality or superiority claims.

### Confirmatory run

- Freeze the corrected task set and all fingerprints after pilot review.
- Minimum 72 independent holdout tasks and 2 paired repetitions per
  task/condition. Pilot variance may change this to 72–144 only through a
  human-approved amendment frozen before any holdout execution.
- Analyze all started trials by intention-to-treat; failures, timeouts, retries,
  and stopped runs remain in the canonical trace.
- Any sample-size change, endpoint change, task removal, model change, or budget
  change requires a dated amendment before inspecting confirmatory outcomes.

## Co-primary endpoints

The primary effectiveness endpoint is binary `audit_ready_success`. It is one
only when all task-applicable correctness, exact citation/integrity, approval,
negative-result/uncertainty, and critical-policy requirements pass.

The primary burden endpoint is provider-reported total model tokens summed over
every attempt in agent execution. If unavailable, standardized non-overlapping
tokens are reported as a sensitivity analysis rather than silently substituted.

The two co-primary outcomes are not collapsed into one arbitrary score. Their
joint interpretation distinguishes dominance, quality–cost trade-off,
inferiority, and inconclusive results.

The following blinded, equally weighted composite is secondary:

- task success
- factual correctness
- evidence grounding
- citation validity
- policy compliance
- uncertainty calibration

Each component is scored in `[0, 1]`. Weights are frozen before execution.

## Secondary endpoints

- unsupported claim count and rate
- policy violation count and rate
- valid citations / citations emitted
- verified evidence items per tool call and per response KB
- integrity mismatch detection rate
- provider-reported and standardized non-overlapping model tokens
- input, output, cached input, cache write, visible output, and reasoning tokens
- model calls, retries, MCP calls by tool, filesystem calls, other tool calls
- tool request/response/content bytes and estimated context tokens
- run wall latency, model latency, MCP latency, retry latency
- provider-billed and normalized-list cost
- tokens/cost/latency per successful run
- quality per 1,000 tokens and quality per normalized USD

## Token and call accounting

Token accounting is limited to agent execution for the primary resource
comparison. Setup/index build and evaluator/judge usage are recorded in separate
scopes and never added to agent execution.

Provider-reported usage is authoritative. Gateway-reported, locally measured,
tokenizer-estimated, derived, and unavailable values are labeled separately.
Unavailable is `null`, never zero. Raw provider usage is retained by artifact
hash so inclusion semantics can be audited.

Cached and reasoning token inclusion differs by provider. The harness must not
blindly calculate `input + output + reasoning`. It reports:

- provider total tokens, when supplied
- standardized non-overlapping tokens, only when adapter semantics are known
- token-by-category values with provenance
- authoritative-usage coverage

MCP itself consumes bytes and latency, not billable model tokens. Tool request,
response, and content bytes plus tokenizer-estimated context are recorded as
retrieval overhead. If a tool result is included in a later model request, its
actual billing is already represented by provider input usage and must not be
added again.

All calls count started, completed, failed, and retried operations. Retry
consumption remains included and links to the original call.

## Pricing

Prices are not hardcoded into the harness. A frozen pricing artifact records
provider/model revision, effective date, input/cache/output/reasoning rates,
request/tool prices, currency, source, and SHA-256.

- `provider_billed_cost`: direct provider/gateway report when available
- `normalized_list_cost`: recomputed for both arms using one frozen snapshot

Normalized list cost is the primary cost comparison. Discounts and credits are
reported separately.

## Environment isolation

Each trial gets a new sandbox directory containing only:

- frozen task prompt and public task metadata
- one read-only copy or bind mount of the source bundle
- condition-specific configuration
- an empty output/trace directory
- an independently rebuilt derived index for the MCP condition

Required controls:

- no shared conversation, cache, writable index, or output between trials
- no access to repository history, answer keys, evaluator notes, reference
  project, other condition output, or prior runs
- source and canonical ledger mounted/read as read-only
- MCP SQLite opened with `mode=ro` and `query_only`
- network denied except the explicitly approved model API endpoint
- credentials injected through environment/secret store only and redacted from
  child environment snapshots
- subprocess command expressed as an argument array, never shell-expanded text
- wall/token/call/output limits enforced outside the agent process
- failure/timeout preserves partial trace and stderr hash, not secret content

Both arms receive bounded read/search tools scoped to the same source root. The
MCP arm additionally receives only the read-only MCP tools. Neither arm can
write canonical data or execute research actions.

## Fingerprints

Every run records a reproducibility fingerprint over:

- benchmark/config/task/source-bundle versions and SHA-256
- repository Git commit and installed package version
- Core/schema/MCP versions and tool-schema hash
- model provider/ID/revision and decoding/budget settings
- system/user prompt hashes
- runtime OS, Python, dependency lock, locale, and timezone
- ledger/index/artifact fingerprints
- pricing snapshot ID

## Evaluation

Deterministic checks run first: required output shape, citation path/range,
source containment, hash/integrity status, approval answer, and forbidden action.

At least two human evaluators then score answers with condition labels removed.
Disagreement is retained and adjudicated by a third human. If an LLM judge is
used as a secondary analysis, its calls/tokens/cost are recorded under the
evaluation scope and never mixed with agent execution.

## Statistical analysis

- Analyze the attached-versus-not-attached contrast by intention-to-treat and
  pair on task ID and repetition.
- Report arm means, medians, IQR, completion/failure/timeout counts.
- Report paired mean and median differences and task-cluster bootstrap 95% CIs;
  use a paired randomization/sign-flip test for the confirmatory endpoint.
- Stratify by registered category and difficulty.
- Report intention-to-treat first, then completed-pair sensitivity analysis.
- Report authoritative-token coverage and estimated-token sensitivity analysis.
- Resource measures with long tails also report log-ratio where defined.
- Apply Holm correction within the registered secondary-endpoint family.
- Ratio metrics and ICER are secondary; if quality does not improve, report
  `no quality gain` or `dominated`, not a favorable efficiency ratio.

## Stopping and amendments

Stop immediately for secret exposure, source leakage, cross-arm contamination,
write outside the trial output root, unplanned network access, fingerprint
mismatch, or more than 10% instrumentation failures in the pilot.

Do not delete failed trials. Protocol/config/task changes are append-only
amendments and require human approval before resuming.

## Reporting boundary

The final report must separate Expected, Observed, Interpretation, and
Uncertainty. Search scores, pilot outcomes, and successful CI do not establish
research benefit. Only the frozen confirmatory set supports bounded conclusions.
