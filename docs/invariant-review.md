# Semantic construction checks for CodeStudio

Status: tested example improvement and design for the general planning gate.
This does not establish autonomous product readiness or install a new engine.
The user-approved original invariant test from upstream commit
`796407a216e9b4ae31e3463492bedac3a64a3688` is preserved unchanged.

## Finding and replacement rule

A construction error should be caught inside planning/building, not handed to
the user as an unexplained failed task. A model choosing plausible coordinates
is not a geometric solver. Separate the topology decision from its arithmetic:

1. A planner chooses which named platforms must be connected.
2. A deterministic constructor resolves these references and intersects their
   horizontal intervals. No shared interval means an impossible direct ladder;
   return the two intervals and ask for a different layout or connection.
3. For a valid pair, derive ladder x from the shared interval and ladder y/height
   from the two platform surfaces. Apply explicitly specified ladder width and
   clearance constraints. Do not independently guess endpoints or silently
   move platforms.
4. An independent validator checks the resulting data and the complete route.
   It must not use the constructor's result as its expected answer.
5. Only after the data checks pass should dependent physics/rendering modules
   be built. Full integration and keyboard checks remain required afterward.

The same pattern applies to foreign keys (resolve IDs rather than guess them),
totals (derive a sum rather than generate a second independent number), ranges,
uniqueness, graph connectivity and component interfaces. Known arithmetic and
workflow steps belong in programs. Models select a suitable construction method
or revise an impossible specification.

## What the source already checked

`test_modules.cjs` already checked ladder centers and endpoint surfaces with
less than 0.01 pixel tolerance, plus start support and reachability. It was not
only a shape test. However, its route traversal seeded every platform at the
start height; a horizontally disconnected island could incorrectly seed a route.
The additional graph check seeds only platforms actually supporting the player.
An independent negative fixture reproduces this loophole while keeping all
ladder endpoints valid.

The supplied test uses a 2px tolerance and any horizontal overlap; these are
weaker than the existing center check. Its title says "exactly one" although the
assertion accepts at least one. It also generates no tests for an empty level
array. The additive contract checks preserve the strict center/endpoints rule,
execute a fixed shape/count check, and report all failures with numeric values.
Neither ladder graph proves full physics or gameplay. The 60px banana rule is
an explicit example design choice, not a universal definition of collectibility.

## Implemented example

`examples/donkey-monkey/tests/level_invariants.cjs` exports pure validators and
generic adjacency traversal. It returns requirement IDs, level indices,
descriptions and measured values. It does not modify generated source.

| Requirement ID | Executable check | Counterexample |
| --- | --- | --- |
| `levels.shape` | Four levels, valid finite nonempty geometry | Empty/malformed data, NaN, invalid size |
| `ladders.endpoints` | Center x supported at both exact endpoint surfaces | Wrong x, shifted y, edge overlap without center support |
| `route.start-to-exit` | Graph traversal from actual player support | Disconnected same-height island, unsupported start/exit |
| `bananas.support` | Platform below within the defined 60px gap | Missing horizontal support or excessive gap |

`test_level_contract.cjs` binds these IDs to fixed tests. Eight independent
validator tests demonstrate that invalid examples fail, including a check that
every registered requirement has a failing counterexample. These are additions
to the upstream twelve per-level tests. `run_all.cjs` runs the new small checks
first and retains every original acceptance check.

## Exact source identities and evidence

Tests were executed separately on copies, without restoring the live project:

| Input | SHA256 of levels.js | Result |
| --- | --- | --- |
| Repository example | `444ecb5cdfb8f487aee9f5c16652bbbd6c28f6bf9c35c621caafc95d2e3001d5` | Pass |
| Installed DonkeyMonkey baseline | `92bbc99384570e1fc64c8f2061d238bc65a116dcdafbaaa3669cabbf405983f9` | Pass |
| Saved autonomous candidate a58eb16821821601fd4e63b3b5fb01e2 | `604abfb934e94242e31b0d67e6ecdf616f81faefbc4785488375aec765cc6feb` | Fail, three unsupported endpoints and three disconnected exits |

The failed candidate contains Jungle Entrance, Climbing Heights, Temple Ruins
and Final Challenge. The baseline instead contains Jungle Level 1 through 4.
The saved candidate exists outside the rolled-back project in its run store.
Its actual unsupported lower endpoints are:

| Level index | Center x | Endpoint y | Platform intervals at that y |
| --- | --- | --- | --- |
| 0 | 816 | 380 | [200,400], [600,750] |
| 2 | 816 | 380 | [300,400], [600,700] |
| 3 | 466 | 380 | [200,350], [600,750] |

Thus the baseline's y=390 geometry does not invalidate this candidate's x
diagnosis. Candidate identity must accompany every diagnostic and repair.
Local detailed outputs are under `C:/CodeStudio/evidence/invariant-review-20260915`,
with separate `repo-baseline`, `installed-baseline` and `retained-candidate`
evidence.json files. A passing baseline is not a passing autonomous build.

## General planning gate: design, not yet installed functionality

Extend the existing approved test-profile mechanism, rather than accepting
model-generated shell commands:

1. Establish a caller-approved requirement catalog: stable ID, description,
   mandatory/advisory classification, approved check-profile IDs, protected test
   files, required inputs and the earliest stage where the check can run.
   Version/hash the catalog with the job's test contract.
2. Require each planned module to identify the requirements it implements.
   Deterministically reject missing mandatory IDs, unknown check IDs, unavailable
   prerequisite inputs, or no scheduled check before a dependent module.
   A mapping proves scheduling completeness, not semantic correctness of tests.
3. For a new invariant, prepare its validator before generated implementation,
   including a valid fixture and a deliberately invalid fixture. An unrelated
   green test does not discharge that requirement. Keep unformalized requirements
   explicitly unverified. A quantifier/keyword lint can advise; it cannot prove
   that a test covers prose.
4. Run the earliest eligible checks, collect structured violations and provide
   the relevant input data and numeric explanation to the repair step. Preserve
   existing approved argv, test protections, final checks and transaction rules.
5. Continue within the bounded attempt budget only if a specific changed action
   is justified. On four unsuccessful attempts: stop, preserve the candidate,
   roll back, and report requirement ID, phase, source identity, observed facts,
   suspected cause and next discriminating action. Do not mislabel a hypothesis
   as a proven cause or reset the budget merely by replanning.
6. On a later successful repair, retain the failure fixture and validated rule
   with its scope and checks. Reuse it only when applicability is verified.
   Saving a failure is experience storage; it is not model training or proof that
   a repair generalizes. Do not weaken tests to manufacture a successful memory.

Remaining engine work: governed requirement catalog, deterministic scheduling
at the earliest eligible stage, constructor/tool selection, and binding the new
checks into future autonomous job configurations. This patch does not alter
the immutable test contract of an already saved candidate. The installed
autonomous build still requires a successful model-driven repair and final QC.
