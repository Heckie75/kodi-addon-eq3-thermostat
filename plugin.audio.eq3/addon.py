#!/usr/bin/python
# -*- coding: utf-8 -*-

from datetime import datetime
from datetime import timedelta
import json
import os
import re
import subprocess
import sys
import urlparse

import xbmcgui
import xbmcplugin
import xbmcaddon

__PLUGIN_ID__ = "plugin.audio.eq3"

SLOTS = 5

reload(sys)
sys.setdefaultencoding('utf8')

settings = xbmcaddon.Addon(id=__PLUGIN_ID__);
addon_dir = xbmc.translatePath( settings.getAddonInfo('path') )

_menu = []


class ContinueLoop(Exception):
    pass




def _exec_gatttool(mac, params):

    xbmc.log("mac: " + mac, xbmc.LOGNOTICE)
    xbmc.log("params: " + " ".join(params), xbmc.LOGNOTICE)

    call = [addon_dir + os.sep + "lib" + os.sep + "eq3.exp"]
    call += [ mac ] + params

    p = subprocess.Popen(call,
                         stdout=subprocess.PIPE, 
                         stderr=subprocess.PIPE)
    
    out, err = p.communicate()
    
    return out.decode("utf-8").split("\n")




def _exec_bluetoothctl():

    macs = []
    p1 = subprocess.Popen(["echo", "-e", "quit\n\n"], 
                          stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["bluetoothctl"], stdin=p1.stdout, 
                          stdout=subprocess.PIPE, 
                          stderr=subprocess.PIPE)
    p1.stdout.close()
    out, err = p2.communicate()
    
    for match in re.finditer('([0-9A-F:]+) CC-RT-BLE', 
                             out.decode("utf-8"), 
                             re.S):
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
                    free += [ i ]
                
                    
            inserts += [ mac ]
        
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

    xbmc.log("path: " + path, xbmc.LOGNOTICE)

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




def _build_param_string(param, values, current = ""):

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

    if settings.getSetting("display%s" % item_id) == "false":
        return

    param_string = ""
    if "send" in entry:
        param_string = _build_param_string(
            param = "send",
            values = entry["send"],
            current = param_string)
        
    if "param" in entry:
        param_string = _build_param_string(
            param = entry["param"][0],
            values = [ entry["param"][1] ],
            current = param_string)

    if "msg" in entry:
        param_string = _build_param_string(
            param = "msg",
            values = [ entry["msg"] ],
            current = param_string)

    if "node" in entry:
        is_folder = True
    else:
        is_folder = False

    label = entry["name"]
    if settings.getSetting("label%s" % item_id) != "":
        label = settings.getSetting("label%s" % item_id)

    if "icon" in entry:
        icon_file = os.path.join(addon_dir, "resources", "assets", entry["icon"] + ".png")
    else:
        icon_file = None

    li = xbmcgui.ListItem(label, iconImage=icon_file)

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
                "path" : "%i" % i,
                "name" : "%s hours" % i,
                "send" : [ "vacation", i, temp ],
                "msg" : "Set hold temperature of %.1f°C for %i hours" % (temp, i)
            }
        ]

    return entries




def _build_temperature():

    entries = []
    _min = float(settings.getSetting("temp_min"))
    _max = float(settings.getSetting("temp_max")) + 1
    for i in range(int((_max - 1) * 2), int(_min * 2) - 1, -1):

        t = i / 2.0
        entries += [
            {
                "path" : "%i" % i,
                "name" : "%.1f°C" % t,
                "icon" : "icon_temp_%.0f" % (t * 10),
                "send" : ["temp", "%.1f" % t ],
                "msg" : "Set target temperature to %.1f°C" % t
            }
        ]

    return entries


def _parse_status(output):
  
    status = {
        "temp" : None,
        "valve" : None,
        "auto" : None,
        "boost" : None,
        "dst" : None,
        "window" : None,
        "locked" : None,
        "battery" : None,
        "vacation" : None,
        "until" : None,
        "success" : False
    }
  
    for line in output:
        
        xbmc.log(">> " + line, xbmc.LOGNOTICE)
        
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

    return status




def _get_status(mac):
    
    output = _exec_gatttool(mac, ["sync"])
    return _parse_status(output)




def _build_device_menu(mac, status = None):
    
    if not status:
        status = _get_status(mac)
    
    stemp = "Target temperature: %.1f°C" % (status["temp"])
    stemp += "" if status["until"] == None else " hold until %s" \
                % status["until"]       
    stext = "Status: " 
    stext += "Auto" if status["auto"] else "Manual"
    stext += ", valve %i%%" % status["valve"] 

    stext += "" if not status["boost"] else ", boost"
    stext += "" if not status["window"] else ", window open"
    stext += "" if not status["locked"] else ", locked"
    stext += "" if not status["battery"] else ", battery is low!"
    
    device = [
        {
            "path" : "target",
            "name" : stemp,
            "icon" : "icon_temp_205" # todo
        },
        {
            "path" : "status",
            "name" : stext,
            "icon" : "icon_info" # todo
        }
    ]
    device += [
        {
            "path" : "temp",
            "name" : "Set new target temperature ...",
            "icon" : "icon_temp",
            "node" : []
        },
        {
            "path" : "hold",
            "param" : ["temp", status["temp"]],
            "name" : "Hold target temperature of %.1f°C for the next ..." % (status["temp"]),
            "icon" : "icon_vacation",
            "node" : []
        }
    ]        
    if status["boost"]:
        device += [
            {
                "path" : "boost",
                "name" : "Stop boost",
                "icon" : "icon_boost_off",
                "send" : ["boost", "off"],
                "msg" : "Stop boost"
            }
        ]
    else:
        device += [
            {
                "path" : "boost",
                "name" : "Start boost",
                "icon" : "icon_boost",
                "send" : ["boost"],
                "msg" : "Start boost"
            }
        ]
    if status["auto"]:
        device += [
            {
                "path" : "mode",
                "name" : "Set manual",
                "icon" : "icon_manual",
                "send" : ["manual"],
                "msg" : "Set manual mode"
            }
        ]
    else:
        device += [
            {
                "path" : "mode",
                "name" : "Set auto",
                "icon" : "icon_auto",
                "send" : ["auto"],
                "msg" : "Set auto mode"
            }
        ]
    
    return device




def build_dir_structure(path, url_params):

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

            entries = [
                {
                    "path" : mac,
                    "name" : alias,
                    "node" : []
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
                "path" : mac,
                "node" : _build_device_menu(mac, status)
            }
        ]

    # submenu "set temperature"
    elif len(splitted_path) == 2 and splitted_path[1] == "temp":
        entries = [
            {
                "path" : splitted_path[0],
                "node" : [
                    {
                        "path" : "temp",
                        "node" : _build_temperature()
                    }
                ]
            }
        ]

    # submenu "set temperature"
    elif len(splitted_path) == 2 and splitted_path[1] == "hold":
        entries = [
            {
                "path" : splitted_path[0],
                "node" : [
                    {
                        "path" : "hold",
                        "node" : _build_vacation(
                            float(url_params["temp"][0]))
                    }
                ]
            }
        ]


    _menu = [
        { # root
        "path" : "",
        "node" : entries
        }
    ]




def browse(path, url_params):

    build_dir_structure(path, url_params)
    
    directory = _get_directory_by_path(path)
    for entry in directory["node"]:
        _add_list_item(entry, path)

    xbmcplugin.endOfDirectory(addon_handle)




def execute(path, params):

    splitted_path = path.split("/")
    if len(splitted_path) < 2:
        return


    xbmc.log(json.dumps(params, indent = 2), xbmc.LOGNOTICE)
    mac = splitted_path[1]

    xbmc.executebuiltin("Notification(%s, %s, %s/icon.png)"
                        % (params["msg"][0], "Be patient ...", addon_dir))

    output = _exec_gatttool(mac, params["send"])
    status = _parse_status(output)

    xbmc.executebuiltin("Notification(%s, %s, %s/icon.png)"
                        % (params["msg"][0], "successful", addon_dir))

    xbmc.executebuiltin('Container.Update("plugin://%s/%s?status=%s","update")' 
                        % (__PLUGIN_ID__, mac, json.dumps(status)))




if __name__ == '__main__':

    if sys.argv[1] == "discover":
        discover()
    else:    
        addon_handle = int(sys.argv[1])
        path = urlparse.urlparse(sys.argv[0]).path
        url_params = urlparse.parse_qs(sys.argv[2][1:])

        if "send" in url_params:
            execute(path, url_params)
        else:
            browse(path, url_params)
