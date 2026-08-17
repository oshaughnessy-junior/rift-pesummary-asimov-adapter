import configparser
import importlib.metadata
import json
from pathlib import Path
import types

import pytest

from asimov.pipeline import PipelineException
from rift_pesummary_asimov_adapter import RIFTPESummary
from rift_pesummary_asimov_adapter import pipeline as adapter_module


class FakeRiftPipeline:
    name = "RIFT"

    def __init__(self, assets):
        self.assets = assets
        self.absolute_requests = []

    def collect_assets(self, absolute=False):
        self.absolute_requests.append(absolute)
        return self.assets


def source(
    tmp_path,
    name,
    approximant,
    f_low,
    f_ref,
    *,
    psd_suffix="shared",
    calmarg=False,
    likelihood_grid=False,
):
    sample = tmp_path / f"{name}.dat"
    sample.write_text("# samples\n")
    config = tmp_path / f"{name}.ini"
    config.write_text("[analysis]\n")
    psd = tmp_path / f"H1-{psd_suffix}-psd.dat"
    psd.write_text("20 1e-46\n")
    calibration = tmp_path / f"H1-{psd_suffix}-cal.dat"
    calibration.write_text("20 0 0\n")
    assets = {
        "asset_contract": "rift-assets/v1",
        "samples": [str(sample)],
        "samples_raw": str(sample),
        "config": str(config),
        "psds": {"H1": str(psd)},
        "calibration": {"H1": str(calibration)},
        "provenance": {
            "pipeline": "rift",
            "event": "S250202cu",
            "analysis": name,
        },
    }
    if calmarg:
        calmarg_sample = tmp_path / f"{name}-calmarg.dat"
        calmarg_sample.write_text("# calibration-marginalized samples\n")
        assets["samples"] = [str(calmarg_sample)]
        assets["samples_calmarg"] = str(calmarg_sample)
    if likelihood_grid:
        all_net = tmp_path / f"{name}-all.net"
        all_net.write_text("# marginalized likelihood grid\n")
        assets["lnL_marg"] = str(all_net)
    return types.SimpleNamespace(
        name=name,
        pipeline=FakeRiftPipeline(assets),
        category="C01_offline",
        meta={
            "waveform": {
                "approximant": approximant,
                "reference frequency": f_ref,
            },
            "likelihood": {"minimum frequency": {"H1": f_low}},
        },
    )


@pytest.fixture
def configured(monkeypatch, tmp_path):
    values = {
        ("general", "webroot"): "public_html",
        ("project", "root"): str(tmp_path),
        ("pipelines", "environment"): "/opt/igwn",
        ("condor", "user"): "rift-ci",
    }

    def fake_get(section, option):
        if section == "pesummary":
            raise configparser.NoSectionError(section)
        return values[(section, option)]

    monkeypatch.setattr(adapter_module.config, "get", fake_get)
    return tmp_path


def production(tmp_path, sources, *, dependencies=False, settings=None):
    event = types.SimpleNamespace(
        name="S250202cu",
        work_dir=str(tmp_path / "working"),
        productions=list(sources),
    )
    kwargs = {
        "name": "combined-rift-pesummary",
        "event": event,
        "category": "subject_analyses",
        "rundir": str(tmp_path / "postprocessing"),
        "status": "ready",
        "meta": {"postprocessing": {"pesummary": settings or {}}},
        "resolved_dependencies": [],
    }
    if dependencies:
        kwargs["dependencies"] = [item.name for item in sources]
    else:
        kwargs["dependencies"] = []
        kwargs["analyses"] = list(sources)
    return types.SimpleNamespace(**kwargs)


def values_after(command, option, next_options):
    start = command.index(option) + 1
    end = len(command)
    for candidate in next_options:
        try:
            end = min(end, command.index(candidate, start))
        except ValueError:
            pass
    return command[start:end]


def test_combined_command_aligns_all_rift_metadata(configured):
    first = source(configured, "rift-v5PHM", "SEOBNRv5PHM", 18, 20, psd_suffix="a")
    second = source(configured, "rift-XPHM", "IMRPhenomXPHM", 20, 30, psd_suffix="b")
    prod = production(
        configured,
        [first, second],
        settings={
            "multiprocess": 4,
            "evolve spins": ["forwards", "backwards"],
            "calculate": "precessing snr",
            "regenerate posteriors": "redshift",
            "additional arguments": {"publication": None, "seed": 42},
        },
    )

    command = RIFTPESummary(prod).build_command()

    assert values_after(command, "--labels", ["--gw"]) == [
        "rift-v5PHM",
        "rift-XPHM",
    ]
    assert values_after(command, "--approximant", ["--f_low"]) == [
        "SEOBNRv5PHM",
        "IMRPhenomXPHM",
    ]
    assert values_after(command, "--f_low", ["--f_ref"]) == ["18", "20"]
    assert values_after(command, "--f_ref", ["--samples"]) == ["20", "30"]
    assert values_after(command, "--samples", ["--config"]) == [
        str(configured / "rift-v5PHM.dat"),
        str(configured / "rift-XPHM.dat"),
    ]
    assert values_after(
        command, "--config", ["--rift-v5PHM_psd"]
    ) == [
        str(configured / "rift-v5PHM.ini"),
        str(configured / "rift-XPHM.ini"),
    ]
    assert command[command.index("--rift-v5PHM_psd") + 1] == (
        f"H1:{configured / 'H1-a-psd.dat'}"
    )
    assert command[command.index("--rift-XPHM_calibration") + 1] == (
        f"H1:{configured / 'H1-b-cal.dat'}"
    )
    assert "--evolve_spins_forwards" in command
    assert "--evolve_spins_backwards" in command
    assert "--calculate_precessing_snr" in command
    assert command[command.index("--regenerate") + 1] == "redshift"
    assert "--publication" in command
    assert command[command.index("--seed") + 1] == "42"
    assert prod.resolved_dependencies == ["rift-v5PHM", "rift-XPHM"]
    assert first.pipeline.absolute_requests == [True]
    assert second.pipeline.absolute_requests == [True]


def test_single_dependency_uses_same_contract(configured):
    analysis = source(configured, "rift-single", "SEOBNRv5PHM", 20, 20)
    prod = production(configured, [analysis], dependencies=True)

    command = RIFTPESummary(prod).build_command()

    assert values_after(command, "--labels", ["--gw"]) == ["rift-single"]
    assert values_after(command, "--samples", ["--config"]) == [
        str(configured / "rift-single.dat")
    ]


def test_multiple_samples_get_unique_aligned_labels(configured):
    analysis = source(configured, "rift multi", "SEOBNRv5PHM", 20, 20)
    second_sample = configured / "second.dat"
    second_sample.write_text("# samples\n")
    analysis.pipeline.assets["samples"].append(str(second_sample))
    prod = production(configured, [analysis])

    command = RIFTPESummary(prod).build_command()

    assert values_after(command, "--labels", ["--gw"]) == [
        "rift_multi-1",
        "rift_multi-2",
    ]
    assert len(values_after(command, "--config", ["--rift_multi-1_psd"])) == 2


def test_all_sample_variants_include_standard_and_calmarg(configured):
    analysis = source(
        configured,
        "rift-both",
        "SEOBNRv5PHM",
        20,
        20,
        calmarg=True,
    )
    prod = production(
        configured,
        [analysis],
        settings={"sample variants": "all"},
    )

    command = RIFTPESummary(prod).build_command()

    assert values_after(command, "--labels", ["--gw"]) == [
        "rift-both-standard",
        "rift-both-calmarg",
    ]
    assert values_after(command, "--samples", ["--config"]) == [
        str(configured / "rift-both.dat"),
        str(configured / "rift-both-calmarg.dat"),
    ]
    assert len(
        values_after(command, "--config", ["--rift-both-standard_psd"])
    ) == 2


def test_explicit_missing_calmarg_is_an_error(configured):
    analysis = source(configured, "rift-standard", "SEOBNRv5PHM", 20, 20)
    prod = production(
        configured,
        [analysis],
        settings={"sample variants": "calmarg"},
    )

    with pytest.raises(PipelineException, match="no available calmarg samples"):
        RIFTPESummary(prod).build_command()


def test_rejects_old_or_unknown_asset_contract(configured):
    analysis = source(configured, "rift-old", "SEOBNRv5PHM", 20, 20)
    analysis.pipeline.assets["asset_contract"] = "rift-assets/v2"
    prod = production(configured, [analysis])

    with pytest.raises(PipelineException, match="does not publish rift-assets/v1"):
        RIFTPESummary(prod).build_command()


def test_rejects_non_rift_source(configured):
    analysis = source(configured, "bilby", "IMRPhenomXPHM", 20, 20)
    analysis.pipeline.name = "Bilby"
    prod = production(configured, [analysis])

    with pytest.raises(PipelineException, match="is not a RIFT analysis"):
        RIFTPESummary(prod).build_command()


def test_managed_arguments_cannot_be_overridden(configured):
    analysis = source(configured, "rift-single", "SEOBNRv5PHM", 20, 20)
    prod = production(
        configured,
        [analysis],
        settings={"additional arguments": {"samples": "/tmp/wrong.dat"}},
    )

    with pytest.raises(PipelineException, match="override managed inputs"):
        RIFTPESummary(prod).build_command()


def test_dryrun_writes_reproducible_script_without_submission(configured):
    analysis = source(configured, "rift-single", "SEOBNRv5PHM", 20, 20)
    prod = production(configured, [analysis])
    adapter = RIFTPESummary(prod)

    assert adapter.submit_dag(dryrun=True) == 0

    script = Path(adapter.rundir) / "pesummary.sh"
    assert script.exists()
    text = script.read_text()
    assert text.startswith("/opt/igwn/bin/summarypages ")
    assert "--samples" in text
    assert "--config" in text


def test_build_phase_writes_script(configured):
    analysis = source(configured, "rift-single", "SEOBNRv5PHM", 20, 20)
    adapter = RIFTPESummary(production(configured, [analysis]))

    assert adapter.build_dag(dryrun=True) == 0
    assert (Path(adapter.rundir) / "pesummary.sh").exists()


def test_optional_all_net_capture_publishes_copy(configured):
    analysis = source(
        configured,
        "rift-grid",
        "SEOBNRv5PHM",
        20,
        20,
        likelihood_grid=True,
    )
    prod = production(
        configured,
        [analysis],
        settings={"capture all.net": True},
    )
    adapter = RIFTPESummary(prod)

    assert adapter.build_dag(dryrun=True) == 0
    expected = Path(adapter.rundir) / "auxiliary" / "rift-grid" / "all.net"
    assert expected.read_text() == "# marginalized likelihood grid\n"
    assert adapter.collect_assets()["likelihood"] == {
        "rift-grid": str(expected)
    }
    assert adapter.results()["likelihood"] == {"rift-grid": str(expected)}


def test_collect_assets_and_completion_location(configured):
    analysis = source(configured, "rift-single", "SEOBNRv5PHM", 20, 20)
    adapter = RIFTPESummary(production(configured, [analysis]))
    assets = adapter.collect_assets()

    assert assets["pages"] == adapter.webdir
    assert assets["samples"].endswith("samples/posterior_samples.h5")
    assert adapter.results() == {
        "metafile": assets["samples"],
        "pages": adapter.webdir,
    }
    assert adapter.detect_completion() is False
    Path(assets["samples"]).parent.mkdir(parents=True)
    Path(assets["samples"]).write_text("placeholder")
    assert adapter.detect_completion() is True


def test_entry_point_is_discoverable():
    entries = importlib.metadata.entry_points(group="asimov.pipelines")
    entry = next(entry for entry in entries if entry.name == "rift-pesummary")
    assert entry.load() is RIFTPESummary


def test_golden_assets_validate_against_schema():
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "schema" / "rift-assets-v1.schema.json").read_text())
    assets = json.loads((root / "tests" / "fixtures" / "rift-assets-v1.json").read_text())

    jsonschema.validate(assets, schema)
