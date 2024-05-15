#!/usr/bin/env python3
# Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from pathlib import Path

import pytest

from omdlib.contexts import SiteContext
from omdlib.site_name import sitename_must_be_valid


@pytest.mark.parametrize(
    "name",
    [
        "lulu",
        "asd0",
        "aaaaaaaaaaaaaaaa",
    ],
)
def test_sitename_must_be_valid_ok(tmp_path: Path, name: str) -> None:
    tmp_path.joinpath("omd/sites/lala").mkdir(parents=True)
    sitename_must_be_valid(SiteContext(name))


@pytest.mark.parametrize(
    "name",
    [
        "0asd",
        "",
        "aaaaaaaaaaaaaaaaa",
    ],
)
def test_sitename_must_be_valid_not_ok(tmp_path: Path, name: str) -> None:
    tmp_path.joinpath("omd/sites/lala").mkdir(parents=True)
    with pytest.raises(SystemExit, match="Invalid site name"):
        sitename_must_be_valid(SiteContext(name))


@pytest.mark.usefixtures("omd_base_path")
def test_sitename_must_be_valid_already_exists(tmp_path: Path) -> None:
    tmp_path.joinpath("omd/sites/lala").mkdir(parents=True)

    with pytest.raises(SystemExit, match="already existing"):
        sitename_must_be_valid(SiteContext("lala"))
