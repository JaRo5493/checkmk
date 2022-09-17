#!/usr/bin/env python3
# Copyright (C) 2019 tribe29 GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

# Uses next windows msi build tools
# - lcab
# - msiinfo
# - msibuild

import argparse
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

from cmk.utils import msi_patch

AGENT_MSI_FILE: Final = "check_mk_agent_unsigned.msi"
_MSI_FILES: Final = sorted(["check_mk_install_yml", "checkmk.dat", "plugins_cap", "python_3.cab"])
_MSI_COMPONENTS: Final = sorted(
    [
        "check_mk_install_yml_",
        "checkmk.dat",
        "plugins_cap_",
        "python_3.cab",
    ]
)

opt_verbose = True


def _verbose(text: str) -> None:
    if opt_verbose:
        sys.stdout.write(text + "\n")


def bail_out(text: str) -> NoReturn:
    sys.stderr.write("ERROR: %s\n" % text)
    sys.exit(1)


def msi_file_table() -> list[str]:
    """return sorted table of files in MSI"""
    return _MSI_FILES


def msi_component_table() -> list[str]:
    """return sorted table of components in MSI"""
    return _MSI_COMPONENTS


def _remove_cab(path_to_msibuild: Path, *, msi: Path) -> None:
    _verbose("Removing product.cab from %s" % msi)
    cmd: Final = f"{path_to_msibuild/'msibuild'} {msi} -q \"DELETE FROM _Streams where Name = 'product.cab'\""

    if (result := os.system(cmd)) != 0:  # nosec B605 # BNS:f6c1b9
        bail_out(f"msibuild is failed on remove cab, {result=}")


def _create_new_cab(work_dir: Path) -> None:
    _verbose("Generating new product.cab")
    files = " ".join(map(lambda f: f"{work_dir/f}", msi_file_table()))
    cmd: Final = f"lcab -n {files} {work_dir}/product.cab > nul"
    if (result := os.system(cmd)) != 0:  # nosec B605 # BNS:f6c1b9
        bail_out(f"lcab is failed in create new cab, {result=}")


def _add_cab(path_to_msibuild: Path, *, msi: Path, working_dir: Path) -> None:
    _verbose("Add modified product.cab")
    cmd: Final = f"{path_to_msibuild/'msibuild'} {msi} -a product.cab {working_dir}/product.cab"
    if (result := os.system(cmd)) != 0:  # nosec B605 # BNS:f6c1b9
        bail_out(f"msi build is failed, {result=}")


def update_package_code(file_name: Path, *, package_code: str | None) -> None:
    """Patch package code of MSI with new random package_code_hash"""

    # NOTES:
    # Update summary info with new uuid (HACK! - the msibuild tool is not able to do this on all
    # systems). We replace the package code with a new uuid. This uuid is important, because it is
    # the unique identifier for this package.
    # Inside the package the uuid is split into two halfs. Each of it is updated with the
    # corresponding new package code.

    if not msi_patch.patch_package_code_by_marker(
        file_name, new_uuid=package_code, state_file=None
    ):
        raise Exception("Failed to patch package code")


def read_file_as_lines(f_to_read: Path) -> list[str]:
    with f_to_read.open("r", newline="", encoding="utf8") as in_file:
        return in_file.readlines()


def _patch_msi_files(use_dir: Path, version_build: str) -> None:
    name = "File.idt"
    lines_file_idt = read_file_as_lines(use_dir / name)

    with (use_dir / (name + ".new")).open("w", newline="", encoding="utf8") as out_file:
        out_file.write("".join(lines_file_idt[:3]))

        for line in lines_file_idt[3:]:
            words = line.split("\t")
            filename = words[0]
            # check every file from the table whether it should be replaced
            for file_to_replace in msi_file_table():  # sorted(cabinet_files):
                if file_to_replace == filename:
                    work_file = use_dir / filename
                    if work_file.exists():
                        file_stats = work_file.stat()
                        new_size = file_stats.st_size
                        words[3] = str(new_size)
                    else:
                        _verbose("'{}' doesn't exist".format(work_file))
                    break  # one file per table

            # The version of this file is different from the msi installer version !
            words[4] = version_build if words[4] else ""
            out_file.write("\t".join(words))


def _patch_msi_components(use_dir: Path) -> None:
    name = "Component.idt"
    lines_component_idt = read_file_as_lines(use_dir / name)

    with (use_dir / (name + ".new")).open("w", newline="", encoding="utf8") as out_file:
        out_file.write("".join(lines_component_idt[:3]))

        for line in lines_component_idt[3:]:
            words = line.split("\t")
            if words[0] in msi_component_table():
                words[1] = ("{%s}" % uuid.uuid1()).upper()
            out_file.write("\t".join(words))


def _patch_msi_properties(use_dir: Path, *, product_code: str, version_build: str) -> None:
    name = "Property.idt"
    lines_property_idt = read_file_as_lines(use_dir / name)
    with (use_dir / (name + ".new")).open("w", newline="", encoding="utf8") as out_file:
        out_file.write("".join(lines_property_idt[:3]))

        for line in lines_property_idt[3:]:
            tokens = line.split("\t")
            if tokens[0] == "ProductName":
                tokens[1] = "Check MK Agent 2.1\r\n"
            # The upgrade code defines the product family. Do not change it!
            #    elif tokens[0] == "UpgradeCode":
            #        tokens[1] = upgrade_code
            elif tokens[0] == "ProductCode":
                tokens[1] = product_code
            elif tokens[0] == "ProductVersion":
                tokens[1] = "%s\r\n" % ".".join(version_build.split(".")[:4])
            out_file.write("\t".join(tokens))


def _copy_file_safe(s: Path, *, d: Path) -> bool:
    try:
        shutil.copy(s, d)
        return True
    except IOError as ex:
        _verbose("exception in copy safe {}".format(ex))
    return False


def copy_or_create(src_file: Path, *, dst_file: Path, text: str) -> None:
    if src_file.exists():
        _copy_file_safe(src_file, d=dst_file)
        return

    # fallback
    with dst_file.open("w", newline="", encoding="utf8") as f:
        f.write(text)


def _strip_ascii_suffix(version: str) -> str:
    # Remove any traces of i, p, b versions. Windows can't handle them...
    # The revision should be enough to uniquely identify this build
    # The original version name is also still visible in the list of programs
    match = re.search("[a-z]", version)
    if match:
        result = version[: match.start(0)]
        if result[-1] == ".":
            result += "0"
        return result

    return version


def generate_product_version(version: str, *, revision_text: str) -> str:
    major, minor, build = "1", "0", "0"
    try:
        major, minor, build = [x.lstrip("0") for x in version.split("-")[0].split(".")[:3]]
        minor = minor or "0"
        build = build or "0"
        if len(major) > 3:
            # Looks like a daily build: 2015.03.05
            major = major[2:].lstrip("0")
    except Exception as _:
        pass

    product_version = _strip_ascii_suffix(f"{major}.{minor}.{build}")

    return f"{product_version}.{revision_text}"


# tested
def _export_msi_file_table(exe_dir: Path, *, name: str, msi_in: Path, out_dir: Path) -> None:
    _verbose("Export table %s from file %s" % (name, msi_in))
    exe = exe_dir / "msiinfo"
    if not exe.exists():
        bail_out(f"{exe} is absent")

    command = f"{exe} export {msi_in} {name} > {out_dir}/{name}.idt"
    result = os.system(command)  # nosec B605 # BNS:f6c1b9
    if result != 0:
        bail_out(f"Failed unpack msi table {name} from {msi_in}, code is {result}.Cmd: {command}")


@dataclass(frozen=True)
class _Parameters:
    msi: Path
    src_dir: Path
    revision: str
    version: str
    package_code_hash: str | None


def parse_command_line(argv: Sequence[str]) -> _Parameters:
    global opt_verbose
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=False, help="increase verbosity"
    )
    parser.add_argument("msi", type=Path, help="msi container to create")
    parser.add_argument("src_dir", type=Path, help="directory with data files")
    parser.add_argument("revision", type=str, help="Revision calculated from version")
    parser.add_argument(
        "version", type=str, help="Official version: 2015.04.12 or 1.2.6-2015.04.12"
    )
    parser.add_argument(
        "config_hash", type=str, nargs="?", help="hash of agent configuration(aka aghash)"
    )
    result = parser.parse_args(argv[1:])
    opt_verbose = result.verbose
    return _Parameters(
        msi=result.msi,
        src_dir=result.src_dir,
        revision=result.revision,
        version=result.version,
        package_code_hash=result.config_hash,
    )


_TABLE_NAMES: Final = ["File", "Property", "Component"]


def _rename_modified_tables(work_dir: Path) -> None:
    for entry in _TABLE_NAMES:
        filename = (work_dir / entry).with_suffix(".idt.new")
        filename.rename(filename.with_suffix(""))


def _insert_modified_tables_in_msi(bin_dir: Path, *, msi: Path, work_dir: Path) -> None:
    for entry in _TABLE_NAMES:
        cmd = f"{bin_dir / 'msibuild'} {msi} -i {work_dir}/{entry}.idt"
        if (result := os.system(cmd)) != 0:  # nosec B605 # BNS:f6c1b9
            bail_out(f"failed main msibuild, {result=}")


def _copy_required_files(work_dir: Path, *, src_dir: Path) -> None:
    yml_file = Path(src_dir, "check_mk.install.yml")
    yml_target = Path(work_dir, "check_mk_install_yml")
    copy_or_create(
        yml_file,
        dst_file=yml_target,
        text="# test file\r\nglobal:\r\n  enabled: yes\r\n  install: no\r\n",
    )

    if src_dir != work_dir:
        shutil.copy(src_dir / "checkmk.dat", work_dir / "checkmk.dat")
    shutil.copy(src_dir / "plugins.cap", work_dir / "plugins_cap")
    shutil.copy(src_dir / "python-3.cab", work_dir / "python_3.cab")


def _export_required_tables(bin_dir: Path, *, msi: Path, work_dir: Path) -> None:
    for name in _TABLE_NAMES:
        _export_msi_file_table(bin_dir, name=name, msi_in=msi, out_dir=work_dir)


def msi_update_core(
    msi_file_name: Path,
    src_dir: Path,
    revision_text: str,
    version: str,
    package_code_base: str | None,
) -> None:
    work_dir = Path()
    try:
        new_version_build = generate_product_version(version, revision_text=revision_text)

        if "OMD_ROOT" in os.environ:
            omd_root = Path(os.environ["OMD_ROOT"])
            bin_dir = omd_root / "bin"
            tmp_dir = omd_root / "tmp"
        else:
            bin_dir = Path(".")
            tmp_dir = Path(".")

        new_msi_file: Final = src_dir / AGENT_MSI_FILE
        work_dir = Path(tempfile.mkdtemp(prefix=str(tmp_dir) + "/msi-update."))

        # When this script is run in the build environment then we need to specify
        # paths to the msitools. When running in an OMD site, these tools are in
        # our path

        _export_required_tables(bin_dir, msi=new_msi_file, work_dir=work_dir)

        _verbose("Modify extracted files..")

        # ==============================================
        # Modify File.idt

        # Convert Input Files to Internal-MSI Presentation
        _copy_required_files(work_dir, src_dir=src_dir)

        _patch_msi_files(work_dir, new_version_build)
        _patch_msi_components(work_dir)
        _patch_msi_properties(
            work_dir,
            product_code=f"{uuid.uuid1()}\r\n".upper(),
            version_build=new_version_build,
        )
        # ==============================================

        # 1. TABLES:
        _rename_modified_tables(work_dir)
        _insert_modified_tables_in_msi(bin_dir, msi=new_msi_file, work_dir=work_dir)
        # 2. PATCH
        update_package_code(new_msi_file, package_code=package_code_base)
        # 3.CAB:
        _remove_cab(bin_dir, msi=new_msi_file)
        _create_new_cab(work_dir)
        _add_cab(bin_dir, msi=new_msi_file, working_dir=work_dir)

        shutil.rmtree(work_dir)
        _verbose(f"Successfully created file {new_msi_file}")
    except Exception as e:
        # if work_dir and os.path.exists(work_dir):
        #    shutil.rmtree(work_dir)
        bail_out(f"Error on creating msi file: {e}, work_dir is {work_dir}")


# NOTES:
# Typical command line:
# msi-update -v ../../wnx/test_files/msibuild/msi/check_mk_agent.msi ../../wnx/test_files/msibuild . 1.7.0i1
#   package_code can be None: used in Windows Build machine to generate random package code
#   in bakery we are sending aghash to _generate_ package code

# MAIN:
if __name__ == "__main__":
    p = parse_command_line(sys.argv)
    msi_update_core(p.msi, p.src_dir, p.revision, p.version, package_code_base=p.package_code_hash)
