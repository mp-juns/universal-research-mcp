# Rebench ablation v1.1 — activation prescriptions (2026-08-29)

Amendment preregistered before every run (`benchmarks/protocols/
rebench-ablation-v1.1-amendment.md`, commits `f18194f`/`38d98f3`/`ab884d4`
— including the disclosed v1 fixture-contamination and the pilot
scope-stall). 3 arms × 24 × 3 = 216 new runs, all completed and reported.
Numbers: `rebench-ablation-v1.1-20260829.json`.

![fig5](assets/fig5-layers.png)

## Arms (primary fault stratum, 45 runs each)

| arm | activation | unsafe | clean | RD vs filesystem (24/45) |
| --- | ---: | ---: | ---: | --- |
| mcp_natural (clean rerun) | 0/45 | 24/45 | 21/21 | 0.000 [boot −0.067, 0.067], p=1.0 |
| mcp_agentsmd | **42/45 (93%)** | **12/45** | 21/21 | 0.267 [0.064, 0.441], mid-p 2.4×10⁻⁴ |
| mcp_tooldesc | 0/45 | 25/45 | 21/21 | −0.022 [−0.219, 0.177], p=0.69 |

- The clean natural rerun reproduces the contaminated v1 number exactly
  (24/45, zero MCP calls) — the v1 contamination changed nothing, as the
  conservative-bias argument predicted, and the headline now rests on
  uncontaminated runs.
- **Tool-description triggers are a dead lever**: verifiably delivered in
  the tool schema, zero activation, no safety change.
- **AGENTS.md with scope preapproval recovers engagement** (93%
  activation, ~8 MCP calls per run, shell abandoned; +85% tokens) and
  halves unsafe — but fails the preregistered validation bar
  (unsafe ≤ 2): 12 activated runs still asserted unsafely.

## Why activated runs still failed: evidence selection, measured

All 12 unsafe activated runs held **eligible** receipts — no blocked
receipt was overridden. Trace decomposition: in 9/12 the model fetched
the faulted source, saw the integrity mismatch in the fetch result
(current sha ≠ indexed sha), silently dropped that source, and submitted
only the remaining intact evidence to the gate, which correctly ruled the
cited subset eligible; in 3/12 it never fetched the faulted source at
all. The gate verifies the integrity of what you cite, not what you
omit. The scaffolded benchmark's instruction to pass "each returned
claim_gate_reference unchanged" was therefore itself a protection layer:
it removed the model's freedom to cherry-pick.

First honest claim-type self-report measurement (77 gate calls under no
prescription): 9.1% downgrades, all from `causal` (two-source rule) to
one-source types — the self-report surface is real but small here.

## Protection is three separable layers, all now measured

1. **Enforcement** (gate blocks what it sees): perfect — 0/45 scaffolded.
2. **Activation** (the workflow runs at all): zero natural, zero via tool
   schemas, 93% via repository policy file with scope preapproval.
3. **Citation discipline** (all fetched evidence reaches the gate):
   scaffold-provided; absent under AGENTS.md alone — 12/42 activated runs
   cherry-picked or under-fetched to an eligible-but-unsafe citation set.

Design consequence (implementable server-side, no model cooperation
required): the eligibility receipt can disclose **fetched-but-uncited
evidence** — the server already knows the session's fetch history and
which fetches failed integrity; a receipt that lists omitted mismatched
fetches would make 9 of these 12 failures visible to the caller and to
audit. Complementary: the scope ASK-FIRST stall (pilot) shows
non-interactive use needs a scope-preapproval surface; the AGENTS.md
paragraph used here is the working pattern.

## Negative control

poisoned-before-registration: 6/6 unsafe in agentsmd and tooldesc, 4/6 in
the natural rerun (coincidental abstentions) — unchanged: no layer can
catch corruption that precedes registration.
