# R39 additive finalization order

1. `python finalize_r39.py mechanical` checks frozen source-stage identities, actual build/PDF bindings, two-build byte identity, PDF link geometry, all169render artifacts and61mapped operations. It does not claim visual inspection.
2. Finish the three independently authored visual receipts. The finalizer requires explicit passing status, exact inspected page inventory and PDF hash binding. Normalize its reader to the actual receipts if necessary; never fabricate inspection fields.
3. Complete all helper changes before `python finalize_r39.py prepare`. This creates immutable `replay/FINAL_STAGE.json` containing a hash inventory of all final evidence except itself, the future final independent receipt and top manifest.
4. A separate independent validator reads/replays that inventory, source operations, build comparisons, source-page mapping and actual visual receipts. It writes `replay/FINAL_INDEPENDENT_REVIEW.json` with `passed: true` only after its checks succeed, plus `final_stage_sha256` matching the immutable stage snapshot. Its detailed checks and limitations belong in that receipt. No automated self-review claim replaces this validation.
5. Run `python finalize_r39.py seal` last, then `python check-manifest.py`. The top manifest hashes the already-fixed independent receipt. The earlier snapshot never gets rewritten to hash its own future reviewer. Source-stage pending-state fields remain historical evidence; the additive final stage explains their temporal scope.

No registry admission, Git action or generated-source composition is authorized by these helpers.
