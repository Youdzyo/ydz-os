"""Bluetooth du Pi (1.0.13) : marche/arrêt, recherche, appairage, connexion, oubli.

Tout passe par bluetoothctl (BlueZ), sans D-Bus côté Python (bibliothèque standard seulement) :
  - commandes simples en mode non interactif : `bluetoothctl show`, `bluetoothctl info MAC`, `… connect MAC` ;
  - recherche : `bluetoothctl --timeout 15 scan on` dans un fil à part ;
  - appairage : une session bluetoothctl nourrie par stdin, avec délais, qui répond aux questions de l'agent
    (« Confirm passkey 123456 », « Passkey: 123456 », « Enter PIN code ») ; le code est montré dans
    l'interface pour être confirmé (ou tapé) sur l'appareil.
Module à part : le paquet 1.0.16 s'en servira pour les notifications de l'iPhone en BLE (ANCS/AMS).
"""
import queue
import re
import subprocess
import threading
import time

from .core import ApiError

RE_MAC = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
RE_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x01|\x02|\r")
RE_DEVICE = re.compile(r"Device ((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})(?: (.*))?$")
DUREE_SCAN = 15

TYPES = {   # type -> (libellé, icône)
    "manette": ("Manette", "gamepad-2"), "clavier": ("Clavier", "keyboard"), "souris": ("Souris", "mouse"),
    "casque": ("Casque", "headphones"), "enceinte": ("Enceinte", "speaker"), "telephone": ("Téléphone", "smartphone"),
    "autre": ("Appareil", "bluetooth"),
}


def propre(texte):
    return RE_ANSI.sub("", texte or "")


def lire_show(texte):
    t = propre(texte)
    if "No default controller" in t or "Controller" not in t:
        return None
    def oui(cle):
        m = re.search(r"^\s*%s:\s*(\S+)" % cle, t, re.M)
        return bool(m and m.group(1) == "yes")
    m = re.search(r"Controller ((?:[0-9A-F]{2}:){5}[0-9A-F]{2})", t)
    return {"mac": m.group(1) if m else None, "allume": oui("Powered"), "recherche": oui("Discovering")}


def lire_devices(texte):
    """`bluetoothctl devices` (ou la sortie d'un scan) -> {mac: nom}."""
    out = {}
    for l in propre(texte).splitlines():
        m = RE_DEVICE.search(l.strip())
        if m and " DEL " not in " %s " % l and "[DEL]" not in l:
            mac = m.group(1).upper()
            nom = (m.group(2) or "").strip()
            if nom.startswith(("RSSI:", "ManufacturerData", "TxPower", "UUIDs:", "Connected:", "ServicesResolved")):
                nom = out.get(mac, "")
            out[mac] = nom or out.get(mac, "")
    return out


def lire_info(texte):
    t = propre(texte)
    d = {}
    for l in t.splitlines():
        m = re.match(r"^\s+([A-Za-z][A-Za-z ]*?):\s*(.*)$", l)
        if m and m.group(1) not in d:
            d[m.group(1)] = m.group(2).strip()
    if not d:
        return None
    bat = re.search(r"\((\d+)\)", d.get("Battery Percentage", ""))
    def oui(k):
        return d.get(k, "no").startswith("yes")
    return {"nom": d.get("Alias") or d.get("Name") or "", "icone_bluez": d.get("Icon", ""), "classe": d.get("Class", ""),
            "apparence": d.get("Appearance", ""), "appaire": oui("Paired") or oui("Bonded"), "confiance": oui("Trusted"),
            "connecte": oui("Connected"), "batterie": int(bat.group(1)) if bat else None}


def _hex(v):
    m = re.search(r"0x([0-9a-fA-F]+)", v or "")
    return int(m.group(1), 16) if m else None


def type_appareil(icone="", classe="", apparence="", nom=""):
    """Type d'appareil d'après l'icône BlueZ, puis la classe (Bluetooth classique), l'apparence (BLE), le nom."""
    i = (icone or "").lower()
    if i == "input-gaming":
        return "manette"
    if i == "input-keyboard":
        return "clavier"
    if i in ("input-mouse", "input-tablet"):
        return "souris"
    if i in ("audio-headset", "audio-headphones"):
        return "casque"
    if i in ("audio-card", "audio-speakers", "multimedia-player"):
        return "enceinte"
    if i == "phone":
        return "telephone"
    c = _hex(classe)
    if c is not None:
        majeur, mineur = (c >> 8) & 0x1F, (c >> 2) & 0x3F
        if majeur == 0x02:
            return "telephone"
        if majeur == 0x04:
            return "casque" if mineur in (0x01, 0x02, 0x06) else "enceinte"
        if majeur == 0x05:
            if (mineur & 0x0F) in (0x01, 0x02):
                return "manette"
            if mineur & 0x10:
                return "clavier"
            if mineur & 0x20:
                return "souris"
    a = _hex(apparence)
    if a is not None:
        cat = a >> 6
        if cat == 0x0F:
            sous = a & 0x3F
            return {1: "clavier", 2: "souris", 3: "manette", 4: "manette"}.get(sous, "autre")
        if cat == 0x01:
            return "telephone"
        if cat in (0x25, 0x21):
            return "casque" if cat == 0x25 else "enceinte"
    n = (nom or "").lower()
    for mots, t in ((("controller", "manette", "gamepad", "dualsense", "dualshock", "xbox", "joy-con", "8bitdo"), "manette"),
                    (("keyboard", "clavier", "keys"), "clavier"), (("mouse", "souris", "trackpad"), "souris"),
                    (("airpods", "buds", "headphone", "casque", "wh-1000", "headset"), "casque"),
                    (("speaker", "enceinte", "jbl", "boom", "soundlink", "sonos", "marshall"), "enceinte"),
                    (("iphone", "galaxy", "pixel", "phone"), "telephone")):
        if any(m in n for m in mots):
            return t
    return "autre"


class SessionReelle:
    """bluetoothctl interactif, nourri par stdin ; les lignes lues arrivent dans une file."""

    def __init__(self, agent="NoInputNoOutput"):
        self.p = subprocess.Popen(["bluetoothctl", "--agent", agent], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.q = queue.Queue()
        threading.Thread(target=self._lire, daemon=True).start()

    def _lire(self):
        try:
            for l in self.p.stdout:
                self.q.put(l)
        except (OSError, ValueError):
            pass
        self.q.put(None)

    def envoyer(self, ligne):
        try:
            self.p.stdin.write(ligne + "\n")
            self.p.stdin.flush()
        except (OSError, ValueError):
            pass

    def ligne(self, delai):
        try:
            return self.q.get(timeout=delai)
        except queue.Empty:
            return ""

    def fermer(self):
        self.envoyer("quit")
        try:
            self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.p.kill()


class Bluetooth:
    def __init__(self, sys, events=None, lanceur=None, horloge=None):
        self.sys, self.events = sys, events
        self.reel = type(sys).__name__ == "System"
        self.lanceur = lanceur or SessionReelle
        self.horloge = horloge or time.monotonic
        self.lock = threading.Lock()
        self.scan_fin = 0
        self.vus = {}               # mac -> nom vu pendant la dernière recherche
        self.infos = {}             # mac -> (monotonic, info)
        self.appairage = None       # {mac, nom, etat, code, message}
        self.pin = None             # code PIN donné par l'interface (appareils anciens)
        self.confirme = None        # 1.0.15 : accord explicite pour le code à 6 chiffres affiché
        self.auto_confirmer = None  # simulation/tests : accepte le code sans attendre l'interface

    def ctl(self, *args, timeout=10):
        return self.sys.run(["bluetoothctl"] + list(args), timeout=timeout)

    def present(self):
        return bool(self.sys.which("bluetoothctl"))

    def controleur(self):
        r = self.ctl("show", timeout=6)
        return lire_show(r.out)

    def info(self, mac, frais=False):
        now = self.sys.monotonic()
        c = self.infos.get(mac)
        if c and not frais and now - c[0] < 20:
            return c[1]
        r = self.ctl("info", mac, timeout=6)
        i = lire_info(r.out)
        self.infos[mac] = (now, i)
        return i

    def appareils(self):
        r = self.ctl("devices", timeout=6)
        liste = dict(self.vus)
        liste.update({k: v or self.vus.get(k, "") for k, v in lire_devices(r.out).items()})
        out = []
        for mac, nom in list(liste.items())[:40]:
            i = self.info(mac) or {}
            nom = i.get("nom") or nom
            if not nom or nom.replace("-", ":").upper() == mac:
                nom = ""
            t = type_appareil(i.get("icone_bluez"), i.get("classe"), i.get("apparence"), nom)
            out.append({"mac": mac, "nom": nom or "Appareil sans nom", "anonyme": not nom, "type": t,
                        "type_nom": TYPES[t][0], "icone": TYPES[t][1], "appaire": i.get("appaire", False),
                        "connecte": i.get("connecte", False), "confiance": i.get("confiance", False),
                        "batterie": i.get("batterie")})
        out.sort(key=lambda d: (not d["connecte"], not d["appaire"], d["anonyme"], d["nom"].lower()))
        return out

    def status(self):
        if not self.present():
            return {"present": False, "raison": "BlueZ (bluetoothctl) n'est pas installé."}
        c = self.controleur()
        if not c:
            return {"present": False, "raison": "Aucune puce Bluetooth trouvée (ou elle est bloquée)."}
        en_scan = self.horloge() < self.scan_fin
        return {"present": True, "actif": c["allume"], "mac": c["mac"], "recherche": en_scan or c["recherche"],
                "recherche_reste_s": max(0, int(self.scan_fin - self.horloge())) if en_scan else 0,
                "appareils": self.appareils() if c["allume"] else [], "appairage": self.appairage}

    # --- actions --------------------------------------------------------------------------------------
    def _mac(self, mac):
        mac = (mac or "").upper() if isinstance(mac, str) else ""
        if not RE_MAC.match(mac):
            raise ApiError(400, "Adresse Bluetooth invalide.", "mac")
        return mac

    def regler(self, actif):
        if not isinstance(actif, bool):
            raise ApiError(400, "Champ « actif » invalide (vrai ou faux).", "actif")
        if actif:
            self.sys.run(["rfkill", "unblock", "bluetooth"], timeout=5)
        r = self.ctl("power", "on" if actif else "off", timeout=10)
        if not r.ok and "succeeded" not in r.out:
            raise ApiError(500, "Le Bluetooth n'a pas pu être %s." % ("allumé" if actif else "coupé"), "bluetoothctl")
        return actif

    def rechercher(self):
        if self.horloge() < self.scan_fin:
            return DUREE_SCAN
        self.scan_fin = self.horloge() + DUREE_SCAN

        def scan():
            r = self.ctl("--timeout", str(DUREE_SCAN), "scan", "on", timeout=DUREE_SCAN + 10)
            self.vus.update({k: v for k, v in lire_devices(r.out).items() if v or k not in self.vus})
            self.scan_fin = 0
        if self.reel:
            threading.Thread(target=scan, name="ydz-bt-scan", daemon=True).start()
        else:
            scan()
        return DUREE_SCAN

    def action(self, mac, action):
        mac = self._mac(mac)
        if action == "appairer":
            return self.appairer(mac)
        cmd = {"connecter": "connect", "deconnecter": "disconnect", "oublier": "remove"}.get(action)
        if not cmd:
            raise ApiError(400, "Action inconnue.", "action")
        r = self.ctl(cmd, mac, timeout=25)
        sortie = propre(r.out + r.err)
        reussi = ("successful" in sortie or "removed" in sortie.lower() or "succeeded" in sortie) and "Failed" not in sortie
        if not reussi and not r.ok:
            m = re.search(r"(?:Failed to \w+|Device \S+ not available)[^\n]*", sortie)
            raise ApiError(502, {"connect": "Connexion impossible : l'appareil est-il allumé et à portée ?",
                                 "disconnect": "Déconnexion impossible.", "remove": "L'appareil n'a pas pu être oublié."}[cmd]
                           + (" (%s)" % m.group(0)[:80] if m else ""), "bluetoothctl")
        self.infos.pop(mac, None)
        if cmd == "remove":
            self.vus.pop(mac, None)
        return True

    def confirmer_appairage(self, mac, accepter):
        """1.0.15 : Justin accepte (ou refuse) explicitement le code à 6 chiffres affiché pendant l'appairage."""
        mac = self._mac(mac)
        if not self.appairage or self.appairage["mac"] != mac or self.appairage["etat"] != "confirmer":
            raise ApiError(409, "Aucune confirmation n'est demandée pour cet appareil.", "confirmer")
        self.confirme = bool(accepter)
        return True

    def donner_pin(self, mac, pin):
        mac = self._mac(mac)
        if not self.appairage or self.appairage["mac"] != mac or self.appairage["etat"] != "pin":
            raise ApiError(409, "Aucun code n'est demandé pour cet appareil.", "pin")
        if not isinstance(pin, str) or not re.match(r"^[0-9]{4,16}$", pin):
            raise ApiError(400, "Le code fait 4 à 16 chiffres.", "pin")
        self.pin = pin
        return True

    def appairer(self, mac):
        with self.lock:
            if self.appairage and self.appairage["etat"] in ("en_cours", "confirmer", "taper", "pin"):
                raise ApiError(409, "Un appairage est déjà en cours.", "occupe")
            nom = next((d["nom"] for d in self.appareils() if d["mac"] == mac), mac)
            self.appairage = {"mac": mac, "nom": nom, "etat": "en_cours", "code": None,
                              "message": "Appairage avec %s…" % nom}
            self.pin = None
            self.confirme = None
        if self.reel:
            threading.Thread(target=self._appairer, args=(mac,), name="ydz-bt-appairage", daemon=True).start()
        else:
            self._appairer(mac)
        return self.appairage

    def _maj(self, **k):
        self.appairage = dict(self.appairage or {}, **k)

    def _attendre(self, s, delai, motifs, cible=None):
        """Lit la session jusqu'à un des motifs (regex) ; gère les questions de l'agent au passage.
        1.0.15 (F-12) : on ne répond QU'aux invites de l'agent (« [agent] … ») ; une invite qui nomme un AUTRE
        appareil que celui qu'on appaire est refusée (« no ») ; on n'autorise jamais un service tout seul (le
        « trust » explicite après l'appairage s'en charge) ; un code à confirmer attend l'accord de Justin."""
        fin = self.horloge() + delai
        while self.horloge() < fin:
            l = s.ligne(min(1.0, max(0.05, fin - self.horloge())))
            if l is None:
                return None, None
            l = propre(l)
            if not l:
                continue
            if not l.lstrip().startswith("[agent]"):     # [CHG]/[NEW]/texte d'un nom d'appareil : jamais une invite
                for cle, rx in motifs:
                    if re.search(rx, l):
                        return cle, l.strip()
                continue
            m_mac = re.search(r"([0-9A-Fa-f]{2}[:_]){5}[0-9A-Fa-f]{2}", l)
            if cible and m_mac and m_mac.group(0).upper().replace("_", ":") != cible:
                s.envoyer("no")                          # invite de l'agent pour un AUTRE appareil : refusée
                continue
            if re.search(r"Authorize service|Accept pairing", l):
                s.envoyer("no")                          # jamais d'autorisation de service automatique (on « trust »)
                continue
            m = re.search(r"Confirm passkey (\d+)|Request confirmation.*?(\d{6})", l)
            if m:
                code = m.group(1) or m.group(2)
                self._maj(etat="confirmer", code=code,
                          message="Vérifie que l'appareil affiche %s, puis accepte ici." % code)
                ok = self._attendre_confirmation()
                s.envoyer("yes" if ok else "no")
                if not ok:
                    return "echec", "Confirmation refusée."
                self._maj(etat="en_cours", message="Code confirmé…")
                continue
            m = re.search(r"Passkey:\s*(\d+)", l)
            if m:
                self._maj(etat="taper", code=m.group(1),
                          message="Tape %s sur l'appareil, puis Entrée." % m.group(1))
                continue
            if re.search(r"Enter (PIN code|passkey)", l):
                self._maj(etat="pin", code=None, message="L'appareil demande un code : saisis-le ici (souvent 0000).")
                attente = self.horloge() + 60
                while self.pin is None and self.horloge() < attente:
                    s.ligne(0.5)
                if self.pin is None:
                    return "echec", "Aucun code saisi."
                s.envoyer(self.pin)
                self._maj(etat="en_cours", message="Code envoyé…")
                continue
            for cle, rx in motifs:
                if re.search(rx, l):
                    return cle, l.strip()
        return "delai", None

    def _attendre_confirmation(self):
        """Attend l'accord explicite de Justin (route /api/bluetooth/confirmer). `auto_confirmer` (simulation et
        tests) répond tout de suite ; sur le vrai système il reste None et l'accord vient de l'interface."""
        if self.auto_confirmer is not None:
            self.confirme = bool(self.auto_confirmer)
            return self.confirme
        attente = self.horloge() + 60
        while self.confirme is None and self.horloge() < attente:
            if self.reel:
                time.sleep(0.2)
            else:
                break
        return bool(self.confirme)

    def _appairer(self, mac):
        s = None
        try:
            s = self.lanceur()
            s.envoyer("pair %s" % mac)
            r, l = self._attendre(s, 45, [("ok", r"Pairing successful|Paired: yes|AlreadyExists"),
                                          ("echec", r"Failed to pair|AuthenticationFailed|AuthenticationCanceled|"
                                                    r"not available|ConnectionAttemptFailed|AuthenticationRejected")], cible=mac)
            if r != "ok":
                msg = {"delai": "L'appareil n'a pas répondu. Mets-le en mode appairage et réessaie."}.get(
                    r, "Appairage refusé%s." % (" (mauvais code ?)" if l and "Authentication" in l else ""))
                self._maj(etat="echec", message=msg)
                return
            s.envoyer("trust %s" % mac)
            self._attendre(s, 6, [("ok", r"trust succeeded|Trusted: yes"), ("echec", r"Failed")], cible=mac)
            self._maj(etat="en_cours", code=None, message="Appairé, connexion…")
            s.envoyer("connect %s" % mac)
            r, _ = self._attendre(s, 20, [("ok", r"Connection successful|Connected: yes"),
                                          ("echec", r"Failed to connect|not available")], cible=mac)
            nom = (self.appairage or {}).get("nom") or mac
            if r == "ok":
                self._maj(etat="ok", message="%s est appairé et connecté." % nom)
            else:
                self._maj(etat="ok", message="%s est appairé. Allume-le pour qu'il se connecte." % nom)
            if self.events:
                self.events.publish("notification", {"niveau": "ok", "texte": "Bluetooth : %s appairé" % nom})
        except Exception as ex:          # jamais de trace : un message clair
            self._maj(etat="echec", message="Appairage impossible (%s)." % str(ex)[:80])
        finally:
            self.infos.pop(mac, None)
            if s:
                try:
                    s.fermer()
                except Exception:
                    pass
