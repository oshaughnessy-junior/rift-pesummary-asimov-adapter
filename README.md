# RIFT PESummary ASIMOV adapter

ASIMOV 0.7+ postprocessing adapter for producing single-analysis and combined
PESummary pages from RIFT results.

The adapter consumes the versioned `rift-assets/v1` contract published by the
RIFT ASIMOV plugin. It intentionally lives outside RIFT so PESummary CLI and
metadata changes can be tested and released independently.

Development starts on reviewable feature branches. See the repository's draft
pull requests for the first implementation and compatibility matrix.

## Install

Install this package in the same environment as ASIMOV and PESummary:

```console
python -m pip install rift-pesummary-asimov-adapter
```

ASIMOV discovers the `rift-pesummary` pipeline through its standard
`asimov.pipelines` entry-point group. RIFT itself remains responsible for the
inference job and publishes a versioned `rift-assets/v1` dictionary. This
adapter consumes that dictionary and owns the mutable `summarypages` command.

## Use

Apply `examples/single-rift-pesummary.yaml` after changing its `needs` entry to
the name of one completed RIFT analysis. For one page comparing every RIFT
analysis on an event, apply `examples/combined-rift-pesummary.yaml`. These are
separate analysis blueprints: neither changes the RIFT inference workflow.

```console
asimov apply -f examples/combined-rift-pesummary.yaml -e EVENT
asimov manage build --event EVENT --dry-run
```

The generated command aligns every sample, configuration, approximant, low and
reference frequency, PSD, and calibration envelope. Detector assets use
PESummary's per-label arguments, which avoids incorrectly sharing one RIFT
analysis's files across a combined page.

## Compatibility policy

The adapter currently targets ASIMOV 0.7 and PESummary 1.6 or newer. Both RIFT
`rift_O4c` (0.0.17.13) and `rift_O4d` publish the same `rift-assets/v1`
contract. See `compatibility.yaml` and `docs/coordination.md` for the supported
matrix and synchronized-change policy.
