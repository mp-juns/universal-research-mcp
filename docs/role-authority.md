# URAG Role Authority

The `agent-governance/2.0` role registry is the authority source. It contains
exactly seven operational roles and four critical reviewers. A task packet may
reduce authority but never
expand it: its allowed actions must be an intersection of the selected role
manifest and user-approved scope; its forbidden actions must retain all role
prohibitions.

Auditors and critical reviewers are read-only. `correction_executor` may modify
only approved derived artifacts and cannot verify-close its own correction.
`research_memory_maintainer` may repair a derived index only through a future
approved write boundary and may never rewrite canonical history.

`scope_and_cost_governor` is always active. It decides whether a plan is
required, emits a declaration-based cost/resource estimate, and rejects a
proposed operation outside the task's actions, paths, sources, providers,
capabilities, plan references, or cost ceiling. It may request a user decision
but cannot approve or execute an operation, handle credentials, or grant itself
more authority.

`host_visualization` means a Codex host visualization skill and is
off by default. It requires both task-scope inclusion and explicit user opt-in.
`data_plot_generation` is a separate capability and never implies permission to
invoke the host visualization skill.
