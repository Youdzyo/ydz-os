"""« Perso » (1.0.18) : agenda du jour (iCloud, lecture seule), rappels du jour, Wi-Fi invités (QR code).

Trois petites briques réunies ici car elles servent le quotidien de Justin sur l'écran et dans l'appli :

- Agenda : Justin colle un ou plusieurs liens de calendrier PUBLIC iCloud (« Calendrier public » partagé, soit
  webcal:// soit https://….ics). ydzd télécharge le .ics toutes les ~30 min (HTTPS seulement, l'hôte saisi et
  lui seul, taille bornée, 10 s, aucune redirection suivie), lit les VEVENT avec un petit analyseur maison, et
  expose les événements du jour + le prochain. AUCUN identifiant, AUCUNE connexion CalDAV : seulement un
  abonnement en lecture.
- Rappels : la petite liste de choses à faire du jour, cochable, affichée sur l'écran « maison ».
- WifiInvite : un réseau Wi-Fi « invités » (SSID + mot de passe) transformé en QR code scannable pour que les
  amis rejoignent le Wi-Fi sans taper le mot de passe. Le mot de passe est un secret (jamais renvoyé en clair).

Comme ailleurs dans YDZ : tout ce qui touche au réseau ne tourne que sur le vrai système ; la simulation et les
tests n'envoient rien dehors. Les textes affichés sont nettoyés (contrôle, bidi) comme le `_propre` du hub.
"""
import ipaddress
import re
import socket
import threading
import time
import unicodedata
from urllib.parse import urlsplit

from .core import ApiError
from . import qr


def hote_public(hote):
    """Vrai si TOUTES les adresses IP de l'hôte sont publiques (routables sur Internet).
    1.0.18.1 (audit S-18-01) : empêche qu'un lien d'agenda piégé fasse interroger le réseau local du Pi,
    le loopback ou une adresse de métadonnées (SSRF). Couvre aussi les IP écrites en décimal/hexa/octal,
    169.254.169.254, 0.0.0.0, etc., car getaddrinfo les normalise. En cas de doute (résolution impossible)
    on refuse."""
    if not hote:
        return False
    try:
        infos = socket.getaddrinfo(hote, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    adresses = {ai[4][0] for ai in infos}
    if not adresses:
        return False
    for a in adresses:
        try:
            ip = ipaddress.ip_address(a.split("%")[0])
        except ValueError:
            return False
        if not ip.is_global or ip.is_multicast:
            return False
    return True

# --- limites ------------------------------------------------------------------------------------------
MAX_CALENDRIERS = 5
MAX_ICS = 1024 * 1024               # 1 Mo par calendrier au plus
DELAI_FETCH = 10                    # 10 s
INTERVALLE_FETCH = 30 * 60         # toutes les 30 min
FENETRE_JOURS = 14                 # on développe les récurrences sur 2 semaines
MAX_EVENEMENTS = 200               # garde-fou mémoire par calendrier
RE_URL = re.compile(r"^https://[A-Za-z0-9.\-]+(:\d+)?(/[^\s]*)?$")
RE_CTRL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")
JOURS_SEMAINE = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _propre(s, maxi=200):
    """Texte lisible : sans caractère de contrôle ni caractère Unicode invisible/bidi (comme le hub)."""
    s = RE_CTRL.sub("", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) not in ("Cc", "Cf"))
    return s.strip()[:maxi]


# =====================================================================================================
# Analyseur iCalendar minimal (bibliothèque standard seule)
# =====================================================================================================
def deplier(texte):
    """Déplie les lignes repliées (RFC 5545 : une ligne suivante qui commence par une espace ou une
    tabulation continue la précédente)."""
    lignes = []
    for brute in (texte or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if brute[:1] in (" ", "\t") and lignes:
            lignes[-1] += brute[1:]
        else:
            lignes.append(brute)
    return lignes


def _decouper_ligne(ligne):
    """« NOM;PARAM=val:valeur » -> (nom, {param: val}, valeur). Le premier « : » non entre guillemets sépare."""
    i, entre = 0, False
    while i < len(ligne):
        c = ligne[i]
        if c == '"':
            entre = not entre
        elif c == ":" and not entre:
            break
        i += 1
    tete, valeur = ligne[:i], ligne[i + 1:]
    bouts = tete.split(";")
    nom = bouts[0].upper()
    params = {}
    for p in bouts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return nom, params, valeur


def _detexte(v):
    """Dé-échappe une valeur TEXT iCalendar (\\n, \\, \\; \\\\)."""
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            n = v[i + 1]
            out.append("\n" if n in ("n", "N") else n)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_dt(valeur, params):
    """Valeur DTSTART/DTEND -> dict {annee, mois, jour, heure, minute, journee, utc}. None si illisible."""
    v = valeur.strip()
    journee = params.get("VALUE", "").upper() == "DATE" or (len(v) == 8 and "T" not in v)
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?$", v)
    if not m:
        return None
    a, mo, j = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if journee or m.group(4) is None:
        return {"annee": a, "mois": mo, "jour": j, "heure": 0, "minute": 0, "journee": True, "utc": False}
    return {"annee": a, "mois": mo, "jour": j, "heure": int(m.group(4)), "minute": int(m.group(5)),
            "journee": False, "utc": m.group(7) == "Z"}


def _parse_rrule(valeur):
    """RRULE -> dict. On gère au moins FREQ=DAILY/WEEKLY, INTERVAL, BYDAY, COUNT, UNTIL."""
    d = {}
    for part in valeur.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.upper()] = v
    freq = d.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY"):
        return None
    out = {"freq": freq, "interval": 1, "byday": [], "count": None, "until": None}
    try:
        out["interval"] = max(1, int(d.get("INTERVAL", "1")))
    except ValueError:
        pass
    out["byday"] = [JOURS_SEMAINE[x[-2:]] for x in d.get("BYDAY", "").split(",")
                    if x[-2:] in JOURS_SEMAINE]
    try:
        out["count"] = int(d["COUNT"]) if "COUNT" in d else None
    except ValueError:
        pass
    u = _parse_dt(d.get("UNTIL", ""), {}) if "UNTIL" in d else None
    out["until"] = u
    return out


def parse_ics(texte):
    """Analyse un .ics -> liste d'événements bruts {titre, lieu, debut, fin, rrule}. Ignore ce qui résiste."""
    evs, cur = [], None
    for ligne in deplier(texte):
        if not ligne:
            continue
        nom, params, valeur = _decouper_ligne(ligne)
        if nom == "BEGIN" and valeur.upper() == "VEVENT":
            cur = {"titre": "", "lieu": "", "debut": None, "fin": None, "rrule": None}
        elif nom == "END" and valeur.upper() == "VEVENT":
            if cur is not None and cur["debut"]:
                evs.append(cur)
                if len(evs) >= MAX_EVENEMENTS:
                    break
            cur = None
        elif cur is not None:
            if nom == "SUMMARY":
                cur["titre"] = _propre(_detexte(valeur), 120)
            elif nom == "LOCATION":
                cur["lieu"] = _propre(_detexte(valeur), 80)
            elif nom == "DTSTART":
                cur["debut"] = _parse_dt(valeur, params)
            elif nom == "DTEND":
                cur["fin"] = _parse_dt(valeur, params)
            elif nom == "RRULE":
                cur["rrule"] = _parse_rrule(valeur)
    return evs


# --- développement des occurrences --------------------------------------------------------------------
def _epoch(dt):
    """Un dict date/heure -> epoch. UTC si `utc`, sinon heure locale (mur) du Pi."""
    t = (dt["annee"], dt["mois"], dt["jour"], dt["heure"], dt["minute"], 0, 0, 0, -1)
    if dt.get("utc"):
        import calendar
        return calendar.timegm(t[:6] + (0, 0, 0))
    try:
        return int(time.mktime(t))
    except (OverflowError, ValueError):
        return None


def occurrences(ev, debut_fenetre, fin_fenetre):
    """Epochs de début de l'événement `ev` tombant dans [debut_fenetre, fin_fenetre]. Développe une RRULE
    simple (quotidienne/hebdomadaire). Pour un événement unique, au plus une occurrence."""
    import datetime as _dt
    d0 = ev["debut"]
    base = _epoch(d0)
    if base is None:
        return []
    rr = ev["rrule"]
    if not rr:
        return [base] if debut_fenetre <= base <= fin_fenetre else []
    date0 = _dt.date(d0["annee"], d0["mois"], d0["jour"])
    fin_date = _dt.date(*time.localtime(fin_fenetre)[:3])
    until = None
    if rr["until"]:
        until = _dt.date(rr["until"]["annee"], rr["until"]["mois"], rr["until"]["jour"])
    out, compte = [], 0
    jour = date0
    limite = 0
    while jour <= fin_date and limite < 800:
        limite += 1
        ok = False
        if rr["freq"] == "DAILY":
            delta = (jour - date0).days
            ok = delta >= 0 and delta % rr["interval"] == 0
        else:   # WEEKLY
            jours = rr["byday"] or [date0.weekday()]
            semaine = ((jour - date0).days + date0.weekday()) // 7
            ok = jour.weekday() in jours and semaine % rr["interval"] == 0 and jour >= date0
        if ok:
            compte += 1
            if rr["count"] and compte > rr["count"]:
                break
            if until and jour > until:
                break
            e = _epoch({"annee": jour.year, "mois": jour.month, "jour": jour.day,
                        "heure": d0["heure"], "minute": d0["minute"], "journee": d0["journee"],
                        "utc": False})
            if e is not None and debut_fenetre <= e <= fin_fenetre:
                out.append(e)
        jour += _dt.timedelta(days=1)
    return out


def _hhmm(t):
    return time.strftime("%H:%M", time.localtime(t))


# =====================================================================================================
# Agenda (module de fond du hub)
# =====================================================================================================
class Agenda:
    def __init__(self, hub):
        self.hub = hub
        self.sys = hub.sys
        self.state = hub.node.state
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.caches = {}               # id -> {"evs": [...], "maj": epoch, "erreur": str|None}
        self._alertes_envoyees = set()

    # --- réglages persistants ---------------------------------------------------------------------
    def _calendriers(self):
        return list(self.state.get("agenda_calendriers") or [])

    @property
    def alerte_min(self):
        v = self.state.get("agenda_alerte_min")
        return int(v) if isinstance(v, int) and 0 <= v <= 120 else 0

    # --- actions ----------------------------------------------------------------------------------
    def ajouter(self, url, nom=None):
        url = (url or "").strip()
        if url.lower().startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        if not RE_URL.match(url) or len(url) > 400:
            raise ApiError(400, "Adresse de calendrier invalide : colle un lien iCloud « Calendrier public » "
                                "(il commence par webcal:// ou https:// et finit souvent par .ics).", "url")
        cals = self._calendriers()
        if len(cals) >= MAX_CALENDRIERS:
            raise ApiError(409, "Au plus %d calendriers. Retires-en un d'abord." % MAX_CALENDRIERS, "trop")
        if any(c["url"] == url for c in cals):
            raise ApiError(409, "Ce calendrier est déjà là.", "doublon")
        import secrets
        cid = secrets.token_hex(4)
        hote = urlsplit(url).hostname or ""
        cals.append({"id": cid, "url": url, "hote": hote, "nom": _propre(nom, 40) or hote})
        self.state.set("agenda_calendriers", cals)
        if self.reel:
            threading.Thread(target=self._fetch_un, args=(cals[-1],), daemon=True).start()
        return self.status_complet()

    def retirer(self, cid):
        cals = [c for c in self._calendriers() if c["id"] != cid]
        self.state.set("agenda_calendriers", cals)
        with self.lock:
            self.caches.pop(cid, None)
        return self.status_complet()

    def regler_alerte(self, minutes):
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 120:
            raise ApiError(400, "Alerte « bientôt » : de 0 (coupée) à 120 minutes.", "alerte_min")
        self.state.set("agenda_alerte_min", minutes)
        return self.status_complet()

    # --- téléchargement (vrai système seulement) --------------------------------------------------
    def _fetch_un(self, cal):
        """Télécharge et analyse UN calendrier. HTTPS seulement, l'hôte saisi et lui seul, sans suivre de
        redirection (http_brut), taille bornée."""
        url = cal["url"]
        u = urlsplit(url)
        if u.scheme != "https" or (u.hostname or "") != cal.get("hote"):
            self._poser_cache(cal["id"], None, "Adresse refusée (sécurité).")
            return
        # S-18-01 : l'hôte doit pointer sur une adresse publique (jamais le réseau local, le loopback ni une
        # adresse de métadonnées). Vérifié juste avant la requête pour réduire la fenêtre de « DNS rebinding ».
        if self.reel and not hote_public(u.hostname or ""):
            self._poser_cache(cal["id"], None, "Adresse refusée : le calendrier doit être sur Internet, "
                                               "pas sur le réseau local.")
            return
        try:
            code, _ent, corps = self.sys.http_brut("GET", url, {"Accept": "text/calendar, text/plain"},
                                                   timeout=DELAI_FETCH, max_octets=MAX_ICS)
        except Exception:
            self._poser_cache(cal["id"], None, "Calendrier injoignable.")
            return
        if code == -1:
            self._poser_cache(cal["id"], None, "Calendrier trop volumineux.")
            return
        if code != 200 or not corps:
            # une redirection (3xx) ou une erreur : on ne suit jamais ailleurs
            self._poser_cache(cal["id"], None, "Calendrier indisponible (code %s)." % code)
            return
        try:
            evs = parse_ics(corps.decode("utf-8", "replace"))
        except Exception:
            self._poser_cache(cal["id"], None, "Calendrier illisible.")
            return
        self._poser_cache(cal["id"], evs, None)

    def _poser_cache(self, cid, evs, erreur):
        with self.lock:
            c = self.caches.get(cid) or {}
            if evs is not None:
                c["evs"] = evs
            c["maj"] = int(self.sys.now())
            c["erreur"] = erreur
            self.caches[cid] = c

    # --- construction de la vue -------------------------------------------------------------------
    def _tous_evenements(self):
        with self.lock:
            return [(cid, c.get("evs") or []) for cid, c in self.caches.items()]

    def vue(self):
        """Prochain événement, événements du jour, nombre d'événements à venir dans la fenêtre."""
        now = int(self.sys.now())
        lt = time.localtime(now)
        debut_jour = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
        fin_jour = debut_jour + 86400
        fin_fenetre = debut_jour + FENETRE_JOURS * 86400
        occ = []
        for cid, evs in self._tous_evenements():
            for ev in evs:
                for e in occurrences(ev, debut_jour, fin_fenetre):
                    occ.append((e, ev))
        occ.sort(key=lambda x: x[0])
        aujourdhui, prochain, suivants = [], None, 0
        for e, ev in occ:
            jd = ev["debut"].get("journee")
            if debut_jour <= e < fin_jour:
                aujourdhui.append({"debut": e, "heure": "" if jd else _hhmm(e), "titre": ev["titre"] or "(sans titre)",
                                   "lieu": ev["lieu"] or None, "journee_entiere": bool(jd)})
            elif e >= now:
                suivants += 1
            # prochain = première occurrence encore à venir (y compris aujourd'hui), non journée entière passée
            if prochain is None:
                fin_ev = e + 86400 if jd else e + 3600
                if fin_ev >= now:
                    prochain = {"titre": ev["titre"] or "(sans titre)", "debut": e, "lieu": ev["lieu"] or None,
                                "journee_entiere": bool(jd),
                                "bientot_min": max(0, (e - now) // 60) if not jd else None}
        return {"prochain": prochain, "aujourdhui": aujourdhui[:8], "jours_suivants_n": suivants}

    # --- alerte « bientôt » -----------------------------------------------------------------------
    def _verifier_alertes(self):
        seuil = self.alerte_min
        if not seuil:
            return
        v = self.vue()
        p = v.get("prochain")
        if not p or p.get("journee_entiere") or p.get("bientot_min") is None:
            return
        if 0 < p["bientot_min"] <= seuil:
            cle = (p["titre"], p["debut"])
            if cle not in self._alertes_envoyees:
                self._alertes_envoyees.add(cle)
                self.hub.alerte("agenda", "info", "Bientôt (%d min) : %s à %s"
                                % (p["bientot_min"], p["titre"], _hhmm(p["debut"])))

    # --- statuts ----------------------------------------------------------------------------------
    def status(self):
        """Pour /api/etat (écran, lecture) : jamais les URL complètes des calendriers."""
        v = self.vue()
        with self.lock:
            erreurs = [c.get("erreur") for c in self.caches.values() if c.get("erreur")]
        v.update(actif=bool(self._calendriers()), erreur=erreurs[0] if erreurs else None)
        return v

    def status_complet(self):
        """Pour la route /api/agenda (lecture) et les actions : liste des calendriers (URL comprise : ce
        n'est pas un secret, mais la route reste derrière « lecture »)."""
        s = self.status()
        cals = []
        with self.lock:
            for c in self._calendriers():
                cache = self.caches.get(c["id"]) or {}
                cals.append({"id": c["id"], "nom": c.get("nom"), "hote": c.get("hote"), "url": c.get("url"),
                             "maj": cache.get("maj"), "erreur": cache.get("erreur"),
                             "evenements_n": len(cache.get("evs") or [])})
        s["calendriers"] = cals
        s["alerte_min"] = self.alerte_min
        return s

    # --- boucle -----------------------------------------------------------------------------------
    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-agenda", daemon=True).start()

    def _boucle(self):
        self.stop.wait(6)
        dernier = 0
        while not self.stop.is_set():
            try:
                now = self.sys.monotonic()
                if now - dernier >= INTERVALLE_FETCH or dernier == 0:
                    dernier = now
                    for cal in self._calendriers():
                        if self.stop.is_set():
                            break
                        self._fetch_un(cal)
                self._verifier_alertes()
            except Exception:
                pass
            self.stop.wait(60)


# =====================================================================================================
# Rappels du jour
# =====================================================================================================
MAX_RAPPELS = 20
MAX_TEXTE = 120
RE_TEXTE_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class Rappels:
    def __init__(self, hub):
        self.hub = hub
        self.state = hub.node.state
        self.sys = hub.sys

    def _liste(self):
        return list(self.state.get("rappels") or [])

    def status(self):
        return {"rappels": self._liste()}

    def _valider_texte(self, texte):
        if not isinstance(texte, str):
            raise ApiError(400, "Texte du rappel manquant.", "texte")
        t = _propre(RE_TEXTE_CTRL.sub("", texte).strip(), MAX_TEXTE + 1)
        if not t:
            raise ApiError(400, "Écris le rappel.", "texte")
        if len(t) > MAX_TEXTE:
            raise ApiError(400, "Rappel trop long (%d caractères au plus)." % MAX_TEXTE, "texte")
        return t

    def ajouter(self, texte):
        t = self._valider_texte(texte)
        l = self._liste()
        if len(l) >= MAX_RAPPELS:
            raise ApiError(409, "Trop de rappels (%d au plus) : coches-en ou retires-en." % MAX_RAPPELS, "trop")
        import secrets
        l.append({"id": secrets.token_hex(4), "texte": t, "fait": False, "cree": int(self.sys.now())})
        self.state.set("rappels", l)
        return self.status()

    def basculer(self, rid):
        l = self._liste()
        for r in l:
            if r["id"] == rid:
                r["fait"] = not r.get("fait")
                self.state.set("rappels", l)
                return self.status()
        raise ApiError(404, "Rappel inconnu.", "rappel")

    def retirer(self, rid):
        l = [r for r in self._liste() if r["id"] != rid]
        self.state.set("rappels", l)
        return self.status()

    def vider_faits(self):
        l = [r for r in self._liste() if not r.get("fait")]
        self.state.set("rappels", l)
        return self.status()


# =====================================================================================================
# Wi-Fi invités (QR code)
# =====================================================================================================
RE_SSID = re.compile(r"^.{1,32}$")


class WifiInvite:
    def __init__(self, hub):
        self.hub = hub
        self.state = hub.node.state
        self.sys = hub.sys

    def _reglages(self):
        return dict(self.state.get("wifi_invite") or {})

    @property
    def defini(self):
        r = self._reglages()
        return bool(r.get("ssid")) and (bool(r.get("mdp")) or r.get("securite") == "nopass")

    def regler(self, b):
        r = self._reglages()
        if "ssid" in b:
            v = b["ssid"]
            if v in (None, ""):
                r.pop("ssid", None)
                r.pop("mdp", None)
            else:
                v = _propre(v, 32)
                if not isinstance(b["ssid"], str) or not RE_SSID.match(v) or len(v.encode("utf-8")) > 32:
                    raise ApiError(400, "Nom de réseau (SSID) invalide (1 à 32 caractères).", "ssid")
                r["ssid"] = v
        if "securite" in b:
            if b["securite"] not in ("WPA", "nopass"):
                raise ApiError(400, "Sécurité « WPA » (avec mot de passe) ou « nopass » (ouvert).", "securite")
            r["securite"] = b["securite"]
        if "mdp" in b:
            v = b["mdp"]
            if v in (None, ""):
                r.pop("mdp", None)
            else:
                if not isinstance(v, str) or not 1 <= len(v) <= 63 or RE_TEXTE_CTRL.search(v):
                    raise ApiError(400, "Mot de passe Wi-Fi invalide (1 à 63 caractères, sans caractère de "
                                        "contrôle).", "mdp")
                r["mdp"] = v
                r.setdefault("securite", "WPA")
        if "ecran" in b:
            if not isinstance(b["ecran"], bool):
                raise ApiError(400, "Champ « ecran » invalide.", "ecran")
            r["ecran"] = b["ecran"]
        r.setdefault("securite", "WPA")
        self.state.set("wifi_invite", r)
        return self.status()

    def _charge(self):
        r = self._reglages()
        if not r.get("ssid"):
            return None
        securite = r.get("securite", "WPA")
        if securite != "nopass" and not r.get("mdp"):
            return None
        return qr.charge_wifi(r["ssid"], r.get("mdp", ""), securite)

    def status(self):
        """Pour /api/etat et /api/wifi-invite (lecture) : JAMAIS le mot de passe, JAMAIS le QR (il encode le
        mot de passe). On dit seulement si c'est défini et masqué."""
        r = self._reglages()
        return {"ssid": r.get("ssid"), "securite": r.get("securite", "WPA"), "defini": self.defini,
                "mdp_defini": bool(r.get("mdp")), "mdp_masque": "••••••" if r.get("mdp") else "",
                "ecran": bool(r.get("ecran"))}

    def status_snapshot(self):
        """Dans /api/etat : l'état masqué ci-dessus, plus le QR UNIQUEMENT si Justin a choisi de l'afficher
        sur l'écran HDMI (toujours derrière « lecture » : écran local ou session). Le QR encode le mot de
        passe : il n'apparaît jamais sans ce choix explicite."""
        s = self.status()
        if s["ecran"] and self.defini:
            s["qr_svg"] = self.qr_svg()
        return s

    def qr_svg(self, taille=360):
        charge = self._charge()
        if not charge:
            raise ApiError(409, "Renseigne d'abord le nom du réseau et le mot de passe.", "non_defini")
        return qr.svg(charge, taille=taille)

    def qr(self):
        """Pour la route /api/wifi-invite/qr (lecture) : le SVG du QR. Contient de quoi rejoindre le Wi-Fi,
        donc jamais servi à un anonyme (route « lecture »)."""
        r = self._reglages()
        return {"ssid": r.get("ssid"), "securite": r.get("securite", "WPA"), "svg": self.qr_svg()}
