"""Espace visiteurs (1.0.16) : un petit coin public, en lecture seule, pour les amis de Justin.

Deux moitiés étanches, et un flux de données À SENS UNIQUE :

  ydzd (root)                          ydz-visiteur (DynamicUser, aucun privilège)
  -----------                          --------------------------------------------
  Visiteur.ecrire_public()  ---->  /run/ydz/public/etat.json   ---->  ServeurVisiteur (lecture seule)
  (liste blanche de champs)         /run/ydz/public/config.json        127.0.0.1:8090, exposé par
                                                                       `tailscale funnel` sur /g/<secret>

Le serveur visiteur ne connaît NI le hub, NI les nœuds, NI l'authentification : il ne sait que lire deux
fichiers sur le disque et les rendre. Il ne peut donc rien demander à ydzd ni atteindre l'interface d'admin.
Ce qui est publié est strictement la liste blanche de `etat_public()` : jamais de nom d'hôte, d'IP, de MAC,
de SSID, de version, d'inventaire d'appareils, de journal, d'état du PIN ni de nom Tailscale.
"""
import hmac
import json
import os
import re
import secrets
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn, TCPServer
from urllib.parse import unquote, urlsplit

from .core import ApiError

# --- emplacements partagés -------------------------------------------------------------------------
DOSSIER_PUBLIC = "/run/ydz/public"           # écrit par ydzd, lu seul par le serveur visiteur
FICHIER_ETAT = DOSSIER_PUBLIC + "/etat.json"
FICHIER_CONFIG = DOSSIER_PUBLIC + "/config.json"
DOSSIER_COMPTEURS = "/run/ydz-visiteur"      # écrit par le serveur visiteur (compteurs agrégés seulement)
FICHIER_COMPTEURS = DOSSIER_COMPTEURS + "/compteurs.json"

HOTE = "127.0.0.1"
PORT = 8090                                  # jamais 0.0.0.0, jamais un autre port
SECRET_LONGUEUR = 24
RE_SECRET = re.compile(r"^[A-Za-z0-9_-]{%d}$" % SECRET_LONGUEUR)
LABO = "Home Pi Lab"                         # nom « invité », jamais le vrai nom d'hôte

# --- en-têtes de sécurité du serveur visiteur ------------------------------------------------------
CSP = ("default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'")
ENTETES = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
           "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}

# --- limites (anti-abus) ---------------------------------------------------------------------------
MAX_REQUETE = 8 * 1024                        # 8 Ko par requête
DELAI_LECTURE = 5.0                           # 5 s pour lire la requête (slowloris)
FILS_MAX = 16                                 # connexions traitées en même temps
PAR_IP_MIN = 60                               # requêtes/minute par visiteur (dernier saut X-Forwarded-For)
GLOBAL_MIN = 600                              # requêtes/minute en tout
ANNEAU = 200                                  # mémoire courte des dernières visites (horodatages seulement)


# ===================================================================================================
# 1. Liste blanche : ce que les visiteurs ont le droit de voir (construit par ydzd)
# ===================================================================================================
def _texte_propre(s, maxi=120):
    """Garde un texte lisible : retire les caractères de contrôle ET les caractères Unicode
    invisibles ou de direction (catégories Cc/Cf : zéro-largeur, bidi RLO/LRO…), qui servent à
    maquiller une adresse affichée. Borne la longueur. Même rigueur que le `_propre` du hub (1.0.15)."""
    s = "".join(c for c in str(s or "")
                if c == " " or (c >= " " and unicodedata.category(c) not in ("Cc", "Cf")))
    return s.strip()[:maxi]


def _niveau_tour(hub, nodes):
    """État global très grossier : ok / warn / crit, SANS aucun détail (pas de nom, pas de raison)."""
    niveau = "ok"
    for n in nodes:
        if not n.get("connu"):
            continue
        if not n.get("en_ligne"):
            niveau = "warn" if niveau == "ok" else niveau
        if n.get("etat") == "crit":
            niveau = "crit"
        elif n.get("etat") == "warn" and niveau == "ok":
            niveau = "warn"
    try:
        if not hub.fan.status().get("pwm"):
            niveau = "warn" if niveau == "ok" else niveau
    except Exception:
        pass
    return niveau


def etat_public(hub):
    """Instantané PUBLIC, réduit à la liste blanche. Aucune donnée privée n'y entre.

    Autorisé : nom du labo (« Home Pi Lab »), état de la tour (ok/warn/crit, sans détail), uptime,
    température arrondie à 5 °C, et pour les seuls serveurs de jeux {nom d'app générique, jeu, en ligne,
    adresse publique à donner aux amis, joueurs/max, MOTD filtré}.
    Interdit (jamais présent) : noms d'hôtes, IP, MAC, SSID, versions, liste des appareils/apps, journaux,
    état du PIN, noms Tailscale, pseudos des joueurs.
    """
    own = hub.node.status()
    nodes = hub.nodes(own)
    temp = own.get("temp")
    temp5 = int(round(temp / 5.0) * 5) if isinstance(temp, (int, float)) else None
    uptime = own.get("uptime")
    uptime = int(uptime) if isinstance(uptime, (int, float)) else None

    adresses = dict(hub.node.state.get("visiteur_adresses") or {})
    jeux = []
    vus = set()
    mc = hub.minecraft.etat or {}
    for n in nodes:
        if not n.get("en_ligne"):
            continue
        for a in n.get("apps", []):
            app = hub.node.apps.catalog.get(a.get("id")) or {}
            if app.get("categorie") != "Jeux" or a["id"] in vus:
                continue
            vus.add(a["id"])
            info = {"nom": _texte_propre(app.get("nom", "Serveur"), 40),
                    "jeu": _texte_propre(app.get("nom", a["id"]), 40),
                    "en_ligne": a.get("etat") in ("actif", "partiel"),
                    "adresse": _texte_propre(adresses.get(a["id"]), 80) or None,
                    "joueurs": None, "max": None, "motd": None}
            if a["id"] == "minecraft":           # seul serveur dont YDZ connaît l'état en direct
                info["en_ligne"] = bool(mc.get("en_ligne"))
                if mc.get("en_ligne"):
                    info["joueurs"] = int(mc.get("joueurs") or 0)
                    info["max"] = int(mc.get("max") or 0)
                    info["motd"] = _texte_propre(mc.get("motd"), 120) or None
            jeux.append(info)

    return {"labo": LABO, "tour": {"etat": _niveau_tour(hub, nodes)},
            "uptime": uptime, "temperature": temp5, "jeux": jeux, "maj": int(hub.sys.now())}


# ===================================================================================================
# 2. Gestionnaire côté ydzd : écriture du fichier public, funnel, secret, interrupteur d'arrêt
# ===================================================================================================
class Visiteur:
    def __init__(self, hub):
        self.hub = hub
        self.sys = hub.sys
        self.state = hub.node.state
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.funnel_ok = False
        self.erreur = None

    # --- réglages persistants ----------------------------------------------------------------------
    @property
    def actif(self):
        return bool(self.state.get("visiteur_actif"))

    def _secret(self):
        s = self.state.get("visiteur_secret")
        if not s or not RE_SECRET.match(s):
            s = secrets.token_urlsafe(18)[:SECRET_LONGUEUR].ljust(SECRET_LONGUEUR, "0")
            self.state.set("visiteur_secret", s)
        return s

    # --- écriture du fichier public (sens unique) --------------------------------------------------
    def ecrire_public(self):
        """ydzd -> /run/ydz/public/{etat.json,config.json}. Écriture atomique, 0644 (données publiques)."""
        try:
            os.makedirs(DOSSIER_PUBLIC, exist_ok=True)
            os.chmod(DOSSIER_PUBLIC, 0o755)
        except OSError:
            pass
        actif = self.actif
        self.sys.write(FICHIER_ETAT, json.dumps(etat_public(self.hub), ensure_ascii=False), mode=0o644)
        # config.json : seulement ce dont le serveur a besoin pour filtrer (actif + secret courant).
        # Le serveur ne sert JAMAIS ce fichier ; il ne fait que le lire pour valider le préfixe /g/<secret>.
        cfg = {"actif": actif, "secret": self._secret() if actif else None}
        # 0644 : le service visiteur (DynamicUser, uid distinct) doit pouvoir lire ce fichier pour valider
        # le préfixe /g/<secret>. Le secret ne protège que des données publiques, donc sa lecture locale
        # n'ouvre aucun privilège ; le vrai cloisonnement vient de la liste blanche de etat_public.
        self.sys.write(FICHIER_CONFIG, json.dumps(cfg), mode=0o644)

    def _effacer_public(self):
        for f in (FICHIER_ETAT, FICHIER_CONFIG):
            try:
                os.remove(f)
            except OSError:
                pass

    # --- funnel (exposition Internet) --------------------------------------------------------------
    def _funnel(self, actif):
        """Active ou coupe le funnel Tailscale, sur le SEUL port permis (8443 -> 127.0.0.1:8090)."""
        from . import acces
        cmd = acces.commande_funnel(actif)
        # garde-fou : on refuse de lancer tout funnel qui ne serait pas l'unique funnel visiteur permis
        if actif and not acces.funnel_autorise(acces.PORT_FUNNEL, acces.FUNNEL_CIBLE):
            raise ApiError(500, "Configuration de funnel refusée (sécurité).", "funnel_refuse")
        if not self.reel:
            self.funnel_ok = actif
            return True
        r = self.sys.run(cmd, timeout=25)
        if not r.ok:
            self.erreur = (r.err or r.out or "tailscale funnel a échoué").strip().splitlines()[-1][:200] if (r.err or r.out) else "tailscale funnel a échoué"
            return False
        self.erreur = None
        return True

    def _lire_funnel(self):
        if not self.reel:
            return self.funnel_ok
        from . import acces
        r = self.sys.run(["tailscale", "funnel", "status", "--json"], timeout=8)
        return acces.lire_funnel(r.out) if r.ok else False

    def _dispo(self):
        """Tailscale prêt pour un funnel ? Réutilise la vérification de l'accès HTTPS (MagicDNS, certificats)."""
        try:
            self.hub.acces.lire()
            from .acces import raison
            return raison(self.hub.acces.st, self.hub.acces.installe), self.hub.acces.st
        except Exception:
            return "Tailscale ne répond pas sur ce Pi.", None

    def _service(self, demarrer):
        """Démarre/arrête l'unité systemd du serveur visiteur (vrai système seulement)."""
        if not self.reel:
            return
        self.sys.run(["systemctl", "start" if demarrer else "stop", "ydz-visiteur.service"], timeout=15)

    # --- actions -----------------------------------------------------------------------------------
    def regler(self, actif):
        if not isinstance(actif, bool):
            raise ApiError(400, "Champ « actif » invalide (vrai ou faux).", "actif")
        with self.lock:
            if actif:
                raison, _ = self._dispo()
                if raison:
                    raise ApiError(409, raison + " Active d'abord les adresses HTTPS (Réglages > Accès à distance).",
                                   "indispo")
                self._secret()                    # s'assure qu'un secret existe
                self.state.set("visiteur_actif", True)
                self.state.set("visiteur_derniere_activation", int(self.sys.now()))
                self._service(True)
                self.ecrire_public()
                self.funnel_ok = self._funnel(True)
                self.hub.alerte("visiteur", "warn" if self.funnel_ok else "crit",
                                "Espace visiteurs OUVERT : toute personne ayant le lien peut voir tes serveurs."
                                if self.funnel_ok else "Espace visiteurs activé mais le lien public n'a pas pu être créé "
                                "(Tailscale funnel). Vérifie Tailscale.")
            else:
                self.state.set("visiteur_actif", False)
                self._funnel(False)
                self.funnel_ok = False
                self._effacer_public()
                self._service(False)
                self.hub.alerte("visiteur", "info", "Espace visiteurs fermé : le lien public ne répond plus.")
        return self.status_complet()

    def roter(self):
        """Nouveau secret : l'ancien lien meurt aussitôt."""
        with self.lock:
            self.state.set("visiteur_secret", "")
            self._secret()
            if self.actif:
                self.ecrire_public()
            self.hub.alerte("visiteur", "info", "Nouveau lien de l'espace visiteurs : l'ancien ne marche plus.")
        return self.status_complet()

    def couper(self):
        """Interrupteur d'arrêt : coupe le funnel, arrête le service, efface le fichier public."""
        with self.lock:
            self.state.set("visiteur_actif", False)
            self._funnel(False)
            self.funnel_ok = False
            self._effacer_public()
            self._service(False)
            self.hub.alerte("visiteur", "info", "Espace visiteurs coupé (arrêt d'urgence).")
        return self.status()

    def regler_adresse(self, app_id, adresse):
        """Adresse publique d'un serveur de jeu à donner aux amis (saisie par Justin, jamais une IP interne)."""
        from .apps import RE_ID
        if not RE_ID.match(app_id or ""):
            raise ApiError(400, "Application inconnue.", "app")
        adresse = (adresse or "").strip()
        if len(adresse) > 80 or any(c in adresse for c in "\n\r\x00<>\"'"):
            raise ApiError(400, "Adresse invalide.", "adresse")
        m = dict(self.state.get("visiteur_adresses") or {})
        if adresse:
            m[app_id] = adresse
        else:
            m.pop(app_id, None)
        self.state.set("visiteur_adresses", m)
        if self.actif:
            self.ecrire_public()
        return self.status_complet()

    # --- lecture -----------------------------------------------------------------------------------
    def _lien(self):
        """Lien public complet (avec secret) à partager. None si l'accès Tailscale n'est pas prêt."""
        st = (self.hub.acces.st or {})
        nom = st.get("nom_dns")
        if not nom:
            return None
        from .acces import PORT_FUNNEL
        return "https://%s:%d/g/%s" % (nom, PORT_FUNNEL, self._secret())

    def _compteurs(self):
        """Compteurs agrégés écrits par le serveur visiteur (entiers seulement, jamais d'IP)."""
        try:
            d = json.loads(self.sys.read(FICHIER_COMPTEURS, "") or "{}")
            n = int(d.get("recentes_n"))
            return max(0, min(n, 100000))
        except (ValueError, TypeError):
            return None

    def status(self):
        """État pour /api/etat (snapshot) : JAMAIS le secret ni le lien complet.

        `lien_public` est toujours None ici : le vrai lien (avec secret) n'est rendu que par la route
        dédiée /api/visiteur (status_complet), réservée à Justin (écran local ou session), jamais dans un
        instantané qui part sur le flux SSE."""
        raison, _ = self._dispo()
        return {"actif": self.actif, "lien_public": None, "funnel_ok": bool(self.funnel_ok and self.actif),
                "disponible": raison is None, "raison_si_indispo": raison, "erreur": self.erreur,
                "derniere_activation": self.state.get("visiteur_derniere_activation"),
                "visites_recentes_n": self._compteurs(), "adresses": dict(self.state.get("visiteur_adresses") or {})}

    def status_complet(self):
        """État pour la route protégée /api/visiteur et les réponses d'action : inclut le lien avec secret."""
        s = self.status()
        s["lien_public"] = self._lien() if self.actif else None
        return s

    # --- boucle ------------------------------------------------------------------------------------
    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-visiteur", daemon=True).start()

    def _boucle(self):
        self.stop.wait(15)
        tours = 0
        while not self.stop.is_set():
            try:
                if self.actif:
                    self.ecrire_public()
                    self._service(True)
                    etat = self._lire_funnel()
                    if not etat:                  # le funnel est tombé : on le relance
                        etat = self._funnel(True)
                    self.funnel_ok = etat
                elif tours == 0:
                    self._effacer_public()        # au démarrage, rien ne traîne si c'est coupé
            except Exception:
                pass
            tours += 1
            self.stop.wait(10)


# ===================================================================================================
# 3. Le serveur visiteur : processus séparé, bibliothèque standard seule, AUCUN accès à ydzd
#    (il ne sait que lire deux fichiers et les rendre ; il n'importe ni le hub, ni les nœuds, ni l'auth)
# ===================================================================================================
class _Compteurs:
    """Compteurs agrégés et anneau d'horodatages (jamais d'IP sur la carte SD)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.total = 0
        self.anneau = []
        self._ip = {}
        self._global = []
        self._ecrit = 0

    def _fenetre(self, liste, maintenant):
        seuil = maintenant - 60
        liste[:] = [t for t in liste if t > seuil]

    def autorise(self, ip, maintenant):
        """Limite par IP (dernier saut) et budget global, sur une minute glissante."""
        with self.lock:
            g = self._global
            self._fenetre(g, maintenant)
            if len(g) >= GLOBAL_MIN:
                return False
            l = self._ip.setdefault(ip, [])
            self._fenetre(l, maintenant)
            if len(l) >= PAR_IP_MIN:
                return False
            g.append(maintenant)
            l.append(maintenant)
            if len(self._ip) > 512:               # ménage : on oublie les IP sans activité récente
                self._ip = {k: v for k, v in self._ip.items() if v}
            return True

    def visite(self, maintenant):
        with self.lock:
            self.total += 1
            self.anneau.append(maintenant)
            if len(self.anneau) > ANNEAU:
                self.anneau.pop(0)

    def peut_ecrire(self, maintenant):
        with self.lock:
            if maintenant - self._ecrit < 20:
                return None
            self._ecrit = maintenant
            seuil = maintenant - 3600
            n = sum(1 for t in self.anneau if t > seuil)
            return {"total": self.total, "recentes_n": n, "maj": int(maintenant)}


def _ecrire_compteurs(data):
    try:
        os.makedirs(DOSSIER_COMPTEURS, exist_ok=True)
        tmp = FICHIER_COMPTEURS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data))
        os.replace(tmp, FICHIER_COMPTEURS)
    except OSError:
        pass


def _lire_config():
    try:
        with open(FICHIER_CONFIG, "r", encoding="utf-8") as f:
            d = json.load(f)
        return bool(d.get("actif")), (d.get("secret") or "")
    except (OSError, ValueError):
        return False, ""


def _type_mime(chemin):
    if chemin.endswith(".html"):
        return "text/html; charset=utf-8"
    if chemin.endswith(".css"):
        return "text/css; charset=utf-8"
    if chemin.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if chemin.endswith(".json"):
        return "application/json; charset=utf-8"
    if chemin.endswith(".svg"):
        return "image/svg+xml; charset=utf-8"
    return "application/octet-stream"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ydz-visiteur"
    sys_version = ""
    timeout = DELAI_LECTURE

    def log_message(self, fmt, *args):          # jamais de journal par requête (pas d'IP sur la carte SD)
        pass

    def _entetes(self, code, ctype, longueur):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(longueur))
        self.send_header("Cache-Control", "no-store")
        for k, v in ENTETES.items():
            self.send_header(k, v)
        self.end_headers()

    def _repondre(self, code, corps, ctype="text/plain; charset=utf-8"):
        if isinstance(corps, str):
            corps = corps.encode("utf-8")
        self._entetes(code, ctype, len(corps))
        if self.command != "HEAD":
            try:
                self.wfile.write(corps)
            except OSError:
                self.close_connection = True

    def _dernier_saut(self):
        """Dernier saut X-Forwarded-For posé par tailscaled (le relais funnel) : sert à limiter par visiteur."""
        xff = self.headers.get("X-Forwarded-For") or ""
        if xff:
            return xff.split(",")[-1].strip()[:64] or self.client_address[0]
        return self.client_address[0]

    def do_GET(self):
        serveur = self.server
        maintenant = time.time()
        # limites : concurrence bornée, budget par IP et global
        if not serveur.places.acquire(blocking=False):
            return self._repondre(503, "Occupé.")
        try:
            if not serveur.compteurs.autorise(self._dernier_saut(), maintenant):
                return self._repondre(429, "Trop de requêtes.")
            actif, secret = _lire_config()
            # off par défaut, ou secret invalide : tout est introuvable (URL publique indevinable)
            if not actif or not secret or not RE_SECRET.match(secret):
                return self._repondre(404, "Introuvable.")
            chemin = unquote(urlsplit(self.path).path)   # la chaîne de requête est ignorée (aucun paramètre)
            # le chemin doit être /g/<secret>/… ; le secret est comparé en temps constant (pas d'oracle de minutage)
            if not chemin.startswith("/g/"):
                return self._repondre(404, "Introuvable.")
            segment, _, reste = chemin[3:].partition("/")
            if len(segment) != len(secret) or not hmac.compare_digest(segment, secret):
                return self._repondre(404, "Introuvable.")
            serveur.compteurs.visite(maintenant)
            lot = serveur.compteurs.peut_ecrire(maintenant)
            if lot:
                _ecrire_compteurs(lot)
            if reste == "" or reste == "index.html":
                return self._fichier_statique("index.html")
            if reste == "etat.json":
                return self._etat()
            if "/" in reste or ".." in reste or not re.match(r"^[A-Za-z0-9._-]{1,64}$", reste):
                return self._repondre(404, "Introuvable.")
            return self._fichier_statique(reste)
        finally:
            serveur.places.release()

    do_HEAD = do_GET

    def _etat(self):
        try:
            with open(FICHIER_ETAT, "rb") as f:
                data = f.read()
        except OSError:
            data = b"{}"
        self._repondre(200, data, "application/json; charset=utf-8")

    def _fichier_statique(self, nom):
        racine = self.server.ui_dir
        plein = os.path.realpath(os.path.join(racine, nom))
        if not (plein == racine or plein.startswith(racine + os.sep)) or not os.path.isfile(plein):
            return self._repondre(404, "Introuvable.")
        try:
            with open(plein, "rb") as f:
                data = f.read()
        except OSError:
            return self._repondre(404, "Introuvable.")
        self._repondre(200, data, _type_mime(plein))

    def _refus_methode(self):
        self._repondre(405, "Méthode non permise.")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refus_methode

    def finish(self):
        try:
            BaseHTTPRequestHandler.finish(self)
        except OSError:
            pass


class ServeurVisiteur(ThreadingMixIn, TCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, ui_dir, hote=HOTE, port=PORT):
        self.ui_dir = os.path.realpath(ui_dir)
        self.places = threading.BoundedSemaphore(FILS_MAX)
        self.compteurs = _Compteurs()
        TCPServer.__init__(self, (hote, port), _Handler)

    def get_request(self):
        sock, addr = TCPServer.get_request(self)
        sock.settimeout(DELAI_LECTURE)
        return sock, addr

    def handle_error(self, request, client_address):
        import sys as _sys
        if isinstance(_sys.exc_info()[1], (OSError, ValueError)):
            return
        TCPServer.handle_error(self, request, client_address)


def servir(ui_dir=None, hote=HOTE, port=PORT):
    """Point d'entrée du processus séparé (bin/ydz-visiteur). N'ouvre que 127.0.0.1:8090."""
    if ui_dir is None:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ui_dir = os.path.join(base, "ui-visiteur")
    srv = ServeurVisiteur(ui_dir, hote, port)
    print("ydz-visiteur : écoute sur %s:%d, interface %s" % (hote, port, ui_dir), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
