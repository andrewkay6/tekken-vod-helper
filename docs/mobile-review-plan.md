# Mobile boundary review — delivery requirement

Updated 2026-11-07 from Andy's clarification: the current Tk desktop review is an interim interface;
he ideally wants to verify footage and make recovery decisions on a phone. Existing fullstack work must
be inspected before another UI/rewrite is proposed. This does not cancel Qwen's shared observation and
boundary-reasoning fixes already in progress.

Current truth: Boundary review is a desktop app; no mobile review route has been verified or delivered.
The earlier roughly 75% completion estimate concerned BB9's desktop review-to-export workflow, not a
mobile delivery estimate. Boundary correctness/coverage and independent validation remain separate
unfinished requirements on either interface.

## Existing fullstack work

Restored TVH chat history identifies **plan/full-stack-single-app**. A saved tool result at
2026-11-06T20:51:05.838Z lists local stashes on that branch: one labelled “full-stack planning docs +
recovered backup” and another “ts implementation”. Their contents have not been inspected here.
The current devbox refs and current GitHub remote heads do not contain that branch or those stashes.
Andy will arrange to push the WIP later. A normal branch push does not transfer stash entries, so the
saved implementation/planning work needs to be included deliberately when it is made available.

Do not fetch from the old desktop, restore stashes, commit/push or start a replacement frontend as part
of the current review. Assess the actual branch/files once available, preserving the current dirty tree.
Clarification questions should use normal chat; Andy cannot answer the question-popup flow directly
through tmux.

## Required mobile experience

- Play a short preview around each proposed start/end, seek backward/forward and inspect more context.
  Do not require downloading the entire ~57 GB master to a phone. Determine browser-compatible media
  delivery after inspecting the existing stack; no encoding is authorized by this note.
- Answer New match / Same match / Other-adjust / Unsure, edit boundaries, approve explicit partials,
  preserve contradictions/unknowns, and see which clips remain held.
- Review unclaimed intervals; claim missing matches/partials or describe what was visible.
- Choose a bracket match, confirm side orientation and correct per-game characters without silently
  overwriting observed evidence with remembered defaults.
- Save/resume/undo from the same durable project/decision journal used by the backend. No CLI or JSON
  editing for ordinary review. Handle stale concurrent views without silently losing decisions.
- Normal review actions use zero LLM inference. Runtime investigation stays separately bounded, quoted
  and authorized, with shared budgets, saved-answer reuse and explicit stops.

Reuse/test the existing headless review/controller rules rather than duplicating boundary semantics in
JavaScript. Inspect the fullstack branch's actual server, player, project API and authentication/access
model before choosing adapters or a web framework. Keep reference evaluation separate from human review.
Source media and inference remain on the appropriate existing machines; assess access using the existing
private network. Do not change host services, firewall, credentials, router, Mac model services or the
separate sandbox architecture under this planning note.

Next concrete deliverable after locating the branch: a read-only comparison and a scoped mobile review
integration plan, identifying what can be reused and a first end-to-end phone review case. Preserve all
uncommitted work, owner checkpoint and raw evidence. No new inference, encoding, deployment, commit or
push is authorized by this note.
