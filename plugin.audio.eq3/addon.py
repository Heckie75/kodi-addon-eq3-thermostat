#!/usr/bin/python3
# -*- coding: utf-8 -*-

import json
import os
import re
import subprocess
import sys
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
import xbmcvfs

__PLUGIN_ID__ = "plugin.audio.eq3"

SLOTS = 5

settings = xbmcaddon.Addon(id=__PLUGIN_ID__)
addon_dir = xbmcvfs.translatePath(settings.getAddonInfo('path'))

_menu = []


class ContinueLoop(Exception):
    pass


class Eq3Exception(Exception):
    pass


def _exec_gatttool(mac, params):

    if settings.getSetting("host") == "1":
        # remote over ssh
        call = ["ssh", settings.getSetting("host_ip"),
                "-p %s" % settings.getSetting("host_port"),
                settings.getSetting("host_path")]
        call += [mac] + params

    else:
        # local
        call = [addon_dir + os.sep + "lib" + os.sep + "eq3.exp"]
        call += [mac] + params

    p = subprocess.Popen(call,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)

    out, err = p.communicate()
    return out.decode("utf-8").split("\n")


def _exec_bluetoothctl():

    macs = []

    if settings.getSetting("host") == "1":
        # remote over ssh
        p2 = subprocess.Popen(["ssh", settings.getSetting("host_ip"),
                               "-p %s" % settings.getSetting("host_port"),
                               "echo -e 'devices\nquit\n\n' | bluetoothctl"],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)

    else:
        # local
        p1 = subprocess.Popen(["echo", "-e", "devices\nquit\n\n"],
                              stdout=subprocess.PIPE)
        p2 = subprocess.Popen(["bluetoothctl"], stdin=p1.stdout,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        p1.stdout.close()

    out, err = p2.communicate()

    for match in re.finditer('([0-9A-F:]+) CC-RT-BLE',
                             out.decode("utf-8")):
        macs += [match.group(1)]

    return macs


def discover():

    inserts = []
    free = []

    macs = _exec_bluetoothctl()

    for mac in macs:
        try:
            for i in range(SLOTS):
                smac = settings.getSetting("dev_mac_%i" % i)
                senabled = settings.getSetting("dev_enable_%i" % i)
                if smac == mac:
                    raise ContinueLoop

                elif (smac == "" or senabled == "false") and i not in free:
                    free += [i]

            inserts += [mac]

        except ContinueLoop:
            continue

    if len(free) == 0 and len(inserts) > 0:
        xbmc.executebuiltin(
            "Notification(All slots are occupied, "
            "Disable a device from list!)")
        return

    for mac in inserts:
        slot = None
        if len(free) > 0:
            slot = free.pop(0)
        else:
            continue

        settings.setSetting("dev_mac_%i" % slot, mac)
        alias = settings.getSetting("dev_alias_%i" % slot)
        if alias == "":
            settings.setSetting("dev_alias_%i" % slot,
                                "Thermostat %i" % (slot + 1))

    if len(macs) == 0:
        xbmc.executebuiltin(
            "Notification(No thermostates found, "
            "Check if at least one thermostat is paired!)")

    elif len(inserts) == 0:
        xbmc.executebuiltin(
            "Notification(No new devices found, "
            "Check already paired thermostates!)")
    else:
        xbmc.executebuiltin(
            "Notification(New thermostates found, "
            "%i new thermostates added to device list)" % len(inserts))


def _get_directory_by_path(path):

    if path == "/":
        return _menu[0]

    tokens = path.split("/")[1:]
    directory = _menu[0]

    while len(tokens) > 0:
        path = tokens.pop(0)
        for node in directory["node"]:
            if node["path"] == path:
                directory = node
                break

    return directory


def _build_param_string(param, values, current=""):

    if values == None:
        return current

    for v in values:
        current += "?" if len(current) == 0 else "&"
        current += param + "=" + str(v)

    return current


def _add_list_item(entry, path):

    if path == "/":
        path = ""

    item_path = path + "/" + entry["path"]
    item_id = item_path.replace("/", "_")

    param_string = ""
    if "send" in entry:
        param_string = _build_param_string(
            param="send",
            values=entry["send"],
            current=param_string)

    if "param" in entry:
        param_string = _build_param_string(
            param=entry["param"][0],
            values=[entry["param"][1]],
            current=param_string)

    if "msg" in entry:
        param_string = _build_param_string(
            param="msg",
            values=[entry["msg"]],
            current=param_string)

    if "node" in entry:
        is_folder = True
    else:
        is_folder = False

    label = entry["name"]
    if settings.getSetting("label%s" % item_id) != "":
        label = settings.getSetting("label%s" % item_id)

    if "icon" in entry:
        icon_file = os.path.join(
            addon_dir, "resources", "assets", entry["icon"] + ".png")
    else:
        icon_file = None

    li = xbmcgui.ListItem(label)
    li.setArt({"thumb": icon_file})

    xbmcplugin.addDirectoryItem(handle=addon_handle,
                                listitem=li,
                                url="plugin://" + __PLUGIN_ID__
                                + item_path
                                + param_string,
                                isFolder=is_folder)


def _build_vacation(temp):

    entries = []
    for i in [1, 2, 3, 4, 6, 9, 12, 24]:

        entries += [
            {
                "path": "%i" % i,
                "name": "%s hours" % i,
                "send": ["vacation", i, temp],
                "icon": "icon_timer",
                "msg": "Hold %.1f°C for %i hours" % (temp, i)
            }
        ]

    return entries


def _build_temperature():

    _min = float(settings.getSetting("temp_min"))
    _max = float(settings.getSetting("temp_max")) + 1

    entries = []
    for i in range(int((_max - 1) * 2), int(_min * 2) - 1, -1):

        t = i / 2.0
        entries += [
            {
                "path": "%i" % i,
                "name": "%.1f°C" % t,
                "icon": "icon_temp_%.0f" % (t * 10),
                "send": ["temp", "%.1f" % t],
                "msg": "Set temperature to %.1f°C" % t
            }
        ]

    return entries


def _parse_status(output):

    status = {
        "temp": None,
        "valve": None,
        "auto": None,
        "boost": None,
        "dst": None,
        "window": None,
        "locked": None,
        "battery": None,
        "vacation": None,
        "until": None,
        "success": False
    }

    for line in output:

        if line.startswith("Connection failed."):
            break

        elif line.startswith("Temperature:"):
            m = re.findall("([0-9\.]+)", line)
            status["temp"] = float(m[0])
            status["success"] = True

        elif line.startswith("Valve:"):
            m = re.findall("([0-9]+)", line)
            status["valve"] = int(m[0])

        elif line.startswith("Mode:"):
            status["auto"] = "auto" in line
            status["vacation"] = "vacation" in line
            status["boost"] = "boost" in line
            status["dst"] = "dst" in line
            status["window"] = "open window" in line
            status["locked"] = "locked" in line
            status["battery"] = "low battery" in line

        elif line.startswith("Vacation until:"):
            m = re.findall("(20[0-9-: ]+0)", line)
            status["until"] = m[0]

    if not status["success"]:
        raise Eq3Exception

    return status


def _get_status(mac):

    output = _exec_gatttool(mac, ["sync"])
    return _parse_status(output)


def _build_device_menu(mac, status=None):

    if not status:
        status = _get_status(mac)

    stemp = "Target temperature: %.1f°C" % (status["temp"])
    stemp += "" if status["until"] == None else " hold until %s" \
        % status["until"]

    stext = "Mode: "
    stext += "Auto" if status["auto"] else "Manual"
    stext += ", heating" if status["valve"] > 0 else ", not heating"
    stext += "" if not status["boost"] else ", boost"
    stext += "" if not status["window"] else ", window open"
    stext += "" if not status["locked"] else ", locked"
    stext += "" if not status["battery"] else ", battery is low!"

    sicon = ""
    if status["battery"]:
        sicon = "icon_battery"
    elif status["window"]:
        sicon = "icon_window"
    elif status["boost"]:
        sicon = "icon_boost"
    elif status["vacation"]:
        sicon = "icon_timer"
    elif status["auto"]:
        sicon = "icon_auto"
    elif not status["auto"]:
        sicon = "icon_manual"
    else:
        sicon = "icon_info"

    device = [
        {
            "path": "target",
            "name": stemp,
            "icon": "icon_temp_%i" % int(status["temp"] * 10),
            "node": []
        },
        {
            "path": "mode",
            "param": ["status", json.dumps(status)],
            "name": stext,
            "icon": sicon,
            "node": []
        },
        {
            "path": "hold",
            "param": ["temp", status["temp"]],
            "name": "Hold %.1f°C for the next ..." % (status["temp"]),
            "icon": "icon_timer",
            "node": []
        }
    ]

    return device


def _build_mode_menu(mac, status=None):

    if not status:
        status = _get_status(mac)

    mode = []

    if status["boost"]:
        mode += [
            {
                "path": "boost",
                "name": "Stop boost ...",
                "icon": "icon_boost_off",
                "send": ["boost", "off"],
                "msg": "Stop boost ...",
                "node": []
            }
        ]
    else:
        mode += [
            {
                "path": "boost",
                "name": "Boost for 5 minutes ...",
                "icon": "icon_boost",
                "send": ["boost"],
                "msg": "Start boost ...",
                "node": []
            }
        ]

    name = "Hold temperature is active. " if status["vacation"] else ""
    if not status["auto"] or status["vacation"]:
        mode += [
            {
                "path": "mode",
                "name": name + "Switch to auto mode ...",
                "icon": "icon_auto",
                "send": ["auto"],
                "msg": "Switch to auto mode ...",
                "node": []
            }
        ]

    if status["auto"] or status["vacation"]:
        mode += [
            {
                "path": "mode",
                "name": name + "Switch to manual mode ...",
                "icon": "icon_manual",
                "send": ["manual"],
                "msg": "Switch to manual mode ...",
                "node": []
            }
        ]

    if status["valve"] > 0:
        mode += [
            {
                "path": "valve",
                "name": "Heating. Valve is %i%% opened." % status["valve"],
                "icon": "icon_info"
            }
        ]
    else:
        mode += [
            {
                "path": "valve",
                "name": "Heating is paused. Valve is closed.",
                "icon": "icon_info"
            }
        ]

    if status["battery"]:
        mode += [
            {
                "path": "battery",
                "name": "Battery is low.",
                "icon": "icon_battery"
            }
        ]
    if status["window"]:
        mode += [
            {
                "path": "window",
                "name": "Window is open.",
                "icon": "icon_window"
            }
        ]

    return mode


def _build_dir_structure(path, url_params):

    global _menu

    splitted_path = path.split("/")
    splitted_path.pop(0)

    entries = []

    # root
    if path == "/":
        for i in range(SLOTS):

            mac = settings.getSetting("dev_mac_%i" % i)
            alias = settings.getSetting("dev_alias_%i" % i)
            enabled = settings.getSetting("dev_enabled_%i" % i)

            if mac == "" or enabled != "true":
                continue

            entries += [
                {
                    "path": mac,
                    "name": alias,
                    "node": []
                }
            ]

    # device main menu with status
    elif path != "/" and len(splitted_path) == 1:

        status = None
        if "status" in url_params:
            status = json.loads(url_params["status"][0])

        mac = splitted_path[0]
        entries = [
            {
                "path": mac,
                "node": _build_device_menu(mac, status)
            }
        ]

    # submenu "set temperature"
    elif len(splitted_path) == 2 and splitted_path[1] == "target":
        entries = [
            {
                "path": splitted_path[0],
                "node": [
                    {
                        "path": "target",
                        "node": _build_temperature()
                    }
                ]
            }
        ]

    # submenu "set mode"
    elif len(splitted_path) == 2 and splitted_path[1] == "mode":

        status = None
        if "status" in url_params:
            status = json.loads(url_params["status"][0])

        mac = splitted_path[0]

        entries = [
            {
                "path": splitted_path[0],
                "param": ["status", json.dumps(status)],
                "node": [
                    {
                        "path": "mode",
                        "node": _build_mode_menu(mac, status)
                    }
                ]
            }
        ]

    # submenu "set vacation"
    elif len(splitted_path) == 2 and splitted_path[1] == "hold":
        entries = [
            {
                "path": splitted_path[0],
                "node": [
                    {
                        "path": "hold",
                        "icon": "icon_timer",
                        "node": _build_vacation(
                            float(url_params["temp"][0]))
                    }
                ]
            }
        ]

    _menu = [
        {  # root
            "path": "",
            "node": entries
        }
    ]


def browse(path, url_params):

    try:
        _build_dir_structure(path, url_params)

        directory = _get_directory_by_path(path)
        for entry in directory["node"]:
            _add_list_item(entry, path)

        xbmcplugin.endOfDirectory(addon_handle)

    except Eq3Exception:
        xbmc.executebuiltin("Notification(%s, %s, %s/icon.png)"
                            % ("Synchronization failed!",
                               "Try again!", addon_dir))


def execute(path, params):

    splitted_path = path.split("/")
    if len(splitted_path) < 2:
        return

    mac = splitted_path[1]

    if "silent" not in params:
        xbmc.executebuiltin("Notification(%s, %s, %s/icon.png)"
                            % (params["msg"][0], "Be patient ...", addon_dir))

    try:
        output = _exec_gatttool(mac, params["send"])
        status = _parse_status(output)

        if "silent" not in params:
            xbmc.executebuiltin("Notification(%s, %s, %s/icon.png)"
                                % (params["msg"][0], "successful", addon_dir))

            xbmc.executebuiltin('Container.Update("plugin://%s/%s?status=%s","update")'
                                % (__PLUGIN_ID__, mac, json.dumps(status)))

    except Eq3Exception:
        if "silent" not in params:
            xbmc.executebuiltin("Notification(%s, %s, %s/icon.png)"
                                % (params["msg"][0], "Failed! Try again", addon_dir))


if __name__ == '__main__':

    if sys.argv[1] == "discover":
        discover()
    else:
        addon_handle = int(sys.argv[1])
        path = urllib.parse.urlparse(sys.argv[0]).path
        url_params = urllib.parse.parse_qs(sys.argv[2][1:])

        if "send" in url_params:
            execute(path, url_params)
        else:
            browse(path, url_params)
