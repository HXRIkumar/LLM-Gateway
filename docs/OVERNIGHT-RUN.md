# Overnight Run — Build Phases 2 → 5 Autonomously

This is the operator runbook for building the rest of the Conduit roadmap in one unattended session and pushing it to GitHub. The detailed specs live in `docs/phases/PHASE-02..05-*.md`; this file is what you actually paste and how you run it.

## Before you start (2-minute checklist)

- **Phase 1 is on `main` and pushed.** If not, the goal will do it first, but ideally merge/tag/push Phase 1 beforehand so `origin/main` already exists.
- **GitHub auth is live.** You've run `gh auth login` as `HXRIkumar` over HTTPS, so `git push` works non-interactively — the run pushes after every phase, so progress is safe on the remote even if it stops early.
- **Start Claude Code in unattended mode.** Turn on auto-accept / bypass-permissions and make sure it has Docker access. Hands-off breaks on the first permission prompt otherwise.
- **Free the ports (or let the override handle it).** Your other stacks hold 5432/6379; the run recreates the gitignored `docker-compose.override.yml` to remap host ports for its live checks. `:8080` should be free.
- **Expect a long run.** This is ~30+ tasks across four phases. It checkpoints after every task (a commit) and every phase (a merge + tag + push), so it resumes cleanly and you keep whatever it finishes.

## The goal to paste

```text
/goal
Autonomously build the entire remaining Conduit roadmap — Phase 2 (Reliability), Phase 3 (Smart Routing), Phase 4 (Observability), Phase 5 (Optimization) — in that order, in one unattended run, committing continuously and pushing to GitHub after each phase. Do NOT stop for confirmation between steps. Keep going until all four phases meet their Definition of Done, or until a genuine blocker you cannot work around.

PREREQUISITE: Phase 1 is merged to main, make check is green, and main is pushed to origin (github.com/HXRIkumar/LLM-Gateway, gh authenticated over HTTPS). If Phase 1 is not merged/green/pushed, do that first (merge feat/phase-1-mvp → main, tag v0.1.0, push), then proceed.

AT THE START OF EVERY PHASE: read CLAUDE.md in full (esp. §4 layout, §5 pipeline, §7 guardrails, §10 status), docs/ROADMAP.md (that phase's scope + Definition of Done + non-goals), and that phase's task file in docs/phases/. Work its tasks strictly top-to-bottom.

PER-TASK CADENCE — repeat autonomously for every task, no human check-in:
1. Implement the task into the layout in CLAUDE.md §4. Reliability/routing/optimization logic is PURE in domain/ behind ports; its state lives in Redis/Postgres via infra/ adapters — never the reverse. Telemetry is wired in infra/telemetry and instrumented at services/ boundaries, never inside domain/.
2. Write its tests (pure logic → unit with injected clock/rng/stats/embedder; anything touching Postgres/Redis → integration on testcontainers; providers and embeddings mocked via respx / a mock port). Re-run the Phase 1 OpenAI-SDK compat gate to prove no regression.
3. Run make check (lint + types + tests). If red, fix and re-run until green — never advance or commit on a failing check.
4. Commit with a Conventional Commit message scoped to that task.
5. Tick that task's boxes in the phase file and update CLAUDE.md §10.
6. Move to the next task immediately.

PER-PHASE GATE + PUSH: do each phase on a branch feat/phase-N-<slug> cut from main. When a phase's entire exit checklist and its ROADMAP Definition of Done hold and make check is green:
- Verify no secret-bearing file is tracked: .env must be gitignored and untracked, and docker-compose.override.yml must not be tracked. If either is tracked, STOP and report — never push secrets.
- Merge the branch into main with git merge --no-ff, tag the release, and push: git push origin main; git push origin --tags; git push origin <branch>.
- Never force-push. If origin/main has diverged, git pull --rebase origin main resolving conflicts in favor of this repo's real files; if a clean push still isn't possible without a force, STOP and report.
- Then start the next phase from the updated main. Pushing the working branch between tasks to preserve progress is encouraged.

PHASE SEQUENCE (file → release tag → new ADR):
- Phase 2 — docs/phases/PHASE-02-RELIABILITY.md → tag v0.2.0 → ADR-0005. New dependency: arq.
- Phase 3 — docs/phases/PHASE-03-ROUTING.md → tag v0.3.0 → ADR-0006. Concrete model names MUST keep routing exactly as in Phase 1.
- Phase 4 — docs/phases/PHASE-04-OBSERVABILITY.md → tag v0.4.0 → ADR-0007. Fill deploy/otel, deploy/prometheus, deploy/grafana.
- Phase 5 — docs/phases/PHASE-05-OPTIMIZATION.md → tag v0.5.0 → ADR-0008. Embeddings behind a mockable port.

INVARIANTS FOR THE WHOLE RUN (CLAUDE.md §7): the OpenAI-compatible contract (request/response/error/streaming shape) must NOT regress and the compat gate must stay green at every phase; providers are called only from adapters (never api/ or domain/); domain/ imports no framework/vendor code; no secrets, API keys, or full prompt/response bodies in logs, traces, metric labels, or the repo; every schema change ships an Alembic migration; add no dependency beyond those named in the phase files / stack table without an ADR; a cache/dedup/semantic hit must be byte-identical to a fresh OpenAI-compatible response and must never fire for tool/vision/non-deterministic requests.

BLOCKER POLICY: providers and embeddings are mocked and datastores are real via testcontainers, so no external credential is needed to build or prove any phase. If some piece can only be verified with a live external service you don't have (real OpenAI/embedding calls, or a headless GUI like Grafana), verify everything you can (configs parse, metric series and dashboard queries resolve, mocked paths pass), record the caveat in CLAUDE.md §10, skip ONLY that unverifiable piece, finish everything else in the phase, and report it at the end. Do NOT halt the whole run over a single blocked item. If truly and irrecoverably blocked on a whole phase, commit + push what's done, record why in §10, and stop with a clear report.

DONE WHEN: Phases 2–5 each have their full exit checklist ticked and their ROADMAP Definition of Done satisfied; all four are merged to main; tags v0.2.0, v0.3.0, v0.4.0, v0.5.0 are pushed to origin; CLAUDE.md §10 marks the roadmap complete (next area: post-V5 hardening); make check is green on main. Then print a final summary: what was built per phase, every ADR added (0005–0008), the tags pushed, and any caveats or blocked items — and stop.
```

## If it stops early — resume one phase

Because state lives in commits and `CLAUDE.md` §10, resuming is trivial: open the session, and if you want to drive a single phase, paste the template below with the row filled in from the table. (The master goal also resumes on its own — it re-reads §10 and continues — so this is only if you want tighter control.)

```text
/goal
Continue the Conduit build: complete PHASE <N> — <NAME> autonomously, end to end, then push. Do NOT stop for confirmation between steps.

Prerequisite: the previous phase is merged to main and green (make check passes, compat gate intact). Work on branch feat/phase-<N>-<slug> cut from main. If the previous phase isn't merged/green, STOP and report.

Read first: CLAUDE.md in full; docs/ROADMAP.md (Phase <N> scope + Definition of Done); docs/phases/<PHASE FILE>. Then implement its tasks strictly top-to-bottom.

Cadence per task: implement → write tests (unit for pure logic; testcontainers for Postgres/Redis; providers/embeddings mocked) and re-run the compat gate → make check until green → Conventional-Commit → tick the phase file + update CLAUDE.md §10 → next.

Invariants (CLAUDE.md §7): OpenAI compat must not regress; providers only from adapters; domain/ imports no framework/vendor code; no secrets/bodies in logs/traces/labels/repo; migrations for every schema change; new deps only if named in the phase file / stack table.

Blocker policy: mock providers/embeddings, real datastores via testcontainers; if a live-only or headless-GUI piece can't be verified, verify everything else, record the caveat in §10, skip only that piece, finish the phase, report it. Don't halt over one blocked item.

Done when: the Phase <N> exit checklist and ROADMAP DoD hold and make check is green. Then verify no secret-bearing file is tracked, merge --no-ff into main, tag <TAG>, push main + tags + branch (never force-push; on divergence pull --rebase favoring our files, else STOP and report), update CLAUDE.md §10 to point at the next phase, print a summary, and stop.
```

| N | NAME | slug | PHASE FILE | TAG | New ADR |
|---|---|---|---|---|---|
| 2 | Reliability | reliability | docs/phases/PHASE-02-RELIABILITY.md | v0.2.0 | ADR-0005 |
| 3 | Smart Routing | routing | docs/phases/PHASE-03-ROUTING.md | v0.3.0 | ADR-0006 |
| 4 | Observability | observability | docs/phases/PHASE-04-OBSERVABILITY.md | v0.4.0 | ADR-0007 |
| 5 | Optimization | optimization | docs/phases/PHASE-05-OPTIMIZATION.md | v0.5.0 | ADR-0008 |

## What you'll wake up to

- **Best case:** `main` at `v0.5.0`, four new tags on GitHub, `make check` green, `CLAUDE.md` §10 marking the roadmap complete, and a summary of everything built plus the four ADRs.
- **Realistic case:** it got partway — say through Phase 3. Then `main` is at `v0.3.0`, everything completed is green and pushed, `CLAUDE.md` §10 shows exactly where it stopped and why, and you resume with the single-phase goal above.

Either way, every finished piece is committed, green, tagged, and on GitHub. Nothing is left in a half-broken state, because the run never commits red and never merges a phase that isn't done.
