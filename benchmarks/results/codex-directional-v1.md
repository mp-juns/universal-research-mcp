# Codex Directional Diagnostic v1

## Status

This is an exploratory live diagnostic, not a confirmatory benchmark. It used
`gpt-5.6-terra` with low reasoning effort, a synthetic 20-line source bundle,
one run per condition, and host-reported telemetry. It establishes neither a
general quality effect nor a provider comparison.

The ordinary four-task comparison used a direct local-file condition and an
MCP-only evidence-flow condition. The latter is intentionally diagnostic, not
the repository's preregistered primary attached-versus-not-attached comparison.
The [source snapshot](codex-directional-v1-source.md) SHA-256 was
`4e8366c5223a4ae954625b048b91dc43031eaa34fecc35affb42bf179c30c72b`.

## Ordinary source tasks

| Condition | Tasks | Factual answer + evidence-line citation | Input tokens | Output tokens | Wall latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| Local filesystem | 4 | 4 / 4 | 109,004 | 714 | 42.572 s |
| Universal Research MCP | 4 | 4 / 4 | 246,791 | 1,243 | 85.933 s |

For these simple, unmodified single-source tasks, MCP added 137,787 input
tokens (2.26x total) and 43.361 seconds (2.02x total) without an observed
quality advantage. This is negative evidence for enabling governed retrieval
by default on low-risk lookups.

## Adversarial integrity task

After the canonical source was indexed, its policy line was changed to state
the opposite rule. The direct filesystem condition read that altered text and
answered `Yes`. The MCP condition detected that the derived index was stale
before retrieval and the model returned `abstain`; it did not receive altered
content as verified evidence.

| Condition | Answer | Input tokens | Output tokens | Wall latency |
| --- | --- | ---: | ---: | ---: |
| Local filesystem | `Yes` — follows altered text | 26,986 | 215 | 10.243 s |
| Universal Research MCP | `abstain` — stale index blocked retrieval | 44,145 | 230 | 15.981 s |

This is the measured use case for the MCP: integrity-sensitive or otherwise
high-risk claims where refusing unsupported evidence is more valuable than a
cheap answer. It does not show that the MCP improves ordinary factual recall.

## Evidence and limitations

Machine-readable final answers, exact host telemetry, tool-event hashes, and
the runner/source hashes are in
[`codex-directional-v1.json`](codex-directional-v1.json). The source is
synthetic; the runs were not repeated; pricing/cost was unavailable from the
host and is therefore not estimated. The report intentionally omits model
internal reasoning and raw command output.
