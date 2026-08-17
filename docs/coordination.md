# Cross-repository coordination

RIFT owns bootstrap behavior and the versioned producer contract. This adapter
owns translation to mutable PESummary command-line interfaces. ASIMOV owns
plugin loading and analysis selection.

Interface changes use one coordination issue and linked draft PRs for
`rift_O4c`, `rift_O4d`, and this adapter. Tolerant consumer support lands first;
both RIFT producer branches then land together. Removing or changing an asset
type requires a new contract major version.

Every adapter PR uses the repository pull-request checklist. A contract change
cannot be marked ready until both RIFT branch links and their matrix results are
present. The JSON schema here is a tested consumer snapshot; the producer
implementations in RIFT remain authoritative. A mismatch is a release blocker,
not something the adapter guesses around.

## Required matrix

| RIFT | ASIMOV | Expected behavior |
|---|---|---|
| 0.0.17.x / O4c | 0.5 | legacy automatic PESummary |
| 0.0.17.x / O4c | 0.7 | this separate adapter |
| 0.0.18 prerelease / O4d | 0.5 | legacy automatic PESummary |
| 0.0.18 prerelease / O4d | 0.7 | this separate adapter |

ASIMOV and PESummary development heads run as scheduled canaries. They become
blocking before a release candidate. Stable combinations use explicit pins.

The matrix must cover plugin discovery, no-submit command construction,
single and combined pages, detector assets, configuration cardinality,
standard plus calibration-marginalized sample variants, auxiliary `all.net`
capture, combined-metafile bootstrap ambiguity, and idempotent reruns.
