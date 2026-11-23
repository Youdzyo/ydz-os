"""Hub (FORGE) : découverte et suivi des pairs, agrégation, ordres signés vers
les agents, ventilateur, arrêt propre de la tour."""
import json
import re
import secrets
import threading
from urllib.parse import urlsplit

from .voisins import Voisins
from .maison import Maison
from .agenda import Agenda, Rappels, WifiInvite
from .reseau import Reseau
from .notifs import Notifications, Veilleur
from .sauvegarde import Sauvegarde
from .minecraft import Minecraft
from .activite import Activite
from .historique import Historique
from .acces import Acces
from .visiteur import Visiteur
from .noms import Noms
from .wifi import Wifi
from .bluetooth import Bluetooth
from .iphone import IPhone
from .general import Heure, Son
from .agents import Agents
from .majydz import MajYdz
from . import paquet

from . import auth as authmod
from . import config
from .core import ApiError
from .fan import FanController
from .services import Screen, Wol

OFFLINE_AFTER = 8.0        # secondes sans réponse avant « hors ligne »
PAIR_TTL = 300           # une demande d'appairage vit 5 minutes
PAIR_COOLDOWN = 600      # après un refus, on ne redemande pas pendant 10 minutes
RE_NODE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")


def parse_avahi_browse(text):
    """Sortie de `avahi-browse -rpt _ydz._tcp` -> {nom d'hôte: ip}."""
    out = {}
    for line in (text or "").splitlines():
        p = line.split(";")
        if len(p) >= 9 and p[0] == "=" and p[2] == "IPv4":
            host = p[6].split(".")[0].lower()
            if RE_NODE.match(host):
                out[host] = p[7]
    return out


# --- 1.0.15 : ce qu'un pair raconte est nettoyé avant d'entrer dans /api/etat ------------------------------
MAX_CANDIDATS = 8          # Pi inconnus vus sur le réseau (mDNS) : demandes d'appairage seulement
MAX_PAIRS = 16             # pairs suivis en permanence (noms de ydz.conf et Pi déjà acceptés)
MAX_STATUS = 256 * 1024    # réponse /api/status d'un pair : 256 Ko au plus
RE_TACHE = re.compile(r"^[0-9a-f]{1,16}$")
RE_APP = re.compile(r"^[a-z0-9-]{1,40}$")
REMPLACE = str.maketrans({"<": "‹", ">": "›", '"': "”", "'": "’", "`": "ˋ", "\\": "/"})
RE_CTRL = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
LIMITES_LISTES = {"apps": 32, "taches": 8, "alertes": 8, "disques": 16, "conteneurs": 32, "journal": 60,
                  "charge": 3}


def _propre(v, prof=0, cle=None):
    """Nettoyage générique : chaînes bornées sans caractère de balisage ni de contrôle, nombres finis, listes
    et dictionnaires bornés, profondeur bornée. Les guillemets deviennent typographiques (texte lisible)."""
    if prof > 6:
        return None
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return v if -2**53 < v < 2**53 else None
    if isinstance(v, float):
        return v if v == v and abs(v) < 1e18 else None
    if isinstance(v, str):
        return RE_CTRL.sub("", v[:300]).translate(REMPLACE)
    if isinstance(v, list):
        n = LIMITES_LISTES.get(cle, 64)
        return [_propre(i, prof + 1) for i in v[:n]]
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:80]:
            if not isinstance(k, str) or not re.match(r"^[A-Za-z0-9_.-]{1,40}$", k):
                continue
            out[k] = _propre(x, prof + 1, k)
        return out
    return None


def nettoyer_status(st, nom, addr):
    """1.0.15 (audit F-01/F-09) : garde un /api/status de pair sûr à afficher. Le nom est TOUJOURS celui que le
    hub connaît, l'adresse celle que le hub a résolue lui-même ; tâches et apps aux identifiants valides."""
    if not isinstance(st, dict):
        return None
    out = _propre(st) or {}
    out["v"] = 1
    out["nom"] = nom
    out["ip"] = addr
    taches = []
    for t in out.get("taches") or []:
        if isinstance(t, dict) and isinstance(t.get("id"), str) and RE_TACHE.match(t["id"]) \
                and isinstance(t.get("debut"), (int, float)):
            t["noeud"] = nom
            taches.append(t)
    out["taches"] = taches
    out["apps"] = [a for a in out.get("apps") or [] if isinstance(a, dict) and isinstance(a.get("id"), str)
                   and RE_APP.match(a["id"])]
    out["alertes"] = [a for a in out.get("alertes") or [] if isinstance(a, str)]
    out["disques"] = [d for d in out.get("disques") or [] if isinstance(d, dict) and isinstance(d.get("point"), str)]
    if out.get("etat") not in ("ok", "warn", "crit", "halt"):
        out["etat"] = "warn"
    if out.get("role") is not None and out["role"] not in config.ROLES:
        out["role"] = None
    return out


def status_reduit(st, nom, addr):
    """Ce qu'on garde d'un pair qui ne prouve pas son identité : de quoi l'afficher « non vérifié », rien d'autre."""
    st = st if isinstance(st, dict) else {}
    v = st.get("version")
    return {"v": 1, "nom": nom, "ip": addr, "role": None,
            "version": v if isinstance(v, str) and re.match(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$", v) else None,
            "appaire": st.get("appaire") if isinstance(st.get("appaire"), bool) else None,
            "etat": "warn", "alertes": [], "apps": [], "taches": [], "disques": [], "non_verifie": True}


class Peer:
    def __init__(self, name):
        self.name = name
        self.status = None
        self.seen = None           # monotonic du dernier succès
        self.addr = None
        self.bad_key = False
        self.verifie = False       # 1.0.15 : le pair a prouvé qu'il a la clé de la tour (preuve dans /api/status)
        self.clock = 0             # heure du pair - heure du hub (s), pour l'alerte « horloge décalée » seulement
        self.next_resolve = 0
        self.thread = None


class Peers:
    def __init__(self, sys, conf, self_name, key_fn):
        self.sys, self.conf, self.self_name, self.key_fn = sys, conf, self_name, key_fn
        self.port = conf.int("port")
        self.peers = {}
        self.candidats = {}        # 1.0.15 : nom -> Peer, vus par mDNS, jamais suivis ni affichés comme nœuds
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self._browse_at = 0
        self.on_unpaired = None
        self.ignores = set()       # 1.0.13 : anciens noms d'hôte du hub (forge -> pi-one), nœuds remplacés
        self.connus = lambda: set()  # 1.0.15 : noms déjà acceptés dans le labo (Noms), posé par le Hub
        for n in conf.peers():
            self.add(n, autorise=True)

    def autorise(self, name):
        return name in self.conf.peers() or name in self.connus()

    def add(self, name, addr=None, autorise=False):
        """Ajoute un pair suivi. 1.0.15 : seulement un nom de ydz.conf ou un Pi déjà accepté ; un nom inconnu
        (annonce mDNS) devient au plus un « candidat » qui peut demander l'appairage, rien d'autre."""
        name = name.lower().split(".")[0]
        if name == self.self_name or name in self.ignores or not RE_NODE.match(name):
            return
        if not (autorise or self.autorise(name)):
            return self._candidat(name, addr)
        with self.lock:
            p = self.peers.get(name)
            if p is None:
                if len(self.peers) >= MAX_PAIRS:
                    return
                p = self.peers[name] = self.candidats.pop(name, None) or Peer(name)
            if addr and not p.addr:
                p.addr = addr

    def _candidat(self, name, addr):
        with self.lock:
            c = self.candidats.get(name)
            if c is None:
                if len(self.candidats) >= MAX_CANDIDATS:
                    vieux = min(self.candidats.values(), key=lambda x: x.seen or 0)
                    self.candidats.pop(vieux.name, None)
                c = self.candidats[name] = Peer(name)
            if addr:
                c.addr = addr
        return None

    def sonder_candidats(self):
        """Un coup d'œil (non signé) aux candidats : seul « appaire: false » compte (demande d'appairage)."""
        with self.lock:
            cands = list(self.candidats.values())
        for c in cands:
            if not c.addr:
                continue
            code, st = self.sys.http("GET", "http://%s:%d/api/status" % (c.addr, self.port), timeout=1.5,
                                     max_octets=MAX_STATUS)
            if code == 200 and isinstance(st, dict) and st.get("v") == 1:
                c.status, c.seen = status_reduit(st, c.name, c.addr), self.sys.monotonic()
                if st.get("appaire") is False and self.on_unpaired:
                    self.on_unpaired(c)

    def url(self, peer):
        over = getattr(self.sys, "peer_url", None)
        if over:
            return over(peer.name)
        now = self.sys.monotonic()
        if not peer.addr and now >= peer.next_resolve:
            peer.next_resolve = now + 30
            peer.addr = self.sys.resolve(peer.name + ".local") or self.sys.resolve(peer.name)
            if not peer.addr and self.sys.which("avahi-resolve"):     # libnss-mdns absent ou muet
                r = self.sys.run(["avahi-resolve", "-4", "-n", peer.name + ".local"], timeout=4)
                parts = r.out.split()
                if r.ok and len(parts) >= 2 and parts[1].count(".") == 3:
                    peer.addr = parts[1]
        return "http://%s:%d" % (peer.addr, self.port) if peer.addr else None

    def poll_one(self, peer):
        url = self.url(peer)
        if not url:
            return False
        cle = self.key_fn()
        hdr, n = None, None
        if cle:
            n = authmod.nonce()
            hdr = {"X-YDZ-Sig": authmod.sig_header(cle, "GET", peer.name, "/api/status", self.sys.now(), b"", n)}
        code, st = self.sys.http("GET", url + "/api/status", None, hdr, timeout=1.5, max_octets=MAX_STATUS)
        if code != 200 or not isinstance(st, dict) or st.get("v") != 1:
            if peer.seen is None or self.sys.monotonic() - peer.seen > 30:
                peer.addr = None                      # l'adresse a pu changer (DHCP)
            return False
        adresse = peer.addr or (urlsplit(url).hostname if url else None)
        peer.verifie = bool(n and authmod.verifier_preuve(cle, peer.name, n, st))
        peer.status = nettoyer_status(st, peer.name, adresse) if peer.verifie else status_reduit(st, peer.name, adresse)
        peer.seen = self.sys.monotonic()
        try:
            peer.clock = int(float(st.get("heure", self.sys.now())) - self.sys.now())
        except (TypeError, ValueError):
            peer.clock = 0
        # appairé, mais sans preuve valable : mauvaise clé (ou un imposteur) -> ordres impossibles
        peer.bad_key = st.get("appaire") is True and not peer.verifie
        if st.get("appaire") is False and self.on_unpaired:
            self.on_unpaired(peer)        # jamais d'envoi de clé ici : une demande attend l'accord de l'utilisateur
        return True

    def remove(self, name):
        """Retire un pair (nœud remplacé) : son fil s'arrête au tour suivant."""
        with self.lock:
            p = self.peers.pop(name, None)
        if p:
            p.retire = True
        return p is not None

    def _loop(self, peer):
        while not self.stop.is_set() and not getattr(peer, "retire", False):
            ok = False
            try:
                ok = self.poll_one(peer)
            except Exception:
                pass
            self.stop.wait(2.0 if ok or (peer.seen and self.sys.monotonic() - peer.seen < 60) else 6.0)

    def browse(self):
        """Découverte mDNS des nœuds non listés dans ydz.conf (toutes les 60 s)."""
        now = self.sys.monotonic()
        if now < self._browse_at or not self.sys.which("avahi-browse"):
            return
        self._browse_at = now + 60
        r = self.sys.run(["avahi-browse", "-rpt", "_ydz._tcp"], timeout=8)
        for host, ip in parse_avahi_browse(r.out).items():
            self.add(host, ip)
        try:
            self.sonder_candidats()
        except Exception:
            pass
        self.start()

    def start(self):
        with self.lock:
            for p in self.peers.values():
                if p.thread is None:
                    p.thread = threading.Thread(target=self._loop, args=(p,), name="ydz-pair-" + p.name,
                                                daemon=True)
                    p.thread.start()

    def online(self, peer):
        return peer.seen is not None and self.sys.monotonic() - peer.seen < OFFLINE_AFTER

    def get(self, name):
        return self.peers.get(name)

    def candidat(self, name):
        return self.candidats.get(name)

    def all(self):
        with self.lock:
            return list(self.peers.values())


class Hub:
    def __init__(self, node):
        self.node = node
        s, conf, state = node.sys, node.conf, node.state
        self.sys = s
        if not state.get("cle_tour"):
            state.set("cle_tour", secrets.token_urlsafe(32))
        self.auth = authmod.Auth(s, state)
        self.peers = Peers(s, conf, node.name, lambda: state.get("cle_tour"))
        self.noms = Noms(s, state, node.name)        # 1.0.13 : « Home Pi Lab », « Pi One »…
        self.peers.ignores.update(self.noms.anciens)
        self.peers.connus = lambda: set(self.noms.noeuds())        # 1.0.15 : seuls les Pi acceptés sont suivis
        self.auth.sur_verrou_global = lambda: self.alerte(
            "pin", "warn", "Trop de codes PIN faux sur la tour : connexion par code bloquée 15 minutes.")
        for a in self.noms.anciens:                  # l'ancien nom du hub n'est pas un autre nœud
            self.peers.remove(a)
        self.wifi = Wifi(s, state, node.events)      # 1.0.13 : réglages généraux
        self.bluetooth = Bluetooth(s, node.events)
        self.heure = Heure(s)
        self.son = Son(s)
        self.fan = FanController(s, state, conf.int("ventilo_gpio"), conf.int("ventilo_hz"),
                                 conf.flag("ventilo_inverse"))
        self.wol = Wol(s, conf, state)
        self.screen = Screen(s, conf, state)
        self.relais = []                  # fonctions(alerte) appelées pour chaque alerte (Discord/ntfy : 1.0.11)
        self.activite = Activite(s)       # 1.0.12 : journal d'activité (actions + événements automatiques)
        self.relais.append(self.activite.relayer)
        self.historique = Historique(s)   # 1.0.12 : 7 jours de mesures
        for a in self.noms.anciens:       # 1.0.13 : l'historique de « forge » continue sous « pi-one »
            self.historique.renommer(a, node.name)
        self.acces = Acces(self)          # 1.0.12 : adresses HTTPS dans le tailnet (tailscale serve)
        self.voisins = Voisins(s, state, self.alerte)
        self.maison = Maison(self)
        self.reseau = Reseau(self)
        self.notifs = Notifications(self)
        self.relais.append(self.notifs.relayer)
        self.veilleur = Veilleur(self.alerte, s.now, self.aff)
        self.sauvegarde = Sauvegarde(self)
        self.minecraft = Minecraft(self)
        self.visiteur = Visiteur(self)    # 1.0.16 : espace visiteurs public isolé (off par défaut)
        self.agenda = Agenda(self)        # 1.0.18 : agenda du jour (iCloud, lecture seule)
        self.rappels = Rappels(self)      # 1.0.18 : rappels du jour
        self.wifi_invite = WifiInvite(self)   # 1.0.18 : Wi-Fi invités (QR code)
        self.iphone = IPhone(self)        # 1.0.20 : notifications iPhone + musique en BLE (ANCS/AMS, affichage seul)
        self.agents = Agents(self)        # 1.0.19 : agents assistants + Canal Claude (ntfy signé)
        self.majydz = MajYdz(self)        # 1.0.14 : mises à jour automatiques de YDZ OS (GitHub)
        self.halting = False
        self.pairing = {}                 # nom -> {"ip", "expire", "etat": attente|refuse}
        self.pair_lock = threading.Lock()
        self.auto_pair = False            # simulation seulement
        self.peers.on_unpaired = self.pair_request

    def start(self):
        self.peers.start()
        self.voisins.start()
        self.maison.start()
        self.reseau.start()
        self.sauvegarde.start()
        self.minecraft.start()
        self.acces.start()
        self.visiteur.start()
        self.majydz.start()
        self.agenda.start()
        self.agents.start()
        self.iphone.start()

    def arreter(self):
        """Arrêt propre du démon : l'historique est écrit une dernière fois."""
        self.acces.stop.set()
        self.visiteur.stop.set()
        self.majydz.stop.set()
        self.agenda.stop.set()
        self.iphone.stop.set()
        self.agents.canal.stop.set()
        self.historique.sauver(force=True)

    # --- alertes : notification dans l'interface + relais extérieurs (Discord, ntfy : 1.0.11) -------
    def alerte(self, type_, niveau, texte, ui=True, **details):
        if ui:
            self.node.events.publish("notification", {"niveau": niveau, "texte": texte})
        a = dict(details, type=type_, niveau=niveau, texte=texte, heure=int(self.sys.now()))
        for relais in list(self.relais):
            try:
                relais(a)
            except Exception:             # un relais en panne ne doit jamais gêner le démon
                pass

    def aff(self, hote):
        """Nom d'affichage d'un nœud (« Pi One ») pour les messages."""
        try:
            return self.noms.affiche(hote)
        except Exception:
            return str(hote or "").upper()

    def tick(self, own):
        self.peers.browse()
        temps = {own["nom"]: own.get("temp")}
        for p in self.peers.all():
            if self.peers.online(p):
                temps[p.name] = p.status.get("temp")
        self.fan.tick(temps)
        self.screen.tick()
        nodes = self.nodes(own)
        try:
            self.wifi.annoncer()              # 1.0.13 : fin d'un changement de réseau Wi-Fi
        except Exception:
            pass
        try:
            self.veilleur.observer(nodes)     # changements d'état -> alertes (1.0.11)
        except Exception:
            pass
        try:
            self.historique.observer(nodes, self._extra_historique(nodes))
        except Exception:
            pass
        try:
            self.surveiller_maj_apps(nodes)
        except Exception:
            pass

    def _extra_historique(self, nodes):
        moi = self.node.name
        w = (self.reseau.wifi or {}).get("signal_dbm") if isinstance(self.reseau.wifi, dict) else None
        conso = self.maison.conso(nodes)
        return {(moi, "ventilo"): self.fan.status()["vitesse"], (moi, "watts"): conso.get("watts"), (moi, "wifi"): w}

    def surveiller_maj_apps(self, nodes):
        """Une alerte « maj_apps » par nouvelle empreinte d'image (jamais deux fois pour la même)."""
        vues = dict(self.node.state.get("maj_apps_vues") or {})
        nouvelles, change = [], False
        for n in nodes:
            if not n.get("en_ligne"):
                continue
            for a in n.get("apps", []):
                m = a.get("maj") or {}
                cle = "%s:%s" % (n["nom"], a["id"])
                if m.get("maj_disponible") and m.get("empreinte"):
                    if vues.get(cle) != m["empreinte"]:
                        vues[cle] = m["empreinte"]
                        change = True
                        nouvelles.append(self.node.apps.catalog.get(a["id"], {}).get("nom", a["id"]))
        if change:
            self.node.state.set("maj_apps_vues", vues)
        if nouvelles:
            self.alerte("maj_apps", "info", "Mise à jour disponible : %s" % ", ".join(sorted(nouvelles))
                        if len(nouvelles) > 1 else "Mise à jour disponible pour %s" % nouvelles[0])
        return nouvelles

    def maj_apps(self, nodes):
        """Résumé pour /api/etat : apps qui ont une nouvelle version."""
        out = []
        for n in nodes:
            if n.get("en_ligne"):
                for a in n.get("apps", []):
                    if (a.get("maj") or {}).get("maj_disponible"):
                        out.append({"id": a["id"], "noeud": n["nom"],
                                    "nom": self.node.apps.catalog.get(a["id"], {}).get("nom", a["id"])})
        return out

    def relancer_annulation(self, horo):
        """Après « Annuler ce paquet » : ydzd puis l'écran redémarrent (unités systemd détachées)."""
        if type(self.sys).__name__ == "System":
            paquet.relancer_apres_annulation(horo)
        else:
            self.sys.spawn(["ydz-relance-annulation", horo])

    # --- appairage : le hub ne donne la clé de la tour qu'après un « Accepter » explicite ----------
    def pair_request(self, peer):
        """Appelé quand un pair se présente sans clé. Crée (ou laisse vivre) une demande de 5 min."""
        now = self.sys.monotonic()
        with self.pair_lock:
            e = self.pairing.get(peer.name)
            if e and e["expire"] > now and (e["etat"] == "refuse" or e["ip"] == peer.addr):
                return
            self.pairing[peer.name] = {"ip": peer.addr, "expire": now + PAIR_TTL, "etat": "attente"}
        if self.auto_pair:
            try:
                self.accept_pair(peer.name)
            except ApiError:
                pass
            return
        self.node.events.publish("notification", {
            "niveau": "info", "texte": "%s (%s) demande à rejoindre la tour : Accepter ou Refuser"
                                       % (self.aff(peer.name), peer.addr)})

    def _request(self, name):
        e = self.pairing.get(name)
        if e and e["expire"] > self.sys.monotonic() and e["etat"] == "attente":
            return e
        return None

    def pending(self):
        now = self.sys.monotonic()
        with self.pair_lock:
            # 1.0.15 : « attendu » = nom prévu dans ydz.conf ; sinon l'interface ne s'ouvre pas toute seule
            return [{"nom": k, "ip": e["ip"], "expire_s": int(e["expire"] - now),
                     "attendu": k in self.node.conf.peers()}
                    for k, e in sorted(self.pairing.items()) if self._request(k)]

    def accept_pair(self, name, remplace=None):
        """Accepte un nœud. `remplace` (1.0.13) : nom d'hôte du Pi qu'il remplace (il hérite de son nom
        d'affichage, de son numéro et, si possible, de son nom d'hôte ; l'ancien quitte le labo)."""
        if remplace is not None:
            if not isinstance(remplace, str) or remplace not in self.noms.noeuds() or remplace in (name, self.node.name):
                raise ApiError(400, "Choisis le Pi à remplacer parmi ceux du labo (pas celui-ci).", "remplace")
            vieux = self.peers.get(remplace)
            if vieux and self.peers.online(vieux):
                raise ApiError(409, "%s est encore allumé : éteins-le (ou débranche-le) avant de le remplacer."
                               % self.aff(remplace), "remplace_en_ligne")
        if self.peers.get(name) is None and self.peers.candidat(name) is not None:
            try:                                      # Pi inconnu vu par mDNS : on revérifie maintenant (hors verrou)
                self.peers.sonder_candidats()
            except Exception:
                pass
        with self.pair_lock:
            e = self._request(name)
            if not e:
                raise ApiError(404, "Aucune demande d'appairage en cours pour %s (elle expire après 5 minutes : "
                                    "le nœud la renouvelle tout seul)." % self.aff(name), "appairage_aucune")
            peer = self.peers.get(name)
            candidat = None
            if peer is None:
                candidat = peer = self.peers.candidat(name)
                if candidat is not None and (candidat.seen is None or self.sys.monotonic() - candidat.seen > 15):
                    raise ApiError(503, "%s ne répond plus." % self.aff(name), "hors_ligne")
            if not peer or not peer.status or (candidat is None and not self.peers.online(peer)):
                raise ApiError(503, "%s ne répond plus." % self.aff(name), "hors_ligne")
            if peer.addr != e["ip"] or peer.status.get("appaire") is not False:
                self.pairing.pop(name, None)
                raise ApiError(409, "%s a changé depuis la demande. Rien n'a été envoyé : attends la nouvelle demande."
                               % self.aff(name), "appairage_change")
            body = json.dumps({"cle": self.node.state.get("cle_tour"), "hub": self.node.name}).encode()
            url = self.peers.url(peer) if candidat is None else "http://%s:%d" % (peer.addr, self.peers.port)
            code, _ = self.sys.http("POST", url + "/api/pair", body, timeout=3)
            if code != 200:
                raise ApiError(502, "%s a refusé l'appairage." % self.aff(name), "appairage")
            self.pairing.pop(name, None)
            peer.bad_key = False
            peer.verifie = True          # on vient d'établir la clé avec lui : les ordres signés peuvent partir
        if candidat is not None:                      # accepté : il devient un nœud suivi du labo
            self.noms.assurer([name])
            self.peers.add(name, candidat.addr, autorise=True)
            if type(self.sys).__name__ == "System":
                self.peers.start()
        if remplace:
            return dict(self.remplacer(name, remplace), ok=True, noeud=name)
        nds = self.noms.assurer([name])
        return {"ok": True, "noeud": name, "nom_affiche": nds.get(name, {}).get("nom")}

    def remplacer(self, nouveau, ancien):
        """Le Pi `nouveau` prend la place d'`ancien` : nom d'affichage et numéro tout de suite, puis nom d'hôte
        (le nœud se renomme lui-même et redémarre son démon). L'ancien quitte le labo."""
        vieux = self.peers.get(ancien)
        role = ((vieux.status or {}).get("role") if vieux else None) or config.role_du_nom(ancien)
        herite = self.noms.remplacer(nouveau, ancien)
        self.peers.remove(ancien)
        out = {"remplace": ancien, "nom_affiche": herite.get("nom"), "numero": herite.get("numero"),
               "nom_hote": nouveau, "nom_hote_change": False}
        try:
            self.agent(nouveau, "POST", "/api/agent/nom-hote", {"nom": ancien, "role": role}, timeout=8)
        except ApiError as ex:
            out["a_faire"] = ("Le nouveau Pi garde le nom réseau « %s » (%s). Pour qu'il reprenne « %s », lance "
                              "sur lui : sudo /opt/ydz/bin/ydz-nom-hote %s%s"
                              % (nouveau, ex.message, ancien, ancien, " --role " + role if role else ""))
            self._oublier_dans_conf(ancien)
            self.alerte("noeud", "info", "%s remplace l'ancien Pi (nom réseau inchangé : %s)" % (herite.get("nom"), nouveau))
            return out
        # le nœud redémarre sous l'ancien nom : ses nom et numéro le suivent, on l'attend à ancien.local
        self.noms.deplacer(nouveau, ancien)
        self.peers.remove(nouveau)
        self.peers.add(ancien)
        if type(self.sys).__name__ == "System":
            self.peers.start()
        out.update(nom_hote=ancien, nom_hote_change=True)
        self.alerte("noeud", "info", "%s remplace l'ancien Pi et reprend son nom réseau (%s)" % (herite.get("nom"), ancien))
        return out

    def _oublier_dans_conf(self, hote):
        """Retire un nœud de la liste « noeuds » de ydz.conf (sinon il reviendrait en emplacement libre)."""
        conf = self.node.conf
        reste = [x for x in conf.peers() if x != hote]
        if len(reste) == len(conf.peers()):
            return
        chemin = "/etc/ydz/ydz.conf"
        try:
            self.sys.write(chemin, config.update_conf_text(self.sys.read(chemin, ""), {"noeuds": ",".join(reste)}),
                           mode=0o644)
            conf.vals["noeuds"] = ",".join(reste)
        except Exception:
            pass

    def refuse_pair(self, name):
        with self.pair_lock:
            if not self._request(name):
                raise ApiError(404, "Aucune demande d'appairage en cours pour %s." % self.aff(name), "appairage_aucune")
            self.pairing[name] = {"ip": self.pairing[name]["ip"], "expire": self.sys.monotonic() + PAIR_COOLDOWN,
                                  "etat": "refuse"}
        return {"ok": True, "noeud": name}

    # --- agrégation -------------------------------------------------------
    def nodes(self, own):
        out = [dict(own, en_ligne=True, vu_il_y_a=0, connu=True, cle_invalide=False, verifie=True)]
        now = self.sys.monotonic()
        for p in sorted(self.peers.all(), key=lambda x: x.name):
            base = dict(p.status) if p.status else {"nom": p.name, "role": p.name if p.name in
                                                    ("forge", "vault", "pulse") else None}
            base.update(nom=p.name, en_ligne=self.peers.online(p), connu=p.status is not None,
                        vu_il_y_a=int(now - p.seen) if p.seen is not None else None,
                        cle_invalide=p.bad_key, adresse=p.addr, verifie=p.verifie,
                        horloge_ecart=p.clock if abs(p.clock) > authmod.SIG_SKEW else None)
            out.append(base)
        # 1.0.13 : noms d'affichage (« Pi One »…) ; un nœud connu reçoit le premier numéro libre
        try:
            nds = self.noms.assurer([n["nom"] for n in out if n.get("connu") and n.get("verifie")
                                     and n.get("appaire") is not False])
            prov = self.noms.provisoires([n["nom"] for n in out if n["nom"] not in nds])
            for n in out:
                v = nds.get(n["nom"]) or {}
                n["nom_affiche"] = v.get("nom") or prov.get(n["nom"]) or n["nom"].upper()
                n["numero"] = v.get("numero")
            out[1:] = sorted(out[1:], key=lambda n: (n.get("numero") is None, n.get("numero") or 0, n["nom"]))
        except Exception:
            pass
        return out

    def snapshot(self, own):
        nodes = self.nodes(own)
        alerts, level, tasks = [], "ok", []
        for n in nodes:
            if not n["connu"]:
                continue
            if not n["en_ligne"]:
                alerts.append({"noeud": n["nom"], "niveau": "warn", "texte": "%s ne répond plus" % self.aff(n["nom"])})
                level = "warn" if level == "ok" else level
                continue
            for a in n.get("alertes", []):
                alerts.append({"noeud": n["nom"], "niveau": n.get("etat", "warn"), "texte": a})
            if n.get("etat") == "crit" or (n.get("etat") == "warn" and level == "ok"):
                level = n["etat"]
            if n["cle_invalide"]:
                alerts.append({"noeud": n["nom"], "niveau": "warn",
                               "texte": "%s ne prouve pas qu'il a la clé de la tour : lance « sudo ydz appairer » dessus"
                                        % self.aff(n["nom"])})
            if n.get("horloge_ecart"):
                alerts.append({"noeud": n["nom"], "niveau": "warn",
                               "texte": "L'horloge de %s est décalée de %d s : les ordres signés sont refusés "
                                        "(vérifie sa synchronisation NTP)" % (self.aff(n["nom"]), n["horloge_ecart"])})
            tasks += n.get("taches", [])
        fan = self.fan.status()
        if not fan["pwm"]:
            alerts.append({"noeud": own["nom"], "niveau": "warn",
                           "texte": "Ventilateur non piloté (il tourne à 100 % par sécurité)"})
        tasks.sort(key=lambda t: -t["debut"])
        return {"heure": int(self.sys.now()), "hub": own["nom"], "version": own["version"],
                "tour": {"etat": level, "alertes": alerts, "arret_en_cours": self.halting},
                "noeuds": nodes, "ventilo": fan, "ecran": self.screen.status(),
                "reseau_maison": self.reseau_maison(), "pihole": self.reseau.pihole, "affichage": self.maison.status(nodes),
                "sauvegarde": self.sauvegarde.status(), "minecraft": self.minecraft.etat,
                "taches": tasks[:12], "auth": {"pin_defini": self.auth.pin_set, "pin_court": self.auth.pin_court,
                                               "pin_min": authmod.PIN_MIN, "verrou": self.auth.verrou()},
                "appairage": self.pending(),
                "maj_apps": self.maj_apps(nodes), "acces": self.acces.status(),
                "visiteur": self._visiteur_status(),
                "agenda": self._bloc(self.agenda.status), "rappels": self._bloc(self.rappels.status),
                "wifi_invite": self._bloc(self.wifi_invite.status_snapshot),
                "agents": self._bloc(self.agents.snapshot),
                "iphone": self._bloc(self.iphone.status),
                "labo": self.noms.status([n["nom"] for n in nodes]), "maj_ydz": self._maj_ydz()}

    @staticmethod
    def _bloc(fn):
        """Un bloc de /api/etat qui ne doit jamais casser l'instantané si une lecture échoue."""
        try:
            return fn()
        except Exception:
            return None

    def _visiteur_status(self):
        try:
            return self.visiteur.status()         # jamais le secret : status() ne l'inclut pas
        except Exception:
            return None

    def _maj_ydz(self):
        try:
            return self.majydz.status()
        except Exception:                 # un fichier illisible ne doit jamais casser /api/etat
            return None

    def reseau_maison(self):
        r = self.voisins.status()
        r["debit"] = self.reseau.status_debit()
        r["wifi"] = self.reseau.wifi
        r["controle"] = self.reseau.status_controle()   # 1.0.15 : contrôle réseau horaire
        return r

    # --- ordres vers un agent ----------------------------------------------
    def resolve_node(self, name, default=None):
        name = name or default or self.node.name
        if name == self.node.name:
            return name
        p = self.peers.get(name) if isinstance(name, str) else None
        if not p or not p.status:
            raise ApiError(404, "Nœud « %s » inconnu." % name, "noeud")
        if not self.peers.online(p):
            raise ApiError(503, "%s est hors ligne." % self.aff(name), "hors_ligne")
        if not p.verifie:                 # 1.0.15 : jamais d'ordre signé vers un pair qui n'a pas prouvé son identité
            raise ApiError(502, "%s n'a pas prouvé qu'il a la clé de la tour : sur ce nœud, lance « sudo ydz appairer »."
                           % self.aff(name), "appairage")
        return name

    def agent(self, noeud, method, path, body=None, timeout=20, default=None):
        """Exécute une route d'agent sur `noeud` (appel direct si c'est le hub).
        Retourne le dict de réponse ; lève ApiError avec le message de l'agent."""
        name = self.resolve_node(noeud, default)
        if name == self.node.name:
            return self.node.dispatch_local(method, path, body)
        peer = self.peers.get(name)
        raw = json.dumps(body or {}).encode() if method == "POST" else b""
        # 1.0.15 : signature liée au nœud visé, avec un nonce, à l'heure DU HUB (jamais celle du pair)
        hdr = {"X-YDZ-Sig": authmod.sig_header(self.node.state.get("cle_tour"), method, name, path,
                                               self.sys.now(), raw)}
        code, data = self.sys.http(method, self.peers.url(peer) + path, raw if method == "POST" else None,
                                   hdr, timeout=timeout)
        if code == 0:
            raise ApiError(503, "%s ne répond pas." % self.aff(name), "hors_ligne")
        if code == 401:
            peer.bad_key = True
            raise ApiError(502, "%s refuse les ordres du hub. Sur ce nœud, lance « sudo ydz appairer »."
                           % self.aff(name), "appairage")
        if not isinstance(data, dict):
            raise ApiError(502, "Réponse illisible de %s." % self.aff(name), "agent")
        if code >= 400:
            extra = {k: v for k, v in data.items() if k not in ("erreur", "code")}
            raise ApiError(code, data.get("erreur", "Erreur sur %s." % name), data.get("code", "agent"), **extra)
        peer.bad_key = False
        data.setdefault("noeud", name)
        return data

    # --- arrêt / redémarrage de la tour --------------------------------------
    def tower(self, action):
        """Éteint (ou redémarre) d'abord les autres nœuds, vault en premier
        (ses disques), puis le hub en dernier."""
        if action not in ("eteindre", "redemarrer"):
            raise ApiError(400, "Action inconnue.", "action")
        self.halting = True
        res = []
        order = sorted(self.peers.all(), key=lambda p: (p.name != "vault", p.name))
        for p in order:
            if not self.peers.online(p):
                continue
            try:
                self.agent(p.name, "POST", "/api/" + action, {}, timeout=5)
                res.append({"noeud": p.name, "ok": True})
            except ApiError as ex:
                res.append({"noeud": p.name, "ok": False, "erreur": ex.message})
        self.node.power(action, delay=6 if res else 2)
        res.append({"noeud": self.node.name, "ok": True})
        return res
