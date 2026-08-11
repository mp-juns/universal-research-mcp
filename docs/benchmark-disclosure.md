# Development Benchmark Disclosure

## Status

This is an archived development measurement, not a confirmatory evaluation.
The public repository intentionally excludes raw worker transcripts, private
fixture material, and benchmark-run logs. The summary below preserves the
outcome and its limitations without treating the result as product evidence.

## Observed setup

- Host: Codex.
- Model reported by the host: `gpt-5.6-terra`, high reasoning effort.
- Cases: 10 paired public synthetic lexical questions.
- Conditions: direct file lookup versus evidence pre-fetched through the real
  read-only MCP search and hash-verification path.
- MCP operations: 20 verified search/fetch operations for the prefetch
  treatment.
- Token measure: host-reported `input_tokens + output_tokens +
  reasoning_output_tokens`; cached input is displayed separately by the host
  and is not added a second time.

## Observed results

| Measure | Direct file | Verified MCP prefetch |
| --- | ---: | ---: |
| Source-text fact matches in the non-mutating audit | 10 / 10 | 10 / 10 |
| Non-overlapping host-reported tokens | 229,372 | 113,931 |
| Shell command events after prefetch | 20 | 0 |

The MCP-prefetched treatment reported 115,441 fewer non-overlapping tokens
(50.3%) for this exact synthetic fixture.

## Result eligibility and limitations

The original one-shot strict evaluation recorded `terminal_failed`: its scorer
required an exact answer string and a canonical event identifier in cases where
the direct-file condition supplied a valid path reference or quoted evidence.
A later source-text audit did not change the retained answers and found both
conditions matched all ten documented values. That audit supports the narrow
observations above, but it does not convert the run into a passed confirmatory
benchmark.

This result must not be interpreted as a claim about price, latency, general
model quality, research quality, contradiction handling, citation entailment,
long-horizon reproducibility, independent agent behavior, or performance on
real research corpora. In particular, the treatment received centrally
pre-fetched evidence; it is not an end-to-end autonomous-agent comparison.
