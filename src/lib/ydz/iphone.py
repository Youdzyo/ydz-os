"""iPhone en Bluetooth LE (1.0.20) : AFFICHAGE SEUL des notifications iOS (ANCS) et de la musique (AMS).

Le Pi est client GATT : une fois l'iPhone appairé (Réglages > Général > Bluetooth), iOS expose deux
services Apple que l'on peut écouter :
  - ANCS (Apple Notification Center Service) : la liste des notifications (app, titre, texte) de l'iPhone.
    Le Pi s'abonne à « Notification Source » (les arrivées/retraits) et, pour chaque notification, demande
    ses attributs par « Control Point » ; la réponse arrive sur « Data Source ».
  - AMS (Apple Media Service) : le morceau en cours (artiste/album/titre), l'état (lecture/pause) et, par
    « Remote Command », quelques commandes simples (lecture/pause, suivant, précédent).

RESTRICTION APPLE (impérative) : affichage seul. On NE PEUT PAS répondre à une notification par écrit
depuis le Pi : iOS ne l'autorise pas via ANCS. Seules les commandes média d'AMS sont possibles, et elles
restent derrière l'accès « action » (code PIN ou écran local), jamais ouvertes au réseau anonyme.

Contrainte technique honnête : s'abonner aux caractéristiques GATT exige un vrai lien chiffré/apparié ;
sur Raspberry Pi OS, BlueZ expose GATT par D-Bus. Comme ailleurs dans YDZ (stdlib + sous-processus, pas
de bibliothèque tierce), on s'appuie sur `bluetoothctl` (menu `gatt`, `notify on`) lancé en sous-processus
et on lit sa sortie dans un fil. **Ce lien BLE n'a pas pu être testé sur un vrai iPhone ici** : il est
marqué « expérimental ». Toute la logique décodable (trames ANCS/AMS, file bornée, nettoyage, troncature)
est isolée dans des fonctions pures, testées à fond ; le reste dégrade proprement (jamais de plantage).

Tout ce qui vient de l'iPhone (titres, textes, métadonnées) est traité comme HOSTILE : nettoyé (contrôle,
bidi, caractères invisibles), borné en taille, et le nombre de notifications gardées est limité. Rien n'est
jamais exécuté ni interprété comme du HTML (l'interface échappe tout).
"""
import re
import struct
import threading
import time
import unicodedata

from .core import ApiError

# --- UUID des services/caractéristiques Apple (pour détecter les attributs GATT par bluetoothctl) --------
ANCS_SERVICE = "7905f431-b5ce-4e99-a40f-4b1e122d00d0"
ANCS_NOTIFICATION_SOURCE = "9fbf120d-6301-42d9-8c58-25e699a21dbd"   # notify
ANCS_CONTROL_POINT = "69d1d8f3-45e1-49a8-9821-9bbdfdaad9d9"         # write
ANCS_DATA_SOURCE = "22eac6e9-24d6-4bb5-be44-b36ace7c7bfb"          # notify
AMS_SERVICE = "89d3502b-0f36-433a-8ef4-c502ad55f8dc"
AMS_REMOTE_COMMAND = "9b3c81d8-57b1-4a8a-b8df-0e56f7ca51c2"         # write / notify
AMS_ENTITY_UPDATE = "2f7cabce-808d-411f-9a0c-bb92ba96c102"         # write / notify

# --- limites (garde-fous mémoire + anti-abus d'un iPhone menteur) ----------------------------------------
MAX_NOTIFS = 20              # notifications gardées en mémoire (les plus anciennes tombent)
MAX_TITRE = 100
MAX_SOUSTITRE = 100
MAX_MESSAGE = 240
MAX_APP = 60
MAX_ATTR_BRUT = 1024        # longueur brute d'un attribut qu'on accepte de lire (au-delà : tronqué)
MAX_VALEUR_AMS = 240        # longueur d'une valeur AMS (artiste/titre/album)

RE_CTRL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")
RE_HEX_LIGNE = re.compile(r"(?:[0-9a-fA-F]{2}[ :]){1,}[0-9a-fA-F]{2}|[0-9a-fA-F]{4,}")

# ANCS EventID (Notification Source, octet 0)
ANCS_EVENTS = {0: "ajout", 1: "modif", 2: "retrait"}
# ANCS CategoryID (octet 2)
ANCS_CATEGORIES = {
    0: ("Autre", "bell"), 1: ("Appel entrant", "phone-call"), 2: ("Appel manqué", "phone-missed"),
    3: ("Messagerie", "voicemail"), 4: ("Réseau social", "message-square"), 5: ("Agenda", "calendar"),
    6: ("E-mail", "mail"), 7: ("Actualités", "newspaper"), 8: ("Forme et santé", "heart-pulse"),
    9: ("Finances", "banknote"), 10: ("Localisation", "map-pin"), 11: ("Divertissement", "music"),
}
# ANCS AttributeID (Data Source)
ANCS_ATTR_APP = 0
ANCS_ATTR_TITRE = 1
ANCS_ATTR_SOUSTITRE = 2
ANCS_ATTR_MESSAGE = 3
ANCS_ATTR_DATE = 5

# AMS EntityID / AttributeID (Entity Update)
AMS_ENTITE_LECTEUR = 0
AMS_ENTITE_FILE = 1
AMS_ENTITE_MORCEAU = 2
AMS_LECTEUR_ETAT = 1          # PlaybackInfo : « état,débit,écoulé »
AMS_MORCEAU_ARTISTE = 0
AMS_MORCEAU_ALBUM = 1
AMS_MORCEAU_TITRE = 2
AMS_MORCEAU_DUREE = 3
AMS_ETAT_LECTURE = {0: "pause", 1: "lecture", 2: "rembobinage", 3: "avance"}

# AMS RemoteCommandID (les seules commandes qu'on expose, affichage + télécommande simple)
AMS_COMMANDES = {
    "lecture": 0, "pause": 1, "lecture_pause": 2, "suivant": 3, "precedent": 4,
    "volume_plus": 5, "volume_moins": 6,
}


# =====================================================================================================
# Nettoyage (tout texte reçu de l'iPhone est hostile)
# =====================================================================================================
def propre(s, maxi):
    """Texte lisible et borné : sans caractère de contrôle, invisible ni bidi (comme agenda/hub).
    Jamais de HTML ici : l'interface échappe à l'affichage ; on ne garde qu'un texte court et sûr."""
    s = RE_CTRL.sub("", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) not in ("Cc", "Cf"))
    s = s.strip()
    if len(s) > maxi:
        s = s[:maxi].rstrip() + "…"
    return s


def _depuis_hex(texte):
    """Extrait les octets d'une ligne de sortie bluetoothctl (« Value: 01 00 04 01 2a 00 00 00 » ou un
    bloc hexadécimal collé). Renvoie bytes (vide si rien d'hexa). Tolérant : ignore ce qui n'est pas hexa."""
    if not isinstance(texte, (str, bytes)):
        return b""
    if isinstance(texte, bytes):
        texte = texte.decode("latin-1", "replace")
    paires = re.findall(r"[0-9a-fA-F]{2}", re.sub(r"\((?:[^)]*)\)", "", texte))
    try:
        return bytes(int(p, 16) for p in paires)
    except ValueError:
        return b""


# =====================================================================================================
# ANCS : décodage des trames (fonctions pures, testées)
# =====================================================================================================
def decoder_source(trame):
    """Notification Source (8 octets) : EventID, EventFlags, CategoryID, CategoryCount, UID (uint32 LE).
    Renvoie un dict, ou None si la trame est trop courte/illisible."""
    b = trame if isinstance(trame, (bytes, bytearray)) else _depuis_hex(trame)
    if len(b) < 8:
        return None
    event, flags, cat, cat_n = b[0], b[1], b[2], b[3]
    uid = struct.unpack_from("<I", b, 4)[0]
    cat_nom, cat_icone = ANCS_CATEGORIES.get(cat, ANCS_CATEGORIES[0])
    return {
        "event": event, "event_nom": ANCS_EVENTS.get(event, "autre"), "flags": flags,
        "categorie_id": cat, "categorie": cat_nom, "categorie_icone": cat_icone,
        "categorie_compte": cat_n, "uid": uid,
        "silencieux": bool(flags & 0x02), "important": bool(flags & 0x01), "pre_existante": bool(flags & 0x04),
    }


def decoder_attributs(trame):
    """Data Source — réponse « Get Notification Attributes » :
       CommandID(0) | UID(uint32 LE) | [AttributeID(1) AttributeLength(uint16 LE) Valeur(UTF-8)]*
    Renvoie (uid, {nom: texte}) avec les textes NETTOYÉS et bornés, ou None si illisible.
    Robuste : une longueur absurde (au-delà de ce qui reste, ou > MAX_ATTR_BRUT) est tronquée, pas plantée."""
    b = trame if isinstance(trame, (bytes, bytearray)) else _depuis_hex(trame)
    if len(b) < 5 or b[0] != 0:
        return None
    uid = struct.unpack_from("<I", b, 1)[0]
    i = 5
    out = {}
    noms = {ANCS_ATTR_APP: ("app", MAX_APP), ANCS_ATTR_TITRE: ("titre", MAX_TITRE),
            ANCS_ATTR_SOUSTITRE: ("sous_titre", MAX_SOUSTITRE), ANCS_ATTR_MESSAGE: ("message", MAX_MESSAGE),
            ANCS_ATTR_DATE: ("date", 20)}
    while i + 3 <= len(b):
        attr = b[i]
        longueur = struct.unpack_from("<H", b, i + 1)[0]
        i += 3
        longueur = min(longueur, MAX_ATTR_BRUT, max(0, len(b) - i))
        valeur = b[i:i + longueur]
        i += longueur
        cle = noms.get(attr)
        if cle:
            out[cle[0]] = propre(valeur.decode("utf-8", "replace"), cle[1])
    return uid, out


def commande_attributs(uid, titre=True, sous_titre=False, message=True, app=True):
    """Trame à écrire sur le Control Point ANCS pour demander les attributs d'une notification :
       CommandID(0) | UID(uint32 LE) | [AttributeID (+ MaxLen uint16 LE pour les attributs texte)].
    (Produite côté Pi — pas une donnée hostile — mais testée pour garantir le format attendu par iOS.)"""
    if not isinstance(uid, int) or not (0 <= uid < 2 ** 32):
        raise ValueError("UID invalide")
    out = bytearray()
    out.append(0)
    out += struct.pack("<I", uid)
    if app:
        out.append(ANCS_ATTR_APP)
    if titre:
        out += bytes([ANCS_ATTR_TITRE]) + struct.pack("<H", MAX_TITRE)
    if sous_titre:
        out += bytes([ANCS_ATTR_SOUSTITRE]) + struct.pack("<H", MAX_SOUSTITRE)
    if message:
        out += bytes([ANCS_ATTR_MESSAGE]) + struct.pack("<H", MAX_MESSAGE)
    return bytes(out)


# =====================================================================================================
# AMS : décodage des trames (fonctions pures, testées)
# =====================================================================================================
def decoder_ams(trame):
    """Entity Update AMS : EntityID(1) | AttributeID(1) | EntityUpdateFlags(1) | Valeur(UTF-8).
    Renvoie un dict décrivant la mise à jour (entité, attribut, texte nettoyé), ou None si illisible.
    Pour le lecteur + PlaybackInfo (« état,débit,écoulé »), on extrait l'état de lecture."""
    b = trame if isinstance(trame, (bytes, bytearray)) else _depuis_hex(trame)
    if len(b) < 3:
        return None
    entite, attr, flags = b[0], b[1], b[2]
    valeur = propre(b[3:].decode("utf-8", "replace"), MAX_VALEUR_AMS)
    out = {"entite": entite, "attribut": attr, "tronque": bool(flags & 0x01), "valeur": valeur}
    if entite == AMS_ENTITE_MORCEAU:
        out["champ"] = {AMS_MORCEAU_ARTISTE: "artiste", AMS_MORCEAU_ALBUM: "album",
                        AMS_MORCEAU_TITRE: "titre", AMS_MORCEAU_DUREE: "duree"}.get(attr)
    elif entite == AMS_ENTITE_LECTEUR:
        out["champ"] = {0: "nom", AMS_LECTEUR_ETAT: "etat", 2: "volume"}.get(attr)
        if attr == AMS_LECTEUR_ETAT:
            premier = (valeur.split(",")[0] or "").strip()
            try:
                out["etat"] = AMS_ETAT_LECTURE.get(int(premier))
            except ValueError:
                out["etat"] = None
    return out


def commande_ams(nom):
    """Octet unique à écrire sur Remote Command AMS pour une commande média (lecture, pause, suivant…).
    Lève ValueError pour une commande inconnue."""
    if nom not in AMS_COMMANDES:
        raise ValueError("commande média inconnue : %r" % nom)
    return bytes([AMS_COMMANDES[nom]])


# =====================================================================================================
# File de notifications bornée (état d'affichage, alimentée par les décodeurs)
# =====================================================================================================
class FileNotifs:
    """Notifications iOS en cours, bornées à MAX_NOTIFS. Chaque notification est repérée par son UID ANCS :
    « ajout »/« modif » créent ou mettent à jour, « retrait » enlève. Les attributs (app, titre, message)
    arrivent séparément (Data Source) et complètent la fiche. Quand la file est pleine, la plus ancienne
    tombe. Tout texte est déjà nettoyé par les décodeurs."""

    def __init__(self, maxi=MAX_NOTIFS, horloge=None):
        self.maxi = maxi
        self.horloge = horloge or time.time
        self.ordre = []            # UID du plus ancien au plus récent
        self.par_uid = {}          # uid -> fiche
        self.lock = threading.Lock()

    def appliquer_source(self, src):
        """Applique une décodée de Notification Source. Renvoie l'UID à compléter (ajout/modif) ou None."""
        if not src:
            return None
        uid = src["uid"]
        with self.lock:
            if src["event"] == 2:                        # retrait
                self._retirer(uid)
                return None
            fiche = self.par_uid.get(uid)
            if fiche is None:
                fiche = {"uid": uid, "app": "", "titre": "", "sous_titre": "", "message": "",
                         "recue": int(self.horloge())}
                self.par_uid[uid] = fiche
                self.ordre.append(uid)
                self._borner()
            fiche.update(categorie=src["categorie"], categorie_id=src["categorie_id"],
                         categorie_icone=src["categorie_icone"], silencieux=src["silencieux"],
                         important=src["important"])
            fiche["maj"] = int(self.horloge())
            return uid

    def completer(self, uid, attrs):
        """Pose les attributs (app/titre/message) décodés depuis Data Source sur la fiche de l'UID."""
        if not attrs:
            return
        with self.lock:
            fiche = self.par_uid.get(uid)
            if fiche is None:
                return
            for cle in ("app", "titre", "sous_titre", "message"):
                if attrs.get(cle):
                    fiche[cle] = attrs[cle]
            fiche["maj"] = int(self.horloge())

    def _retirer(self, uid):
        if uid in self.par_uid:
            del self.par_uid[uid]
            try:
                self.ordre.remove(uid)
            except ValueError:
                pass

    def _borner(self):
        while len(self.ordre) > self.maxi:
            self._retirer(self.ordre[0])

    def vider(self):
        with self.lock:
            self.ordre.clear()
            self.par_uid.clear()

    def liste(self):
        """Liste d'affichage, de la plus récente à la plus ancienne. Déjà nettoyée et bornée."""
        with self.lock:
            fiches = [dict(self.par_uid[u]) for u in self.ordre if u in self.par_uid]
        fiches.reverse()
        return fiches


class Media:
    """Dernier état média connu (AMS), alimenté par decoder_ams. Textes déjà nettoyés."""

    def __init__(self):
        self.artiste = ""
        self.album = ""
        self.titre = ""
        self.etat = None           # "lecture" | "pause" | ...
        self.app = ""
        self.maj = None
        self.lock = threading.Lock()

    def appliquer(self, upd, horloge=None):
        if not upd:
            return
        now = int((horloge or time.time)())
        with self.lock:
            if upd.get("champ") == "artiste":
                self.artiste = upd["valeur"]
            elif upd.get("champ") == "album":
                self.album = upd["valeur"]
            elif upd.get("champ") == "titre":
                self.titre = upd["valeur"]
            elif upd.get("champ") == "nom":
                self.app = upd["valeur"]
            elif upd.get("champ") == "etat" and "etat" in upd:
                self.etat = upd["etat"]
            self.maj = now

    def vider(self):
        with self.lock:
            self.artiste = self.album = self.titre = self.app = ""
            self.etat = None
            self.maj = None

    def status(self):
        with self.lock:
            a_contenu = bool(self.titre or self.artiste)
            return {"titre": self.titre, "artiste": self.artiste, "album": self.album,
                    "app": self.app, "etat": self.etat, "en_lecture": self.etat == "lecture",
                    "present": a_contenu, "maj": self.maj}


# =====================================================================================================
# Module de fond du hub : lien BLE (best-effort, expérimental) + statuts + commandes
# =====================================================================================================
# A-20-01 : ancrage \A…\Z (et non ^…$) pour qu'un saut de ligne final ne passe pas la validation.
RE_MAC = re.compile(r"\A([0-9A-F]{2}:){5}[0-9A-F]{2}\Z")
INTERVALLE = 15                 # secondes entre deux tentatives de (ré)abonnement
PERIME_S = 180                  # au-delà, l'état média est considéré « plus à jour »


class IPhone:
    def __init__(self, hub):
        self.hub = hub
        self.sys = hub.sys
        self.state = hub.node.state
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.notifs = FileNotifs(horloge=self.sys.now)
        self.media = Media()
        self.lien = "indisponible"     # indisponible | attente | connecte
        self.raison = None
        self.session = None

    # --- réglages persistants ---------------------------------------------------------------------
    def _reglages(self):
        return dict(self.state.get("iphone") or {})

    @property
    def actif(self):
        return bool(self._reglages().get("actif"))

    def _mac(self):
        """MAC de l'iPhone à écouter : celui réglé, sinon le premier téléphone appairé. Jamais renvoyé au
        réseau (ni dans status, ni dans status_complet) : c'est un identifiant d'appareil privé."""
        m = (self._reglages().get("mac") or "").upper()
        if RE_MAC.match(m):
            return m
        try:
            for d in self.hub.bluetooth.appareils():
                if d.get("type") == "telephone" and d.get("appaire"):
                    # A-20-01 : on revalide la MAC même venue de bluetooth (défense en profondeur)
                    mac = (d.get("mac") or "").upper()
                    if RE_MAC.match(mac):
                        return mac
        except Exception:
            pass
        return None

    def regler(self, b):
        r = self._reglages()
        if "actif" in b:
            if not isinstance(b["actif"], bool):
                raise ApiError(400, "Champ « actif » invalide (vrai ou faux).", "actif")
            r["actif"] = b["actif"]
        if "mac" in b:
            v = b["mac"]
            if v in (None, ""):
                r.pop("mac", None)
            else:
                v = v.upper() if isinstance(v, str) else ""
                if not RE_MAC.match(v):
                    raise ApiError(400, "Adresse Bluetooth invalide.", "mac")
                r["mac"] = v
        self.state.set("iphone", r)
        if not r.get("actif"):
            self.notifs.vider()
            self.media.vider()
            self.lien = "indisponible"
            self.raison = "Coupé."
        return self.status_complet()

    def effacer_notifs(self):
        self.notifs.vider()
        return self.status_complet()

    # --- commandes média (accès « action ») -------------------------------------------------------
    def commande(self, nom):
        """Envoie une commande média AMS (lecture/pause, suivant, précédent). Affichage + télécommande :
        JAMAIS de réponse écrite à une notification (interdit par iOS/ANCS). Dégrade proprement si le lien
        BLE n'est pas établi (jamais de plantage)."""
        try:
            charge = commande_ams(nom)
        except ValueError:
            raise ApiError(400, "Commande média inconnue.", "commande")
        if not self.actif:
            raise ApiError(409, "Active d'abord les notifications iPhone (Réglages > iPhone).", "inactif")
        if not self.reel or self.lien != "connecte":
            raise ApiError(409, "iPhone non connecté en Bluetooth : la commande n'a pas pu être envoyée. "
                                "(Lien BLE expérimental, non vérifié sur matériel.)", "hors_ligne")
        mac = self._mac()
        if not mac:
            raise ApiError(409, "Aucun iPhone appairé.", "hors_ligne")
        ok = self._ecrire_gatt(mac, AMS_SERVICE, AMS_REMOTE_COMMAND, charge)
        if not ok:
            raise ApiError(502, "La commande n'a pas pu être envoyée à l'iPhone.", "bluetooth")
        return {"ok": True, "commande": nom}

    # --- statuts ----------------------------------------------------------------------------------
    def _bluetooth_present(self):
        try:
            return self.hub.bluetooth.present()
        except Exception:
            return False

    def status(self):
        """Pour /api/etat (écran, « lecture ») : état du lien, notifications et musique. JAMAIS la MAC ni
        aucun identifiant d'appareil (données privées : la route « lecture » bloque déjà l'anonyme)."""
        actif = self.actif
        return {
            "actif": actif,
            "experimental": True,
            "bluetooth": self._bluetooth_present(),
            "lien": self.lien if actif else "indisponible",
            "raison": self.raison if actif else None,
            "notifications": self.notifs.liste() if actif else [],
            "notifications_n": len(self.notifs.ordre) if actif else 0,
            "media": self.media.status() if actif else {"present": False},
            "reponse_possible": False,      # rappel : iOS n'autorise pas de répondre depuis le Pi
        }

    def status_complet(self):
        """Pour /api/iphone (« lecture ») : l'état ci-dessus, plus si un iPhone est appairé (sans sa MAC)."""
        s = self.status()
        appaire = False
        try:
            appaire = any(d.get("type") == "telephone" and d.get("appaire")
                          for d in self.hub.bluetooth.appareils())
        except Exception:
            pass
        s["iphone_appaire"] = bool(self._mac()) or appaire
        return s

    # --- boucle (vrai système seulement, best-effort, EXPÉRIMENTAL) -------------------------------
    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-iphone", daemon=True).start()

    def _boucle(self):
        """Tentative best-effort de lien GATT via bluetoothctl. Non vérifiée sur matériel : chaque étape est
        protégée, l'état dégrade proprement en « indisponible »/« attente ». Décodage en Python pur."""
        self.stop.wait(8)
        while not self.stop.is_set():
            try:
                self._tour()
            except Exception as ex:          # jamais de trace, jamais de plantage du démon
                self.lien = "indisponible"
                self.raison = "Lien Bluetooth indisponible (%s)." % str(ex)[:60]
            self.stop.wait(INTERVALLE)

    def _tour(self):
        if not self.actif:
            self.lien = "indisponible"
            self.raison = None
            return
        if not self._bluetooth_present():
            self.lien = "indisponible"
            self.raison = "BlueZ (bluetoothctl) n'est pas installé."
            return
        mac = self._mac()
        if not mac:
            self.lien = "attente"
            self.raison = ("Appaire ton iPhone dans Réglages > Général > Bluetooth, puis, sur l'iPhone, "
                           "autorise les notifications pour « Home Pi Lab ».")
            return
        # Détection des services ANCS/AMS : s'ils n'apparaissent pas, l'iPhone n'a pas (encore) autorisé
        # le partage des notifications. On reste en « attente » avec un message clair.
        attrs = self._gatt_attributs(mac)
        if attrs is None:
            self.lien = "attente"
            self.raison = "iPhone hors de portée ou déconnecté."
            return
        a_ancs = any(ANCS_NOTIFICATION_SOURCE in u for u in attrs)
        if not a_ancs:
            self.lien = "attente"
            self.raison = ("Sur l'iPhone : Réglages > Bluetooth > (i) à côté du Pi, puis active « Afficher "
                           "les notifications ». iOS doit aussi avoir accepté l'appairage.")
            return
        self.lien = "connecte"
        self.raison = None
        self._ecouter(mac, attrs)

    # --- accès GATT par bluetoothctl (sous-processus) : NON testé sur matériel --------------------
    def _gatt_attributs(self, mac):
        """Liste des caractéristiques GATT de l'appareil (bluetoothctl). Renvoie une liste d'UUID (minuscule)
        ou None si l'appareil ne répond pas. Best-effort."""
        r = self.sys.run(["bluetoothctl", "gatt.list-attributes", mac], timeout=10)
        if not r.ok and not r.out:
            return None
        uuids = re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                           r.out or "")
        return [u.lower() for u in uuids]

    def _ecouter(self, mac, attrs):
        """S'abonne (notify on) aux caractéristiques ANCS (Notification Source + Data Source) et AMS (Entity
        Update), lit les trames et les décode. Best-effort, borné dans le temps ; non vérifié sur matériel."""
        from .bluetooth import SessionReelle
        s = SessionReelle()
        try:
            s.envoyer("menu gatt")
            for uuid in (ANCS_NOTIFICATION_SOURCE, ANCS_DATA_SOURCE, AMS_ENTITY_UPDATE):
                if any(uuid in u for u in attrs):
                    s.envoyer("select-attribute %s" % uuid)
                    s.envoyer("notify on")
            fin = self.sys.monotonic() + INTERVALLE
            attr_courant = None
            while self.sys.monotonic() < fin and not self.stop.is_set():
                ligne = s.ligne(1.0)
                if ligne is None:
                    break
                self._traiter_ligne(mac, ligne)
        finally:
            try:
                s.fermer()
            except Exception:
                pass

    def _traiter_ligne(self, mac, ligne):
        """Reçoit une ligne de notify bluetoothctl. On reconnaît la caractéristique source par l'UUID présent
        dans la ligne (bluetoothctl le préfixe), puis on décode les octets. Tolérant : ignore ce qu'il ne
        comprend pas."""
        bas = ligne.lower()
        octets = _depuis_hex(ligne)
        if not octets:
            return
        if ANCS_NOTIFICATION_SOURCE in bas:
            uid = self.notifs.appliquer_source(decoder_source(octets))
            if uid is not None:
                charge = commande_attributs(uid)
                self._ecrire_gatt(mac, ANCS_SERVICE, ANCS_CONTROL_POINT, charge)
        elif ANCS_DATA_SOURCE in bas:
            res = decoder_attributs(octets)
            if res:
                self.notifs.completer(res[0], res[1])
        elif AMS_ENTITY_UPDATE in bas:
            self.media.appliquer(decoder_ams(octets), self.sys.now)

    def _ecrire_gatt(self, mac, service, caract, charge):
        """Écrit `charge` (bytes) sur une caractéristique GATT via bluetoothctl. Best-effort ; non vérifié."""
        try:
            octets = " ".join("0x%02x" % o for o in charge)
            s = ["bluetoothctl", "gatt.select-attribute", caract]
            self.sys.run(s, timeout=6)
            r = self.sys.run(["bluetoothctl", "gatt.write", octets], timeout=6)
            return r.ok
        except Exception:
            return False
