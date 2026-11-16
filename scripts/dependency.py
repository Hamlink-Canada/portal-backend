"""Read the declared shared-library branch without importing runtime code."""

import re
import tomllib
from pathlib import Path

project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
dependencies = [item for item in project["project"]["dependencies"] if item.startswith("rpt-common")]
match = re.fullmatch(
    r"rpt-common(?:\[[^]]+\])? @ git\+https://github\.com/"
    r"Hamlink-Canada/pyCommon\.git@(main)",
    dependencies[0],
) if len(dependencies) == 1 else None
if match is None:
    raise SystemExit("Expected exactly one rpt-common dependency on pyCommon main")
print(match[1])
