# ADR-0001 — Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-01-05
- **Deciders:** Core maintainers

## Context
Conduit is meant to read as production infrastructure with visible architectural maturity. Decisions made implicitly get relitigated, and a reader (or an AI coding agent) cannot tell which choices are deliberate versus accidental. A project constraint is that *every abstraction must have a reason*.

## Decision
We keep lightweight Architecture Decision Records in `docs/adr/`, one file per significant, hard-to-reverse decision, using this short format: Context, Decision, Consequences, Alternatives. ADRs are immutable once accepted; to change a decision we add a new ADR that supersedes the old one (and mark the old one `Superseded by ADR-XXXX`).

A decision warrants an ADR when it: fixes a technology or datastore, defines a public contract, sets a system-wide pattern, or introduces a non-trivial dependency. Small, local, reversible choices do not need one.

## Consequences
- The "why" behind big choices is discoverable and stable.
- Contributors (human or Claude Code) read the relevant ADR before contradicting a decision, and add an ADR to change one.
- A little overhead per real decision; none for routine code.

## Alternatives considered
- **No records** — rejected; leads to drift and repeated debate.
- **A single design doc** — rejected; conflates stable decisions with evolving detail. The living design lives in `docs/ARCHITECTURE.md`; the decisions and their rationale live here.
