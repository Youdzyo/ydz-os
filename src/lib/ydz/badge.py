"""Badge USB (LilyGO T-Display-S3) : détection par la sonde « ? », trame JSON
toutes les 2 s, « halt » à l'extinction. Voir badge/PROTOCOL.md (v1)."""
import json
import re

from . import config

STATES = ("ok", "warn", "crit", "boot", "halt")
MAX_LINE = 512
PAUSE_FILE = "/run/ydz/badge.pause"
PORT_GLOBS = ("/dev/ttyACM*", "/dev/ttyUSB*")


def clip(text, max_bytes):
    """Tronque à `max_bytes` octets UTF-8 sans couper un caractère."""
    b = str(text).encode("utf-8")
    if len(b) <= max_bytes:
        return str(text)
    return b[:max_bytes].decode("utf-8", "ignore")


def _pct(v):
    if v is None:
        return None
    return max(0, min(100, int(round(float(v)))))


def build_frame(status, state=None, accent="D9713F", bri=80, rot=None):
    """Construit la ligne JSON (bytes, terminée par \\n) à partir d'un /api/status."""
    state = state or status.get("etat", "ok")
    if state not in STATES:
        state = "ok"
    if not re.match(r"^[0-9A-Fa-f]{6}$", accent or ""):
        accent = "D9713F"
    f = {"v": 1, "name": clip((status.get("nom") or "YDZ").upper(), 16),
         "role": clip(status.get("role_nom") or "", 47)}
    t = status.get("temp")
    if isinstance(t, (int, float)) and -50 <= t <= 200:
        f["temp"] = round(float(t), 1)
    for k, src in (("cpu", status.get("cpu")), ("ram", (status.get("ram") or {}).get("pct")),
                   ("disk", status.get("disk"))):
        p = _pct(src)
        if p is not None:
            f[k] = p
    f["ip"] = clip(status.get("ip") or "", 45)
    f["state"] = state
    f["accent"] = accent.upper()
    f["bri"] = _pct(bri)
    if rot is not None:
        f["rot"] = 1 if rot else 0
    line = json.dumps(f, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(line) > MAX_LINE:      # impossible avec les bornes ci-dessus, par sécurité
        f["role"] = ""
        line = json.dumps(f, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    return line


def parse_probe(line):
    """Réponse à la sonde -> dict {fw, board} ou None."""
    try:
        d = json.loads(line or "")
    except ValueError:
        return None
    if isinstance(d, dict) and d.get("ydz_badge") == 1:
        return {"fw": str(d.get("fw", "?")), "board": str(d.get("board", "?"))}
    return None


def probe(ser, tries=3, timeout=0.6, seen=None):
    """Sonde « ? ». `seen` (liste) reçoit les lignes non vides qui ne sont pas la réponse du badge :
    un appareil qui parle autre chose fait tourner un autre firmware."""
    for _ in range(tries):
        ser.flush_input()
        ser.write(b"?\n")
        for _ in range(4):       # le badge peut émettre autre chose avant sa réponse
            line = ser.readline(timeout)
            if line is None:
                break
            info = parse_probe(line)
            if info:
                return info
            if seen is not None and line.strip():
                seen.append(line[:80])
    return None


# --- flash automatique : garde-fous -------------------------------------------------
VUS_FILE = "/var/lib/ydz/badge-vus.json"       # identité USB -> "ydz" | "autre"
FLASH_LOG = "/var/log/ydz/badge-flash.log"
ESPRESSIF = "303a"


def load_vus(sys):
    try:
        d = json.loads(sys.read(VUS_FILE, "") or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def mark_vus(sys, ident, val):
    """Retient ce qu'on a vu sur cet appareil. Retourne True si ça change quelque chose."""
    if not ident:
        return False
    d = load_vus(sys)
    if d.get(ident) == val:
        return False
    d[ident] = val
    sys.write(VUS_FILE, json.dumps(d, indent=1), mode=0o600)
    return True


def log_flash(sys, text):
    """Une ligne datée par décision de flash (150 dernières conservées)."""
    import time
    lines = (sys.read(FLASH_LOG, "") or "").splitlines()[-149:]
    lines.append("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sys.now())), text))
    sys.write(FLASH_LOG, "\n".join(lines) + "\n")


def decide_flash(act, vendor, info, vu, tries, now):
    """(flasher ?, raison). `auto` : ESP32-S3 Espressif (303a) ET muette à la sonde ET jamais vue avec un
    autre firmware ET moins de 3 essais en 24 h. `flasher` (commande explicite) : toujours."""
    if act != "auto":
        return True, "commande explicite"
    if info:
        return False, "badge YDZ déjà prêt (firmware %s)" % info["fw"]
    if vendor != ESPRESSIF:
        return False, "ce n'est pas une carte Espressif"
    if vu == "autre":
        return False, "déjà vue avec un autre firmware : « sudo ydz badge flasher » pour la reprendre"
    if tries.get("n", 0) >= 3 and now - tries.get("t", 0) < 86400:
        return False, "déjà 3 essais de flash en 24 h"
    return True, "carte Espressif muette, jamais vue avec un autre firmware"


class BadgeLink:
    """Appelé toutes les 2 s par la boucle du démon. Ne lève jamais."""

    def __init__(self, sys, conf, state):
        self.sys, self.conf, self.state = sys, conf, state
        self.ser = None
        self.port = None
        self.info = None
        self.error = None
        self._next_scan = 0
        self._bad = {}            # port -> prochaine tentative

    def _close(self):
        if self.ser:
            try:
                self.ser.close()
            except OSError:
                pass
        self.ser = self.port = self.info = None

    def _scan(self):
        now = self.sys.monotonic()
        ports = []
        for g in PORT_GLOBS:
            ports += self.sys.glob(g)
        if not ports:
            self.error = "aucun port série USB (badge débranché ?)"
        for p in ports:
            if self._bad.get(p, 0) > now:
                continue
            v = self.sys.usb_vendor(p)
            if v and v != "303a":         # clé Zigbee, onduleur, modem… : on n'y écrit rien et on ne l'ouvre pas
                continue
            try:
                ser = self.sys.open_serial(p)
            except OSError as ex:
                self._bad[p] = now + 30
                self.error = "%s : %s" % (p, ex)
                continue
            other = []
            try:
                info = probe(ser, seen=other)
            except OSError:
                info = None
            ident = self.sys.usb_ident(p) or p
            if info:
                mark_vus(self.sys, ident, "ydz")
                self.ser, self.port, self.info, self.error = ser, p, info, None
                return True
            if other:      # il parle, mais pas YDZ : le flash automatique n'y touchera plus jamais
                mark_vus(self.sys, ident, "autre")
            ser.close()
            self._bad[p] = now + 30
            self.error = "%s ne répond pas à la sonde (firmware YDZ absent ?)" % p
        return False

    def tick(self, status, state=None):
        if not self.conf.flag("badge"):
            return False
        if self.sys.exists(PAUSE_FILE):       # flash en cours : on lâche le port
            self._close()
            self.error = "en pause (flash en cours)"
            return False
        if self.ser is None:
            now = self.sys.monotonic()
            if now < self._next_scan:
                return False
            self._next_scan = now + 6
            if not self._scan():
                return False
        try:
            self.ser.write(build_frame(
                status, state, accent=self.state.get("accent", self.conf.get("accent")),
                bri=self.state.get("badge_lum", self.conf.int("badge_lum")),
                rot=self.state.get("badge_rot")))
            return True
        except OSError as ex:
            self.error = "écriture impossible : %s" % ex
            self._close()
            return False

    def halt(self, status):
        """Extinction : trois trames « halt » (le badge affiche « Arrêt en cours »)."""
        for _ in range(3):
            if not self.tick(status, "halt"):
                break
            self.sys.sleep(0.05)

    def status(self):
        return {"actif": self.conf.flag("badge"), "present": self.ser is not None, "port": self.port,
                "fw": self.info["fw"] if self.info else None,
                "carte": self.info["board"] if self.info else None,
                "luminosite": self.state.get("badge_lum", self.conf.int("badge_lum")),
                "accent": self.state.get("accent", self.conf.get("accent")),
                "erreur": None if self.ser else self.error}


__all__ = ["build_frame", "parse_probe", "probe", "BadgeLink", "clip", "config"]
