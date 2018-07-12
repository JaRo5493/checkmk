#!/usr/bin/python
# -*- encoding: utf-8; py-indent-offset: 4 -*-
# +------------------------------------------------------------------+
# |             ____ _               _        __  __ _  __           |
# |            / ___| |__   ___  ___| | __   |  \/  | |/ /           |
# |           | |   | '_ \ / _ \/ __| |/ /   | |\/| | ' /            |
# |           | |___| | | |  __/ (__|   <    | |  | | . \            |
# |            \____|_| |_|\___|\___|_|\_\___|_|  |_|_|\_\           |
# |                                                                  |
# | Copyright Mathias Kettner 2014             mk@mathias-kettner.de |
# +------------------------------------------------------------------+
#
# This file is part of Check_MK.
# The official homepage is at http://mathias-kettner.de/check_mk.
#
# check_mk is free software;  you can redistribute it and/or modify it
# under the  terms of the  GNU General Public License  as published by
# the Free Software Foundation in version 2.  check_mk is  distributed
# in the hope that it will be useful, but WITHOUT ANY WARRANTY;  with-
# out even the implied warranty of  MERCHANTABILITY  or  FITNESS FOR A
# PARTICULAR PURPOSE. See the  GNU General Public License for more de-
# tails. You should have  received  a copy of the  GNU  General Public
# License along with GNU Make; see the file  COPYING.  If  not,  write
# to the Free Software Foundation, Inc., 51 Franklin St,  Fifth Floor,
# Boston, MA 02110-1301 USA.

import cmk.gui.wato as wato

register_handlers({
    "wato"                      : wato.page_handler,
    "user_profile"              : wato.page_user_profile,

    "ajax_start_activation"     : lambda: wato.ModeAjaxStartActivation().handle_page(),
    "ajax_activation_state"     : lambda: wato.ModeAjaxActivationState().handle_page(),
    "user_change_pw"            : lambda: wato.page_user_profile(change_pw=True),
    "wato_ajax_profile_repl"    : lambda: wato.ModeAjaxProfileReplication().handle_page(),

    "automation_login"          : lambda: wato.ModeAutomationLogin().page(),
    "noauth:automation"         : lambda: wato.ModeAutomation().page(),
    "ajax_set_foldertree"       : lambda: wato.ModeAjaxSetFoldertree().handle_page(),
    "wato_ajax_diag_host"       : lambda: wato.ModeAjaxDiagHost().handle_page(),
    "wato_ajax_execute_check"   : lambda: wato.ModeAjaxExecuteCheck().handle_page(),
    "fetch_agent_output"        : lambda: wato.PageFetchAgentOutput().page(),
    "download_agent_output"     : lambda: wato.PageDownloadAgentOutput().page(),
    "ajax_popup_move_to_folder" : lambda: wato.ModeAjaxPopupMoveToFolder().page(),
    "ajax_backup_job_state"     : lambda: wato.ModeAjaxBackupJobState().page(),
})
