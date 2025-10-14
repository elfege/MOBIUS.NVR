elfege@laptopwsl:~$ gflex1


BusyBox v1.34.1 (2022-11-08 01:19:20 UTC) built-in shell (ash)


********************************* NOTICE **********************************
* By logging in to, accessing, or using any Ubiquiti product, you are     *
* signifying that you have read our Terms of Service (ToS) and End User   *
* License Agreement (EULA), understand their terms, and agree to be       *
* fully bound to them. The use of CLI (Command Line Interface) can        *
* potentially harm Ubiquiti devices and result in lost access to them and *
* their data. By proceeding, you acknowledge that the use of CLI to       *
* modify device(s) outside of their normal operational scope, or in any   *
* manner inconsistent with the ToS or EULA, will permanently and          *
* irrevocably void any applicable warranty.                               *
***************************************************************************

  ___ ___      .__________.__
 |   |   |____ |__\_  ____/__|
 |   |   /    \|  ||  __) |  |   (c) 2010-2022
 |   |  |   |  \  ||  \   |  |   Ubiquiti Networks, Inc.
 |______|___|  /__||__/   |__|
            |_/                  https://www.ui.com/

      Welcome to UVC G5 Flex! (v4.59.32.67.M_0f30ce3.221108.0139)

UVC G5 Flex-4.59.32# ls -l
drwxr-xr-x    2 ubnt     admin           60 Aug  5 11:50 MY_NVR
-rw-------    1 ubnt     admin          141 Dec 31  1999 dropbear_ecdsa_host_key
-rw-r--r--    1 ubnt     admin         1009 Dec 31  1999 server.pem
drwxr-xr-x    2 ubnt     admin           40 Dec 31  1999 support
-rw-r--r--    1 ubnt     admin           79 Dec 31  1999 ubnt_avclient.conf
-rw-r--r--    1 ubnt     admin         1496 Aug  5 11:24 ubnt_encoder.conf
-rw-r--r--    1 ubnt     admin         2399 Dec 31  1999 ubnt_isp.conf
-rw-r--r--    1 ubnt     admin          230 Dec 31  1999 ubnt_networkd.conf
-rw-r--r--    1 ubnt     admin          179 Aug  3 23:48 ubnt_nvr.conf
-rw-r--r--    1 ubnt     admin         1166 Aug  3 23:11 ubnt_osd.conf
-rw-r--r--    1 ubnt     admin         1046 Aug  5 11:24 ubnt_smart_detect.conf
-rw-r--r--    1 ubnt     admin           69 Dec 31  1999 ubnt_sounds_leds.conf
UVC G5 Flex-4.59.32#


# Check if there are any PTZ-related configs
find /etc -name "*ptz*" -o -name "*motor*" -o -name "*stepper*" 2>/dev/null

# Look at the ISP config (might contain motor settings)
cat ubnt_isp.conf

# Check if there are any PTZ references in existing configs
grep -i "ptz\|motor\|stepper\|pan\|tilt" *.conf

# Look for device tree or hardware config files
find /boot -name "*.dtb" -o -name "*.dts" 2>/dev/null
find /proc/device-tree -name "*motor*" -o -name "*ptz*" 2>/dev/null

# Check for any motor-related kernel modules
lsmod | grep -i motor
cat /proc/modules | grep -i motor

# Look in system config directories
ls -la /etc/
ls -la /usr/share/unifi-protect/

UVC G5 Flex-4.59.32# cat ubnt_isp.conf
{
  "ae": {
    "autoFreq": 60,
    "blc": 0,
    "daclevel": 0,
    "dss": {
      "min": 4,
      "mode": "off"
    },
    "gain": {
      "max": 100,
      "min": 0
    },
    "manual": {
      "gain": 0,
      "iris": 0,
      "shutter": 30
    },
    "mode": "auto",
    "shutter": {
      "max": 10000,
      "min": 30
    },
    "target": 50
  },
  "awb": {
    "awbAlgoMethod": "advanced",
    "bgain": 1024,
    "colortemp": 6000,
    "mode": "auto",
    "rgain": 1024
  },
  "cfgver": 1,
  "image": {
    "aggressiveAntiFlicker": 0,
    "autoFlipMirror": 1,
    "brightness": 50,
    "contrast": 50,
    "criticalTmpOfProtect": 40,
    "dZoomCenterX": 50,
    "dZoomCenterY": 50,
    "dZoomScale": 0,
    "dZoomStreamId": 4,
    "denoise": 50,
    "dzoom": false,
    "enable3dnr": 1,
    "enableExternalIr": 0,
    "enableMicroTmpProtect": 1,
    "enablePauseMotion": 0,
    "flip": 1,
    "forceFilterIrSwitchEvents": 0,
    "hdr": 2,
    "hue": 50,
    "irOnStsBrightness": 0,
    "irOnStsContrast": 0,
    "irOnStsDenoise": 0,
    "irOnStsHue": 0,
    "irOnStsSaturation": 0,
    "irOnStsSharpness": 0,
    "irOnStsWdr": 0,
    "irOnValBrightness": 50,
    "irOnValContrast": 50,
    "irOnValDenoise": 50,
    "irOnValHue": 50,
    "irOnValSaturation": 50,
    "irOnValSharpness": 50,
    "irOnValWdr": 1,
    "lensDistortionCorrection": 1,
    "masks": {},
    "mirror": 1,
    "mountPosition": "wall",
    "queryIrLedStatus": 0,
    "saturation": 50,
    "sharpness": 50,
    "wdr": 1,
    "zonesAutoFlipMirror": 1
  },
  "ir": {
    "filter": {
      "cnt2day": 5,
      "icrLightSensorNightThd": 0,
      "mode": "auto",
      "sensitivity": 0,
      "thd2day": 750,
      "thd2night": 200
    },
    "led": {
      "level": 255,
      "mode": "auto"
    }
  },
  "misc": {
    "mode": "auto",
    "powerled": {
      "interval": 245,
      "mode": "auto"
    },
    "vport0in": 0,
    "vport0out": false,
    "vport1in": 0,
    "vport1out": false
  },
  "motor": {
    "focus": {
      "afNearLimit": 800,
      "afRange": 0,
      "mode": "ztrig",
      "objDistance": 0,
      "position": 0,
      "speed": 5,
      "touchX": 1001,
      "touchY": 1001
    },
    "pan": {
      "position": 0,
      "speed": 5
    },
    "tilt": {
      "position": 0,
      "speed": 5
    },
    "zoom": {
      "position": 0,
      "speed": 5
    }
  },
  "pir": {
    "sensitivity": 2
  }
}
UVC G5 Flex-4.59.32# grep -i "ptz\|motor\|stepper\|pan\|tilt" *.conf
ubnt_isp.conf:  "motor": {
ubnt_isp.conf:    "pan": {
ubnt_isp.conf:    "tilt": {
ubnt_smart_detect.conf:                "motorcycle"
UVC G5 Flex-4.59.32# # Look for device tree or hardware config files
UVC G5 Flex-4.59.32# find /boot -name "*.dtb" -o -name "*.dts" 2>/dev/null
UVC G5 Flex-4.59.32# find /proc/device-tree -name "*motor*" -o -name "*ptz*" 2>/dev/null
UVC G5 Flex-4.59.32# # Check for any motor-related kernel modules
UVC G5 Flex-4.59.32# lsmod | grep -i motor
UVC G5 Flex-4.59.32# cat /proc/modules | grep -i motor
UVC G5 Flex-4.59.32# # Look in system config directories
UVC G5 Flex-4.59.32# ls -la /etc/
drwxr-xr-x   15 ubnt     admin         1060 Aug  5 11:24 .
drwxrwxrwt    8 ubnt     admin          160 Jan  1  1970 ..
-rw-------    1 ubnt     admin           23 Dec 31  1999 TZ
lrwxrwxrwx    1 ubnt     admin           20 Dec 31  1999 asound.conf -> /usr/etc/asound.conf
lrwxrwxrwx    1 ubnt     admin           21 Dec 31  1999 asound.state -> /usr/etc/asound.state
-rw-r--r--    1 ubnt     admin         2525 Dec 31  1999 audio_tuning_fcd.json
lrwxrwxrwx    1 ubnt     admin           53 Dec 31  1999 audio_tuning_nature.json -> /usr/etc/audio_tuning/0xa593/audio_tuning_nature.json
lrwxrwxrwx    1 ubnt     admin           60 Dec 31  1999 audio_tuning_noise-reduced.json -> /usr/etc/audio_tuning/0xa593/audio_tuning_noise-reduced.json
-rw-r--r--    1 ubnt     admin          130 Dec 31  1999 avclient_state.json
lrwxrwxrwx    1 ubnt     admin           15 Dec 31  1999 banner -> /usr/etc/banner
-rw-------    1 ubnt     admin         1017 Dec 31  1999 board.info
lrwxrwxrwx    1 ubnt     admin           19 Dec 31  1999 default.cfg -> /usr/etc/system.cfg
drwx------    2 ubnt     admin           60 Dec 31  1999 dropbear
lrwxrwxrwx    1 ubnt     admin           16 Dec 31  1999 dynamic -> /usr/etc/dynamic
drwxr-xr-x    2 ubnt     admin           60 Dec 31  1999 ems
lrwxrwxrwx    1 ubnt     admin           19 Dec 31  1999 ethertypes -> /usr/etc/ethertypes
-rw-r--r--    1 ubnt     admin          304 Dec 31  1999 features.conf
-rw-r--r--    1 ubnt     admin            0 Dec 31  1999 fstab
lrwxrwxrwx    1 ubnt     admin           22 Dec 31  1999 fw_env.config -> /usr/etc/fw_env.config
-rw-r--r--    1 ubnt     admin          150 Dec 31  1999 group
-rw-r--r--    1 ubnt     admin           26 Dec 31  1999 host.conf
-rw-r--r--    1 ubnt     admin           42 Dec 31  1999 hosts
drwxr-xr-x    2 ubnt     admin           80 Dec 31  1999 httpd
-rw-------    1 ubnt     admin           18 Dec 31  1999 hwaddr
lrwxrwxrwx    1 ubnt     admin           13 Dec 31  1999 idsp -> /usr/etc/idsp
drwxr-xr-x    2 ubnt     admin           40 Dec 31  1999 init.d
-rw-------    1 ubnt     admin         1500 Dec 31  1999 inittab
drwxr-xr-x    2 ubnt     admin          180 Aug  5 12:14 ispserver
-rw-r--r--    1 ubnt     admin            6 Aug  5 11:24 last_timesync_monotonic_ts
-rw-------    1 ubnt     admin          786 Dec 31  1999 lighttpd.conf
lrwxrwxrwx    1 ubnt     admin           36 Dec 31  1999 localtime -> /usr/share/zoneinfo/America/New_York
-rw-r--r--    1 ubnt     admin           12 Dec 31  1999 login.defs
lrwxrwxrwx    1 ubnt     admin           19 Dec 31  1999 mime.types -> /usr/etc/mime.types
drwxr-xr-x    2 ubnt     admin          640 Dec 31  1999 modules.d
-rw-r--r--    1 ubnt     admin           82 Dec 31  1999 passwd
drwxr-xr-x    4 ubnt     admin          280 Aug  5 11:37 persistent
-rwxr-xr-x    1 ubnt     admin         2527 Dec 31  1999 profile
lrwxrwxrwx    1 ubnt     admin           18 Dec 31  1999 protocols -> /usr/etc/protocols
drwxr-xr-x    2 ubnt     admin          100 Dec 31  1999 rc.d
-rw-r--r--    1 ubnt     admin           48 Dec 31  1999 resolv.conf
-rw-r--r--    1 ubnt     admin         1009 Dec 31  1999 server.pem
lrwxrwxrwx    1 ubnt     admin           17 Dec 31  1999 services -> /usr/etc/services
lrwxrwxrwx    1 ubnt     admin           15 Dec 31  1999 shells -> /usr/etc/shells
drwxr-xr-x    2 ubnt     admin          120 Dec 31  1999 sounds
drwxr-xr-x    2 ubnt     admin           40 Dec 31  1999 ssh
drwxr-xr-x    2 ubnt     admin           60 Dec 31  1999 ssl
-rw-r--r--    1 ubnt     admin           52 Dec 31  1999 startup.list
lrwxrwxrwx    1 ubnt     admin           18 Dec 31  1999 sysconfig -> /usr/etc/sysconfig
drwxr-xr-x    2 ubnt     admin          160 Dec 31  1999 sysinit
-rw-r--r--    1 ubnt     admin         2253 Dec 31  1999 ubnt_isp.conf
-rw-r--r--    1 ubnt     admin           69 Dec 31  1999 ubnt_sounds_leds.conf
drwxr-xr-x    3 ubnt     admin          100 Dec 31  1999 udev
-rw-------    1 ubnt     admin           16 Dec 31  1999 version
UVC G5 Flex-4.59.32# ls -la /usr/share/unifi-protect/


UVC G5 Flex-4.59.32# # Let's see what happens if we change the pan position
UVC G5 Flex-4.59.32# # Edit the config to change pan position from 0 to 30
UVC G5 Flex-4.59.32# sed 's/"position": 0/"position": 30/' ubnt_isp.conf > ubnt_isp_test.conf
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Check the difference
UVC G5 Flex-4.59.32# diff ubnt_isp.conf ubnt_isp_test.conf
--- ubnt_isp.conf
+++ ubnt_isp_test.conf
@@ -106,21 +106,21 @@
       "afRange": 0,
       "mode": "ztrig",
       "objDistance": 0,
-      "position": 0,
+      "position": 30,
       "speed": 5,
       "touchX": 1001,
       "touchY": 1001
     },
     "pan": {
-      "position": 0,
+      "position": 30,
       "speed": 5
     },
     "tilt": {
-      "position": 0,
+      "position": 30,
       "speed": 5
     },
     "zoom": {
-      "position": 0,
+      "position": 30,
       "speed": 5
     }
   },
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Check what process uses this config file
UVC G5 Flex-4.59.32# ps aux | grep isp
ps: invalid option -- 'a'
BusyBox v1.34.1 (2022-11-08 01:19:20 UTC) multi-call binary.
Usage: ps
Show list of processes
        w       Wide output
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Look for the ISP server process
UVC G5 Flex-4.59.32# ls -la /etc/ispserver/
lrwxrwxrwx    1 ubnt     admin           20 Dec 31  1999 * -> /usr/etc/ispserver/*
drwxr-xr-x    2 ubnt     admin          180 Aug  5 12:16 .
drwxr-xr-x   15 ubnt     admin         1060 Aug  5 11:24 ..
lrwxrwxrwx    1 ubnt     admin           28 Dec 31  1999 iq.bin -> /config/iqfile/0xa593/iq.bin
lrwxrwxrwx    1 ubnt     admin           32 Dec 31  1999 iq_hdr.bin -> /config/iqfile/0xa593/iq_hdr.bin
lrwxrwxrwx    1 ubnt     admin           38 Dec 31  1999 iq_hdr_night.bin -> /config/iqfile/0xa593/iq_hdr_night.bin
lrwxrwxrwx    1 ubnt     admin           34 Dec 31  1999 iq_night.bin -> /config/iqfile/0xa593/iq_night.bin
-rw-------    1 ubnt     admin           78 Dec 31  1999 ispserver.conf
-rw-r--r--    1 ubnt     admin           88 Aug  5 12:16 lensbias.conf
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Check if there's a service that reloads this config
UVC G5 Flex-4.59.32# systemctl status ubnt-ispserver || service ubnt-ispserver status
-sh: systemctl: not found
-sh: service: not found
UVC G5 Flex-4.59.32#


UVC G5 Flex-4.59.32# # Check what processes are running (BusyBox ps)
UVC G5 Flex-4.59.32# ps w
if there are any ubnt processes
ps w | grep ubnt

# Look at the   PID USER       VSZ STAT COMMAND
ISP server config
cat /etc/ispserver/ispserver.conf

# Check what's in the startup scripts
ls -la /etc/init.d/
cat /etc/init.d/*    1 ubnt      2204 S    /sbin/init
 | grep -i isp

# Let's try to a    2 ubnt         0 SW   [kthreadd]
    3 ubnt         0 SW   [ksoftirqd/0]
    4 ubnt         0 SW   [kworker/0:0]
    5 ubnt         0 SW<  [kworker/0:0H]
    7 ubnt         0 RW   [rcu_preempt]
    8 ubnt         0 SW   [rcu_sched]
pply the config with the modifie    9 ubnt         0 SW   [rcu_bh]
d pan position
cp ubnt_isp_test.conf ubnt_isp.conf

# Look for a   10 ubnt         0 SW   [migration/0]
ny reload or restart commands
ls   11 ubnt         0 SW<  [lru-add-drain]
 -la /usr/bin/ |   12 ubnt         0 SW   [watchdog/0]
   13 ubnt         0 SW   [cpuhp/0]
 grep -i isp
ls -la /usr/sbin/ |   14 ubnt         0 SW   [cpuhp/1]
 grep -i isp

#    15 ubnt         0 SW   [watchdog/1]
   16 ubnt         0 SW   [migration/1]
Check if there's   17 ubnt         0 SW   [ksoftirqd/1]
 a way to signal   19 ubnt         0 SW<  [kworker/1:0H]
   20 ubnt         0 SW   [kdevtmpfs]
 the ISP server
killall -HUP ubn   21 ubnt         0 SW<  [netns]
t_ispserver 2>/dev/null || echo "No ubnt_ispserver process"

# Try to restart an  187 ubnt         0 SW   [oom_reaper]
y camera-related services
ls -la  188 ubnt         0 SW<  [writeback]
 /etc/rc.d/  190 ubnt         0 SW   [kcompactd0]
  191 ubnt         0 SW<  [crypto]
  192 ubnt         0 SW<  [bioset]
  194 ubnt         0 SW<  [kblockd]
  228 ubnt         0 SW<  [cfg80211]
  229 ubnt         0 SW<  [watchdogd]
  318 ubnt         0 SW   [kswapd0]
  319 ubnt         0 SW<  [vmstat]
  404 ubnt         0 SW   [kapmd]
  462 ubnt         0 SW   [hwrng]
  464 ubnt         0 SW   [monitor_temp]
  471 ubnt         0 SW<  [bioset]
  476 ubnt         0 SW<  [bioset]
  481 ubnt         0 SW<  [bioset]
  486 ubnt         0 SW<  [bioset]
  491 ubnt         0 SW<  [bioset]
  496 ubnt         0 SW<  [bioset]
  501 ubnt         0 SW<  [bioset]
  506 ubnt         0 SW<  [bioset]
  509 ubnt         0 SW   [kworker/1:1]
  512 ubnt         0 SW<  [bioset]
  517 ubnt         0 SW<  [bioset]
  522 ubnt         0 SW<  [bioset]
  526 ubnt         0 SW   [spi0]
  531 ubnt         0 SW   [spi1]
  567 ubnt         0 SW   [ubi_bgt0d]
  568 ubnt         0 SW   [kworker/0:2]
  599 ubnt         0 SW   [ubi_bgt1d]
  603 ubnt         0 SW   [ubifs_bgt1_0]
  662 ubnt      3228 S    /bin/syslogd -S -s 0 -C1024
  758 ubnt      2204 S    /bin/udevd --daemon --children-max=1
  886 ubnt         0 SW<  [cryptodev_queue]
  910 ubnt      2072 S<   /bin/watchdog -t 1 /dev/watchdog
  972 ubnt         0 SW<  [cifsiod]
  973 ubnt         0 SW<  [cifsoplockd]
  977 ubnt         0 SW<  [rpciod]
  978 ubnt         0 SW<  [xprtiod]
  981 ubnt         0 SW<  [nfsiod]
  996 ubnt         0 SW   [kworker/1:2]
  998 ubnt         0 DW   [ehci_monitor]
 1005 ubnt         0 SW   [SensorIfThreadW]
 1006 ubnt         0 SW   [IspDriverThread]
 1007 ubnt         0 SW   [IspMidThreadWq]
 1088 ubnt      2204 S    /sbin/init
 1090 ubnt      2204 S    /bin/syslogd -S -s 0 -C1024 -n
 1092 ubnt      2204 S    /bin/klogd -n
 1093 ubnt      5696 S    /bin/lighttpd -D -f /etc/lighttpd.conf
 1095 ubnt      1988 S    /bin/dropbear -F -r /etc/persistent/dropbear_ecdsa_host_key -p 22
 1098 ubnt     39072 S    /bin/ubnt_audio_agent_sstar -p=50
 1105 ubnt     17936 D    /bin/ubnt_ctlserver
 1107 ubnt     56836 S    /bin/ubnt_streamer
 1108 ubnt     10604 S    /bin/ubnt_networkd
 1110 ubnt     10060 S    /bin/ubnt_nvr
 1111 ubnt     11160 S    /bin/ubnt_sounds_leds
 1112 ubnt     11216 S    /bin/ubnt_talkback -a
 1116 ubnt     16704 S    /bin/ubnt_audio_events
 1117 ubnt      2324 S    /bin/crond -l5 -f
 1118 ubnt      2204 S    /bin/sh /bin/watchdog.sh
 1119 ubnt     12076 S    /bin/ubnt_reportd
 1125 ubnt         0 DW   [mi_wq]
 1134 ubnt         0 DWN  [mi_log]
 1141 ubnt         0 DW   [ai1_P0_MAIN]
 1149 ubnt     25452 S    /bin/ubnt_smart_detect
 1150 ubnt      8812 S    /bin/ubnt_pmask_sstar
 1151 ubnt     20196 S    /bin/ubnt_ispserver -d=0
 1152 ubnt     16260 S    /bin/ubnt_osd
 1153 ubnt      2204 S    /bin/udhcpc --retries 9 -f -x hostname:UVC G5 Flex -i eth0 -S -s /bin/udhcpc_cb.sh -v
 1160 ubnt         0 DW   [vpe0_P0_MAIN]
 1161 ubnt         0 DW   [vpe0_P1_MAIN]
 1162 ubnt         0 DW   [vpe0_P2_MAIN]
 1163 ubnt         0 DWN  [VEP_DumpTaskThr]
 1170 ubnt         0 DW   [vif0_P0_MAIN]
 1171 ubnt         0 DW   [vif1_P0_MAIN]
 1172 ubnt         0 DW   [vif2_P0_MAIN]
 1176 ubnt         0 DW   [divp0_P0_MAIN]
 1179 ubnt         0 DW   [venc0_P0_MAIN]
 1180 ubnt         0 DW   [venc1_P0_MAIN]
 1183 ubnt         0 SW   [RGN BUF WQ]
 1188 ubnt         0 DW   [ipu0_P0_MAIN]
 1202 ubnt      5700 S    /bin/infctld
 1204 ubnt     11008 S    /bin/ubnt_avclient
 1236 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetStatus"}
 1237 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetPosition"}
 1238 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetCurrentPosition"}
 1244 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Home"}
 1245 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Center"}
 1246 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Recenter"}
 1252 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo"}
 1253 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Move"}
 1254 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"AbsolutePosition"}
 1260 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"RelativePosition"}
 1261 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Stop"}
 1262 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetCapabilities"}
 1268 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetInfo"}
 1269 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Initialize"}
 1303 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetStatus"}
 1304 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"GetStatus"}
 1305 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"GetStatus"}
 1306 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Home"}
 1312 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Center"}
 1313 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"Recenter"}
 1314 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","pan":10,"tilt":0}
 1378 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1384 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 15, "tilt": 0}
 1385 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": -30, "tilt": 0}
 1386 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 0, "tilt": 10}
 1387 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1388 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "AbsolutePosition", "panPos": 45, "tiltPos": 0}
 1389 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Stop"}
 1390 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1396 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 0, "tilt": 10}
 1402 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 0, "tilt": 10}
 1646 ubnt         0 SW   [kworker/u4:0]
 1783 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1784 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 15, "tilt": 0}
 1785 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": -30, "tilt": 0}
 1786 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 0, "tilt": 10}
 1792 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1793 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "AbsolutePosition", "panPos": 45, "tiltPos": 0}
 1794 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Stop"}
 1795 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1837 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Initialize"}
 1838 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Enable"}
 1839 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Activate"}
 1840 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Start"}
 1841 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"Initialize"}
 1842 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"EnablePTZ"}
 1843 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"SetMode","mode":"ptz"}
 1844 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Calibrate"}
 1845 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Reset"}
 1846 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"FindHome"}
 1852 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","angle":30,"axis":"pan"}
 1853 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","degrees":30,"direction":"right"}
 1854 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","x":30,"y":0}
 1855 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"PanTo","position":30}
 1856 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"SetPosition","pan":30,"tilt":0}
 1857 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GoTo","pan":30,"tilt":0}
 1858 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","pan":30,"tilt":0,"speed":50}
 1859 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","pan":30,"tilt":0,"panSpeed":23,"tiltSpeed":23}
 1860 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Move","motor":"pan","steps":100}
 1861 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Step","direction":"right","steps":50}
 1901 ubnt         0 SW   [kworker/u4:1]
 1925 ubnt      2108 R    /bin/dropbear -F -r /etc/persistent/dropbear_ecdsa_host_key -p 22
 1926 ubnt      2332 S    -sh
 2024 ubnt      2072 S    timeout 5 logger ruok
 2026 ubnt      2072 S    sleep 30
 2027 ubnt      2324 R    ps w
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Look for ISP-related processes
UVC G5 Flex-4.59.32# ps w | grep isp
 1151 ubnt     20196 S    /bin/ubnt_ispserver -d=0
 2030 ubnt      2204 S    grep isp
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Check if there are any ubnt processes
UVC G5 Flex-4.59.32# ps w | grep ubnt
    1 ubnt      2204 S    /sbin/init
    2 ubnt         0 SW   [kthreadd]
    3 ubnt         0 SW   [ksoftirqd/0]
    4 ubnt         0 SW   [kworker/0:0]
    5 ubnt         0 SW<  [kworker/0:0H]
    7 ubnt         0 SW   [rcu_preempt]
    8 ubnt         0 SW   [rcu_sched]
    9 ubnt         0 SW   [rcu_bh]
   10 ubnt         0 SW   [migration/0]
   11 ubnt         0 SW<  [lru-add-drain]
   12 ubnt         0 SW   [watchdog/0]
   13 ubnt         0 SW   [cpuhp/0]
   14 ubnt         0 SW   [cpuhp/1]
   15 ubnt         0 SW   [watchdog/1]
   16 ubnt         0 SW   [migration/1]
   17 ubnt         0 SW   [ksoftirqd/1]
   19 ubnt         0 SW<  [kworker/1:0H]
   20 ubnt         0 SW   [kdevtmpfs]
   21 ubnt         0 SW<  [netns]
  187 ubnt         0 SW   [oom_reaper]
  188 ubnt         0 SW<  [writeback]
  190 ubnt         0 SW   [kcompactd0]
  191 ubnt         0 SW<  [crypto]
  192 ubnt         0 SW<  [bioset]
  194 ubnt         0 SW<  [kblockd]
  228 ubnt         0 SW<  [cfg80211]
  229 ubnt         0 SW<  [watchdogd]
  318 ubnt         0 SW   [kswapd0]
  319 ubnt         0 SW<  [vmstat]
  404 ubnt         0 SW   [kapmd]
  462 ubnt         0 SW   [hwrng]
  464 ubnt         0 SW   [monitor_temp]
  471 ubnt         0 SW<  [bioset]
  476 ubnt         0 SW<  [bioset]
  481 ubnt         0 SW<  [bioset]
  486 ubnt         0 SW<  [bioset]
  491 ubnt         0 SW<  [bioset]
  496 ubnt         0 SW<  [bioset]
  501 ubnt         0 SW<  [bioset]
  506 ubnt         0 SW<  [bioset]
  509 ubnt         0 SW   [kworker/1:1]
  512 ubnt         0 SW<  [bioset]
  517 ubnt         0 SW<  [bioset]
  522 ubnt         0 SW<  [bioset]
  526 ubnt         0 SW   [spi0]
  531 ubnt         0 SW   [spi1]
  567 ubnt         0 SW   [ubi_bgt0d]
  568 ubnt         0 SW   [kworker/0:2]
  599 ubnt         0 SW   [ubi_bgt1d]
  603 ubnt         0 SW   [ubifs_bgt1_0]
  662 ubnt      3228 S    /bin/syslogd -S -s 0 -C1024
  758 ubnt      2204 S    /bin/udevd --daemon --children-max=1
  886 ubnt         0 SW<  [cryptodev_queue]
  910 ubnt      2072 S<   /bin/watchdog -t 1 /dev/watchdog
  972 ubnt         0 SW<  [cifsiod]
  973 ubnt         0 SW<  [cifsoplockd]
  977 ubnt         0 SW<  [rpciod]
  978 ubnt         0 SW<  [xprtiod]
  981 ubnt         0 SW<  [nfsiod]
  996 ubnt         0 SW   [kworker/1:2]
  998 ubnt         0 DW   [ehci_monitor]
 1005 ubnt         0 SW   [SensorIfThreadW]
 1006 ubnt         0 SW   [IspDriverThread]
 1007 ubnt         0 SW   [IspMidThreadWq]
 1088 ubnt      2204 S    /sbin/init
 1090 ubnt      2204 S    /bin/syslogd -S -s 0 -C1024 -n
 1092 ubnt      2204 S    /bin/klogd -n
 1093 ubnt      5696 S    /bin/lighttpd -D -f /etc/lighttpd.conf
 1095 ubnt      1988 S    /bin/dropbear -F -r /etc/persistent/dropbear_ecdsa_host_key -p 22
 1098 ubnt     39072 S    /bin/ubnt_audio_agent_sstar -p=50
 1105 ubnt     17936 S    /bin/ubnt_ctlserver
 1107 ubnt     56836 S    /bin/ubnt_streamer
 1108 ubnt     10604 S    /bin/ubnt_networkd
 1110 ubnt     10060 S    /bin/ubnt_nvr
 1111 ubnt     11160 S    /bin/ubnt_sounds_leds
 1112 ubnt     11216 S    /bin/ubnt_talkback -a
 1116 ubnt     16704 S    /bin/ubnt_audio_events
 1117 ubnt      2324 S    /bin/crond -l5 -f
 1118 ubnt      2204 S    /bin/sh /bin/watchdog.sh
 1119 ubnt     12076 S    /bin/ubnt_reportd
 1125 ubnt         0 DW   [mi_wq]
 1134 ubnt         0 DWN  [mi_log]
 1141 ubnt         0 DW   [ai1_P0_MAIN]
 1149 ubnt     25452 S    /bin/ubnt_smart_detect
 1150 ubnt      8812 S    /bin/ubnt_pmask_sstar
 1151 ubnt     20196 S    /bin/ubnt_ispserver -d=0
 1152 ubnt     16260 S    /bin/ubnt_osd
 1153 ubnt      2204 S    /bin/udhcpc --retries 9 -f -x hostname:UVC G5 Flex -i eth0 -S -s /bin/udhcpc_cb.sh -v
 1160 ubnt         0 DW   [vpe0_P0_MAIN]
 1161 ubnt         0 DW   [vpe0_P1_MAIN]
 1162 ubnt         0 DW   [vpe0_P2_MAIN]
 1163 ubnt         0 DWN  [VEP_DumpTaskThr]
 1170 ubnt         0 DW   [vif0_P0_MAIN]
 1171 ubnt         0 DW   [vif1_P0_MAIN]
 1172 ubnt         0 DW   [vif2_P0_MAIN]
 1176 ubnt         0 DW   [divp0_P0_MAIN]
 1179 ubnt         0 DW   [venc0_P0_MAIN]
 1180 ubnt         0 DW   [venc1_P0_MAIN]
 1183 ubnt         0 SW   [RGN BUF WQ]
 1188 ubnt         0 DW   [ipu0_P0_MAIN]
 1202 ubnt      5700 S    /bin/infctld
 1204 ubnt     11008 S    /bin/ubnt_avclient
 1236 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetStatus"}
 1237 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetPosition"}
 1238 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetCurrentPosition"}
 1244 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Home"}
 1245 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Center"}
 1246 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Recenter"}
 1252 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo"}
 1253 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Move"}
 1254 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"AbsolutePosition"}
 1260 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"RelativePosition"}
 1261 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Stop"}
 1262 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetCapabilities"}
 1268 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetInfo"}
 1269 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Initialize"}
 1303 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GetStatus"}
 1304 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"GetStatus"}
 1305 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"GetStatus"}
 1306 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Home"}
 1312 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Center"}
 1313 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"Recenter"}
 1314 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","pan":10,"tilt":0}
 1378 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1384 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 15, "tilt": 0}
 1385 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": -30, "tilt": 0}
 1386 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 0, "tilt": 10}
 1387 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1388 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "AbsolutePosition", "panPos": 45, "tiltPos": 0}
 1389 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Stop"}
 1390 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1396 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 0, "tilt": 10}
 1402 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 0, "tilt": 10}
 1646 ubnt         0 SW   [kworker/u4:0]
 1783 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1784 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 15, "tilt": 0}
 1785 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": -30, "tilt": 0}
 1786 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "MoveTo", "pan": 0, "tilt": 10}
 1792 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1793 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "AbsolutePosition", "panPos": 45, "tiltPos": 0}
 1794 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Stop"}
 1795 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName": "Home"}
 1837 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Initialize"}
 1838 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Enable"}
 1839 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Activate"}
 1840 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Start"}
 1841 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"Initialize"}
 1842 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"EnablePTZ"}
 1843 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_camera -m={"functionName":"SetMode","mode":"ptz"}
 1844 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Calibrate"}
 1845 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"Reset"}
 1846 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"FindHome"}
 1852 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","angle":30,"axis":"pan"}
 1853 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","degrees":30,"direction":"right"}
 1854 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","x":30,"y":0}
 1855 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"PanTo","position":30}
 1856 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"SetPosition","pan":30,"tilt":0}
 1857 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"GoTo","pan":30,"tilt":0}
 1858 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","pan":30,"tilt":0,"speed":50}
 1859 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_ptz -m={"functionName":"MoveTo","pan":30,"tilt":0,"panSpeed":23,"tiltSpeed":23}
 1860 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Move","motor":"pan","steps":100}
 1861 ubnt      8168 S    ubnt_ipc_cli -T=ubnt_motor -m={"functionName":"Step","direction":"right","steps":50}
 1901 ubnt         0 SW   [kworker/u4:1]
 1925 ubnt      2108 S    /bin/dropbear -F -r /etc/persistent/dropbear_ecdsa_host_key -p 22
 1926 ubnt      2332 S    -sh
 2024 ubnt      2072 S    timeout 5 logger ruok
 2026 ubnt      2072 S    sleep 30
 2028 ubnt         0 SW   [kworker/u4:2]
 2031 ubnt      2324 R    ps w
 2032 ubnt      2204 S    grep ubnt
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Look at the ISP server config
UVC G5 Flex-4.59.32# cat /etc/ispserver/ispserver.conf
system.model=UnknownSystem_42387
system.debug_stderr=0
system.debug_verbose=0
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Check what's in the startup scripts
UVC G5 Flex-4.59.32# ls -la /etc/init.d/
drwxr-xr-x    2 ubnt     admin           40 Dec 31  1999 .
drwxr-xr-x   15 ubnt     admin         1060 Aug  5 11:24 ..
UVC G5 Flex-4.59.32# cat /etc/init.d/* | grep -i isp
cat: can't open '/etc/init.d/*': No such file or directory
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Let's try to apply the config with the modified pan position
UVC G5 Flex-4.59.32# cp ubnt_isp_test.conf ubnt_isp.conf
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Look for any reload or restart commands
UVC G5 Flex-4.59.32# ls -la /usr/bin/ | grep -i isp
-rwxr-xr-x    1 ubnt     admin         9740 Nov  7  2022 ubnt_isp_fac_cmd
-rwxr-xr-x    1 ubnt     admin       113068 Nov  7  2022 ubnt_ispserver
UVC G5 Flex-4.59.32# ls -la /usr/sbin/ | grep -i isp
-rwxr-xr-x    1 ubnt     admin         9740 Nov  7  2022 ubnt_isp_fac_cmd
-rwxr-xr-x    1 ubnt     admin       113068 Nov  7  2022 ubnt_ispserver
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Check if there's a way to signal the ISP server
UVC G5 Flex-4.59.32# killall -HUP ubnt_ispserver 2>/dev/null || echo "No ubnt_ispserver process"
UVC G5 Flex-4.59.32#
UVC G5 Flex-4.59.32# # Try to restart any camera-related services
UVC G5 Flex-4.59.32# ls -la /etc/rc.d/
drwxr-xr-x    2 ubnt     admin          100 Dec 31  1999 .
drwxr-xr-x   15 ubnt     admin         1060 Aug  5 11:24 ..
-rwxr-xr-x    1 ubnt     admin          264 Dec 31  1999 rc
-rwxr-xr-x    1 ubnt     admin         2432 Dec 31  1999 rc.stop
-rwxr-xr-x    1 ubnt     admin        11617 Dec 31  1999 rc.sysinit
UVC G5 Flex-4.59.32#