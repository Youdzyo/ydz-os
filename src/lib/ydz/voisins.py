"""Appareils connectés au réseau de la maison (accueil, vue Réseau, écran du Pi).

Toutes les 5 minutes, le hub « touche » chaque adresse de son réseau /24 avec un petit paquet UDP
(port 9, ignoré par tout le monde) : le noyau doit alors trouver l'adresse matérielle de chacun, et
la table ARP (/proc/net/arp) contient ensuite les appareils réellement présents. On compte aussi les
appareils Tailscale en ligne. Rien n'est envoyé hors du réseau local.

1.0.10 : chaque appareil vu est retenu (adresse MAC, IP, nom trouvé par la box ou en mDNS, nom
choisi par Justin, première et dernière apparition). Un appareil jamais vu déclenche une alerte,
sauf au tout premier balayage après l'installation, qui apprend simplement ce qui est déjà là.
"""
import ipaddress
import json
import re
import socket
import threading
import time

from .core import ApiError

PERIODE = 300
NOM_MAX = 40
RE_MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
SUFFIXES = (".home", ".lan", ".local", ".localdomain", ".box", ".fritz.box", ".station", ".internal")
PERSISTE_VU = 3600          # la date de dernière vue n'est réécrite sur la carte SD qu'une fois par heure
NOMS_REVUS = 6 * 3600       # les noms trouvés automatiquement sont redemandés toutes les 6 h
NOUVEAUX_MAX = 5


def lire_arp(texte, dev, net):
    """Contenu de /proc/net/arp -> {mac: ip} des voisins complets de `dev` dans le réseau `net`."""
    vus = {}
    for ligne in (texte or "").splitlines()[1:]:
        c = ligne.split()
        if len(c) < 6 or c[2] != "0x2" or c[5] != dev or c[3] == "00:00:00:00:00:00":
            continue
        try:
            if ipaddress.ip_address(c[0]) in net:
                vus[c[3].lower()] = c[0]
        except ValueError:
            pass
    return vus


def joli_nom(brut, ip=""):
    """« iPhone-de-Justin.home » -> « iPhone de Justin ». Vide si rien d'utile."""
    n = (brut or "").strip().rstrip(".")
    if not n or n == ip:
        return ""
    bas = n.lower()
    for s in SUFFIXES:
        if bas.endswith(s):
            n = n[:-len(s)]
            break
    if re.match(r"^[\d.]+$", n):          # une adresse, pas un nom
        return ""
    n = re.sub(r"[-_]+", " ", n).strip()
    return n[:NOM_MAX]


def lire_getent(texte, ip):
    """Sortie de `getent hosts <ip>` -> nom (premier nom de la ligne)."""
    for ligne in (texte or "").splitlines():
        c = ligne.split()
        if len(c) >= 2 and c[0] == ip:
            return joli_nom(c[1], ip)
    return ""


def lire_avahi(texte, ip):
    """Sortie de `avahi-resolve -a <ip>` -> nom."""
    for ligne in (texte or "").splitlines():
        c = ligne.split()
        if len(c) >= 2 and c[0] == ip:
            return joli_nom(c[1], ip)
    return ""


def verifier_nom(v):
    """Nom choisi dans l'interface : chaîne de 0 à 40 caractères, sans caractère de contrôle."""
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ApiError(400, "Le nom doit être un texte.", "nom")
    if any(ord(c) < 32 or c in "<>" for c in v):
        raise ApiError(400, "Ce nom contient un caractère interdit.", "nom")
    v = " ".join(v.split())
    if len(v) > NOM_MAX:
        raise ApiError(400, "Nom trop long : %d caractères au plus." % NOM_MAX, "nom")
    return v


class Voisins:
    def __init__(self, sys, state=None, alerte=None):
        self.sys = sys
        self.state = state
        self.alerte = alerte or (lambda *a, **k: None)
        self.res = None            # {"appareils": n, "tailscale": m|None, "maj": t}
        self.stop = threading.Event()
        self.reel = type(sys).__name__ == "System"   # pas de balayage en simulation / tests
        self.lock = threading.Lock()
        brut = state.get("appareils") if state is not None else None
        self.apprentissage = brut is None            # premier balayage après l'installation : on apprend
        self.connus = {k: dict(v) for k, v in (brut or {}).items() if isinstance(v, dict)}
        self.en_ligne = set()
        self.moi = None            # adresse MAC du Pi
        self.nouveaux = []         # dernières alertes « nouvel appareil » (en mémoire)
        self._noms_le = {}         # mac -> monotonic de la dernière recherche de nom

    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-voisins", daemon=True).start()

    # --- lecture ----------------------------------------------------------------------------
    @staticmethod
    def nom(a):
        return a.get("nom_perso") or a.get("nom_auto") or ""

    def liste(self):
        """Tous les appareils connus, en ligne d'abord, puis du plus récemment vu au plus ancien."""
        with self.lock:
            out = []
            for mac, a in self.connus.items():
                out.append({"mac": mac, "ip": a.get("ip"), "nom": self.nom(a), "nom_perso": a.get("nom_perso") or "",
                            "nom_auto": a.get("nom_auto") or "", "premiere_vue": a.get("premiere_vue"),
                            "derniere_vue": a.get("derniere_vue"), "en_ligne": mac in self.en_ligne,
                            "moi": mac == self.moi})
        out.sort(key=lambda x: (not x["moi"], not x["en_ligne"], -(x["derniere_vue"] or 0), x["nom"].lower(),
                                x["ip"] or ""))
        return out

    def status(self):
        r = dict(self.res or {"appareils": None, "tailscale": None, "maj": None})
        with self.lock:
            r["connus"] = len(self.connus)
            r["nouveaux"] = [dict(x) for x in self.nouveaux]
            recents = sorted(((m, a) for m, a in self.connus.items() if m != self.moi),
                             key=lambda x: -(x[1].get("premiere_vue") or 0))[:6]
            r["recents"] = [{"nom": self.nom(a), "ip": a.get("ip"), "en_ligne": m in self.en_ligne,
                             "premiere_vue": a.get("premiere_vue")} for m, a in recents]
        return r

    # --- modification depuis l'interface --------------------------------------------------------
    def renommer(self, mac, nom):
        mac = (mac or "").lower()
        nom = verifier_nom(nom)
        with self.lock:
            a = self.connus.get(mac)
            if a is None:
                raise ApiError(404, "Cet appareil n'est pas (ou plus) connu.", "appareil")
            if (a.get("nom_perso") or "") == nom:
                return self._vue(mac)
            a["nom_perso"] = nom
            self._sauver()
        return self._vue(mac)

    def oublier(self, mac):
        mac = (mac or "").lower()
        with self.lock:
            if mac == self.moi:
                raise ApiError(400, "Le Pi lui-même ne peut pas être oublié.", "appareil")
            if self.connus.pop(mac, None) is None:
                raise ApiError(404, "Cet appareil n'est pas (ou plus) connu.", "appareil")
            self.en_ligne.discard(mac)
            self._sauver()

    def _vue(self, mac):
        return next((x for x in self.liste() if x["mac"] == mac), None)

    def _sauver(self):
        if self.state is not None:
            self.state.set("appareils", {k: dict(v) for k, v in self.connus.items()})

    # --- balayage ------------------------------------------------------------------------------
    def _boucle(self):
        time.sleep(20)                        # laisse le réseau se lever au démarrage
        while not self.stop.is_set():
            try:
                self.res = self.mesure()
            except Exception:                 # une mesure ratée ne doit jamais gêner le démon
                pass
            self.stop.wait(PERIODE)

    def _reseau(self):
        """(interface, adresse, réseau /24) de la route par défaut."""
        r = self.sys.run(["ip", "-4", "-o", "route", "get", "1.1.1.1"], timeout=5)
        mots = (r.out or "").split()
        if not r.ok or "dev" not in mots or "src" not in mots:
            return None
        dev, src = mots[mots.index("dev") + 1], mots[mots.index("src") + 1]
        net = ipaddress.ip_network(src + "/24", strict=False)
        return dev, src, net

    def chercher_nom(self, ip):
        """Nom d'un appareil : DNS inverse (la Livebox connaît ses clients DHCP), sinon mDNS."""
        r = self.sys.run(["getent", "hosts", ip], timeout=3)
        n = lire_getent(r.out, ip) if r.ok else ""
        if not n and self.sys.which("avahi-resolve"):
            r = self.sys.run(["avahi-resolve", "-4", "-a", ip], timeout=3)
            n = lire_avahi(r.out, ip) if r.ok else ""
        return n

    def integrer(self, vus, moi=None):
        """Range un balayage ({mac: ip}, + (mac, ip, nom) du Pi) dans la liste des appareils connus.
        Écrit l'état au plus une fois, et seulement si quelque chose d'important a changé.
        Retourne la liste des nouveaux appareils (vide au premier balayage, qui apprend)."""
        now = int(self.sys.now())
        mono = self.sys.monotonic()
        vus = {m.lower(): ip for m, ip in vus.items() if RE_MAC.match(m.lower())}
        if moi and moi[0]:
            self.moi = moi[0].lower()
            vus[self.moi] = moi[1]
        # noms cherchés hors verrou (getent peut prendre quelques secondes)
        noms = {}
        for mac, ip in vus.items():
            if mac == self.moi and moi:
                noms[mac] = moi[2]
                continue
            a = self.connus.get(mac)
            if a is None or a.get("ip") != ip or mono - self._noms_le.get(mac, -1e9) > NOMS_REVUS:
                try:
                    noms[mac] = self.chercher_nom(ip)
                except Exception:
                    noms[mac] = (a or {}).get("nom_auto", "")
                self._noms_le[mac] = mono
        nouveaux, change = [], False
        with self.lock:
            persiste = self.state.get("appareils") or {} if self.state is not None else {}
            for mac, ip in vus.items():
                a = self.connus.get(mac)
                if a is None:
                    a = self.connus[mac] = {"nom_perso": "", "premiere_vue": now, "derniere_vue": now,
                                            "ip": ip, "nom_auto": noms.get(mac, "")}
                    change = True
                    if not self.apprentissage and mac != self.moi:
                        nouveaux.append(mac)
                    continue
                if a.get("ip") != ip:
                    a["ip"], change = ip, True
                if mac in noms and noms[mac] and noms[mac] != a.get("nom_auto"):
                    a["nom_auto"], change = noms[mac], True
                a["derniere_vue"] = now
                if now - ((persiste.get(mac) or {}).get("derniere_vue") or 0) >= PERSISTE_VU:
                    change = True
            self.en_ligne = set(vus)
            if change or self.apprentissage:
                self._sauver()
            self.apprentissage = False
            for mac in nouveaux:
                a = self.connus[mac]
                self.nouveaux.insert(0, {"t": now, "mac": mac, "ip": a["ip"], "nom": self.nom(a)})
            del self.nouveaux[NOUVEAUX_MAX:]
        self._alerter(nouveaux)
        return nouveaux

    def _alerter(self, macs):
        if not macs:
            return
        if len(macs) > 3:                     # beaucoup d'un coup (box redémarrée…) : un seul message
            self.alerte("nouvel_appareil", "warn", "%d nouveaux appareils sur le réseau" % len(macs),
                        appareils=macs)
            return
        for mac in macs:
            a = self.connus.get(mac) or {}
            self.alerte("nouvel_appareil", "warn", "Nouvel appareil sur le réseau : %s (%s)"
                        % (self.nom(a) or a.get("ip") or "?", mac), mac=mac, ip=a.get("ip"))

    def mesure(self):
        r = self._reseau()
        if not r:
            return None
        dev, src, net = r
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for h in net.hosts():
                if str(h) == src:
                    continue
                try:
                    s.sendto(b"\0", (str(h), 9))
                except OSError:
                    pass
                time.sleep(0.004)             # ~1 s pour tout le réseau, sans saturer un Wi-Fi faible
        finally:
            s.close()
        time.sleep(3)                         # le temps que les réponses ARP arrivent
        vus = lire_arp(self.sys.read("/proc/net/arp", ""), dev, net)
        mac_pi = (self.sys.read("/sys/class/net/%s/address" % dev, "") or "").strip().lower()
        try:
            self.integrer(vus, (mac_pi, src, self.sys.hostname()) if RE_MAC.match(mac_pi) else None)
        except Exception:
            pass
        ts = None
        if self.sys.which("tailscale"):
            t = self.sys.run(["tailscale", "status", "--json"], timeout=8)
            if t.ok:
                try:
                    pairs = (json.loads(t.out).get("Peer") or {}).values()
                    ts = sum(1 for p in pairs if p.get("Online"))
                except ValueError:
                    ts = None
        # + 1 : le hub lui-même est un appareil du réseau
        return {"appareils": len(vus) + 1, "tailscale": ts, "maj": int(self.sys.now())}
