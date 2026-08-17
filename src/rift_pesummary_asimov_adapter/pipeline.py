"""ASIMOV pipeline adapter for single and combined RIFT PESummary pages."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from asimov import config
from asimov.pipeline import Pipeline, PipelineException


ASSET_CONTRACT = "rift-assets/v1"
_SAFE_LABEL = re.compile(r"[^A-Za-z0-9_.-]+")
_RESERVED_ADDITIONAL_ARGUMENTS = {
    "webdir",
    "labels",
    "samples",
    "config",
    "approximant",
    "f_low",
    "f_ref",
    "gw",
    "psd",
    "calibration",
}


@dataclass(frozen=True)
class _PESummaryInput:
    """One aligned PESummary input row."""

    label: str
    sample: str
    config: str
    approximant: Optional[str]
    f_low: Optional[str]
    f_ref: Optional[str]
    psds: Mapping[str, str]
    calibration: Mapping[str, str]


def _absolute(path: Any) -> str:
    return os.path.abspath(os.fspath(path))


def _normalise_paths(value: Any, *, field: str, analysis: str) -> List[str]:
    if isinstance(value, (str, os.PathLike)):
        paths = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        paths = list(value)
    else:
        raise PipelineException(
            f"RIFT {analysis} asset '{field}' must be a path or list of paths"
        )
    if not paths:
        raise PipelineException(f"RIFT {analysis} publishes no {field}")
    return [_absolute(path) for path in paths]


def _safe_label(name: str) -> str:
    label = _SAFE_LABEL.sub("_", str(name)).strip("_")
    if not label:
        raise PipelineException(f"Cannot construct a PESummary label from {name!r}")
    return label


class RIFTPESummary(Pipeline):
    """Build and submit PESummary pages from one or more RIFT analyses."""

    name = "RIFT-PESummary"

    def __init__(self, production, category=None):
        super().__init__(production, category)
        self.event = getattr(production, "subject", None) or getattr(
            production, "event", None
        )
        if self.event is None:
            raise PipelineException(
                "RIFT-PESummary requires an event or subject",
                production=getattr(production, "name", None),
            )
        if category is not None:
            self.category = category
        self.meta = self._settings()
        self.rundir = self._rundir()
        self.webdir = self._webdir()
        self.executable = self._executable()

    def _settings(self) -> Dict[str, Any]:
        meta = getattr(self.production, "meta", {}) or {}
        postprocessing = meta.get("postprocessing", {})
        if isinstance(postprocessing, Mapping):
            settings = postprocessing.get("pesummary", {})
            if isinstance(settings, Mapping):
                return dict(settings)
        settings = meta.get("pesummary", {})
        return dict(settings) if isinstance(settings, Mapping) else {}

    def _rundir(self) -> str:
        existing = getattr(self.production, "rundir", None)
        if existing:
            return _absolute(existing)
        work_dir = getattr(self.event, "work_dir", os.getcwd())
        return _absolute(os.path.join(work_dir, self.production.name))

    def _webdir(self) -> str:
        explicit = self.meta.get("webdir")
        if explicit:
            return _absolute(explicit)
        webroot = config.get("general", "webroot")
        if not os.path.isabs(webroot):
            webroot = os.path.join(config.get("project", "root"), webroot)
        return _absolute(
            os.path.join(webroot, self.event.name, self.production.name, "pesummary")
        )

    def _executable(self) -> str:
        explicit = self.meta.get("executable")
        if explicit:
            return os.fspath(explicit)
        try:
            configured = config.get("pesummary", "executable")
            if configured:
                return configured
        except (configparser.NoOptionError, configparser.NoSectionError):
            pass
        return os.path.join(config.get("pipelines", "environment"), "bin", "summarypages")

    def _sources(self) -> List[Any]:
        for attribute in ("analyses", "productions"):
            candidates = getattr(self.production, attribute, None)
            if candidates:
                return [candidate for candidate in candidates if candidate is not self.production]

        dependencies = list(getattr(self.production, "dependencies", None) or [])
        if dependencies:
            by_name = {
                analysis.name: analysis
                for analysis in getattr(self.event, "productions", [])
            }
            missing = [name for name in dependencies if name not in by_name]
            if missing:
                raise PipelineException(
                    f"RIFT-PESummary dependencies are missing: {missing}",
                    production=self.production.name,
                )
            return [by_name[name] for name in dependencies]

        raise PipelineException(
            "RIFT-PESummary has no source analyses; use 'needs' for one RIFT "
            "analysis or 'analyses' for a combined page",
            production=self.production.name,
        )

    @staticmethod
    def _pipeline_name(source: Any) -> str:
        pipeline = getattr(source, "pipeline", None)
        return str(getattr(pipeline, "name", pipeline)).lower()

    def _source_assets(self, source: Any) -> Dict[str, Any]:
        if self._pipeline_name(source) != "rift":
            raise PipelineException(
                f"RIFT-PESummary source {source.name} is not a RIFT analysis",
                production=self.production.name,
            )
        collector = getattr(source.pipeline, "collect_assets", None)
        if not callable(collector):
            raise PipelineException(f"RIFT source {source.name} publishes no assets")
        try:
            assets = collector(absolute=True)
        except TypeError:
            assets = collector()
        if not isinstance(assets, dict):
            raise PipelineException(
                f"RIFT source {source.name} returned non-dictionary assets"
            )
        if assets.get("asset_contract") != ASSET_CONTRACT:
            raise PipelineException(
                f"RIFT source {source.name} does not publish {ASSET_CONTRACT}"
            )
        return assets

    @staticmethod
    def _frequency(source: Any, section: str, key: str) -> Optional[str]:
        value = (getattr(source, "meta", {}) or {}).get(section, {}).get(key)
        if isinstance(value, Mapping):
            if not value:
                return None
            value = min(value.values())
        return None if value is None else str(value)

    @staticmethod
    def _detector_paths(assets: Mapping[str, Any], key: str) -> Dict[str, str]:
        mapping = assets.get(key, {}) or {}
        if not isinstance(mapping, Mapping):
            raise PipelineException(f"RIFT asset '{key}' must map detector names to paths")
        return {str(ifo): _absolute(path) for ifo, path in mapping.items()}

    def _inputs(self) -> List[_PESummaryInput]:
        inputs: List[_PESummaryInput] = []
        seen_labels = set()
        resolved = []

        for source in self._sources():
            assets = self._source_assets(source)
            samples = _normalise_paths(
                assets.get("samples"), field="samples", analysis=source.name
            )
            configs = _normalise_paths(
                assets.get("config"), field="config", analysis=source.name
            )
            if len(configs) == 1:
                configs *= len(samples)
            elif len(configs) != len(samples):
                raise PipelineException(
                    f"RIFT {source.name} publishes {len(samples)} samples but "
                    f"{len(configs)} configurations"
                )

            waveform = (getattr(source, "meta", {}) or {}).get("waveform", {})
            approximant = waveform.get("approximant")
            f_low = self._frequency(source, "likelihood", "minimum frequency")
            if f_low is None:
                f_low = self._frequency(source, "quality", "minimum frequency")
            f_ref = self._frequency(source, "waveform", "reference frequency")
            psds = self._detector_paths(assets, "psds")
            calibration = self._detector_paths(assets, "calibration")

            for index, (sample, config_path) in enumerate(zip(samples, configs), start=1):
                label_name = source.name if len(samples) == 1 else f"{source.name}-{index}"
                label = _safe_label(label_name)
                if label in seen_labels:
                    raise PipelineException(f"Duplicate PESummary label {label!r}")
                seen_labels.add(label)
                inputs.append(
                    _PESummaryInput(
                        label=label,
                        sample=sample,
                        config=config_path,
                        approximant=None if approximant is None else str(approximant),
                        f_low=f_low,
                        f_ref=f_ref,
                        psds=psds,
                        calibration=calibration,
                    )
                )
            resolved.append(source.name)

        if not inputs:
            raise PipelineException("RIFT-PESummary resolved no usable inputs")
        self.production.resolved_dependencies = resolved
        return inputs

    def _append_aligned(
        self,
        command: List[str],
        option: str,
        values: Iterable[Optional[str]],
    ) -> None:
        values = list(values)
        if all(value is not None for value in values):
            command.extend([option, *[str(value) for value in values]])
        elif any(value is not None for value in values):
            self.logger.warning(
                "%s is missing for some RIFT inputs; omitting it for all inputs",
                option,
            )

    def _additional_arguments(self) -> List[str]:
        raw = self.meta.get("additional arguments", {})
        if isinstance(raw, Mapping):
            items: Iterable[Any] = [raw]
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            items = raw
        elif not raw:
            return []
        else:
            raise PipelineException("'additional arguments' must be a mapping or list")

        command: List[str] = []
        for item in items:
            if isinstance(item, str):
                key, value = item, None
                pairs = [(key, value)]
            elif isinstance(item, Mapping):
                pairs = item.items()
            else:
                raise PipelineException(f"Unsupported additional argument {item!r}")
            for key, value in pairs:
                key = str(key).lstrip("-")
                if key in _RESERVED_ADDITIONAL_ARGUMENTS or key.endswith(
                    ("_psd", "_calibration")
                ):
                    raise PipelineException(
                        f"Additional argument {key!r} would override managed inputs"
                    )
                command.append(f"--{key}")
                if value is None or value == "":
                    continue
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                    command.extend(str(entry) for entry in value)
                else:
                    command.append(str(value))
        return command

    def build_command(self) -> List[str]:
        """Return the argument vector passed to ``summarypages``."""
        inputs = self._inputs()
        command = [
            "--webdir",
            self.webdir,
            "--labels",
            *[item.label for item in inputs],
            "--gw",
        ]
        self._append_aligned(command, "--approximant", [item.approximant for item in inputs])
        self._append_aligned(command, "--f_low", [item.f_low for item in inputs])
        self._append_aligned(command, "--f_ref", [item.f_ref for item in inputs])

        command.extend(["--samples", *[item.sample for item in inputs]])
        command.extend(["--config", *[item.config for item in inputs]])

        for item in inputs:
            if item.psds:
                command.append(f"--{item.label}_psd")
                command.extend(f"{ifo}:{path}" for ifo, path in sorted(item.psds.items()))
            if item.calibration:
                command.append(f"--{item.label}_calibration")
                command.extend(
                    f"{ifo}:{path}" for ifo, path in sorted(item.calibration.items())
                )

        simple_options = {
            "cosmology": "--cosmology",
            "redshift": "--redshift_method",
            "skymap samples": "--nsamples_for_skymap",
            "multiprocess": "--multi_process",
            "preferred": "--preferred",
        }
        for key, option in simple_options.items():
            if key in self.meta:
                command.extend([option, str(self.meta[key])])

        evolve = self.meta.get("evolve spins", []) or []
        if isinstance(evolve, str):
            evolve = [evolve]
        if any("forward" in str(value).lower() for value in evolve):
            command.append("--evolve_spins_forwards")
        if any("backward" in str(value).lower() for value in evolve):
            command.extend(["--evolve_spins_backwards", "precession_averaged"])

        if any(
            item.approximant and "nrsur" in item.approximant.lower()
            for item in inputs
        ):
            command.append("--NRSur_fits")
        calculate = self.meta.get("calculate", []) or []
        if isinstance(calculate, str):
            calculate = [calculate]
        if "precessing snr" in calculate:
            command.append("--calculate_precessing_snr")
        regenerate = self.meta.get("regenerate posteriors")
        if regenerate:
            if isinstance(regenerate, str):
                regenerate = [regenerate]
            command.extend(["--regenerate", *[str(value) for value in regenerate]])
        command.extend(self._additional_arguments())
        return command

    def _write_script(self, command: Sequence[str]) -> None:
        Path(self.rundir).mkdir(parents=True, exist_ok=True)
        script = Path(self.rundir) / "pesummary.sh"
        script.write_text(shlex.join([self.executable, *command]) + "\n")

    def build_dag(self, user=None, dryrun=False):
        """Materialise the reproducible command used by ASIMOV's build phase."""
        command = self.build_command()
        self._write_script(command)
        if dryrun:
            self.logger.info(
                "PESummary command: %s", shlex.join([self.executable, *command])
            )
        return 0

    def submit_dag(self, dryrun=False):
        """Write and optionally submit the PESummary HTCondor job."""
        command = self.build_command()
        self._write_script(command)

        submit_description = {
            "executable": self.executable,
            "arguments": shlex.join(command),
            "output": str(Path(self.rundir) / "pesummary.out"),
            "error": str(Path(self.rundir) / "pesummary.err"),
            "log": str(Path(self.rundir) / "pesummary.log"),
            "request_cpus": str(self.meta.get("multiprocess", 1)),
            "request_memory": str(self.meta.get("request memory", "8192MB")),
            "request_disk": str(self.meta.get("request disk", "8192MB")),
            "getenv": "true",
            "batch_name": f"RIFT-PESummary/{self.event.name}/{self.production.name}",
        }
        accounting_group = self.meta.get("accounting group")
        if accounting_group:
            submit_description["accounting_group"] = accounting_group
            submit_description["accounting_group_user"] = config.get("condor", "user")

        if dryrun:
            self.logger.info("PESummary command: %s", shlex.join([self.executable, *command]))
            return 0

        try:
            import htcondor2 as htcondor
        except ImportError:  # pragma: no cover - depends on site HTCondor version
            import htcondor

        submit = htcondor.Submit(submit_description)
        try:
            scheduler_name = config.get("condor", "scheduler")
            ad = htcondor.Collector().locate(htcondor.DaemonTypes.Schedd, scheduler_name)
            schedd = htcondor.Schedd(ad)
        except Exception:
            schedd = htcondor.Schedd()
        with schedd.transaction() as transaction:
            cluster = submit.queue(transaction)
        self.production.meta["job id"] = int(cluster)
        self.production.status = "running"
        return cluster

    def detect_completion(self):
        return os.path.exists(
            os.path.join(self.webdir, "samples", "posterior_samples.h5")
        )

    def collect_assets(self):
        return {
            "samples": os.path.join(
                self.webdir, "samples", "posterior_samples.h5"
            ),
            "pages": self.webdir,
        }

    def results(self):
        """Expose the combined metafile using ASIMOV's legacy results API."""
        assets = self.collect_assets()
        return {"metafile": assets["samples"], "pages": assets["pages"]}
