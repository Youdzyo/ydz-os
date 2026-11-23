"""Services du nœud et du hub : mises à jour, Wake-on-LAN, écran."""
import re

from . import config
from .core import ApiError

MAJ_LOG = "/var/log/ydz/maj.log"
MAJ_UNIT = "ydz-maj"


# --- mises à jour (chaque nœud) ----------------------------------------------
def parse_upgradable(text):
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^([a-z0-9][a-z0-9.+-]*)/\S+\s+(\S+)\s", line)
        if m:
            out.append({"paquet": m.group(1), "version": m.group(2)})
    return out


class Updates:
    def __init__(self, sys, conf, state, tasks, base):
        self.sys, self.conf, self.state, self.tasks, self.base = sys, conf, state, tasks, base

    def running(self):
        r = self.sys.run(["systemctl", "is-active", MAJ_UNIT + ".service"], timeout=5)
        return r.out.strip() in ("active", "activating")

    def status(self):
        st = self.state.get("maj", {})
        log = (self.sys.read(MAJ_LOG, "") or "").splitlines()[-25:]
        return {"version": config.version(), "paquets": st.get("paquets"), "liste": st.get("liste", []),
                "derniere_verif": st.get("verif"), "derniere_maj": st.get("fait"),
                "redemarrage_requis": self.sys.exists("/run/reboot-required"),
                "auto_securite": self.conf.flag("maj_auto"), "en_cours": self.running(), "journal": log}

    def check(self):
        def job(task):
            r = self.sys.run(["apt-get", "update", "-q"], timeout=600)
            task.log(r.out[-800:] + r.err[-400:])
            if not r.ok:
                raise ApiError(500, "Impossible de joindre les dépôts (pas d'Internet ?).", "apt")
            lst = parse_upgradable(self.sys.run(["apt", "list", "--upgradable"], timeout=120).out)
            st = dict(self.state.get("maj", {}), paquets=len(lst), liste=lst[:40], verif=int(self.sys.now()))
            self.state.set("maj", st)
            return "%d mise(s) à jour disponible(s)." % len(lst) if lst else "Tout est à jour."
        return self.tasks.start("maj", "Recherche de mises à jour", job, None, "maj")

    def apply(self):
        """Lance la mise à jour dans une unité systemd à part : elle survit au
        redémarrage du démon et n'est pas soumise à son bac à sable."""
        if self.running():
            raise ApiError(409, "Une mise à jour est déjà en cours sur ce nœud.", "occupe")
        r = self.sys.run(["systemd-run", "--unit=" + MAJ_UNIT, "--collect", "--quiet",
                          "--description=Mise à jour YDZ OS", "--property=TimeoutStartSec=3h",
                          self.base + "/bin/ydz", "maj", "--appliquer", "--oui"], timeout=20)
        if not r.ok:
            raise ApiError(500, "La mise à jour n'a pas pu démarrer (%s)." % r.err.strip()[:120], "systemd")
        return True


# --- Wake-on-LAN (hub) ---------------------------------------------------------
def magic_packet(mac):
    h = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    if len(h) != 12:
        raise ValueError("adresse MAC invalide")
    return b"\xff" * 6 + bytes.fromhex(h) * 16


class Wol:
    def __init__(self, sys, conf, state):
        self.sys, self.conf, self.state = sys, conf, state
        self._ping = {}

    CLE = "/var/lib/ydz/cle-pc"            # clé SSH de forge pour éteindre les PC (Windows OpenSSH)
    HOTES = "/var/lib/ydz/hotes-pc"

    def targets(self):
        t = [{"id": "pc", "nom": self.conf.get("wol_nom"), "mac": self.conf.get("wol_mac").upper(),
              "diffusion": self.conf.get("wol_diffusion"), "ip": self.conf.get("wol_ip"), "fixe": True,
              "utilisateur": self.conf.get("wol_utilisateur")}]
        return t + [dict(x, fixe=False) for x in self.state.get("wol", [])]

    def _online(self, ip):
        if not ip:
            return None
        now = self.sys.monotonic()
        c = self._ping.get(ip)
        if c and now - c[0] < 10:
            return c[1]
        ok = self.sys.run(["ping", "-c", "1", "-W", "1", ip], timeout=3).ok
        if not ok and hasattr(self.sys, "tcp_ouvert"):
            # Windows bloque souvent le ping : on essaie SSH, partage de fichiers, bureau à distance
            ok = any(self.sys.tcp_ouvert(ip, port, 0.4) for port in (22, 445, 3389))
        self._ping[ip] = (now, ok)
        return ok

    def status(self):
        return {"cibles": [dict(t, en_ligne=self._online(t.get("ip"))) for t in self.targets()]}

    def wake(self, tid):
        t = next((x for x in self.targets() if x["id"] == tid), None)
        if not t:
            raise ApiError(404, "Machine inconnue.", "cible")
        pkt = magic_packet(t["mac"])
        ok = False
        for _ in range(3):
            ok = self.sys.udp_broadcast(t["diffusion"], 9, pkt) or ok
        if not ok:
            raise ApiError(500, "Le paquet de réveil n'a pas pu être envoyé (réseau coupé ?).", "reseau")
        self._ping.pop(t.get("ip"), None)
        return t

    # --- extinction à distance (SSH vers Windows) ---------------------------------------
    def cle_publique(self):
        """Crée la clé de forge au besoin et renvoie sa partie publique."""
        if not self.sys.exists(self.CLE + ".pub"):
            r = self.sys.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "ydz-%s" % self.sys.hostname(), "-f", self.CLE], timeout=20)
            if not r.ok:
                raise ApiError(500, "Impossible de créer la clé SSH : %s" % (r.err or r.out).strip()[:200], "cle")
        return (self.sys.read(self.CLE + ".pub", "") or "").strip()

    def eteindre(self, tid):
        t = next((x for x in self.targets() if x["id"] == tid), None)
        if not t:
            raise ApiError(404, "Machine inconnue.", "cible")
        if not t.get("ip"):
            raise ApiError(400, "Il faut l'adresse IP de la machine pour l'éteindre.", "ip")
        if not self.sys.exists(self.CLE):
            raise ApiError(409, "L'extinction n'est pas encore configurée sur ce PC : ouvre « Configurer l'extinction ».", "non_configure")
        user = t.get("utilisateur") or self.conf.get("wol_utilisateur")
        r = self.sys.run(["ssh", "-i", self.CLE, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
                          "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + self.HOTES,
                          "%s@%s" % (user, t["ip"]), "shutdown /s /f /t 5"], timeout=20)
        if not r.ok:
            detail = (r.err or r.out).strip().splitlines()
            raise ApiError(502, "Le PC n'a pas accepté l'ordre d'extinction%s." %
                           (" (%s)" % detail[-1][:160] if detail else ""), "ssh")
        self._ping.pop(t.get("ip"), None)
        return t

    def add(self, nom, mac, diffusion, ip=""):
        if not isinstance(nom, str) or not 0 < len(nom.strip()) <= 40:
            raise ApiError(400, "Donne un nom à la machine (40 caractères au plus).", "nom")
        if not isinstance(mac, str) or not config._RE_MAC.match(mac):
            raise ApiError(400, "Adresse MAC invalide (forme attendue : 20:0B:74:6A:0B:6C).", "mac")
        for k, v in (("diffusion", diffusion), ("ip", ip)):
            if (v or k == "diffusion") and not (isinstance(v, str) and config._RE_IP.match(v)):
                raise ApiError(400, "Adresse « %s » invalide." % k, k)
        cur = list(self.state.get("wol", []))
        if len(cur) >= 12:
            raise ApiError(409, "Trop de machines enregistrées.", "limite")
        n = 1
        while any(x["id"] == "m%d" % n for x in cur):
            n += 1
        t = {"id": "m%d" % n, "nom": nom.strip(), "mac": mac.upper().replace("-", ":"),
             "diffusion": diffusion, "ip": ip or ""}
        self.state.set("wol", cur + [t])
        return t

    def remove(self, tid):
        cur = list(self.state.get("wol", []))
        left = [x for x in cur if x["id"] != tid]
        if len(left) == len(cur):
            raise ApiError(404 if tid != "pc" else 403,
                           "Machine inconnue." if tid != "pc" else
                           "Le PC par défaut se modifie dans /etc/ydz/ydz.conf (clés wol_*).", "cible")
        self.state.set("wol", left)


# --- écran du CROWN (hub) --------------------------------------------------------
class Screen:
    CONF = "/etc/ydz/ydz.conf"

    def __init__(self, sys, conf, state):
        self.sys, self.conf, self.state = sys, conf, state
        self.last = sys.monotonic()
        self.asleep = False
        self.path = None
        self._find()
        self._apply()

    def _find(self):
        c = self.sys.glob("/sys/class/backlight/*")
        self.path = c[0] if c else None
        try:
            self.max = int((self.sys.read(self.path + "/max_brightness", "") or "").strip()) if self.path else 0
        except ValueError:
            self.max = 0

    def _apply(self):
        if not self.path or not self.max:
            return
        if self.asleep:
            self.sys.write_sys(self.path + "/brightness", 0)
            self.sys.write_sys(self.path + "/bl_power", 4)
        else:
            pct = max(5, min(100, int(self.state.get("ecran_lum", 80))))
            self.sys.write_sys(self.path + "/bl_power", 0)
            self.sys.write_sys(self.path + "/brightness", max(1, self.max * pct // 100))

    def activity(self):
        self.last = self.sys.monotonic()
        if self.asleep:
            self.asleep = False
            self._apply()
            return True
        return False

    def tick(self):
        if not self.path:
            if self.sys.monotonic() - self.last > 60:
                self.last = self.sys.monotonic()
                self._find()
            return
        mins = int(self.state.get("ecran_veille", 10))
        if mins and not self.asleep and self.sys.monotonic() - self.last > mins * 60:
            self.asleep = True
            self._apply()

    def set(self, luminosite=None, veille_min=None, rotation=None):
        restart = False
        if luminosite is not None:
            if not isinstance(luminosite, int) or isinstance(luminosite, bool) or not 5 <= luminosite <= 100:
                raise ApiError(400, "La luminosité va de 5 à 100.", "luminosite")
            self.state.set("ecran_lum", luminosite)
        if veille_min is not None:
            if not isinstance(veille_min, int) or isinstance(veille_min, bool) or not 0 <= veille_min <= 240:
                raise ApiError(400, "La veille va de 0 (jamais) à 240 minutes.", "veille")
            self.state.set("ecran_veille", veille_min)
        if rotation is not None:
            if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation not in (0, 90, 180, 270):
                raise ApiError(400, "Rotation possible : 0, 90, 180 ou 270.", "rotation")
            if str(rotation) != self.conf.get("rotation"):
                self.sys.write(self.CONF, config.update_conf_text(self.sys.read(self.CONF, ""),
                                                                  {"rotation": str(rotation)}), mode=0o644)
                self.conf.vals["rotation"] = str(rotation)
                restart = True
        self.activity()
        self._apply()
        if restart:   # le kiosque relit la rotation à son démarrage
            self.sys.spawn(["systemctl", "--no-block", "restart", "ydz-kiosk.service"])
        return restart

    def set_curseur(self, visible):
        """1.0.12 : curseur de la souris sur l'écran HDMI (curseur=oui|non dans ydz.conf). labwc ne lit le
        thème de curseurs qu'à son démarrage : le kiosque redémarre. Retourne True si quelque chose a changé."""
        if not isinstance(visible, bool):
            raise ApiError(400, "Champ « curseur » invalide (vrai ou faux).", "curseur")
        v = "oui" if visible else "non"
        if v == self.conf.get("curseur"):
            return False
        self.sys.write(self.CONF, config.update_conf_text(self.sys.read(self.CONF, ""), {"curseur": v}), mode=0o644)
        self.conf.vals["curseur"] = v
        self.sys.spawn(["systemctl", "--no-block", "restart", "ydz-kiosk.service"])
        return True

    def status(self):
        return {"present": bool(self.path and self.max), "luminosite": int(self.state.get("ecran_lum", 80)),
                "veille_min": int(self.state.get("ecran_veille", 10)), "en_veille": self.asleep,
                "rotation": self.conf.int("rotation"), "rotations": [0, 90, 180, 270],
                "kiosk": self.conf.get("kiosk"), "largeur": 1280, "hauteur": 720}
