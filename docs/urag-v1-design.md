# URAG v2 Design for Local Host Environments

URAG is a local-first governance pack layered on Universal Research Memory. It
keeps inference outside its deterministic policy engine. The current supported
integration supplies its fixed role contract, evidence boundary, and gate result
to Codex; model selection and execution remain host-owned.

The governance v2 implementation contains the manifest-backed eleven-role
registry, canonical hashes, task and decision validation, deterministic workflow
transitions, gate aggregation, a non-executing governance surface, and the
`urgov` local diagnostic CLI. The policy engine itself cannot dispatch, write,
or call a model. Separate derived-index and bounded harness boundaries perform
those operations only after scope validation.

The Codex adapter renders an already validated packet into a host-owned request
and captures a structured decision without access to the private host scheduler.
Provider-backed harness code and non-Codex adapters are retained design
prototypes, not supported runtime integrations in the 0.3.1 preview. Any future
host keeps its own model entitlement and tool permission layer.
