"""`ydzd --sim` : une tour de 3 nœuds simulés (forge, vault, pulse) sur cette
machine. Les trois agents sont de vrais serveurs HTTP qui se parlent sur
127.0.0.1 ; seuls le matériel et les commandes système sont simulés.

  hub (interface + API)  http://127.0.0.1:<port>      (défaut 8080)
  agents                 forge :8787, vault :8788, pulse :8789
"""
import json
import math
import os
import re
import signal
import threading
import time

from . import config
from .node import Node
from .sysapi import FakeSystem, Res, System

GB = 10**9
PROFILES = {
    "forge": {"ip": "192.168.1.20", "ram": 16, "base_temp": 49.0, "load": 0.22, "port": 8787,
              "model": "Raspberry Pi 5 Model B Rev 1.1"},
    "vault": {"ip": "192.168.1.21", "ram": 8, "base_temp": 43.0, "load": 0.07, "port": 8788,
              "model": "Raspberry Pi 5 Model B Rev 1.0"},
    "pulse": {"ip": "192.168.1.22", "ram": 4, "base_temp": 40.0, "load": 0.04, "port": 8789,
              "model": "Raspberry Pi 5 Model B Rev 1.0"},
}


class SimBadge:
    def __init__(self):
        self.frames = 0
        self.last = None
        self._out = []

    def write(self, data):
        if data.strip() == b"?":
            self._out.append('{"ydz_badge":1,"fw":"1.0.0","board":"t-display-s3"}')
        else:
            self.frames += 1
            self.last = data

    def readline(self, timeout=1.0):
        return self._out.pop(0) if self._out else None

    def flush_input(self):
        self._out = []

    def close(self):
        pass


class SimSystem(FakeSystem):
    sim = True

    def __init__(self, name, real_root, scenario="normal"):
        p = PROFILES[name]
        FakeSystem.__init__(self, host=name, ip=p["ip"])
        self.p, self.real_root, self.scenario = p, real_root.rstrip("/") + "/", scenario
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.badge = SimBadge()
        self.containers = {}       # nom -> {image, state, project}
        self.disks = self._disks(name)
        self.pkgs = 7 if name != "pulse" else 0
        self.maj_until = 0
        f = self.files
        f["/proc/device-tree/model"] = p["model"] + "\x00"
        f["/proc/cpuinfo"] = "".join("processor\t: %d\n" % i for i in range(4))
        f["/sys/class/thermal/thermal_zone0/temp"] = self._temp
        f["/proc/stat"] = self._stat
        f["/proc/meminfo"] = self._meminfo
        f["/proc/loadavg"] = lambda: "%.2f %.2f %.2f 1/300 4242\n" % ((self._load() * 4,) * 3)
        f["/proc/uptime"] = lambda: "%.2f 0\n" % (time.time() - self.t0 + 93784)
        f["/proc/net/route"] = "Iface\tDestination\tGateway\neth0\t00000000\t0101A8C0\n"
        f["/proc/net/dev"] = self._netdev
        f["/sys/class/net/eth0/speed"] = "1000\n"
        f["/sys/class/net/eth0/address"] = "2c:cf:67:00:00:%02x\n" % (p["port"] % 256)
        f["/proc/mounts"] = self._mounts
        f["/proc/swaps"] = "Filename Type Size Used Priority\n/dev/zram0 partition 2097148 0 100\n"
        f["/etc/timezone"] = "Europe/Paris\n"
        f["/etc/fstab"] = "PARTUUID=aaaa-01 /boot/firmware vfat defaults 0 2\nPARTUUID=aaaa-02 / ext4 defaults,noatime 0 1\n"
        f["/boot/firmware/config.txt"] = "usb_max_current_enable=1\ndtparam=pciex1\n" + (
            "dtoverlay=pwm,pin=18,func=2\n" if name == "forge" else "")
        f["/proc/mdstat"] = "Personalities : [raid1]\nunused devices: <none>\n"
        f["/dev/ttyACM0"] = ""
        f["/var/log/ydz/install.log"] = "[ok] installation simulée\n"
        self.vfs = {"/": ((235 if name == "forge" else 29) * GB, (171 if name == "forge" else 21) * GB)}
        if name == "forge":
            f["/sys/class/pwm/pwmchip0/npwm"] = "4\n"
            f["@link:/sys/class/pwm/pwmchip0"] = "/sys/devices/platform/axi/1000120000.pcie/1f00098000.pwm/pwm/pwmchip0"
            f["/sys/class/pwm/pwmchip0/pwm2/period"] = "0"
            f["/sys/class/backlight/11-0045/max_brightness"] = "31\n"
            f["/sys/class/backlight/11-0045/brightness"] = "25\n"
            f["/sys/class/drm/card1-DSI-1/status"] = "connected\n"
            f["/sys/block/nvme0n1/size"] = "0"
        if name == "vault":
            f["/sys/bus/pci/devices/0000:01:00.0/class"] = "0x010601\n"
            f["/sys/block/sda/size"] = f["/sys/block/sdb/size"] = "0"
            if scenario == "normal":
                self.vfs["/srv/ydz/donnees"] = (983 * GB, 678 * GB)
        if scenario == "normal":
            seed = {"forge": ("minecraft", ["ydz-minecraft-minecraft-1"], "itzg/minecraft-server:latest", "running"),
                    "vault": ("jellyfin", ["ydz-jellyfin-jellyfin-1"], "jellyfin/jellyfin:latest", "exited"),
                    "pulse": ("pihole", ["ydz-pihole-pihole-1"], "pihole/pihole:latest", "running")}[name]
            for c in seed[1]:
                self.containers[c] = {"image": seed[2], "state": seed[3], "project": "ydz-" + seed[0]}
            st = {"apps": {seed[0]: {"reglages": {}, "donnees": "/var/lib/ydz/donnees/" + seed[0],
                                     "installe_le": int(self.t0) - 86400 * 12}}}
            if name == "vault":
                st["partages"] = [{"nom": "films", "disque": "donnees", "chemin": "/srv/ydz/donnees/partages/films",
                                   "acces": "invite_lecture", "adresse": "\\\\vault\\films",
                                   "adresse_mac": "smb://vault.local/films"}]
            f["/var/lib/ydz/etat.json"] = json.dumps(st)

    # --- temps réel, fichiers réels du catalogue -----------------------------
    now = System.now
    monotonic = System.monotonic
    sleep = System.sleep
    http = System.http

    def _real(self, path):
        return path.startswith(self.real_root)

    def read(self, path, default=None):
        if self._real(path):
            return System.read(self, path, default)
        with self.lock:
            v = self.files.get(path, default)
        return v() if callable(v) else v

    def write(self, path, data, mode=None):
        with self.lock:
            self.files[path] = data

    def append(self, path, data, mode=None):
        with self.lock:
            self.files[path] = (self.files.get(path) or "") + data

    def listdir(self, path):
        if self._real(path + "/"):
            return System.listdir(self, path)
        with self.lock:
            return FakeSystem.listdir(self, path)

    def exists(self, path):
        if self._real(path):
            return os.path.exists(path)
        with self.lock:
            return FakeSystem.exists(self, path)

    def write_sys(self, path, data):
        with self.lock:
            self.files[path] = str(data)
        return True

    def makedirs(self, path, mode=0o755):
        with self.lock:
            FakeSystem.makedirs(self, path, mode)

    def remove(self, path):
        with self.lock:
            return FakeSystem.remove(self, path)

    def glob(self, pattern):
        with self.lock:
            return FakeSystem.glob(self, pattern)

    def peer_url(self, name):
        return "http://127.0.0.1:%d" % PROFILES[name]["port"] if name in PROFILES else None

    def open_serial(self, path):
        return self.badge

    def spawn(self, argv):
        print("[sim] %s : « %s » (simulé, rien n'est exécuté)" % (self.host, " ".join(argv)), flush=True)
        self.spawned.append(list(argv))
        return True

    def udp_broadcast(self, addr, port, data):
        print("[sim] %s : paquet magique vers %s:%d (%d octets)" % (self.host, addr, port, len(data)), flush=True)
        return True

    # --- mesures vivantes ------------------------------------------------------
    def _load(self):
        t = time.time() - self.t0
        run = sum(1 for c in self.containers.values() if c["state"] == "running")
        v = self.p["load"] + 0.05 * run + 0.06 * math.sin(t / 23.0) + 0.03 * math.sin(t / 5.3)
        return max(0.01, min(0.97, v))

    def _temp(self):
        t = time.time() - self.t0
        return "%d\n" % int(1000 * (self.p["base_temp"] + 16 * self._load() + 1.5 * math.sin(t / 61.0)))

    def _stat(self):
        now = time.time()
        last = getattr(self, "_stat_t", now - 5)
        self._busy = getattr(self, "_busy", 0.0) + (now - last) * 400 * self._load()
        self._total = getattr(self, "_total", 0.0) + (now - last) * 400
        self._stat_t = now
        return "cpu  %d 0 0 %d 0 0 0 0 0 0\n" % (self._busy, self._total - self._busy)

    def _meminfo(self):
        total = self.p["ram"] * 1024 * 1024
        run = sum(1 for c in self.containers.values() if c["state"] == "running")
        used = int(total * 0.08) + run * 1400 * 1024 + int(40000 * math.sin(time.time() / 31.0))
        return "MemTotal: %d kB\nMemFree: %d kB\nMemAvailable: %d kB\n" % (total, total - used, total - used)

    def _netdev(self):
        t = time.time() - self.t0 + 500
        k = 1 + 4 * self._load()
        return ("Inter-|   Receive\n face |bytes\n    lo: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0\n"
                "  eth0: %d 10 0 0 0 0 0 0 %d 10 0 0 0 0 0 0\n" % (int(t * 180000 * k), int(t * 42000 * k)))

    def _mounts(self):
        root = "/dev/nvme0n1p2" if self.host == "forge" else "/dev/mmcblk0p2"
        out = ["%s / ext4 rw,noatime 0 0" % root]
        for c in self._parts():
            for m in c.get("mountpoints") or []:
                if m and m.startswith("/srv/"):
                    out.append("%s %s ext4 rw,noatime 0 0" % (c["path"], m))
        return "\n".join(out) + "\n"

    # --- disques -----------------------------------------------------------------
    def _disks(self, name):
        def sysdisk(dev, size, model, tran):
            p = dev + ("p" if dev[-1].isdigit() else "")
            return {"name": dev, "path": "/dev/" + dev, "type": "disk", "size": size, "model": model,
                    "serial": "SYS" + dev.upper(), "tran": tran, "rota": False, "rm": False, "fstype": None,
                    "children": [
                        {"name": p + "1", "path": "/dev/%s1" % p, "type": "part", "size": 536870912, "fstype": "vfat",
                         "label": "bootfs", "uuid": "AAAA-0001", "mountpoints": ["/boot/firmware"]},
                        {"name": p + "2", "path": "/dev/%s2" % p, "type": "part", "size": size - 536870912,
                         "fstype": "ext4", "label": "rootfs", "uuid": "aaaa-0002", "mountpoints": ["/"]}]}
        if name == "forge":
            return [sysdisk("nvme0n1", 256 * GB, "Samsung PM991a 256GB", "nvme")]
        d = [sysdisk("mmcblk0", 32 * GB, None, None)]
        if name == "vault":
            for i, dev in enumerate(("sda", "sdb")):
                d.append({"name": dev, "path": "/dev/" + dev, "type": "disk", "size": 1000204886016,
                          "model": "Samsung SSD 870 EVO 1TB", "serial": "S6PUNX0T%05dK" % (41207 + i),
                          "tran": "sata", "rota": False, "rm": False, "fstype": None, "children": []})
            if self.scenario == "normal":
                d[1]["children"] = [{"name": "sda1", "path": "/dev/sda1", "type": "part", "size": 1000203837440,
                                     "fstype": "ext4", "label": "ydz-donnees", "uuid": "5d1c0de0-aaaa-bbbb-cccc-000000000001",
                                     "mountpoints": ["/srv/ydz/donnees"]}]
        return d

    def _disk(self, path):
        return next((d for d in self.disks if d["path"] == path), None)

    def _parts(self):
        seen = []
        def walk(devs):
            for c in devs or []:
                if not any(c is x for x in seen):
                    seen.append(c)
                    walk(c.get("children"))
        for d in self.disks:
            walk(d.get("children"))
        return seen

    def _part(self, path):
        return next((c for c in self._parts() if c["path"] == path), None)

    # --- commandes -----------------------------------------------------------------
    def which(self, name):
        if name in ("smbd", "smbpasswd", "mdadm") and self.host != "vault":
            return None
        return "/usr/bin/" + name

    def run(self, argv, input=None, timeout=30, env=None):
        argv = list(argv)
        fn = getattr(self, "_cmd_" + argv[0].replace("-", "_").replace(".", "_"), None)
        if fn is None:
            return Res(0, "", "")
        r = fn(argv, input)
        return r if isinstance(r, Res) else Res(0, r or "", "")

    def _cmd_vcgencmd(self, argv, input):
        return "throttled=0x0\n"

    def _cmd_timedatectl(self, argv, input):
        return "yes\n"

    def _cmd_ping(self, argv, input):
        return Res(1 if argv[-1].endswith(".35") and int(time.time() / 40) % 2 else 0)

    def _cmd_rpi_eeprom_config(self, argv, input):
        return "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf416\nPSU_MAX_CURRENT=5000\n"

    def _cmd_systemctl(self, argv, input):
        if argv[1] == "is-active":
            if argv[2].startswith("ydz-maj"):
                return "active\n" if time.time() < self.maj_until else "inactive\n"
            return "active\n"
        if argv[1] == "is-system-running":
            return "running\n"
        m = re.match(r"^srv-ydz-([a-z0-9]+)\.mount$", argv[-1]) if len(argv) > 2 else None
        if m and argv[1] in ("start", "stop"):
            mp = "/srv/ydz/" + m.group(1)
            for c in self._parts():
                if argv[1] == "stop" and mp in (c.get("mountpoints") or []):
                    c["mountpoints"] = []
                    self.vfs.pop(mp, None)
                if argv[1] == "start" and c.get("label") == "ydz-" + m.group(1):
                    c["mountpoints"] = [mp]
                    self.vfs[mp] = (int(c["size"] * 0.983), int(c["size"] * 0.983))
        return ""

    def _cmd_lsblk(self, argv, input):
        return json.dumps({"blockdevices": self.disks})

    def _cmd_smartctl(self, argv, input):
        d = self._disk(argv[-1])
        if not d:
            return Res(1, "{}", "")
        return json.dumps({"smart_status": {"passed": True}, "temperature": {"current": 31 if "sd" in d["name"] else 44},
                           "power_on_time": {"hours": 4213},
                           "ata_smart_attributes": {"table": [{"id": 5, "raw": {"value": 0}}]}})

    def _cmd_wipefs(self, argv, input):
        d = self._disk(argv[-1])
        if d:
            d["children"], d["fstype"] = [], None
        return ""

    def _cmd_sfdisk(self, argv, input):
        d = self._disk(argv[-1])
        if not d:
            return Res(1, "", "disque absent")
        time.sleep(0.5)
        d["children"] = [{"name": d["name"] + "1", "path": d["path"] + "1", "type": "part", "size": d["size"] - 2097152,
                          "fstype": None, "label": None, "uuid": None, "mountpoints": []}]
        return ""

    def _cmd_mkfs_ext4(self, argv, input):
        c = self._part(argv[-1])
        if not c:
            return Res(1, "", "partition absente")
        time.sleep(2.0)
        c.update(fstype="ext4", label=argv[argv.index("-L") + 1], uuid="5d1c0de0-%04x-4000-8000-%012x"
                 % (len(self.calls) % 65536, int(time.time())))
        return ""

    def _cmd_blkid(self, argv, input):
        c = self._part(argv[-1])
        return (c["uuid"] + "\n") if c and c.get("uuid") else Res(2, "", "")

    def _cmd_mdadm(self, argv, input):
        if "--create" in argv:
            target = argv[argv.index("--create") + 1]
            parts = [a for a in argv if re.match(r"^/dev/sd[a-z]\d$", a)]
            size = min(self._part(p)["size"] for p in parts)
            md = {"name": "md127", "path": target, "type": "raid1", "size": size, "fstype": None, "label": None,
                  "uuid": None, "mountpoints": []}
            for p in parts:
                c = self._part(p)
                c["fstype"] = "linux_raid_member"
                c["children"] = [md]
            self.files["/proc/mdstat"] = ("Personalities : [raid1]\nmd127 : active raid1 sdb1[1] sda1[0]\n"
                                          "      976630464 blocks super 1.2 [2/2] [UU]\n"
                                          "      [=>...................]  resync =  6.4% (62504960/976630464)\n")
            self._md = md
        elif "--detail" in argv:
            return "ARRAY %s metadata=1.2 UUID=11111111:22222222:33333333:44444444\n" % argv[-1]
        return ""

    def _cmd_apt_get(self, argv, input):
        time.sleep(1.5)
        return "Atteint :1 http://deb.debian.org/debian trixie InRelease\nLecture des listes de paquets…\n"

    def _cmd_apt(self, argv, input):
        names = ["libssl3t64", "openssl", "raspi-firmware", "linux-image-rpi-2712", "curl", "libcurl4t64", "bash"]
        return "Listing...\n" + "".join("%s/stable 1.%d-1 arm64 [upgradable from: 1.%d-0]\n" % (n, i, i)
                                        for i, n in enumerate(names[:self.pkgs]))

    def _cmd_systemd_run(self, argv, input):
        self.maj_until = time.time() + 20
        self.pkgs = 0
        self.files["/var/log/ydz/maj.log"] = "Mise à jour simulée : 7 paquets installés.\n"
        return ""

    def _cmd_docker(self, argv, input):
        if argv[1:3] == ["compose", "version"]:
            return "2.26.1\n"
        if argv[1] == "info":
            return "26.1.5\n"
        if argv[1] == "ps":
            return "".join(json.dumps({
                "Names": n, "Image": c["image"], "State": c["state"],
                "Status": "Up 3 days" if c["state"] == "running" else "Exited (0) 2 hours ago",
                "Labels": "com.docker.compose.project=%s,x=y" % c["project"]}) + "\n"
                for n, c in sorted(self.containers.items()))
        if argv[1] != "compose":
            return ""
        proj = argv[argv.index("-p") + 1]
        compose = self.read(argv[argv.index("-f") + 1], "") or ""
        verb = next((a for a in argv[3:] if a in ("pull", "up", "stop", "restart", "down", "logs")), "")
        mine = {n: c for n, c in self.containers.items() if c["project"] == proj}
        if verb == "pull":
            time.sleep(3.0)
            return Res(0, "", " %s Pulled\n" % proj)
        if verb == "up":
            time.sleep(1.0)
            svcs = re.findall(r"^  ([a-z0-9_-]+):\s*\n(?:.*\n)*?\s+image:\s*(\S+)", compose, re.M) or \
                [(proj[4:], "inconnu")]
            for svc, image in svcs:
                self.containers["%s-%s-1" % (proj, svc)] = {"image": re.sub(r"\$\{[^}]*\}", "latest", image),
                                                            "state": "running", "project": proj}
            return Res(0, "", " Container %s  Started\n" % proj)
        if verb in ("stop", "restart"):
            time.sleep(0.6)
            for c in mine.values():
                c["state"] = "exited" if verb == "stop" else "running"
        if verb == "down":
            for n in mine:
                del self.containers[n]
        if verb == "logs":
            return "".join("%s  | [%02d:%02d:%02d] [Server thread/INFO]: ligne de journal simulée n° %d\n"
                           % (proj[4:], 12, i // 60 % 60, i % 60, i) for i in range(40))
        return ""


def run(base, web_port=8080, net_only=False, scenario="normal"):
    from .daemon import serve
    conf_text = "noeuds=forge,vault,pulse\n"
    nodes = []
    for name in ("vault", "pulse", "forge"):
        s = SimSystem(name, base, scenario)
        conf = config.Config(*config.parse_conf(conf_text + "role=%s\n" % name))
        node = Node(s, conf, base, net_only=net_only)
        node.local.open = True                    # simulation : 127.0.0.1 est cru sans jeton
        if node.hub:
            node.hub.auto_pair = True             # simulation : les nœuds s'appairent sans clic
        if not serve(node, PROFILES[name]["port"], host="127.0.0.1"):
            return 1
        if node.hub:
            if not serve(node, web_port, os.path.join(base, "ui"), host="127.0.0.1"):
                return 1
        nodes.append(node)
        threading.Thread(target=node.run, name="ydz-sim-" + name, daemon=True).start()
    print("YDZ OS %s, simulation (scénario « %s »).\n  Interface et API du hub : http://127.0.0.1:%d/\n"
          "  Agents : forge :8787, vault :8788, pulse :8789%s\n  Ctrl+C pour arrêter."
          % (config.version(), scenario, web_port,
             "\n  127.0.0.1 est traité comme un client du RÉSEAU (PIN demandé)." if net_only else ""), flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    while not stop.is_set():
        stop.wait(0.5)
    for n in nodes:
        n.shutdown()
    return 0
