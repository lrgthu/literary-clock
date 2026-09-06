"""Pinned upstream corpus catalog."""

from __future__ import annotations

from pathlib import Path

from litclock.models import SourceSpec


def default_source_specs(project_root: Path) -> list[SourceSpec]:
    base = project_root / "data" / "third_party"
    return [
        SourceSpec(
            name="kapoorankush/litclock",
            slug="kapoorankush-litclock",
            url="https://github.com/kapoorankush/litclock",
            license="CC BY-NC-SA 4.0 (assembled quote database; code license is separate)",
            commit="2d644ae640e74f0b1823fb14f50e12e7f4c089c8",
            corpus_path=base / "kapoorankush-litclock" / "litclock_annotated.csv",
            corpus_sha256="eaf30e5a037a3901a52ad8e3b54ed488a9e548d6d4bbcdcda18eae50a4b6bec5",
            format="pipe_csv",
            sfw_style="nsfw_yes_no",
        ),
        SourceSpec(
            name="zenbuffy/LiteraryClock",
            slug="zenbuffy-literary-clock",
            url="https://github.com/zenbuffy/LiteraryClock",
            license=(
                "NOASSERTION: no explicit root corpus license; README credits data derived "
                "from JohsEnevoldsen/literature-clock"
            ),
            commit="465801e55f1877fe4d02f2090eca5bc87d1480ca",
            corpus_path=base / "zenbuffy-literary-clock" / "litclock.yaml",
            corpus_sha256="f5041a82eeed2f6e9741d7c0872fe3752cdb83e344d41636bd9c49a1e02eb6b9",
            format="yaml",
        ),
        SourceSpec(
            name="JohsEnevoldsen/literature-clock",
            slug="johsenevoldsen-literature-clock",
            url="https://github.com/JohsEnevoldsen/literature-clock",
            license="CC BY-NC-SA 2.5 Generic",
            commit="febdd2821b62e0ff060346a023426f9e2e6456b4",
            corpus_path=base / "johsenevoldsen-literature-clock" / "litclock_annotated.csv",
            corpus_sha256="21a7f457d15984c225852e234c5dc4e7e5a940535c7a68c9521d6256994362d0",
            format="pipe_csv",
            sfw_style="sfw_label",
        ),
    ]
