"""Configuration (ydz.conf, lisible par un humain) et état persistant (JSON)."""
import json
import os
import re
import threading

VERSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "VERSION")

ROLES = ("forge", "vault", "pulse")

# clé -> (valeur par défaut, validateur)
_RE_MAC = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_RE_IP = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
_RE_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")


def _choix(*vals):
    return lambda v: v in vals


def _entier(lo, hi):
    def f(v):
        try:
            return lo <= int(v) <= hi
        except ValueError:
            return False
    return f


def _liste_hotes(v):
    return all(_RE_HOST.match(x.strip()) or _RE_IP.match(x.strip()) for x in v.split(",") if x.strip())


DEFAULTS = {
    "role": ("auto", _choix("auto", *ROLES)),
    "noeuds": ("forge,vault,pulse", _liste_hotes),
    "port": ("8787", _entier(1, 65535)),
    "port_web": ("80", _entier(1, 65535)),
    # écran / kiosque (forge)
    "kiosk": ("labwc", _choix("labwc", "cage", "cog", "non")),
    "rotation": ("90", _choix("0", "90", "180", "270")),
    "ecran_mode": ("complet", _choix("complet", "affichage")),
    "curseur": ("non", _choix("oui", "non")),          # 1.0.12 : curseur de la souris sur l'écran HDMI
    "ecran_dsi": ("1", _choix("0", "1")),
    "nom": ("auto", lambda v: v == "auto" or bool(_RE_HOST.match(v))),
    "badge_lum": ("80", _entier(0, 100)),
    "ecran_overlay": ("auto", _choix("auto", "force", "non")),
    # ventilateur (forge)
    "ventilo_gpio": ("18", _entier(0, 27)),
    "ventilo_hz": ("25000", _entier(100, 100000)),
    "ventilo_inverse": ("oui", _choix("oui", "non")),
    # badge
    "badge": ("oui", _choix("oui", "non")),
    "badge_flash_auto": ("oui", _choix("oui", "non")),
    "accent": ("D9713F", lambda v: bool(re.match(r"^[0-9A-Fa-f]{6}$", v))),
    # Wake-on-LAN : le PC de Justin (contrat §5)
    "wol_nom": ("PC de Justin", lambda v: 0 < len(v) <= 40),
    "wol_mac": ("20:0B:74:6A:0B:6C", lambda v: bool(_RE_MAC.match(v))),
    "wol_diffusion": ("192.168.1.255", lambda v: bool(_RE_IP.match(v))),
    "wol_ip": ("192.168.1.35", lambda v: bool(_RE_IP.match(v))),
    "wol_utilisateur": ("Justin", lambda v: bool(re.match(r"^[A-Za-z0-9._ -]{1,32}$", v))),
    # installation
    "docker": ("debian", _choix("debian", "officiel", "non")),
    "sata_dma32": ("auto", _choix("auto", "oui", "non")),
    "maj_auto": ("oui", _choix("oui", "non")),
    "maj_url": ("", lambda v: v == "" or v.startswith(("http://", "https://"))),
    # 1.0.14 : dépôt GitHub public des mises à jour de YDZ OS (propriétaire/dépôt, rien d'autre)
    "maj_depot": ("Youdzyo/ydz-os", lambda v: bool(re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$", v))
                  and ".." not in v),
}

ROLE_LABELS = {"forge": "jeux · docker", "vault": "stockage", "pulse": "réseau"}


def parse_conf(text):
    """Analyse un fichier clé=valeur. Tolère CRLF, BOM, espaces, guillemets,
    commentaires (# ou ;). Retourne (valeurs, erreurs)."""
    vals, errs = {}, []
    for n, raw in enumerate((text or "").lstrip("﻿").splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if "=" not in line:
            errs.append("ligne %d ignorée (pas de « = ») : %s" % (n, line[:40]))
            continue
        k, v = line.split("=", 1)
        k = k.strip().lower()
        v = v.split(" #", 1)[0].strip().strip('"').strip("'").strip()
        if k not in DEFAULTS:
            errs.append("ligne %d : clé inconnue « %s »" % (n, k))
            continue
        if k in ("role", "kiosk", "docker", "sata_dma32", "ecran_overlay"):
            v = v.lower()
        if not DEFAULTS[k][1](v):
            errs.append("ligne %d : valeur invalide pour %s : « %s » (défaut conservé : %s)"
                        % (n, k, v[:40], DEFAULTS[k][0]))
            continue
        vals[k] = v
    return vals, errs


class Config:
    def __init__(self, vals=None, errors=None):
        self.vals = {k: d for k, (d, _) in DEFAULTS.items()}
        self.vals.update(vals or {})
        self.errors = list(errors or [])

    @classmethod
    def load(cls, sys, path="/etc/ydz/ydz.conf"):
        vals, errs = parse_conf(sys.read(path, ""))
        return cls(vals, errs)

    def get(self, k):
        return self.vals[k]

    def int(self, k):
        return int(self.vals[k])

    def flag(self, k):
        return self.vals[k] == "oui"

    def role(self, hostname):
        return resolve_role(self.vals["role"], hostname)

    def peers(self):
        return [x.strip() for x in self.vals["noeuds"].split(",") if x.strip()]


def role_du_nom(hostname):
    """Rôle déduit du nom d'hôte (forge, forge-2…) ; None si le nom n'en dit rien (pi-one…)."""
    h = (hostname or "").lower().split(".")[0]
    for r in ROLES:
        if h == r or h.startswith(r + "-") or h.startswith(r):
            return r
    return None


def resolve_role(role, hostname):
    """« auto » déduit le rôle du nom d'hôte ; à défaut, simple nœud (pulse)."""
    if role in ROLES:
        return role
    return role_du_nom(hostname) or "pulse"


def choisir_role(conf, hostname, state):
    """1.0.13 : rôle du nœud. Depuis que le hub s'appelle « pi-one », le nom d'hôte ne dit plus le rôle :
    ydz.conf (role=…) d'abord, puis le nom d'hôte, puis le dernier rôle connu (gardé dans l'état).
    Un hub renommé sans role= dans ydz.conf reste donc le hub."""
    r = conf.get("role")
    if r not in ROLES:
        r = role_du_nom(hostname) or state.get("role")
    if r not in ROLES:
        r = "pulse"
    state.set("role", r)
    return r


def update_conf_text(text, changes):
    """Modifie des clés dans un texte ydz.conf en préservant commentaires et ordre."""
    out, done = [], set()
    for raw in (text or "").splitlines():
        s = raw.strip()
        if s and s[0] not in "#;" and "=" in s:
            k = s.split("=", 1)[0].strip().lower()
            if k in changes:
                out.append("%s=%s" % (k, changes[k]))
                done.add(k)
                continue
        out.append(raw.rstrip("\r"))
    for k, v in changes.items():
        if k not in done:
            out.append("%s=%s" % (k, v))
    return "\n".join(out) + "\n"


def version(sys=None):
    try:
        with open(VERSION_FILE) as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def vercmp(a, b):
    def t(v):
        return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v.strip())[:4]]
    ta, tb = t(a), t(b)
    return (ta > tb) - (ta < tb)


class State:
    """État persistant (JSON), écrit seulement quand il change (ménage la carte SD)."""

    def __init__(self, sys, path="/var/lib/ydz/etat.json"):
        self.sys, self.path = sys, path
        self.lock = threading.RLock()
        try:
            self.d = json.loads(sys.read(path, "") or "{}")
            if not isinstance(self.d, dict):
                self.d = {}
        except ValueError:
            self.d = {}

    def get(self, k, default=None):
        with self.lock:
            return self.d.get(k, default)

    def set(self, k, v):
        with self.lock:
            if self.d.get(k) == v:
                return
            self.d[k] = v
            self._save()

    def delete(self, k):
        with self.lock:
            if k in self.d:
                del self.d[k]
                self._save()

    def _save(self):
        self.sys.write(self.path, json.dumps(self.d, ensure_ascii=False, indent=1), mode=0o600)
