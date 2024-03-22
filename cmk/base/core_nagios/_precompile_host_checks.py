#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""
Precompiling creates on dedicated Python file per host, which just
contains that code and information that is needed for executing all
checks of that host. Also static data that cannot change during the
normal monitoring process is being precomputed and hard coded. This
all saves substantial CPU resources as opposed to running Checkmk
in adhoc mode (about 75%).
"""

import itertools
import os
import py_compile
import socket
import sys
from io import StringIO
from pathlib import Path

import cmk.utils.config_path
import cmk.utils.password_store
import cmk.utils.paths
import cmk.utils.store as store
import cmk.utils.tty as tty
from cmk.utils.check_utils import section_name_of
from cmk.utils.config_path import VersionedConfigPath
from cmk.utils.hostaddress import HostName
from cmk.utils.log import console

from cmk.checkengine.checking import CheckPluginName
from cmk.checkengine.inventory import InventoryPluginName

import cmk.base.api.agent_based.register as agent_based_register
import cmk.base.config as config
import cmk.base.server_side_calls as server_side_calls
import cmk.base.utils
from cmk.base.config import ConfigCache
from cmk.base.ip_lookup import AddressFamily

from cmk.discover_plugins import PluginLocation


class HostCheckStore:
    """Caring about persistence of the precompiled host check files"""

    @staticmethod
    def host_check_file_path(config_path: VersionedConfigPath, hostname: HostName) -> Path:
        return Path(config_path) / "host_checks" / hostname

    @staticmethod
    def host_check_source_file_path(config_path: VersionedConfigPath, hostname: HostName) -> Path:
        # TODO: Use append_suffix(".py") once we are on Python 3.10
        path = HostCheckStore.host_check_file_path(config_path, hostname)
        return path.with_suffix(path.suffix + ".py")

    def write(self, config_path: VersionedConfigPath, hostname: HostName, host_check: str) -> None:
        compiled_filename = self.host_check_file_path(config_path, hostname)
        source_filename = self.host_check_source_file_path(config_path, hostname)

        store.makedirs(compiled_filename.parent)

        store.save_text_to_file(source_filename, host_check)

        # compile python (either now or delayed - see host_check code for delay_precompile handling)
        if config.delay_precompile:
            compiled_filename.symlink_to(hostname + ".py")
        else:
            py_compile.compile(
                file=str(source_filename),
                cfile=str(compiled_filename),
                dfile=str(compiled_filename),
                doraise=True,
            )
            os.chmod(compiled_filename, 0o750)  # nosec B103 # BNS:c29b0e

        console.verbose(" ==> %s.\n", compiled_filename, stream=sys.stderr)


def precompile_hostchecks(config_path: VersionedConfigPath) -> None:
    console.verbose("Creating precompiled host check config...\n")
    config_cache = config.get_config_cache()
    hosts_config = config_cache.hosts_config

    config.save_packed_config(config_path, config_cache)

    console.verbose("Precompiling host checks...\n")

    host_check_store = HostCheckStore()
    for hostname in {
        # Inconsistent with `create_config` above.
        hn
        for hn in itertools.chain(hosts_config.hosts, hosts_config.clusters)
        if config_cache.is_active(hn) and config_cache.is_online(hn)
    }:
        try:
            console.verbose(
                "%s%s%-16s%s:",
                tty.bold,
                tty.blue,
                hostname,
                tty.normal,
                stream=sys.stderr,
            )
            host_check = dump_precompiled_hostcheck(
                config_cache,
                config_path,
                hostname,
            )
            if host_check is None:
                console.verbose("(no Checkmk checks)\n")
                continue

            host_check_store.write(config_path, hostname, host_check)
        except Exception as e:
            if cmk.utils.debug.enabled():
                raise
            console.error(f"Error precompiling checks for host {hostname}: {e}\n")
            sys.exit(5)


def dump_precompiled_hostcheck(  # pylint: disable=too-many-branches
    config_cache: ConfigCache,
    config_path: VersionedConfigPath,
    hostname: HostName,
    *,
    verify_site_python: bool = True,
) -> str | None:
    (
        needed_legacy_check_plugin_names,
        needed_agent_based_check_plugin_names,
        needed_agent_based_inventory_plugin_names,
    ) = _get_needed_plugin_names(config_cache, hostname)

    if hostname in config_cache.hosts_config.clusters:
        assert config_cache.nodes(hostname)
        for node in config_cache.nodes(hostname):
            (
                node_needed_legacy_check_plugin_names,
                node_needed_agent_based_check_plugin_names,
                node_needed_agent_based_inventory_plugin_names,
            ) = _get_needed_plugin_names(config_cache, node)
            needed_legacy_check_plugin_names.update(node_needed_legacy_check_plugin_names)
            needed_agent_based_check_plugin_names.update(node_needed_agent_based_check_plugin_names)
            needed_agent_based_inventory_plugin_names.update(
                node_needed_agent_based_inventory_plugin_names
            )

    needed_legacy_check_plugin_names.update(
        _get_required_legacy_check_sections(
            needed_agent_based_check_plugin_names,
            needed_agent_based_inventory_plugin_names,
        )
    )

    if not any(
        (
            needed_legacy_check_plugin_names,
            needed_agent_based_check_plugin_names,
            needed_agent_based_inventory_plugin_names,
        )
    ):
        return None

    output = StringIO()
    output.write("#!/usr/bin/env python3\n")
    output.write("# encoding: utf-8\n\n")

    output.write("import logging\n")
    output.write("import sys\n\n")

    if verify_site_python:
        output.write("if not sys.executable.startswith('/omd'):\n")
        output.write('    sys.stdout.write("ERROR: Only executable with sites python\\n")\n')
        output.write("    sys.exit(2)\n\n")

    # Self-compile: replace symlink with precompiled python-code, if
    # we are run for the first time
    if config.delay_precompile:
        output.write(
            """
import os
if os.path.islink(%(dst)r):
    import py_compile
    os.remove(%(dst)r)
    py_compile.compile(%(src)r, %(dst)r, %(dst)r, True)
    os.chmod(%(dst)r, 0o700)

"""
            % {
                "src": str(HostCheckStore.host_check_source_file_path(config_path, hostname)),
                "dst": str(HostCheckStore.host_check_file_path(config_path, hostname)),
            }
        )

    # Remove precompiled directory from sys.path. Leaving it in the path
    # makes problems when host names (name of precompiled files) are equal
    # to python module names like "random"
    output.write("sys.path.pop(0)\n")

    output.write("import cmk.utils.log\n")
    output.write("import cmk.utils.debug\n")
    output.write("from cmk.utils.exceptions import MKTerminate\n")
    output.write("from cmk.utils.config_path import LATEST_CONFIG\n")
    output.write("\n")
    output.write("import cmk.base.utils\n")
    output.write("import cmk.base.config as config\n")
    output.write("from cmk.discover_plugins import PluginLocation\n")
    output.write("import cmk.base.obsolete_output as out\n")
    output.write("from cmk.utils.log import console\n")
    output.write("from cmk.base.api.agent_based.register import register_plugin_by_type\n")
    output.write("import cmk.base.check_api as check_api\n")
    output.write("import cmk.base.ip_lookup as ip_lookup\n")  # is this still needed?
    output.write("from cmk.checkengine.submitters import get_submitter\n")
    output.write("\n")

    locations = _get_needed_agent_based_locations(
        needed_agent_based_check_plugin_names,
        needed_agent_based_inventory_plugin_names,
    )
    for module in {l.module for l in locations}:
        output.write("import %s\n" % module)
        console.verbose(" %s%s%s", tty.green, module, tty.normal, stream=sys.stderr)
    for location in (l for l in locations if l.name is not None):
        output.write(f"register_plugin_by_type({location!r}, {location.module}.{location.name})\n")

    # Register default Checkmk signal handler
    output.write("cmk.base.utils.register_sigint_handler()\n")

    # initialize global variables
    output.write(
        """
# very simple commandline parsing: only -v (once or twice) and -d are supported

cmk.utils.log.setup_console_logging()
logger = logging.getLogger("cmk.base")

# TODO: This is not really good parsing, because it not cares about syntax like e.g. "-nv".
#       The later regular argument parsing is handling this correctly. Try to clean this up.
cmk.utils.log.logger.setLevel(cmk.utils.log.verbosity_to_log_level(len([ a for a in sys.argv if a in [ "-v", "--verbose"] ])))

if '-d' in sys.argv:
    cmk.utils.debug.enable()

"""
    )

    file_list = sorted(_get_legacy_check_file_names_to_load(needed_legacy_check_plugin_names))
    formatted_file_list = (
        "\n    %s,\n" % ",\n    ".join("%r" % n for n in file_list) if file_list else ""
    )
    output.write(
        "config.load_checks(check_api.get_check_api_context, [%s])\n" % formatted_file_list
    )

    for check_plugin_name in sorted(needed_legacy_check_plugin_names):
        console.verbose(" %s%s%s", tty.green, check_plugin_name, tty.normal, stream=sys.stderr)

    output.write("config.load_packed_config(LATEST_CONFIG)\n")

    # IP addresses
    (
        needed_ipaddresses,
        needed_ipv6addresses,
    ) = (
        {},
        {},
    )
    if hostname in config_cache.hosts_config.clusters:
        assert config_cache.nodes(hostname)
        for node in config_cache.nodes(hostname):
            if AddressFamily.IPv4 in ConfigCache.address_family(node):
                needed_ipaddresses[node] = config.lookup_ip_address(
                    config_cache, node, family=socket.AddressFamily.AF_INET
                )

            if AddressFamily.IPv6 in ConfigCache.address_family(node):
                needed_ipv6addresses[node] = config.lookup_ip_address(
                    config_cache, node, family=socket.AddressFamily.AF_INET6
                )

        try:
            if AddressFamily.IPv4 in ConfigCache.address_family(hostname):
                needed_ipaddresses[hostname] = config.lookup_ip_address(
                    config_cache, hostname, family=socket.AddressFamily.AF_INET
                )
        except Exception:
            pass

        try:
            if AddressFamily.IPv6 in ConfigCache.address_family(hostname):
                needed_ipv6addresses[hostname] = config.lookup_ip_address(
                    config_cache, hostname, family=socket.AddressFamily.AF_INET6
                )
        except Exception:
            pass
    else:
        if AddressFamily.IPv4 in ConfigCache.address_family(hostname):
            needed_ipaddresses[hostname] = config.lookup_ip_address(
                config_cache, hostname, family=socket.AddressFamily.AF_INET
            )

        if AddressFamily.IPv6 in ConfigCache.address_family(hostname):
            needed_ipv6addresses[hostname] = config.lookup_ip_address(
                config_cache, hostname, family=socket.AddressFamily.AF_INET6
            )

    output.write("config.ipaddresses = %r\n\n" % needed_ipaddresses)
    output.write("config.ipv6addresses = %r\n\n" % needed_ipv6addresses)
    output.write("try:\n")
    output.write("    # mode_check is `mode --check hostname`\n")
    output.write("    from cmk.base.modes.check_mk import mode_check\n")
    output.write("    sys.exit(\n")
    output.write("        mode_check(\n")
    output.write("            get_submitter,\n")
    output.write("            {},\n")
    output.write(f"           [{hostname!r}],\n")
    output.write("            active_check_handler=lambda *args: None,\n")
    output.write("            keepalive=False,\n")
    output.write("        )\n")
    output.write("    )\n")
    output.write("except MKTerminate:\n")
    output.write("    out.output('<Interrupted>\\n', stream=sys.stderr)\n")
    output.write("    sys.exit(1)\n")
    output.write("except SystemExit as e:\n")
    output.write("    sys.exit(e.code)\n")
    output.write("except Exception as e:\n")
    output.write("    import traceback, pprint\n")

    # status output message
    output.write(
        '    sys.stdout.write("UNKNOWN - Exception in precompiled check: %s (details in long output)\\n" % e)\n'
    )

    # generate traceback for long output
    output.write('    sys.stdout.write("Traceback: %s\\n" % traceback.format_exc())\n')

    output.write("\n")
    output.write("    sys.exit(3)\n")

    return output.getvalue()


def _get_needed_plugin_names(
    config_cache: ConfigCache, host_name: HostName
) -> tuple[set[str], set[CheckPluginName], set[InventoryPluginName]]:
    ssc_api_special_agents = {p.name for p in server_side_calls.load_special_agents()[1].values()}
    needed_legacy_check_plugin_names = {
        f"agent_{name}"
        for name, _p in config_cache.special_agents(host_name)
        if name not in ssc_api_special_agents
    }

    # Collect the needed check plugin names using the host check table.
    # Even auto-migrated checks must be on the list of needed *agent based* plugins:
    # In those cases, the module attribute will be `None`, so nothing will
    # be imported; BUT: we need it in the list, because it must be considered
    # when determining the needed *section* plugins.
    # This matters in cases where the section is migrated, but the check
    # plugins are not.
    needed_agent_based_check_plugin_names = config_cache.check_table(
        host_name,
        filter_mode=config.FilterMode.INCLUDE_CLUSTERED,
        skip_ignored=False,
    ).needed_check_names()

    legacy_names = (_resolve_legacy_plugin_name(pn) for pn in needed_agent_based_check_plugin_names)
    needed_legacy_check_plugin_names.update(ln for ln in legacy_names if ln is not None)

    # Inventory plugins get passed parsed data these days.
    # Load the required sections, or inventory plugins will crash upon unparsed data.
    needed_agent_based_inventory_plugin_names: set[InventoryPluginName] = set()
    if config_cache.hwsw_inventory_parameters(host_name).status_data_inventory:
        for inventory_plugin in agent_based_register.iter_all_inventory_plugins():
            needed_agent_based_inventory_plugin_names.add(inventory_plugin.name)
            for parsed_section_name in inventory_plugin.sections:
                # check if we must add the legacy check plugin:
                legacy_check_name = config.legacy_check_plugin_names.get(
                    CheckPluginName(str(parsed_section_name))
                )
                if legacy_check_name is not None:
                    needed_legacy_check_plugin_names.add(legacy_check_name)

    return (
        needed_legacy_check_plugin_names,
        needed_agent_based_check_plugin_names,
        needed_agent_based_inventory_plugin_names,
    )


def _resolve_legacy_plugin_name(check_plugin_name: CheckPluginName) -> str | None:
    legacy_name = config.legacy_check_plugin_names.get(check_plugin_name)
    if legacy_name:
        return legacy_name

    if not check_plugin_name.is_management_name():
        return None

    # See if me must include a legacy plugin from which we derived the given one:
    # A management plugin *could have been* created on the fly, from a 'regular' legacy
    # check plugin. In this case, we must load that.
    plugin = agent_based_register.get_check_plugin(check_plugin_name)
    if not plugin or plugin.location is not None:
        # it does *not* result from a legacy plugin, if module is not None
        return None

    # just try to get the legacy name of the 'regular' plugin:
    return config.legacy_check_plugin_names.get(check_plugin_name.create_basic_name())


def _get_legacy_check_file_names_to_load(
    needed_check_plugin_names: set[str],
) -> set[str]:
    # check info table
    # We need to include all those plugins that are referenced in the hosts
    # check table.
    return {
        filename
        for check_plugin_name in needed_check_plugin_names
        for filename in _find_check_plugins(check_plugin_name)
    }


def _find_check_plugins(checktype: str) -> set[str]:
    """Find files to be included in precompile host check for a certain
    check (for example df or mem.used).

    In case of checks with a period (subchecks) we might have to include both "mem" and "mem.used".
    The subcheck *may* be implemented in a separate file."""
    return {
        filename
        for candidate in (section_name_of(checktype), checktype)
        # in case there is no "main check" anymore, the lookup fails -> skip.
        if (filename := config.legacy_check_plugin_files.get(candidate)) is not None
    }


def _get_needed_agent_based_locations(
    check_plugin_names: set[CheckPluginName],
    inventory_plugin_names: set[InventoryPluginName],
) -> list[PluginLocation]:
    modules = {
        plugin.location
        for plugin in [agent_based_register.get_check_plugin(p) for p in check_plugin_names]
        if plugin is not None and plugin.location is not None
    }
    modules.update(
        plugin.location
        for plugin in [agent_based_register.get_inventory_plugin(p) for p in inventory_plugin_names]
        if plugin is not None and plugin.location is not None
    )
    modules.update(
        section.location
        for section in agent_based_register.get_relevant_raw_sections(
            check_plugin_names=check_plugin_names,
            inventory_plugin_names=inventory_plugin_names,
        ).values()
        if section.location is not None
    )

    return sorted(modules, key=lambda l: (l.module, l.name or ""))


def _get_required_legacy_check_sections(
    check_plugin_names: set[CheckPluginName],
    inventory_plugin_names: set[InventoryPluginName],
) -> set[str]:
    """
    new style plugin may have a dependency to a legacy check
    """
    required_legacy_check_sections = set()
    for section in agent_based_register.get_relevant_raw_sections(
        check_plugin_names=check_plugin_names,
        inventory_plugin_names=inventory_plugin_names,
    ).values():
        if section.location is None:
            required_legacy_check_sections.add(str(section.name))
    return required_legacy_check_sections
