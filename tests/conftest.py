import shutil
from pathlib import Path

import pytest

from grazr import config
from grazr.ids import AnimalIds

FIXTURE = Path(__file__).parent / "fixtures" / "farm"


@pytest.fixture
def cfg():
    return config.load(FIXTURE)


@pytest.fixture
def animal_ids(cfg):
    return AnimalIds(cfg)


@pytest.fixture
def farm_root(tmp_path):
    root = tmp_path / "farm"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns(".grazr"))
    return root


@pytest.fixture
def farm(farm_root):
    from grazr.api import Farm
    return Farm(farm_root)
