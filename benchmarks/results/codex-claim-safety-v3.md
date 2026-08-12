# Codex Claim-Safety Instrumentation Pilot v3

## Status

Exploratory live instrumentation pilot; not a confirmatory benchmark. It used
`gpt-5.6-terra`, low reasoning effort, and a synthetic source bundle. It does
not establish general hallucination reduction, research-outcome improvement, or
provider/model superiority.

## Conditions

- **Filesystem:** direct, read-only local source retrieval.
- **MCP-gated:** Universal Research candidate/evidence retrieval only; direct
  source tools were prohibited by the task contract.

This diagnostic is not the preregistered attached-versus-not-attached primary
comparison.

## Observed results

| Measure | Filesystem | MCP-gated |
| --- | ---: | ---: |
| Completed trials | 14 | 14 |
| Host-reported input tokens | 437,417 | 847,320 |
| Host-reported output tokens | 3,583 | 3,727 |
| Wall latency | 155.559 s | 285.935 s |
| Post-index source-mutation trials | 6 | 6 |
| Changed source accepted as verified evidence | 6 / 6 | 0 / 6 |
| Correct abstentions on mutation trials | 0 / 6 | 6 / 6 |

The six mutation trials cover four questions; two were repeated in fresh
workspaces. Every filesystem trial answered from altered source text. Every
MCP-gated trial detected stale retrieval and abstained.

On eight unmodified normal or uncertainty tasks, both conditions generally
produced source-grounded answers. A deterministic keyword scorer rejected one
semantically correct amendment answer in both arms; it is not a product
difference.

## Bounded interpretation

In this synthetic MCP-gated diagnostic, hash-bound retrieval prevented use of a
post-index source mutation as verified evidence in 6/6 trials, at 1.94x input
tokens and 1.84x wall latency across all completed trials. It does not show a
general reduction in hallucination or ordinary overconfident phrasing.

Raw model reasoning and command output are intentionally not published.
