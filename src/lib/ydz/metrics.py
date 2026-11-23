"""Mesures du nœud : température, CPU, RAM, disques, réseau, throttling, Docker.

Tout est lu dans /proc et /sys (aucun processus lancé), sauf `vcgencmd`
(toutes les 30 s) et `docker ps` (toutes les 15 s) : le démon reste sous
1 % de CPU au repos.
"""
import json

from . import config

FS_OK = ("ext4", "ext3", "vfat", "btrfs", "xfs", "exfat", "ntfs3", "f2fs", "fuseblk")
THROTTLE_BITS = (
    (0, "sous_tension"), (1, "limite_freq"), (2, "bride"), (3, "temp_limite"),
    (16, "deja_sous_tension"), (17, "deja_limite_freq"), (18, "deja_bride"), (19, "deja_temp_limite"),
)


def parse_throttled(text):
    """« throttled=0x50005 » -> dict de booléens. None si illisible."""
    try:
        val = int((text or "").strip().split("=", 1)[1], 16)
    except (IndexError, ValueError):
        return None
    d = {"brut": "0x%x" % val}
    for bit, name in THROTTLE_BITS:
        d[name] = bool(val >> bit & 1)
    return d


def parse_meminfo(text):
    d = {}
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) >= 2 and p[1].isdigit():
            d[p[0].rstrip(":")] = int(p[1]) * 1024
    total = d.get("MemTotal", 0)
    avail = d.get("MemAvailable", d.get("MemFree", 0))
    used = max(total - avail, 0)
    return {"total": total, "utilise": used, "pct": round(100.0 * used / total, 1) if total else 0.0}


def parse_cpu(text):
    """Première ligne de /proc/stat -> (total, inactif)."""
    for line in (text or "").splitlines():
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:] if x.isdigit()]
            if len(v) >= 4:
                return sum(v[:8]), v[3] + (v[4] if len(v) > 4 else 0)
    return None


def parse_netdev(text):
    """/proc/net/dev -> {iface: (rx_octets, tx_octets)}."""
    d = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        p = rest.split()
        if len(p) >= 9 and p[0].isdigit():
            d[name.strip()] = (int(p[0]), int(p[8]))
    return d


def default_iface(route_text, netdev):
    for line in (route_text or "").splitlines()[1:]:
        p = line.split()
        if len(p) > 2 and p[1] == "00000000":
            return p[0]
    for cand in ("eth0", "end0", "wlan0"):
        if cand in netdev:
            return cand
    for n in netdev:
        if n != "lo" and not n.startswith(("docker", "veth", "br-", "wg")):
            return n
    return None


def parse_docker_ps(text):
    """Sortie de `docker ps -a --format '{{json .}}'` -> liste de conteneurs."""
    out = []
    for line in (text or "").splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        labels = {}
        for kv in (c.get("Labels") or "").split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                labels[k] = v
        proj = labels.get("com.docker.compose.project", "")
        out.append({
            "nom": c.get("Names", ""), "image": c.get("Image", ""),
            "etat": c.get("State", ""), "statut": c.get("Status", ""),
            "app": proj[4:] if proj.startswith("ydz-") else None,
        })
    return sorted(out, key=lambda c: c["nom"])


def health(st):
    """Retourne (état badge ok|warn|crit, liste d'alertes en français)."""
    crit, warn = [], []
    t = st.get("temp")
    if t is not None:
        if t >= 80:
            crit.append("Température très élevée (%.0f °C)" % t)
        elif t >= 70:
            warn.append("Température élevée (%.0f °C)" % t)
    if st.get("ram", {}).get("pct", 0) >= 92:
        warn.append("Mémoire presque pleine")
    for d in st.get("disques", []):
        if d["pct"] >= 95:
            crit.append("Disque « %s » plein à %d %%" % (d["nom"], d["pct"]))
        elif d["pct"] >= 85:
            warn.append("Disque « %s » rempli à %d %%" % (d["nom"], d["pct"]))
    th = st.get("throttle") or {}
    if th.get("sous_tension"):
        crit.append("Alimentation insuffisante (sous-tension)")
    elif th.get("deja_sous_tension"):
        warn.append("Une sous-tension a eu lieu depuis le démarrage")
    if th.get("bride"):
        warn.append("Processeur bridé par la chaleur")
    return ("crit" if crit else "warn" if warn else "ok"), crit + warn


class Collector:
    def __init__(self, sys, conf):
        self.sys, self.conf = sys, conf
        self._cpu = None
        self._net = None
        self._slow = {"throttle": None, "docker": {"dispo": False, "conteneurs": []}}
        self._t_thr = self._t_dock = -1e9
        self.model = (sys.read("/proc/device-tree/model", "") or "").strip("\x00\n ") or "inconnu"
        self.cores = max(1, (sys.read("/proc/cpuinfo", "") or "").count("processor\t:")) \
            if sys.read("/proc/cpuinfo") else 4

    def refresh_docker(self):
        self._t_dock = -1e9

    def _temp(self):
        raw = self.sys.read("/sys/class/thermal/thermal_zone0/temp")
        try:
            return round(int(raw.strip()) / 1000.0, 1)
        except (AttributeError, ValueError):
            return None

    def _disks(self):
        out, seen = [], set()
        for line in (self.sys.read("/proc/mounts", "") or "").splitlines():
            p = line.split()
            if len(p) < 3 or p[2] not in FS_OK or not p[0].startswith("/dev/"):
                continue
            mp = p[1].replace("\\040", " ")
            if not (mp == "/" or mp.startswith(("/srv/ydz/", "/mnt/", "/media/"))) or p[0] in seen:
                continue
            seen.add(p[0])
            v = self.sys.statvfs(mp)
            if not v or not v[0]:
                continue
            total, free = v
            out.append({"point": mp, "nom": "Système" if mp == "/" else mp.rstrip("/").split("/")[-1],
                        "peripherique": p[0], "fs": p[2], "total": total, "utilise": total - free,
                        "pct": int(round(100.0 * (total - free) / total))})
        return out

    def collect(self):
        s, now = self.sys, self.sys.monotonic()
        host = s.hostname()
        role = self.conf.role(host)
        st = {"v": 1, "nom": host, "role": role, "role_nom": config.ROLE_LABELS.get(role, role),
              "hub": role == "forge", "version": config.version(), "modele": self.model,
              "heure": int(s.now()), "temp": self._temp(), "coeurs": self.cores}
        # CPU
        cur = parse_cpu(s.read("/proc/stat", ""))
        cpu = 0.0
        if cur and self._cpu and cur[0] > self._cpu[0]:
            dt, di = cur[0] - self._cpu[0], cur[1] - self._cpu[1]
            cpu = max(0.0, min(100.0, 100.0 * (dt - di) / dt))
        self._cpu = cur or self._cpu
        st["cpu"] = round(cpu, 1)
        try:
            st["charge"] = [float(x) for x in s.read("/proc/loadavg", "0 0 0").split()[:3]]
        except ValueError:
            st["charge"] = [0.0, 0.0, 0.0]
        st["ram"] = parse_meminfo(s.read("/proc/meminfo", ""))
        st["disques"] = self._disks()
        st["disk"] = next((d["pct"] for d in st["disques"] if d["point"] == "/"), None)
        # réseau
        nd = parse_netdev(s.read("/proc/net/dev", ""))
        iface = default_iface(s.read("/proc/net/route", ""), nd)
        rx = tx = 0
        if iface in nd and self._net and self._net[0] == iface and now > self._net[1]:
            dt = now - self._net[1]
            rx = max(0, int((nd[iface][0] - self._net[2]) * 8 / dt))
            tx = max(0, int((nd[iface][1] - self._net[3]) * 8 / dt))
        if iface in nd:
            self._net = (iface, now, nd[iface][0], nd[iface][1])
        try:
            link = int((s.read("/sys/class/net/%s/speed" % iface, "") or "").strip())
        except ValueError:
            link = None
        st["ip"] = s.primary_ip()
        st["mac"] = (s.read("/sys/class/net/%s/address" % iface, "") or "").strip() or None
        st["reseau"] = {"iface": iface, "rx_bps": rx, "tx_bps": tx,
                        "lien_mbps": link if link and link > 0 else None}
        try:
            st["uptime"] = int(float(s.read("/proc/uptime", "0").split()[0]))
        except (ValueError, IndexError):
            st["uptime"] = 0
        # lents
        if now - self._t_thr >= 30:
            self._t_thr = now
            r = s.run(["vcgencmd", "get_throttled"], timeout=5)
            self._slow["throttle"] = parse_throttled(r.out) if r.ok else None
        if now - self._t_dock >= 15:
            self._t_dock = now
            if s.which("docker"):
                r = s.run(["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"], timeout=10)
                self._slow["docker"] = {"dispo": r.ok, "conteneurs": parse_docker_ps(r.out) if r.ok else []}
            else:
                self._slow["docker"] = {"dispo": False, "conteneurs": []}
        st["throttle"] = self._slow["throttle"]
        st["docker"] = self._slow["docker"]
        st["redemarrage_requis"] = s.exists("/run/reboot-required")
        st["etat"], st["alertes"] = health(st)
        return st
