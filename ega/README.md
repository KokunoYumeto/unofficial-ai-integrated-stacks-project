# EGA discovery and integration scaffold

This directory is a public, machine-readable working scaffold for comparing
*Éléments de géométrie algébrique* (EGA 0--IV) with a pinned Stacks Project
base. It is maintained as a Mathematics Commons Stacks-compatible extension:
official Stacks remains a sync source and reference layer, while upstream
acceptance is not a production gate. It is **not** a claim that EGA has been
fully rewritten in Stacks form or formally verified, and it implies no
endorsement by the Stacks Project.

The complete standalone English EGA source is admitted only as discovery
text. Canonical source claims remain gated by the separately maintained
diplomatic French edition and its page-level authority receipts. The edition
trees are read-only inputs to this directory and are never copied, edited, built,
or published here. Only the bounded local additions explicitly recorded by the
EGA integration manifest enter the repository's root TeX tree.

## Current snapshot

- Current standalone publications are split by language and anonymously
  cross-checked against their GitHub releases. The French edition is
  [Zenodo 22134750](https://doi.org/10.5281/zenodo.22134750) / concept
  [21921588](https://doi.org/10.5281/zenodo.21921588), version
  `EGA-FR-complete-I-IV4-canon-current-r8-20260828`; the English edition is
  [Zenodo 22134751](https://doi.org/10.5281/zenodo.22134751) / concept
  [21921591](https://doi.org/10.5281/zenodo.21921591), version
  `EGA-EN-complete-0-IV4-canon-current-r7-20260828`. Their ten public assets
  are byte-identical across GitHub and Zenodo; exact sizes and hashes are in
  [`publication-current.json`](publication-current.json). The older omnibus
  [Zenodo 21861666](https://doi.org/10.5281/zenodo.21861666) is retained as a
  historical checkpoint, not presented as the current edition or as a release
  of this integrated Stacks repository.
- The integrated repository's latest published EGA semantic checkpoint covers EGA I
  through §6.6.3 and advances the next semantic cursor to §6.6.4. The sealed
  checkpoint receipt is [PASS](../validation/ega-i-6.6.3-semantic-checkpoint-2026-08-30.json)
  (SHA-256
  `58CC0464C1EDAC665CA72B80F8156E77773F9B983F83D76BA948551A3D15456E`). Its
  semantic content commit is `85024a5e3456cadc79c6cde67bf1fcbbc09c48cb`;
  the public release content commit is `f1b8d56b5f3c9999010455a38a289bce76735070`
  at GitHub tag
  [`ega-i-6.6.3-semantic-2026-08-30`](https://github.com/KokunoYumeto/unofficial-ai-integrated-stacks-project/releases/tag/ega-i-6.6.3-semantic-2026-08-30)
  and Zenodo version DOI
  [`10.5281/zenodo.22177421`](https://doi.org/10.5281/zenodo.22177421) under
  concept `10.5281/zenodo.22135180`. This is a semantic-only checkpoint: it
  changes no root TeX, root PDF, visual-QA, authority, issue, or errata
  registry bytes, and makes no new canon correction claim. The fixed point has
  256 agent rows, 328 decision rows, 1,242 operational statement edges, 1,249
  statement-map rows, 825 residual rows, 102 issue rows, and zero quarantined
  rows. I000104 remains routed pending successor adjudication; no root theorem
  is justified by this slice.
- The separate local implementation now covers EGA I §6.6.4 componentwise
  and completes the omitted proof of existing tag 01K5 in `schemes.tex`.
  D000329, S001250--S001259, R000826--R000829, and A000257 record this
  source-bound slice. Its [local implementation receipt](../validation/ega-i-6.6.4-semantic-checkpoint-2026-08-31.json)
  distinguishes source and checker validation from TeX/PDF build, visual QA,
  and publication, which are not claimed complete. It creates no theorem,
  official tag, canon correction, or new visual-evidence item.
- Stacks upstream base: `a04446e57ec1fbc252a871afcec7752fb2807b14`.
- English discovery manifest: R184, 92,445 bytes, SHA-256
  `5C64ECD32FD7C5458D2599D70ED667D2CF06D95517EFFA9C6D6DCEF7626913A0`;
  127 files, 7,283,321 bytes, tree SHA-256
  `3BFB1C5103093481246EF4A6365E08544F6D5E19ACC0EA63E717F3F3643F064D`.
- Latest admitted standalone-English source and reader successor: R261,
  32,444-byte manifest at SHA-256
  `A87DC2EDD0BDA5CE6828A2759095B1F4F3278E993DC5661EBA2E345C33BEEF18`;
  127 files, 7,284,367 bytes, tree SHA-256
  `3FF379C715F99D2A28F231A54D55996E9CDA27153E5DBBFB14BA6F7F70766CB0`.
  It includes the three direct-authority lower-label-side repairs in EGA I
  5.1.5, 5.1.9, and 5.3.5. B239 seals its 1,347-page current reader. The
  deterministic R261-to-R184 reconstruction applies 41 exact inverse
  operations across twelve paths, gates every intermediate tree, rereads the
  sealed source before promotion, and never mutates the producer tree.
- French authority: production is active; the latest sealed interface marker
  is F37ZW through EGA II printed p.37, 13,345 bytes at SHA-256
  `0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0`.
  Its 18-file source tree is 1,014,921 bytes at tree SHA-256
  `5A2A1BC407D5B0395C5E0D10103E0813C4EC9EDE37668D4DCA1091D1D280A841`.
  B37AJ seals its 168-page current reader. This
  graph still binds every reviewed EGA I claim to its exact historical page receipt;
  receipt F33 also repairs 4.1.9 from `g'` to the directly verified printed
  `g` without changing the already-correct English.
  The current admitted closure is F37ZW/R261 with B37AJ/B239, D48,
  DIA48T, Q37CY, and final cleanup-lineage receipt Q37DB. DIA48T records
  53 verified and 31 pending visual items with the next cursor at EGA I
  printed p.112 diagram 1. Publication remains false.
- All malformed D48 predecessors remain byte-exact adverse history. DIA48,
  DIA48R, and DIA48S carried stale or contradictory active pointers; Q37CU,
  Q37CV, Q37CW, and Q37CX carried comma-bound permanent or supersession
  arrays. The admitted interface accepts only DIA48T and Q37DB and forbids
  these predecessors from every active field. The older B235/RF14/REF14
  failure remains separately preserved as superseded adverse history.
- Existing incremental pre-Stacks notes: 566,253 bytes, SHA-256
  `799EF17D0D7D98B2B459EA938C0ABE25647BB7018857FF8D83B656725B932196`.
  They are evidence to normalize into the new schema, not a completed
  deliverable or an upstream mapping.
- Verified discovery inventory: 127 exact source files and 9,585
  metadata-only semantic units including 445 native diagram units. No source
  prose is copied into the branch.
- Current Stacks snapshot: 21,446 chapter labels, 21,437 exact official-tag
  joins, and 2,670 lexical candidates across all 35 discovery topics. Reviewed
  lexical candidates remain exactly zero in `map.json`.
- First bounded review slice: 23 source-subsection-to-Stacks-section bridges
  across 22 EGA I subsections and eight topics. These are explicitly
  topic-level split correspondences; they claim zero theorem equivalences and
  zero complete source-subsection coverage.
- First statement-level slice: all fifteen numbered units in EGA I 1.1.1--1.1.15
  were checked directly against admitted French and the pinned Stacks base.
  `smap.csv` records 22 exact edges across fourteen units; `resid.csv` records
  every partial, derived, unlabelled, terminology, or missing-tag remainder.
  EGA I 1.1.3 has no direct tagged target.
- First local integration: EGA I Proposition 1.1.15 is now a cited Algebra
  lemma with an explicit use in `more-algebra.tex`. It fills a fact previously
  used there without a named target. Its label is local and has no official
  tag or upstream-acceptance claim.
- Second statement-level slice: all seven numbered units in EGA I 1.2.1--1.2.7
  were checked against the same direct French authority. The cumulative map
  now has 34 edges across 21 source units and 13 explicit residuals. Exact
  existing targets include 00E2, 00E3, 00E5, and 00FL.
- Second local integration: EGA I Corollary 1.2.4 supplies a general
  unit-times-image criterion for a spectrum map to be a homeomorphism onto its
  image. The new untagged lemma now supplies the common topology step in the
  quotient and localization proofs.
- Third statement-level slice: all fourteen numbered units in EGA I
  1.3.1--1.3.14 now have direct-French dispositions. The cumulative map has 56
  edges across 34 source units and 24 explicit residuals. This slice required
  no new Stacks lemma: the apparent remainders are notation or consequences of
  the affine equivalence and its exact tensor and colimit behavior.
- Fourth statement-level slice: EGA I 1.4.1--1.4.3 now have direct-French
  dispositions. The cumulative map has 62 edges across 37 source units and 28
  explicit residuals. The exact four-way quasi-coherent characterization is a
  cited local Properties lemma with no official tag. Lemma 1.4.1.1 remains a
  proof-device residual; 01PE covers 1.4.2 more generally; and 01SA--01SB split
  the algebra and module clauses of 1.4.3.
- Fifth statement-level slice: EGA I 1.5.1--1.5.4 now have direct-French
  dispositions. The cumulative map has 72 edges across 41 source units and 31
  explicit residuals. Tags 01XZ and 0GN6 contain the coherent-sheaf results;
  0EHM 01PE 01PF 01IA and 01PB give the two extension packages without a new
  omnibus lemma. This review also repaired one wrong internal reference in the
  proof of 01PI from the finite-presentation criterion to the finite-type
  criterion.
- Sixth statement-level slice: EGA I 1.6.1--1.6.10 and displayed formula
  1.6.5.1 now have direct-French dispositions. The cumulative map has 99 edges
  across 52 source units and 41 explicit residuals. Existing affine-spectrum,
  associated-sheaf, adjunction, localization, tensor, internal-Hom, and
  categorical functoriality results cover the whole slice; no new lemma or
  source correction was needed. The map-identification statement in 1.6.8 is
  deliberately not promoted to flat base-change isomorphism tag 0C6I.
- Seventh statement-level slice: EGA I 1.7.1--1.7.5 and its labelled
  global-to-local square now have direct-French dispositions. The cumulative
  map has 112 edges across 58 source units and 46 explicit residuals. Tags
  01HW 01HB 01I1 and 01I2 exactly split affine recognition and the opposite-
  rings equivalence; 01IG gives a stronger closed-immersion form of the final
  quotient monomorphism. No new Stacks lemma was needed.
- Eighth statement-level slice: EGA I 2.1.1--2.1.8 now have direct-French
  dispositions. The cumulative map has 130 edges across 66 source units and
  51 explicit residuals. Modern scheme terminology exactly absorbs EGA's
  `prescheme`; sobriety merges the Kolmogorov and generic-point results; and
  the rational-function and locally-integral clauses remain split so neither
  fieldhood nor a false stalkwise criterion is introduced. No new Stacks
  lemma was needed.
- Ninth statement-level slice: EGA I 2.2.1--2.2.10 and numbered formula
  2.2.4.1 now have direct-French dispositions. The cumulative map has 168
  edges across 77 source units and 67 explicit residuals. Existing definitions
  and lemmas cover locally ringed morphisms affine targets module mappings
  permanence locality and componentwise birationality; ordinary closedness is
  kept rigorously separate from closed immersions and universal closedness.
  No new Stacks lemma was needed.
- Tenth statement-level slice: EGA I 2.3.1--2.3.2 now have direct-French
  dispositions. The cumulative map has 178 edges across 79 source units and
  71 explicit residuals. Tags 01JB 01JC and 01IT cover unrestricted scheme
  gluing and affine reconstruction. Tag 01JE is the exact projective-line
  inversion example; the nearby doubled-origin example 01JD is explicitly
  excluded. No new Stacks lemma was needed.
- Eleventh statement-level slice: EGA I 2.4.1--2.4.8 and the labelled
  affine-chart diagram now have direct-French dispositions. The cumulative map
  has 219 edges across 88 source units and 82 explicit residuals. Tags 01J6
  and 01J7 organize local-spectrum mapping and generalizations; 00E3 and 02C6
  supply topology and stalks; 0B8M and 0BDA cover invertible modules and the
  factorial comparison. The unrestricted field-point sentence in 2.4.5 is
  false without a closed-image hypothesis and has been referred append-only
  to the canonical edition rather than silently normalized. No Stacks TeX
  change was needed.
- Twelfth statement-level slice: EGA I 2.5.1--2.5.5 and the native commuting
  triangle now have direct-French dispositions. The cumulative map has 242
  edges across 94 source units and 93 explicit residuals. Tags 01JX and 001G
  give the scheme-over-base and slice-category language; 01JB and 01HI cover
  gluing and open factorization; 01KT supplies the section definition. The
  historical change-of-base operation is explicitly distinguished from
  Cartesian base change. The printed S-morphisme type error in 2.5.5 reuses
  the canonical producer's existing correction record and is not silently
  changed or duplicated here. No Stacks TeX change was needed.
- Thirteenth statement-level slice: EGA I 3.1.1 now has a direct-French
  disposition. The cumulative map has 253 edges across 95 source units and
  100 explicit residuals. The arbitrary sum is the empty-overlap specialization
  of 01JB and 01JC with 00AL and 00AM retaining its transported sheaf layers;
  002J records the coproduct property. Tags 00ED and 01I5 cover the binary
  affine formula. The graph explicitly prevents its false extension to
  infinite ring products and leaves the English-only I.3.1 wrapper alias
  unpromoted. No Stacks TeX change was needed.
- Fourteenth statement-level slice part one: EGA I 3.2.1--3.2.5 now have
  direct-French dispositions. The cumulative map has 274 edges across 100
  source units and 115 explicit residuals. Tags 01JP and 01I4 give the exact
  fibre-product definition and affine tensor construction; 003B, 01L3, and
  01KR give invariance under a monomorphic base; 01HI and 01L7 give the
  open-base corollary. Product-in-the-slice terminology,
  tensor-versus-direct-ring-product variance, and categorical monicity remain
  explicit. No Stacks TeX change was needed.
- Fourteenth statement-level slice part two: EGA I 3.2.6--3.2.8 now have
  direct-French dispositions. The cumulative map has 299 edges across 108
  source units and 136 explicit residuals. Tag 01JM supplies global existence;
  01JR gives the open-product identification; 01JJ, 01JB, and 01JC retain the
  local gluing proof; and the two-family disjoint-sum formula remains a derived
  scheme-specific result rather than a generic categorical law. The discovery
  unit 3.2.9 is a translator augmentation citing the EGA II errata and remains
  fail-closed pending that exact French authority. No Stacks TeX change was
  needed.
- The intake registry now recognizes all 430 native Xy-pic commands as well
  as 15 tikz-cd environments. Synthetic diagram IDs are deterministic within
  their semantic parent. This repairs an actual pre-Stacks graph omission;
  source prose and diagram artwork remain uncopied.
- EGA I 3.6.1--3.6.5 now has direct-French statement and proof dispositions.
  Nilpotent thickening is separated from ordinary fibre topology; relative
  field-valued points are distinguished from absolute points; and fibre
  transitivity and local-spectrum stalk preservation retain their exact
  proof-level dependencies. The two French-labelled plain displays remain
  parent components because frozen R184 supplies no child IDs. No new gap or
  Stacks chapter edit was needed.
- EGA I 3.7.1--3.7.3 now closes section 3. Quotient-base reduction is kept
  distinct from nilreduction; the generic/special fibre model construction is
  decomposed into open base change and quasi-compact scheme-theoretic image;
  and the proper DVR point bijection is identified with the valuative
  criterion. A p.119 control phrase that could wrongly extend that bijection
  to higher-dimensional local domains is referred append-only with an exact
  projective-line counterexample. The French source itself is correct.
- EGA I 4.1.1--4.1.10 now has direct-French statement and proof
  dispositions. Quasi-coherent closure properties match the exact global
  enumeration under 01LA while retaining the affine proof dependencies;
  ideal quotients and locally closed subschemes are
  split into their sheaf and scheme layers; and factorization through a
  subscheme is separated from set-theoretic image containment. F33 and its
  P121S superseder correct the one diplomatic error found in this slice:
  printed p.121 has unprimed `g` throughout the 4.1.9 proof. The English was
  already correct. Five genuinely blank frozen page locators are now guarded
  and overlaid append-only; an empty parsed guard is never a wildcard.
- EGA I 4.2.1--4.2.5 now has direct-French statement proof and diagram
  dispositions. Immersions are separated from their canonical locally closed
  factorization; the open and closed criteria are split into topology and
  stalk or sheaf conditions; and affine closed immersions and local-on-target
  criteria retain their exact hypothesis boundaries. The printed 4.2.2 proof
  contains the already catalogued reversal in the prose type of
  `theta_y^sharp`; the proposition, diagram, continuation, and English
  correction are mathematically coherent. No Stacks TeX change or new
  mathematical gap was needed. Direct 5,000-dpi review additionally found that
  4.2.3 prints the induced global map as `Gamma(psi)` although the typed sheaf
  component is `theta`; diplomatic text stays untouched and D000161/I000052
  refer `Gamma(theta)` to the owning corrected French and English layers.
- EGA I 4.3.1--4.3.2 now has direct-French statement and proof dispositions.
  The product of two immersions is decomposed into two base changes followed by
  composition; open and closed pullbacks recover the stated inverse-image
  intersection; and 4.3.2 is exactly the base-change theorem 01JY. Direct
  5,000-dpi review found that the historical affine proof writes the kernel as
  the sum of the plain images `u(b)+v(c)`, which is false without taking the
  generated extension ideals. D000162/I000053 preserve diplomatic French and
  refer the correct tensor-extension formula to the corrected French and
  English layers. The theorem itself and its modern proof are unaffected.
- D000165 and `../reports/qsrc.csv` make the two direct-authority source-error
  witnesses exact and replayable. Q000001 binds the earlier p.123
  `Gamma(psi)` witness at 274,034 bytes, SHA-256
  `AD6EECAD5060C23A5F73C1FC3EF900ED98E4C5426AD522DA6F47FB28773234D5`,
  dimensions 12,639 by 3,403. Q000002 binds the p.125 kernel-formula witness
  at 490,151 bytes, SHA-256
  `9D799B065380ACBEA0217C3E7F50B48EE5367E2A0FF70DA216785FBF7DC811C6`,
  dimensions 29,792 by 2,571. Both are individual tight
  5,000-dpi-equivalent grayscale authority-only receipts. They do not admit
  either correction and do not replace authority/French/English visual QA.
- D000153 and I000049 bind the strengthened diagram gate. The deterministic
  inventory contains 445 registered diagrams and 483 intricate-block
  candidates. D000154, I000050, and V000001--V000014 certify the first bounded
  queue: all twelve diagrams already carrying statement-map claims and two
  exact-sequence blocks at the reviewed frontier. Each item has a separate
  tightly bounded crop from direct authority, cumulative French, and cumulative
  English at an effective scale of at least 5,000 dpi, for 42 committed crop
  files. Complete graph or mathematical-chain masks passed and no EGA source or
  render defect was found. D000160 and V000015 separately certify the newly
  mapped 4.2.2 stalk square on all three surfaces. The remaining 432 discovery
  diagrams and 481
  unselected block candidates remain explicitly uncertified; each future
  promotion requires its own active V row. Shared, full-page, and grouped crops
  do not qualify.
- Current local statement-level frontier: direct-French review and source
  implementation extend through EGA I 6.6.4. The local graph contains 1,252
  operational statement edges, 1,259 statement-map rows, 829 residual rows,
  329 decisions, 257 agent audits, 102 issue rows, and zero quarantined rows.
  The next source-order unit is EGA I 6.6.5 after the current implementation's
  deterministic production checks. The published frontier remains 6.6.3;
  see the [6.6.4 local implementation receipt](../validation/ega-i-6.6.4-semantic-checkpoint-2026-08-31.json)
  for the exact source, target, append-only bindings, and unfinished stages.

## State model

Every source unit and topic advances independently through:

`discovery` -> `candidate` -> `reviewed_existing` or `reviewed_gap` ->
`integrated_local` -> `built` -> `remote_checkpoint`.

`remote_checkpoint` is a pushed and remotely verified checkpoint of the
independent Mathematical Commons mirror. `upstream_feedback` and
`upstream_accepted` remain schema-valid only for append-only historical
records; they are not production goals. No state is inferred from a successful
build alone. A local mirror label is never an official Stacks tag; only tags
verified in the pinned official tag registry are treated as official.
Historical source defects, English corrections, mapping reversals, and
maintainer feedback remain append-only.

## Files

- `scope.json`: exact claims, exclusions, inputs, and upstream base.
- `src.csv`: source surfaces and authority state.
- `topics.csv`: corpus-wide discovery topics; initially unreviewed.
- `dec.csv`: append-only mapping and policy decisions.
- `issues.csv`: source, mathematical, and integration issues.
- `fb.csv`: upstream feedback and its disposition.
- `schema.md`: stable IDs, evidence rules, and promotion gates.
- `check.py`: local structural validator.
- `intake.py`: deterministic manifest verification and metadata-only unit
  extraction; it does not copy source prose.
- `r184.py`: exact no-overwrite reconstruction of the frozen R184 discovery
  tree from sealed source-only R255 through 33 hash-guarded inverse operations
  across 12 files, with exact citation-only R255, R254/R251, R248, R247, R243,
  and R219 intermediate gates; it never mutates edition source.
- `files.csv`, `units.csv`, and `intake.json`: generated exact-file inventory,
  stable unit registry, and fail-closed intake receipt.
- `pages.csv`: append-only direct-authority page evidence for frozen discovery
  files whose printed-page markers are absent; raw-page guards make replay
  atomic and preserve every stable unit ID.
- `vqa.csv` and `qa/{a,f,e}`: append-only per-item visual certifications and
  their separate authority, cumulative-French, and cumulative-English crops.
  Rows bind exact public parent-PDF keys, bytes, hashes, one-based physical
  pages, bounded PDF-point boxes, effective scale, comparison mask, and
  complete normalized signature. Historical V rows and crop bytes are never
  overwritten when a later correction supersedes them.
- `rej.csv` and `qa/r`: immutable rejected or obsolete visual candidates.
  Every row names its accepted same-item successor so clipped, overbroad,
  stale-edition, and below-floor evidence remains auditable after correction.
- `map.py`, `cand.csv`, and `map.json`: lexical candidate generation against
  the exact upstream Stacks snapshot. Candidates are not reviewed mappings.
- `tmap.csv`: French-admitted topic-level bridges to existing Stacks sections;
  granularity and non-coverage claims are explicit in every row.
- `smap.csv`: French-admitted statement and statement-component edges to
  exact existing labels or explicitly untagged local labels; published
  corrections append explicit same-table successors instead of rewriting.
- `resid.csv`: noncoverage, partial coverage, terminology migration,
  stronger-target, derived, and local-mirror residuals with the same
  append-only supersession rule.
- `agent.csv`: exact task IDs, bounded scopes, runtimes when exposed, returned
  findings, owner checks, accepted/rejected dispositions, and write claims.
- `interface.json`: hash-bound read-only contract with the active French and
  English EGA edition task.
- `log.md`: concise operating log, agent/Spark TODOs, and exact outcome
  records.
- `../reports/findings.jsonl`: append-only suspected-correction referrals;
  the edition task alone decides and mutates canonical source.
- `../reports/qsrc.csv` and `../reports/qa`: short flat manifest and immutable
  direct-authority crops for source-error evidence; these are not edition
  outputs or three-surface visual certifications.

### Current local implementation: EGA I 6.6.4

The direct-French F37ZW slice at LF1067--1121 is 3,114 UTF-8/LF bytes,
SHA-256 `95A70DD85C4C0D7EE4C64052082F2DF176C163014762D721144CECEB458316BB`.
It includes the six-part proposition, its proof, and the following
two-summand paragraph. English discovery LF753--785 is a separate
3,017-byte witness at SHA-256
`F36B6FCC7D2B40F6F174C1A3750B7E9EF13C0B556F0DD31A7BCE2AB24EA0784A`.

D000329 and S001250--S001259 map the clauses to 01K7, 04ZA, 01K6, 01K5,
03GI, 04ZB, 01K3, and 01JS at the pinned official Stacks baseline.
R000826--R000829 keep the componentwise nature of the package, the
underlying-space Noetherian hypotheses, the stronger quasi-separated
cancellation result, and the binary rather than infinite coproduct scope
explicit. No single edge claims equivalence with the complete proposition.

The only root TeX change is an independently written completion of the
omitted proof of 01K5. Its official statement and label are unchanged.
Earlier results 01K4 and 01JS give finite affine covers after arbitrary base
change; no Noetherianity, separation, finite-type, or flatness hypothesis is
added. This is a local proof completion, not an official Stacks endorsement.
The graph has 1,252 active / 1,259 physical edges over 418 source units,
1,239 official-tag edges using 350 tags, thirteen local edges, and 61
full-statement equivalences. Residual history has 804 active / 829 physical
rows, with twelve open gaps and thirteen local-mirror rows.

The [implementation receipt](../validation/ega-i-6.6.4-semantic-checkpoint-2026-08-31.json)
records local validation and the exact base. It does not certify TeX/PDF
builds, visual QA, publication, or anonymous readback; those remain explicit
production stages. No authority, issue, registry, or composition-receipt
bytes are changed by this semantic slice.

### Latest published reviewed frontier: EGA I 6.6.3

The latest sealed semantic-only slice closes EGA I §6.6.3 and is bound to the
direct-French F37ZW lines 1046--1065 and the corresponding English discovery
lines 739--751 at pinned Stacks commit
`a04446e57ec1fbc252a871afcec7752fb2807b14`. The complete fixed-point receipt
is [`ega-i-6.6.3-semantic-checkpoint-2026-08-30.json`](../validation/ega-i-6.6.3-semantic-checkpoint-2026-08-30.json),
SHA-256
`58CC0464C1EDAC665CA72B80F8156E77773F9B983F83D76BA948551A3D15456E`.

The semantic content was recorded at commit
`85024a5e3456cadc79c6cde67bf1fcbbc09c48cb` and publicly released from
`f1b8d56b5f3c9999010455a38a289bce76735070` under tag
[`ega-i-6.6.3-semantic-2026-08-30`](https://github.com/KokunoYumeto/unofficial-ai-integrated-stacks-project/releases/tag/ega-i-6.6.3-semantic-2026-08-30)
and Zenodo DOI
[`10.5281/zenodo.22177421`](https://doi.org/10.5281/zenodo.22177421). The
checkpoint is semantic-only: root TeX and PDF, visual QA, authority, issues,
and the errata registry are unchanged. Its exact fixed point is D000328,
S001246--S001249, R000823--R000825, and A000256: 256 agent rows, 328 decision
rows, 1,242 operational statement edges, 1,249 statement-map rows, 825
residual rows, 102 issue rows, and zero quarantined rows. The exact modern
theorem is `01T1`; `01T2`, `01T3`, `01K3`, and `01K4` retain the historical
proof components, so no nonduplicative root theorem is added. Proposed source
issue I000104 remains routed to successor task
`01a047ab-fc94-7120-af1d-5701ba37aacd`, pending its independent adjudication.
The next semantic cursor is EGA I §6.6.4.

### Prior reviewed frontier: EGA I 6.5.1

The local determination and realization proposition is reviewed against the
source-closed direct-French block at lines 771--864 in admitted F37ZW and exact
labels at pinned Stacks commit
`a04446e57ec1fbc252a871afcec7752fb2807b14`. The block is 5,035 bytes at
SHA-256 `9DBE145F16C99F8DA039D0961D4EA123AB3D7437E1848CF04F68C7C37A3D8C25`;
its four-unit semantic core at lines 774--864 is 4,955 bytes at SHA-256
`EEC814858C37A15FDDB1098D9DFC6AB5D42E0CDDCDFA6E2F2C55D048764B0CB2`.
The exact fourteen-unit topology of all §6.5 is separately sealed in
[`../validation/ega-i-6.5-source-topology-2026-08-29.json`](../validation/ega-i-6.5-source-topology-2026-08-29.json).

D000320 and S001196--S001205 record the fixed point. Clause (i) is
stronger-covered by `01T1` followed by `0BX6(1)(a)`, which requires only local
finite type. Clause (ii) is exactly the composite `01TX(2)` followed by
`0BX6(2)(a)`. The proof-level rows retain `00FP`, `00QO`, and `00CR`; the proof
of `0BX6` contains the same finite-generator and finite-relation localization
argument. The numbered EGA square is the same unnumbered square embedded in
that proof. Adding a root theorem or a second diagram would therefore be
duplicative.

R000786--R000793 preserve the stronger target, two-tag composite, explicit
historical denominator construction, unnumbered target square, and both
counterexample-tested hypothesis boundaries. I000102 records the unique
printed defect at French line 862: because `hg` lies in the coordinate ring
`B` of `X`, `D(hg)` is a neighbourhood in `X`, not in `Y`. Diplomatic French
is unchanged; the public English discovery already has the correct wording.
The cumulative graph has 1,198 active / 1,205 physical statement rows across
404 source units, 1,185 official-tag rows using 342 tags, thirteen local rows,
and 57 exact full-statement equivalences. Residual history has 768 active / 793
physical rows, 25 superseded rows, twelve open gaps, and thirteen local rows.
V000045 and D000321 bind the historical square to three independently checked
5,000-dpi crops: direct authority page 150, public French page 102, and public
English page 337. A000245--A000249 record the topology, mapping, hypothesis,
defect, and visual audits.
The next semantic cursor is EGA I 6.5.2.

### Prior reviewed frontier: EGA I 6.4.1--6.4.13

The algebraic-scheme and geometric-point subsection is reviewed against direct
French lines 560--769 in admitted F37ZW and exact labels at pinned Stacks
commit `a04446e57ec1fbc252a871afcec7752fb2807b14`. D000307--D000319 bind all
thirteen numbered statements; S001140--S001195 route twenty mathematical units
through 56 exact existing-tag edges and 36 distinct tag-label-file joins; and
R000764--R000785 preserve every stronger-target partial-target terminology
migration unlabelled proof step and derived theorem package.

All thirteen statements are already exact Stacks results short composites or
expository consequences of existing results, so no duplicate root TeX addition
is warranted. The fixed-large-field criterion deliberately preserves its
one-fixed-field quantifier and uses 0487 only as a partial comparison together
with 01T1 01T2 030F 09GU and 01J9. The unrestricted successor retains the
explicit 01TX dependency and the Jacobson closed-point chain. No formula or
diagram child unit is present and no new visual task is required.

The cumulative graph has 1,188 active / 1,195 physical statement rows across
400 source units, 1,175 official-tag rows using 337 tags, thirteen local-mirror
rows, and 56 exact full-statement equivalences. Residual history has 760 active
/ 785 physical rows, 25 superseded rows, twelve open gaps, and thirteen active
local-mirror rows. The next semantic cursor is EGA I 6.5.1.

### Prior reviewed frontier: EGA I 6.3.1--6.3.10

The finite-type subsection is reviewed against direct French lines 297--558
in admitted F37ZW and exact labels at pinned Stacks commit
`a04446e57ec1fbc252a871afcec7752fb2807b14`. D000296--D000306 bind all eleven
numbered statements and the two diagram referrals; S001077--S001139 route 22
of the 23 registered units through 63 exact tag-label-file joins; and
R000747--R000763 preserve the theorem decompositions, proof-level derivations,
one stronger modern result, the published correction, and both visual gaps.

All eleven mathematical statements are already exact Stacks results or short
composites of existing results, so no duplicate root TeX addition is warranted.
In particular, 6.3.10 follows from 01J9, 01JP, 01T4, 01S1, 06EB, and 01TA;
00FV is the historical Nullstellensatz route, while 06EB strengthens the
necessary direction from finite type to locally finite type. The direct
published erratum at `ega2/ega2-errata-addenda-fr.tex` lines 464--468 corrects
the proof of 6.3.2.1 to `D(g_i)\subset W`; diplomatic French bytes remain
unchanged and the corrected reading is used in semantic records.

The two diagrams in 6.3.10 have exact semantic mappings but no active
authority/French/English crop triples. I000100--I000101 and R000762--R000763
keep those bounded visual tasks open without blocking the theorem coverage or
the next source-order slice. The cumulative graph has 1,132 active / 1,139
physical statement rows across 380 source units, 1,119 official-tag rows using
314 tags, thirteen local-mirror rows, and 50 exact full-statement equivalences.
Residual history has 738 active / 763 physical rows, 25 superseded rows, twelve
open gaps, and thirteen active local-mirror rows. The next semantic cursor is
EGA I 6.4.1.

### Prior reviewed frontier: EGA I 6.2.1--6.2.2

The Artinian-prescheme subsection is reviewed against direct French lines
259--295 in the admitted F33 source and exact labels at pinned Stacks commit
`a04446e57ec1fbc252a871afcec7752fb2807b14`. D000294--D000295 bind its
definition and proposition; S001067--S001076 route all three registered source
units through ten exact tag-label-file joins; and R000742--R000746 retain the
compound terminology, split proof, two necessary-hypothesis counterexamples,
and the elementary quasi-compact-discrete finiteness step.

The definition is exactly 01HW plus 00J5. The three-way Artinian / Noetherian
discrete / Noetherian T1 equivalence is a theorem chain rather than a one-tag
match: 00KJ and 00JB give the affine ring statements; 01OV supplies local
Noetherianity and quasi-compactness; 01IS and 04MT turn T1 into dimension zero;
0AAX decomposes a locally Noetherian dimension-zero scheme; and 02O0 with 01I5
supplies affineness and the finite product description. Neither source unit has
a formula or diagram child. No new root TeX label is warranted and no V, J,
page, issue, or source-error row is added.

The cumulative graph has 1,069 active / 1,076 physical statement rows across
358 source units, 1,056 official-tag rows using 295 tags, thirteen local-mirror
rows, and 49 exact full-statement equivalences. Residual history has 721 active
/ 746 physical rows, 25 superseded rows, ten open gaps, and thirteen active
local-mirror rows. The next semantic cursor is EGA I 6.3.1.

### Earlier reviewed frontier: EGA I 6.1.1--6.1.13

The full Noetherian subsection is reviewed against direct French, the admitted
F33 semantic receipt, and exact labels at pinned Stacks commit
`a04446e57ec1fbc252a871afcec7752fb2807b14`. D000278--D000290 map the thirteen
numbered units and all nine registered proof units; S000987--S001066 supply 80
reviewed semantic edges; and R000696--R000741 retain the theorem splits,
stronger targets, proof-level derivations, historical terminology, essential
hypotheses, and bounded counterexamples. The unnumbered coherence, subquotient,
ideal-sheaf, open-locality, and ascending-chain assertions are explicit
source-part edges rather than silently omitted prose or invented source units.

The product warning in 6.1.5 has complete derived coverage, not a one-tag match:
01OW and 01JQ reduce the question to the tensor ring, while 00RW, 00RX, 00RT,
and 031G show that for `K=k(x_1,x_2,...)` the conormal module of the diagonal
ideal in `K tensor_k K` is the infinitely generated module of differentials.
The 6.1.8 result is strictly broader than 04MF and its universal smaller-open
conclusion remains componentwise derived. The 6.1.10--6.1.13 routes keep local
Noetherianity exactly where the finite/local-finiteness and coherent-nilradical
arguments require it.

Two printed defects are referred without changing diplomatic French.
I000096/D000291 record that the 6.1.8 proof's global-complement aside omits
intersection with the chosen open `U`; `X=Spec(k[t])`, `U=D(t)`, and the generic
point give a bounded counterexample. I000097/D000292 record that 6.1.12 omits
nonemptiness under EGA's convention: the empty scheme satisfies the printed
right side vacuously but is not integral. Q000015--Q000016 bind exact 5,000-dpi
NUMDAM crops, and D000293 admits those authority-only receipts. The sixteen-row
source-error manifest is 5,012 bytes at SHA-256
`167BA57EBD509192C90823DAE4FB9DB928EC2EF35DFC85668293E8298AD9144A`;
its sixteen immutable crops total 6,022,269 bytes. The ordered active referral
set is I000088, I000089, I000091--I000097; I000090 remains resolved.

The cumulative graph now has 1,069 active / 1,076 physical statement rows across
358 source units, 1,056 official-tag rows using 295 tags, thirteen local-mirror
rows, and 49 exact full-statement equivalences. Residual history now has 721 active
/ 746 physical rows, 25 superseded rows, ten open gaps, and thirteen active
local-mirror rows. M000024 adds only a subsection-to-section topical bridge to
01OU; it makes no theorem-equivalence claim. This tranche has no registered
formula or diagram child and appends no V, J, or page row. The next semantic
cursor after the successor §6.2 checkpoint is EGA I 6.3.1.

Candidate generation now reads root TeX files and `tags/tags` from the pinned
Git commit rather than the mutable Stacks worktree. `python ega/map.py --check`
deterministically replays 21,446 labels, 21,437 official-tag joins, 36 topics,
and 2,749 lexical candidates without writing. The dedicated Noetherian topic is
separate from the approximation topic, so component/local-topology candidates
are not dominated by Limits terminology.

### Prior reviewed frontier: EGA I 5.5.1--5.5.13

The complete separatedness subsection is now reviewed from direct French and
the exact F33 semantic receipt. D000258--D000271 bind all numbered statements,
proofs, the labelled identity, and the three registered diagram units;
D000272--D000276 bind four tight authority-only source-error receipts; and
D000277 records the exact D48 visual-admission boundary. S000880--S000986 add
107 reviewed-existing semantic edges. R000635--R000695 retain the theorem
splits, proof-level routes, essential hypotheses, counterexamples, terminology,
source defects, and visual gaps without manufacturing a missing official tag.

Three printed references to Proposition 5.5.4 are type-wrong: the affine-open
target reductions in 5.5.5 and both directions of 5.5.9 uniquely require the
target-locality Proposition 5.5.5. In 5.5.11 the exceptional fibre of the
doubled line lies over `(s)`, not the generic prime `(0)`. The doubled-plane
claim that neither criterion of 5.5.6 holds is also false: its punctured-plane
overlap is nonaffine, but its global ring is `k[s,t]` and both identity-gluing
restriction maps have full image, so condition one fails while condition two
holds. Q000011--Q000014 bind the four exact 5,000-dpi authority crops; the
source-error manifest is 4,416 bytes at SHA-256
`91EAAF72648ACDDE00F6D20D014DB60F0071C8BDEDDA2027D2E07FE4C2182086`
and its fourteen crops total 5,763,117 bytes. Diplomatic French remains
unchanged. Source-error crops must also contain nonwhite rendered content;
dimensions and byte hashes alone do not certify a usable authority crop.

The 5.5.4 finite closed-family hypothesis, the asymmetric quantifiers in
5.5.8--5.5.9, and the historically separated target in 5.5.10 are explicit.
The arbitrary-property arguments in 5.5.12--5.5.13 are composite coverage:
01JZ and 001V handle base change and products, 01KS handles graphs, 01J4 and
0356 handle reductions, and 01L7 supplies separatedness for cancellation. No
single Stacks tag is claimed to state that meta-theorem.

The five semantic edges for the three registered diagrams remain operational
mathematical mappings, but their visual promotion is separately fail closed.
The intricate block at French line 935 has no registered child and is not
invented. D56--D59 and D65 are useful producer discovery evidence only; the
admitted Commons interface remains D48, so this checkpoint appends no V or J
row. The exact active referral set is I000088, I000089, I000091--I000095;
I000090 is resolved. The cumulative graph has 979 active / 986 physical
statement rows across 333 source units, 966 official-tag rows using 269 tags,
thirteen local-mirror rows, and 42 exact full-statement equivalences. Residual
history has 670 active / 695 physical rows, with ten open gaps and thirteen
local-mirror rows. The next semantic cursor is EGA I 6.1.1.

### Earlier reviewed checkpoint: EGA I 5.4.1--5.4.8

Separatedness, the closed-diagonal criterion, closed comparison and graph
morphisms, cancellation of closed immersions, closed pairings, closed
sections, generic-point uniqueness of sections, and all three converse tests
are admitted from the exact F33 authority slice on printed pp.135--136.
Direct French lines 670--772 form 4,208 UTF-8/LF bytes at SHA-256
`AE4ED884CE3E0F16B9854CABCBA9D5F184B7AB2EBD8B2B33AC344E43EDAA07BE`.
S000853--S000879 add 27 reviewed-existing edges and R000617--R000634 retain
every historical-terminology, stronger-target, proof-level, derived, and
hypothesis boundary. D000250--D000257 bind the eight numbered source units.

Tag 01KK is the exact modern separatedness definition; the source
closed-image formulation uses both the diagonal immersion 01KJ and the
closed-image criterion 01IQ. Tags 01KR and 01KS give the stronger comparison
and graph statements. Item three of 07RK is the exact cancellation theorem,
with 01QR and 01QS splitting its historical graph proof. The paired-morphism
corollary is derived from 01KU, 07RK, and 001V; 01KT gives the section result.
Generic-point uniqueness is deliberately composite: unlabelled point prose
under 01J5, the closed equalizer 01KM, generic-point density 004X, reduced
factorization 0356, and fibre-product uniqueness 001V are all retained.
Tag 01RH is not used directly because it assumes agreement on an open
subscheme, whereas a generic point need not be open.

The 5.4.8 citation to 5.4.5 is type-correct. Its middle application has base
`Y`, structural map `p1 : Y times_Z Y -> Y`, `j = id_Y`, and
`g = Delta_Y_over_Z`; 001V identifies the resulting pairing with the
diagonal. The first graph-of-identity test and the final section test are the
same diagonal identification in their respective bases. Counterexample
residuals keep separatedness, reducedness, and irreducibility explicit.

This slice has no registered diagram, equation child, or selected intricate
standalone mathematics block, and it requires no local TeX theorem. VQA,
source-error, page, and chapter-build surfaces are unchanged. The cumulative
totals are 872 active statement edges, 879 physical edge rows, 307 source
units, 859 official-tag rows using 252 distinct tags, thirteen local-mirror
rows, forty exact full-statement equivalences, and 609 active residuals out of
634 physical rows. Six labelled-coverage gaps remain open.

The admitted edition interface remains the exact D48 tuple
F37ZW/R261/B37AJ/B239/D48/DIA48T/Q37CY/Q37DB with publication disabled.
Later producer payloads through D63 remain read-only and fail closed: every
final inventory DIA49R3--DIA63 retains stale aggregate counts, and D59 onward
records character indexes as UTF-8 byte offsets. None of those later controls
changes the Commons interface or the F33 semantic provenance.

### Earlier reviewed checkpoint: EGA I 5.3.9--5.3.14

The diagonal-immersion theorem, canonical fibre-product comparison, graph
criterion, graph base change, immersion cancellation, and pairing criterion
are admitted from exact F33 authority and direct printed pp.133--134.
S000817--S000836 add twenty strictly scoped edges. R000560--R000577 record
the published proof repair, terminology, stronger-target, proof-level, and
derived remainders. The active graph has 829 edges across 286 source units;
physical append-only history has 836 edge rows. Of the active edges 818
resolve to 249 distinct official tags and eleven are explicit local untagged
integrations. There are 38 exact full-statement equivalences and six open
labelled-coverage gaps.

Tag 01KJ gives the diagonal immersion. Tag 01KR gives the canonical
fibre-product comparison and its exact cartesian proof square. Tags 01KS,
01KT, and 001V cover graph immersions, sections, and the projection
characterization. Tags 01JX, 002L, and 001V derive graph base change; item
four of the untagged `schemes-lemma-diagonal-identities` records the exact
scheme identity and its two-projection proof. Tag 07RK supplies ordinary
immersion cancellation, with 01KS, 01JY, and 02V0 splitting the historical
proof and source-local clauses remaining explicit derivations.

Two primary published corrections are carried without altering diplomatic
French. Q000009 binds the printed 5.3.9 proof, which omits the locally closed
image step; EGA III.2 Err_III,10 supplies the complete affine-local repair.
Q000010 binds the printed 5.3.13 citation `4.2.4`, which the same primary
errata list replaces by `4.2.5`. The standalone English successor incorporates
both corrections visibly. This six-unit slice has no diagram or selected
intricate standalone block; the next pending diagram belongs to 5.3.15.

D000234 moves all eleven active untagged integrations into the independent
Mathematical Commons mirror. R000578--R000588 preserve the eleven former
upstream-pending rows as superseded history and replace them by active
`integrated_local_mirror` successors. Residual history now has 572 active and
588 physical rows, sixteen superseded rows, six open gaps, and eleven active
local-mirror rows. Official Stacks remains the pinned sync and reference
source; upstream acceptance and PR submission are not production goals.

### Preceding reviewed checkpoint: EGA I 5.3.5--5.3.8

The change-of-base square, its diagonal-base-change specialization, the
graph-diagonal square, and the monomorphism/diagonal criterion are admitted
from exact F33 authority and direct printed pp.132--133. S000804--S000816 add
13 physical rows and supersede S000793, while R000538--R000559 add 22
target-specific rows and close R000523 and the former visual gap R000552. The
active graph now has 809 edges across 274 source units and 554 residuals;
physical append-only history has 816 edge rows and 559 residual rows. Of the
active edges 799 resolve to 247 distinct official tags and ten remain explicit
local untagged integrations. There are 36 exact full-statement equivalences,
six open labelled-coverage gaps, and ten local-pending residuals.

The proof of 01KR contains the exact 5.3.5 cartesian square; 001V supplies its
universal property. Tags 01KR and 01JX split the 5.3.6 base-change formula.
Tags 01KR, 01KS, and 001V give the graph-diagonal square in 5.3.7, and 01L3
with the stronger categorical 08LR gives the exact 5.3.8 monomorphism
criterion. The previously open categorical-forward residual under 5.3.1 is
therefore closed without adding another local theorem.

Two printed defects remain diplomatic but mathematically controlled. Q000007
and the published EGA II errata supply the missing name `g` for the second
map in 5.3.5. Q000008 records the 5.3.8 proof's false “one element” wording;
monicity gives “at most one,” with the empty-scheme monomorphism as a bounded
counterexample to existence. Both authority crops are exact individual
5,000-dpi receipts and neither changes the diplomatic source.

V000021 certifies the 5.3.5 square on authority, B37AC French, and B233
English surfaces. V000022 certifies the corrected 5.3.7 square against direct
authority, B37AD French, and B234 English, including `Delta_Y` below the
bottom arrow. J000010--J000014 retain clipped, stale-output, and nonfinal
localizers; J000015--J000016 preserve two visually correct producer crops
whose integer raster envelopes measured fractionally below the strict
effective-5,000-dpi floor. The active visual registry now has 22 items
(18 diagrams and four intricate blocks), 66 accepted crops, and 16 rejected
or nonfinal crops. Corpus-wide I000049 remains open for discovery items not
yet individually reviewed.

### Preceding reviewed checkpoint: EGA I 5.3.1--5.3.4

The diagonal definition, pairing identity, product compatibility, and base-
change compatibility are now admitted from exact F33 authority and direct
printed p.132. S000789--S000803 add 15 strictly typed edges and
R000520--R000537 add 18 target-specific residuals. The active graph now has
797 edges across 266 source units and 534 residuals; physical append-only
history has 803 edge rows and 537 residual rows. Of the active edges 787
resolve to 246 distinct official tags and ten remain explicit local untagged
integrations. There are 35 exact full-statement equivalences and seven open
labelled-coverage gaps.

Unlabelled prose under 01KH exactly gives the diagonal and its projection
identities. Official categorical targets 001S, 001V, and 002L derive the
pairing and product identities; proof-level 02X0 records the product formula
for algebraic spaces. Proofs under 01KU and 04YR contain stronger base-change
forms, while 0038 and 001Y supply the already-reviewed pullback-product
comparison. One untagged Schemes lemma makes the three foundational scheme
identities directly citable; it receives no official tag or upstream-
acceptance claim.

The printed French jumps directly from 5.3.2 to 5.3.4. Its stable 5.3.3 row
is therefore retained only as the non-rendering English navigation anchor
controlled by R55, with no statement edge, issue, or correction referral.
The ten frozen rows all lie on I:132. Three ordinary displays remain parent
or formula units, and the slice has no diagram or intricate standalone block,
so it adds no V item. The source remark's forward categorical claim through
5.3.8 remains explicitly open until those later units are reviewed.

Ten visible disposable Spark tasks supplied bounded read-only hash, registry,
manifest, and label-join checks. Their two canaries and three later malformed
or incomplete results remain recorded as rejected evidence; every task was
owner-replayed, archived, and verified absent. The agent ledger records their
actual `low` effort rather than relabelling it, and the validator now enforces
model/effort coupling.

The preceding four results on reduced locally closed subspaces, maps from
reduced schemes, reduced closures of immersions, and comparison of defining
ideals were admitted from exact F33 authority and direct printed pp.131--132.
At that checkpoint S000768--S000788 added 21 strictly typed edges and
R000499--R000519 added 21 target-specific residuals. The active graph then had
782 edges across 259 source units and 516 residuals; physical append-only
history had 788 edge rows and 519 residual rows. Of those active edges 775
resolved to 243 distinct official tags and seven remained explicit local
untagged integrations. There were 32 exact full-statement equivalences and six
open labelled-coverage gaps.

The unique reduced structure on a locally closed subset splits across 0F2L
and 01J3, with 00E0 and 01J2 absorbing the affine proof. Maps from a reduced
scheme factor through a closed subscheme by the stronger exact criterion
0356, with 01JU, proof-level 01S1, 00E0, 01J1, and 001V accounting for the
historical pullback proof. Tag 03DQ exactly matches the reduced closure of an
immersion. The ideal-containment result is composite-covered by 0356, 01JU,
01QP, and 01HP, reusing the already-reviewed 4.4.6 bundle. Counterexamples
keep locally-closedness, reducedness, and the image-ideal convention explicit;
no source defect, Stacks defect, genuine labelled gap, or new local TeX was
found.

L000029 changes only `ega:I.5.2.3:proof` from I:131 to I:132 under exact R55
evidence while preserving all 9,585 stable unit IDs. The slice contains no
diagram, display, equation, or intricate standalone mathematics block, so it
adds no V item. Two historical context-image paths declared by R54/R55 are no
longer live, but the exact authority PDF and page controls remain available;
this is provenance attrition rather than a source or mapping defect.

The preceding complete reduction subsection was admitted from F33 and direct
printed pp.127--131. The bounded 5.1.5--5.1.10 source span is direct-French
lines 101--304: 9,279 UTF-8/LF bytes with SHA-256
`39A8328C323D5140A47D12437CC78B672CE63931B0AB0A77914C7CE4369804EA`.
S000715--S000767 add 53 strictly typed edges and R000469--R000498 add 30
target-specific residuals. At that checkpoint the active graph had 761 edges
across 251 source units and 495 residuals; physical append-only history had 767 edge
rows and 498 residual rows. Of the active edges 754 resolve to 241 distinct
official tags and seven remain explicit local untagged integrations. There
are 31 exact full-statement equivalences and six open labelled-coverage gaps.

Reduction functoriality splits through 01J4, 0356, and monomorphism
uniqueness; the new untagged Schemes lemma packages the exact identity,
composition, and natural-square statement. Proposition 5.1.6 uses 01RZ,
01S3--01S4, 0BR6, 054M, 01IO, 01HE, and 01LD; its new Morphisms lemma records
only the surjective/radicial equivalences and the immersion, closed-immersion,
and open-immersion forward clauses it actually proves. Proposition 5.1.7
combines the fibre-product universal property with 01JU and the universal-
homeomorphism chain; a second local Morphisms lemma gives its exact canonical
comparison. No local label is presented as an official tag or as accepted by
the upstream Stacks Project.

Tags 0CB9, 01J3--01J4, 020F, and 035Z separate reduced products from the
perfect-field boundary. Tags 06AD, 04EX, 05YV, 01IA, 01I1, 05QB, 01XB, and
0B3A split the finite thickening affineness proof, its exact sequence, its two
commutative squares, and the independent H1 argument. The local 04EX text now
correctly says that the relevant object is a closed subscheme of `X'` and
uses `nth order thickening`, not `nth order thinking`. Tag 0EGG preserves the
difference between local nilpotence and the source's stronger single global
nilpotence exponent.

L000028 corrects the frozen 5.1.9 locator from I:129 to I:130 under exact R53
evidence. Q000006 is the immutable authority-only source-error receipt for
the printed 5.1.9.2 restriction `F|Y`, whose uniquely typed local reading is
`F|V`; the EGA Canon accepted the referral without changing diplomatic French
or rebuilding either reader. Its corrected interface successor R243 changes
metadata only and preserves the exact 127-file English tree.

Visual certification is per item and per current surface. V000016 certifies
the 5.1.5 reduction square. V000017--V000020 certify the 5.1.9 A/A0 block,
the labelled exact sequence, the ring square, and the scheme square using one
tight authority, French, and English crop for each item at 5,000 dpi. Their
J000001--J000009 rejected lineage remains immutable; the current cumulative
V/J totals are reported at the reviewed frontier above. Corpus-wide I000049
remains open for the rest of the discovery corpus.

The validator now fails closed on malformed IDs, wrong parent geometry,
inactive or mismatched evidence decisions, cross-finding token splicing,
ledger-prefix mutation, directory nesting, symlinks, and unmanifested crops.
The current R255-to-R184 reconstruction likewise rereads the complete sealed live
tree immediately before atomic promotion, closing the reproduced concurrent-
producer race. Independent mathematical and governance inverse audits both
returned HARD PASS after the repairs while preserving their earlier failures.

The current local semantic slice is EGA I §6.6.4, with source-order
continuation at §6.6.5 after its deterministic production checks. The latest
published semantic frontier remains §6.6.3. Every reviewed claim remains
bound to its own historical source
receipt; the 5.4 and 5.5 rows use F33 plus direct authority evidence rather
than a mutable producer frontier. The §6.6.3 fixed point is sealed by
`ega-i-6.6.3-semantic-checkpoint-2026-08-30.json` (SHA-256
`58CC0464C1EDAC665CA72B80F8156E77773F9B983F83D76BA948551A3D15456E`).
The last admitted reader interface is the D48 tuple
F37ZW/R261/B37AJ/B239/D48/DIA48T/Q37CY/Q37DB. Later D49--D63 producer
controls remain non-admitted because their inventory aggregates are stale;
D59 onward also mislabels character indexes as UTF-8 byte offsets. Discovery
unit I.3.2.9 remains in a separate authority-pending queue for its cited EGA II
erratum witness. The complete English surface drives provisional candidates
only; chapter edits still require direct French evidence, explicit residuals,
bounded mathematical review, visual evidence when applicable, and owner
verification.
