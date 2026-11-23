"""Le nœud YDZ : boucle de mesure, badge, et toutes les routes de l'API.

Niveaux d'accès d'une route :
  libre   lecture pour tout le réseau local ;
  action  écran local (127.0.0.1) OU session PIN (hub) OU ordre signé du hub ;
  agent   écran/CLI local OU ordre signé du hub (routes internes /api/agent/…).
"""
import ipaddress
import re
import threading
from urllib.parse import quote

from . import auth as authmod
from . import config, diag
from .apps import Apps, mask_settings
from .badge import BadgeLink
from .core import ApiError, Events, Tasks
from .disque import DisqueSysteme
from .fan import MODES
from .hub import Hub
from .metrics import Collector
from .services import Updates
from .storage import Storage

ROUTES = []


def route(method, pattern, acces="libre", hub=False):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), acces, hub, fn))
        return fn
    return deco


class Req:
    def __init__(self, params, query, body, ctx):
        self.p, self.q, self.b, self.ctx = params, query, body, ctx

    def str(self, key, default=None):
        v = self.b.get(key, default)
        if v is not None and not isinstance(v, str):
            raise ApiError(400, "Champ « %s » invalide." % key, "champ")
        return v


class Node:
    def __init__(self, sys, conf, base, sync_tasks=False, net_only=False):
        self.sys, self.conf, self.base = sys, conf, base
        self.name = sys.hostname()
        self.state = config.State(sys)
        self.role = config.choisir_role(conf, self.name, self.state)
        conf.vals["role"] = self.role     # métriques, CLI : même rôle partout, même après un renommage
        self.net_only = net_only          # --sim-reseau : 127.0.0.1 traité comme le réseau
        self.local = authmod.LocalToken(sys)
        self.rejeu = authmod.AntiRejeu()  # 1.0.15 : nonces déjà vus (anti-rejeu des ordres signés)
        self.events = Events()
        self.tasks = Tasks(sys, self.name, self._task_changed, sync=sync_tasks)
        self.collector = Collector(sys, conf)
        self.badge = BadgeLink(sys, conf, self.state)
        self.apps = Apps(sys, self.state, self.tasks, base + "/apps", self.collector)
        self.storage = Storage(sys, self.state, self.tasks)
        self.disque = DisqueSysteme(sys, self.state, self.tasks, self.apps)   # 1.0.21 : disque système
        self.updates = Updates(sys, conf, self.state, self.tasks, base)
        self.halting = None
        self.lock = threading.Lock()
        self.hub = Hub(self) if self.role == "forge" else None
        self._raw = self.collector.collect()
        self.stop = threading.Event()

    # --- boucle -------------------------------------------------------------
    def status(self):
        st = dict(self._raw)
        st["heure"] = int(self.sys.now())      # horloge fraîche : le hub signe ses ordres avec
        st["badge"] = self.badge.status()
        st["appaire"] = bool(self.state.get("cle_tour"))
        st["apps"] = self.apps.list(st["docker"]["conteneurs"])
        st["taches"] = self.tasks.list()[:8]
        if self.halting:
            st["etat"] = "halt"
        return st

    def tick(self):
        self._raw = self.collector.collect()
        st = self.status()
        self.badge.tick(st, "halt" if self.halting else None)
        if self.hub:
            self.hub.tick(st)
            if self.events.listeners:
                self.events.publish("etat", self.hub.snapshot(st))
        return st

    def run(self):
        self.apps.maj.start()             # 1.0.12 : nouvelles versions des apps, chaque jour vers 05:00
        if self.hub:
            self.hub.start()
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as ex:           # la boucle ne meurt jamais
                print("ydzd : erreur de boucle : %r" % ex, flush=True)
            self.stop.wait(2.0)

    def shutdown(self, system_halting=False):
        """Arrêt du démon. Ventilateur relâché = 100 % (sécurité)."""
        self.stop.set()
        self.apps.maj.stop.set()
        if self.hub:
            try:
                self.hub.arreter()        # 1.0.12 : dernière écriture de l'historique
            except Exception:
                pass
        if system_halting or self.halting:
            self.badge.halt(self.status())
        if self.hub:
            self.hub.fan.pwm.release()

    def _task_changed(self, task):
        self.events.publish("tache", task.as_dict())

    def power(self, action, delay=2):
        if self.halting:
            return
        self.halting = action
        st = self.status()
        self.badge.halt(st)
        nom = self.hub.aff(self.name) if self.hub else self.name.upper()
        self.events.publish("notification", {"niveau": "info", "texte": "%s : %s en cours" % (
            nom, "extinction" if action == "eteindre" else "redémarrage")})
        cmd = ["systemctl", "poweroff" if action == "eteindre" else "reboot"]
        t = threading.Timer(delay, lambda: self.sys.spawn(cmd))
        t.daemon = True
        t.start()

    # --- accès ----------------------------------------------------------------
    def check_access(self, acces, ctx):
        if acces == "libre" or ctx.get("local") or ctx.get("sig"):
            return
        if acces == "lecture" and self.hub:
            # 1.0.15 : lecture de données privées = écran local, signature du hub, ou session PIN ouverte.
            # Une lecture ne prolonge PAS la session (le verrou d'inactivité se referme même en regardant).
            if self.hub.auth.valid(ctx.get("token")):
                return
            if not self.hub.auth.pin_set:
                raise ApiError(403, "Choisis d'abord un code PIN sur l'écran de la tour (Réglages > Code PIN) "
                                    "pour consulter la tour depuis le réseau.", "pin_non_defini")
            raise ApiError(401, "Code PIN demandé.", "pin_requis")
        if acces == "action" and self.hub:
            if self.hub.auth.valid(ctx.get("token"), activite=True):   # une action repousse le verrou d'inactivité
                return
            if not self.hub.auth.pin_set:
                raise ApiError(403, "Aucun code PIN n'est défini. Choisis-en un sur l'écran de la tour "
                                    "(Réglages > Code PIN) pour agir depuis le réseau.", "pin_non_defini")
            raise ApiError(401, "Code PIN demandé.", "pin_requis")
        raise ApiError(401, "Accès refusé : cette route est réservée au hub et à l'écran local.", "signature")

    def dispatch(self, method, path, query, body, ctx):
        """Retourne (code HTTP, dict). 1.0.12 : chaque POST du hub est noté au journal d'activité."""
        code, data = self._dispatch(method, path, query, body, ctx)
        if self.hub and method == "POST" and not (code in (404, 405) and isinstance(data, dict)
                                                  and data.get("code") in ("inconnu", "methode")):
            try:
                self.hub.activite.action(self, path, body, ctx, code, data)
            except Exception:
                pass
        return code, data

    def _dispatch(self, method, path, query, body, ctx):
        found = False
        for m, rx, acces, hub_only, fn in ROUTES:
            mt = rx.match(path)
            if not mt:
                continue
            found = True
            if m != method:
                continue
            try:
                if hub_only and not self.hub:
                    raise ApiError(404, "Ce nœud n'est pas le hub. Adresse-toi au Pi qui affiche l'interface.", "pas_hub")
                self.check_access(acces, ctx)
                res = fn(self, Req(mt.groupdict(), query or {}, body if isinstance(body, dict) else {}, ctx))
                if isinstance(res, tuple):
                    return res
                return 200, res
            except ApiError as ex:
                return ex.status, ex.body()
            except Exception as ex:          # un bug de route ne doit ni tuer le fil HTTP ni fuiter une trace
                print("ydzd : erreur inattendue sur %s %s : %r" % (method, path, ex), flush=True)
                return 500, {"erreur": "Erreur interne du démon. Détail : ydz journal.", "code": "interne"}
        if found:
            return 405, {"erreur": "Méthode non permise.", "code": "methode"}
        return 404, {"erreur": "Route inconnue.", "code": "inconnu"}

    def dispatch_local(self, method, path, body=None):
        q = {}
        if "?" in path:
            from urllib.parse import parse_qsl
            path, qs = path.split("?", 1)
            q = dict(parse_qsl(qs))
        code, data = self.dispatch(method, path, q, body or {}, {"local": True, "client": "interne"})
        if code >= 400:
            extra = {k: v for k, v in data.items() if k not in ("erreur", "code")}
            raise ApiError(code, data.get("erreur", "Erreur."), data.get("code", "erreur"), **extra)
        data.setdefault("noeud", self.name)
        return data

    def hotes_autorises(self):
        """Noms que l'en-tête Host peut porter (anti « DNS rebinding ») et nom MagicDNS du tailnet."""
        noms = {self.name}
        dns = None
        if self.hub:
            try:
                noms |= set(getattr(self.hub.noms, "anciens", []) or [])
                dns = (self.hub.acces.st or {}).get("nom_dns")
            except Exception:
                pass
        return noms, dns


def _task(t):
    return {"ok": True, "tache": t.as_dict()}


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


# =============================================================================
# Routes de tous les nœuds (port 8787)
# =============================================================================
@route("GET", "/api/status")
def r_status(n, r):
    """Statut du nœud. 1.0.15 : complet (avec preuve d'identité) pour le hub signé ou l'écran local ;
    réduit (nom, rôle, version, santé) pour un visiteur anonyme du réseau."""
    plein = n.status()
    nonce = r.ctx.get("sig_nonce")
    if nonce and n.state.get("cle_tour"):          # le hub a prouvé qu'il a la clé : on lui prouve la nôtre
        plein["preuve"] = authmod.preuve_status(n.state.get("cle_tour"), n.name, nonce, plein)
        return plein
    if r.ctx.get("local"):
        return plein
    garde = ("v", "nom", "role", "version", "etat", "temp", "appaire", "hub", "heure")
    return {k: plein.get(k) for k in garde}


@route("GET", "/api/ping")
def r_ping(n, r):
    return {"ydz": 1, "nom": n.name, "role": n.role, "hub": bool(n.hub), "version": config.version()}


@route("POST", "/api/pair")
def r_pair(n, r):
    """Appairage « à la première rencontre » : un nœud sans clé accepte celle
    du premier hub qui se présente depuis le réseau local."""
    if n.hub:
        raise ApiError(409, "Le hub ne s'appaire pas.", "hub")
    if n.state.get("cle_tour"):
        raise ApiError(409, "Ce nœud est déjà appairé. Pour recommencer : « sudo ydz appairer ».", "deja_appaire")
    try:
        ip = ipaddress.ip_address(r.ctx.get("client", "").replace("::ffff:", ""))
        private = ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        private = False
    key = r.str("cle")
    if not private or not key or not re.match(r"^[A-Za-z0-9_-]{32,64}$", key):
        raise ApiError(403, "Appairage refusé.", "appairage")
    n.state.set("cle_tour", key)
    n.state.set("hub", (r.str("hub") or "")[:32])
    return {"ok": True, "nom": n.name}


@route("POST", "/api/ecran/billet", hub=True)
def r_ticket(n, r):
    """Billet à usage unique que le script du kiosque échange contre le cookie « écran local »."""
    if not r.ctx.get("local"):
        raise ApiError(403, "Réservé au kiosque de la tour.", "local")
    return {"billet": n.local.make_billet()}


@route("POST", "/api/agent/nom-hote", "agent")
def a_nom_hote(n, r):
    """1.0.13 : le hub demande à ce nœud de prendre le nom réseau du Pi qu'il remplace."""
    from . import noms
    if n.hub:
        raise ApiError(409, "Le hub se renomme depuis Réglages > Général.", "hub")
    nom = r.str("nom")
    err = noms.valider_hote(nom)
    if err:
        raise ApiError(400, err, "nom")
    role = r.str("role")
    if role is not None and role not in config.ROLES:
        raise ApiError(400, "Rôle inconnu.", "role")
    if nom == n.name:
        return {"ok": True, "nom": nom, "deja": True}
    res = n.sys.run(noms.commande_renommage(n.base, nom, role=role), timeout=15)
    if not res.ok:
        raise ApiError(500, "Renommage impossible (%s)." % (res.err or "").strip()[:120], "systemd")
    return {"ok": True, "nom": nom}


@route("POST", "/api/appairage/oublier", "agent")
def r_unpair(n, r):
    if n.hub:
        raise ApiError(409, "Sur le hub, il n'y a rien à oublier.", "hub")
    n.state.delete("cle_tour")
    return {"ok": True}


@route("POST", "/api/(?P<action>redemarrer|eteindre)", "action")
def r_power(n, r):
    n.power(r.p["action"])
    return {"ok": True, "noeud": n.name, "action": r.p["action"], "delai_s": 2}


# --- routes d'agent ------------------------------------------------------------
@route("GET", "/api/agent/apps", "agent")
def a_apps(n, r):
    return {"apps": n.apps.list(n._raw["docker"]["conteneurs"], secrets_ok=True)}


@route("POST", "/api/agent/apps/(?P<id>[a-z0-9-]+)/(?P<action>installer|demarrer|arreter|redemarrer|"
               "mettre-a-jour|desinstaller|reglages)", "agent")
def a_app_action(n, r):
    a, i = r.p["action"], r.p["id"]
    if a == "installer":
        return _task(n.apps.install(i, r.b.get("reglages"), r.str("emplacement")))
    if a == "reglages":
        return _task(n.apps.configure(i, r.b.get("reglages")))
    if a == "desinstaller":
        return _task(n.apps.uninstall(i, r.b.get("supprimer_donnees") is True))
    return _task({"demarrer": n.apps.start, "arreter": n.apps.stop, "redemarrer": n.apps.restart,
                  "mettre-a-jour": n.apps.update}[a](i))


@route("POST", "/api/agent/apps/maj/(?P<action>verifier|tout)", "agent")
def a_apps_maj(n, r):
    if r.p["action"] == "verifier":
        return _task(n.apps.maj.lancer_verification())
    return _task(n.apps.update_all())


@route("GET", "/api/agent/apps/(?P<id>[a-z0-9-]+)/journal", "agent")
def a_app_logs(n, r):
    return n.apps.logs(r.p["id"], _int(r.q.get("lignes"), 200, 10, 1000))


@route("GET", "/api/agent/stockage", "agent")
def a_storage(n, r):
    ov = n.storage.overview()
    try:                                   # 1.0.21 : répartition du disque système (jamais bloquante)
        ov["systeme"] = n.disque.repartition((n._raw.get("docker") or {}).get("conteneurs"))
    except Exception:
        ov["systeme"] = None
    return ov


@route("POST", "/api/agent/stockage/liberer/apercu", "agent")
def a_liberer_apercu(n, r):               # 1.0.21 : aperçu AVANT confirmation (rien n'est effacé)
    return n.disque.apercu_liberation(r.b.get("garder"))


@route("POST", "/api/agent/stockage/liberer", "agent")
def a_liberer(n, r):                      # 1.0.21 : exécute la libération (niveau action)
    return _task(n.disque.liberer(r.b.get("postes"), r.b.get("garder")))


@route("POST", "/api/agent/stockage/(?P<kind>formater|miroir)/preparer", "agent")
def a_storage_prepare(n, r):
    mirror = r.p["kind"] == "miroir"
    paths = r.b.get("disques") if mirror else [r.b.get("disque")]
    return n.storage.prepare(paths, r.b.get("nom"), mirror)


@route("POST", "/api/agent/stockage/confirmer", "agent")
def a_storage_confirm(n, r):
    return _task(n.storage.confirm(r.b.get("jeton"), r.b.get("phrase")))


@route("POST", "/api/agent/stockage/remonter", "agent")
def a_storage_remount(n, r):
    return dict(n.storage.remount(r.b.get("disque")), ok=True)


@route("POST", "/api/agent/stockage/partages", "agent")
def a_share_add(n, r):
    return {"ok": True, "partage": n.storage.add_share(r.b.get("nom"), r.b.get("disque"),
                                                       r.b.get("acces", "prive"))}


@route("POST", "/api/agent/stockage/partages/supprimer", "agent")
def a_share_del(n, r):
    n.storage.remove_share(r.b.get("nom"))
    return {"ok": True}


@route("POST", "/api/agent/stockage/samba/mot-de-passe", "agent")
def a_smb_pw(n, r):
    n.storage.set_password(r.b.get("mot_de_passe"))
    return {"ok": True}


@route("GET", "/api/agent/maj", "agent")
def a_maj(n, r):
    return n.updates.status()


@route("POST", "/api/agent/maj/verifier", "agent")
def a_maj_check(n, r):
    return _task(n.updates.check())


@route("POST", "/api/agent/maj/appliquer", "agent")
def a_maj_apply(n, r):
    n.updates.apply()
    return {"ok": True}


@route("POST", "/api/agent/badge", "agent")
def a_badge(n, r):
    b = r.b
    if "luminosite" in b:
        if not isinstance(b["luminosite"], int) or isinstance(b["luminosite"], bool) or not 0 <= b["luminosite"] <= 100:
            raise ApiError(400, "La luminosité va de 0 à 100.", "luminosite")
        n.state.set("badge_lum", b["luminosite"])
    if "accent" in b:
        if not isinstance(b["accent"], str) or not re.match(r"^#?[0-9A-Fa-f]{6}$", b["accent"]):
            raise ApiError(400, "Couleur attendue : RRGGBB.", "accent")
        n.state.set("accent", b["accent"].lstrip("#").upper())
    if "retourne" in b:
        n.state.set("badge_rot", 1 if b["retourne"] else 0)
    return {"ok": True, "badge": n.badge.status()}


@route("GET", "/api/agent/taches/(?P<id>[0-9a-f]+)", "agent")
def a_task(n, r):
    t = n.tasks.get(r.p["id"])
    if not t:
        raise ApiError(404, "Tâche inconnue (trop ancienne ?).", "tache")
    return {"tache": t.as_dict(full=True)}


@route("GET", "/api/agent/diagnostic", "agent")
def a_diag(n, r):
    return {"noeud": n.name, "controles": diag.run(n)}


# =============================================================================
# Routes du hub (ports 80 et 8787 de forge)
# =============================================================================
@route("GET", "/api/etat", "lecture", hub=True)
def h_etat(n, r):
    return n.hub.snapshot(n.status())


@route("GET", "/api/noeuds", "lecture", hub=True)
def h_nodes(n, r):
    return {"noeuds": n.hub.nodes(n.status())}


@route("GET", "/api/noeuds/(?P<nom>[a-z0-9-]+)", "lecture", hub=True)
def h_node(n, r):
    for x in n.hub.nodes(n.status()):
        if x["nom"] == r.p["nom"]:
            return x
    raise ApiError(404, "Nœud inconnu.", "noeud")


@route("POST", "/api/noeuds/(?P<nom>[a-z0-9-]+)/(?P<action>redemarrer|eteindre)", "action", hub=True)
def h_node_power(n, r):
    return n.hub.agent(r.p["nom"], "POST", "/api/" + r.p["action"], {}, timeout=5)


@route("POST", "/api/tour/(?P<action>redemarrer|eteindre)", "action", hub=True)
def h_tower(n, r):
    return {"ok": True, "action": r.p["action"], "noeuds": n.hub.tower(r.p["action"])}


@route("GET", "/api/systeme", "lecture", hub=True)
def h_system(n, r):
    c = n.conf
    return {"nom": n.name, "role": n.role, "version": config.version(), "modele": n.collector.model,
            "config": {k: c.get(k) for k in ("role", "noeuds", "port", "port_web", "kiosk", "rotation",
                                             "ventilo_gpio", "ventilo_hz", "badge", "accent", "docker",
                                             "maj_auto")},
            "erreurs_config": c.errors, "erreurs_catalogue": n.apps.errors, "simulation": bool(n.sys.sim)}


# --- appairage des nœuds (confirmation explicite) -------------------------------------
@route("GET", "/api/appairage", hub=True)
def h_pairing(n, r):
    return {"demandes": n.hub.pending()}


@route("POST", "/api/appairage/(?P<nom>[a-z0-9-]+)/(?P<action>accepter|refuser)", "action", hub=True)
def h_pairing_answer(n, r):
    if r.p["action"] == "accepter":
        return n.hub.accept_pair(r.p["nom"], r.b.get("remplace") or None)   # 1.0.13 : « Remplace Pi X »
    return n.hub.refuse_pair(r.p["nom"])


# --- authentification ------------------------------------------------------------
@route("GET", "/api/auth", hub=True)
def h_auth(n, r):
    a = n.hub.auth
    tok = r.ctx.get("token")
    return {"pin_defini": a.pin_set, "pin_court": a.pin_court, "pin_min": authmod.PIN_MIN,
            "local": bool(r.ctx.get("local")),
            "connecte": bool(r.ctx.get("local")) or a.valid(tok),
            "reste_s": a.reste(tok) if a.valid(tok) else 0, "verrou": a.verrou(),
            "attente_s": a.attente(r.ctx.get("client"))}


@route("POST", "/api/auth/verrou", "action", hub=True)
def h_auth_verrou(n, r):
    """Réglage du verrouillage automatique (minutes d'inactivité, minutes en arrière-plan)."""
    try:
        v = n.hub.auth.regler_verrou(r.b.get("inactivite_min"), r.b.get("fond_min"))
    except ValueError as ex:
        raise ApiError(400, str(ex), "verrou")
    return {"ok": True, "verrou": v}


@route("POST", "/api/auth/deconnexion-partout", "action", hub=True)
def h_logout_all(n, r):
    """Ferme toutes les sessions PIN ouvertes (sur tous les appareils)."""
    n.hub.auth.logout_partout()
    return {"ok": True}


@route("POST", "/api/auth/connexion", hub=True)
def h_login(n, r):
    tok, msg, wait = n.hub.auth.login(r.b.get("pin"), r.ctx.get("client"))
    if tok:
        return {"ok": True, "jeton": tok, "expire_s": authmod.SESSION_TTL}
    if wait:
        raise ApiError(429, msg, "trop_d_essais", attente_s=wait)
    raise ApiError(401 if n.hub.auth.pin_set else 403, msg,
                   "pin_incorrect" if n.hub.auth.pin_set else "pin_non_defini", attente_s=0)


@route("POST", "/api/auth/deconnexion", hub=True)
def h_logout(n, r):
    n.hub.auth.logout(r.ctx.get("token"))
    return {"ok": True}


@route("POST", "/api/auth/pin", hub=True)
def h_set_pin(n, r):
    a = n.hub.auth
    if not r.ctx.get("local"):
        if not a.pin_set:
            raise ApiError(403, "Le premier code PIN se choisit sur l'écran de la tour.", "pin_non_defini")
        if not a.valid(r.ctx.get("token")):
            raise ApiError(401, "Code PIN demandé.", "pin_requis")
        tok, msg, wait = a.login(r.b.get("ancien"), r.ctx.get("client"))
        if not tok:
            raise ApiError(429 if wait else 401, "Ancien code incorrect." if not wait else msg,
                           "trop_d_essais" if wait else "pin_incorrect", attente_s=wait)
    try:
        a.set_pin(r.b.get("pin"))
    except ValueError as ex:
        raise ApiError(400, str(ex), "pin_invalide")
    return {"ok": True}


# --- ventilateur -------------------------------------------------------------------
@route("GET", "/api/ventilo", hub=True)
def h_fan(n, r):
    return n.hub.fan.status()


@route("POST", "/api/ventilo", "action", hub=True)
def h_fan_set(n, r):
    if r.b.get("mode") not in MODES:
        raise ApiError(400, "Mode inconnu (silence, equilibre, performance).", "mode")
    n.hub.fan.set_mode(r.b["mode"])
    return {"ok": True, "ventilo": n.hub.fan.status()}


@route("POST", "/api/ventilo/test", "action", hub=True)
def h_fan_test(n, r):
    n.hub.fan.test(_int(r.b.get("secondes"), 5, 1, 30))
    return {"ok": True}


# --- applications --------------------------------------------------------------------
def _installations(n, app_id=None):
    out = {}
    for node in n.hub.nodes(n.status()):
        if not node.get("en_ligne"):
            continue
        for a in node.get("apps", []):
            if app_id and a["id"] != app_id:
                continue
            app = n.apps.catalog.get(a["id"], {})
            web = app.get("web")
            url = None
            if web and node.get("ip"):
                url = "%s://%s:%d%s" % (web.get("schema", "http"), node["ip"], web["port"], web.get("chemin", "/"))
            url_https = n.hub.acces.url_app(a["id"], node["nom"]) if url else None
            out.setdefault(a["id"], []).append(dict(a, noeud=node["nom"], url=url, url_https=url_https))
    return out


def _app_nodes(n):
    out = []
    for node in n.hub.nodes(n.status()):
        if not node.get("connu"):
            continue
        ram = node.get("ram", {})
        out.append({"nom": node["nom"], "role": node.get("role"), "en_ligne": node["en_ligne"],
                    "docker": bool(node.get("docker", {}).get("dispo")),
                    "ram_total": ram.get("total"), "ram_libre": (ram.get("total", 0) - ram.get("utilise", 0)),
                    "emplacements": [d["point"] for d in node.get("disques", [])
                                     if d["point"].startswith("/srv/ydz/")]})
    return out


@route("GET", "/api/apps", "lecture", hub=True)
def h_apps(n, r):
    inst = _installations(n)
    cat = [dict(a, installations=inst.get(a["id"], [])) for a in
           sorted(n.apps.catalog.values(), key=lambda a: (a["categorie"], a["nom"]))]
    return {"catalogue": cat, "noeuds": _app_nodes(n), "erreurs": n.apps.errors}


# --- mises à jour des apps (1.0.12) ---------------------------------------------------------------
@route("POST", "/api/apps/maj/(?P<action>verifier|tout)", "action", hub=True)
def h_apps_maj(n, r):
    """Sur chaque nœud en ligne qui a des apps : recherche (verifier) ou mise à jour de tout (tout)."""
    res = []
    for node in n.hub.nodes(n.status()):
        if not node.get("en_ligne") or not node.get("apps"):
            continue
        if r.p["action"] == "tout" and not any((a.get("maj") or {}).get("maj_disponible") for a in node["apps"]):
            continue
        try:
            res.append(dict(n.hub.agent(node["nom"], "POST", "/api/agent/apps/maj/" + r.p["action"], {}),
                            noeud=node["nom"]))
        except ApiError as ex:
            res.append({"noeud": node["nom"], "ok": False, "erreur": ex.message})
    if not res:
        raise ApiError(409, "Aucune app à %s." % ("vérifier" if r.p["action"] == "verifier" else "mettre à jour"),
                       "rien_a_faire")
    return {"ok": all(x.get("ok") for x in res), "noeuds": res}


@route("GET", "/api/apps/(?P<id>[a-z0-9-]+)", "lecture", hub=True)
def h_app(n, r):
    app = n.apps.catalog.get(r.p["id"])
    if not app:
        raise ApiError(404, "Application inconnue.", "app_inconnue")
    inst = _installations(n, app["id"]).get(app["id"], [])
    if r.ctx.get("local") or n.hub.auth.valid(r.ctx.get("token")):
        for i in inst:                    # réglages en clair pour qui a le droit d'agir
            try:
                full = n.hub.agent(i["noeud"], "GET", "/api/agent/apps")["apps"]
                i["reglages"] = next((x["reglages"] for x in full if x["id"] == app["id"]), i["reglages"])
            except ApiError:
                pass
    return dict(app, installations=inst, noeuds=_app_nodes(n))


@route("POST", "/api/apps/(?P<id>[a-z0-9-]+)/(?P<action>installer|demarrer|arreter|redemarrer|"
               "mettre-a-jour|desinstaller|reglages)", "action", hub=True)
def h_app_action(n, r):
    app = n.apps.catalog.get(r.p["id"], {})
    body = {k: r.b[k] for k in ("reglages", "emplacement", "supprimer_donnees") if k in r.b}
    return n.hub.agent(r.str("noeud"), "POST", "/api/agent/apps/%s/%s" % (r.p["id"], r.p["action"]), body,
                       default=app.get("noeud_conseille") if r.p["action"] == "installer" and
                       n.hub.peers.get(app.get("noeud_conseille") or "") and
                       n.hub.peers.online(n.hub.peers.get(app["noeud_conseille"])) else None)


@route("GET", "/api/apps/(?P<id>[a-z0-9-]+)/journal", "action", hub=True)
def h_app_logs(n, r):
    return n.hub.agent(r.q.get("noeud"), "GET", "/api/agent/apps/%s/journal?lignes=%d"
                       % (r.p["id"], _int(r.q.get("lignes"), 200, 10, 1000)))


# --- stockage --------------------------------------------------------------------------
def _storage_node(n, name):
    if name:
        return name
    v = n.hub.peers.get("vault")
    return "vault" if v and n.hub.peers.online(v) else n.name


@route("GET", "/api/stockage", "lecture", hub=True)
def h_storage(n, r):
    return n.hub.agent(_storage_node(n, r.q.get("noeud")), "GET", "/api/agent/stockage")


@route("POST", "/api/stockage/(?P<sub>formater/preparer|miroir/preparer|confirmer|remonter|partages|"
               "partages/supprimer|samba/mot-de-passe|liberer/apercu|liberer)", "action", hub=True)
def h_storage_action(n, r):
    body = {k: v for k, v in r.b.items() if k != "noeud"}
    return n.hub.agent(_storage_node(n, r.str("noeud")), "POST", "/api/agent/stockage/" + r.p["sub"], body,
                       timeout=30)


# --- Wake-on-LAN -------------------------------------------------------------------------
@route("GET", "/api/wol", "lecture", hub=True)
def h_wol(n, r):
    return n.hub.wol.status()


@route("POST", "/api/wol", "action", hub=True)
def h_wol_add(n, r):
    return {"ok": True, "cible": n.hub.wol.add(r.b.get("nom"), r.b.get("mac"), r.b.get("diffusion"),
                                               r.b.get("ip", ""))}


@route("POST", "/api/wol/(?P<id>[a-z0-9]+)/reveiller", "action", hub=True)
def h_wol_wake(n, r):
    t = n.hub.wol.wake(r.p["id"])
    return {"ok": True, "message": "Signal de réveil envoyé à %s." % t["nom"]}


@route("POST", "/api/wol/(?P<id>[a-z0-9]+)/eteindre", "action", hub=True)
def h_wol_off(n, r):
    t = n.hub.wol.eteindre(r.p["id"])
    return {"ok": True, "cible": t}


@route("GET", "/api/wol/cle", "action", hub=True)
def h_wol_key(n, r):
    return {"cle": n.hub.wol.cle_publique(), "utilisateur": n.conf.get("wol_utilisateur")}


@route("POST", "/api/wol/(?P<id>[a-z0-9]+)/supprimer", "action", hub=True)
def h_wol_del(n, r):
    n.hub.wol.remove(r.p["id"])
    return {"ok": True}


# --- mises à jour ----------------------------------------------------------------------------
def _targets(n, names):
    online = [x["nom"] for x in n.hub.nodes(n.status()) if x["en_ligne"]]
    if names is None:
        return online
    if not isinstance(names, list) or any(not isinstance(x, str) for x in names):
        raise ApiError(400, "« noeuds » doit être une liste de noms.", "noeuds")
    return names


@route("GET", "/api/maj", "lecture", hub=True)
def h_maj(n, r):
    out = []
    for name in _targets(n, None):
        try:
            out.append(n.hub.agent(name, "GET", "/api/agent/maj", timeout=8))
        except ApiError as ex:
            out.append({"noeud": name, "erreur": ex.message})
    return {"noeuds": out}


@route("POST", "/api/maj/(?P<action>verifier|appliquer)", "action", hub=True)
def h_maj_action(n, r):
    res = []
    names = _targets(n, r.b.get("noeuds"))
    for name in sorted(names, key=lambda x: x == n.name):     # le hub en dernier
        try:
            res.append(dict(n.hub.agent(name, "POST", "/api/agent/maj/" + r.p["action"], {}), noeud=name))
        except ApiError as ex:
            res.append({"noeud": name, "ok": False, "erreur": ex.message})
    return {"ok": all(x.get("ok") for x in res), "noeuds": res}


# 1.0.14 : mises à jour automatiques de YDZ OS (GitHub). « /api/maj/verifier » reste la vérification des
# paquets Debian de chaque nœud : les routes de YDZ OS vivent sous /api/maj/ydz/…
@route("GET", "/api/maj/ydz", "lecture", hub=True)
def h_maj_ydz(n, r):
    return n.hub.majydz.status()


@route("POST", "/api/maj/ydz/verifier", "action", hub=True)
def h_maj_ydz_verifier(n, r):
    t = n.hub.majydz.lancer_verification()
    out = {"ok": True, "maj_ydz": n.hub.majydz.status()}
    if t:
        out["tache"] = t.as_dict()
    return out


@route("POST", "/api/maj/ydz/installer", "action", hub=True)
def h_maj_ydz_installer(n, r):
    ch = n.hub.majydz.installer("manuel")
    return {"ok": True, "versions": ch["versions"], "maj_ydz": n.hub.majydz.status()}


@route("POST", "/api/maj/reglages", "action", hub=True)
def h_maj_reglages(n, r):
    return dict(n.hub.majydz.regler(r.b), ok=True)


def _pas_pendant_la_chaine(n):
    if n.hub.majydz.chaine_active():
        raise ApiError(409, "Une installation de mises à jour est en cours : attends qu'elle se termine.", "occupe")


@route("POST", "/api/maj/paquet", "action", hub=True)
def h_maj_paquet(n, r):
    """Installe un paquet YDZ (.zip) déposé depuis l'interface. Corps : le zip brut."""
    from . import paquet
    import subprocess
    _pas_pendant_la_chaine(n)
    brut = r.b.get("_brut") or b""

    def redemarrer():
        try:
            subprocess.Popen(["systemctl", "restart", "--no-block", "ydzd"])
        except OSError:
            pass
    try:
        res = paquet.installer(n.base, brut, redemarrer)
    except paquet.ErreurPaquet as ex:
        raise ApiError(400, str(ex), "paquet")
    return res


@route("GET", "/api/maj/sauvegardes", "lecture", hub=True)
def h_maj_sauvegardes(n, r):
    """Historique des paquets installés (sans contenu : dates, noms, versions)."""
    from . import paquet
    return {"sauvegardes": paquet.historique(), "version": config.version()}


@route("POST", "/api/maj/annuler", "action", hub=True)
def h_maj_annuler(n, r):
    """Annule le dernier paquet : remet les fichiers d'avant, puis redémarre ydzd et l'écran."""
    from . import paquet
    _pas_pendant_la_chaine(n)
    try:
        return paquet.annuler(n.base, r.str("sauvegarde"), n.hub.relancer_annulation)
    except paquet.ErreurPaquet as ex:
        raise ApiError(409, str(ex), "annulation")


# --- écran, badges ---------------------------------------------------------------------------
@route("GET", "/api/ecran", hub=True)
def h_screen(n, r):
    return n.hub.screen.status()


@route("POST", "/api/ecran", "action", hub=True)
def h_screen_set(n, r):
    restart = n.hub.screen.set(r.b.get("luminosite"), r.b.get("veille_min"), r.b.get("rotation"))
    return {"ok": True, "redemarrage_kiosque": restart, "ecran": n.hub.screen.status()}


@route("POST", "/api/affichage/test-veille", "action", hub=True)
def h_affichage_test_veille(n, r):
    """1.0.14 : l'écran HDMI passe en veille 10 s (sortie coupée), puis revient tout seul."""
    return n.hub.maison.tester_veille(10)


@route("GET", "/api/affichage", "lecture", hub=True)
def h_affichage(n, r):
    return dict(n.hub.maison.status(n.hub.nodes(n.status())), reglages=n.hub.maison.reglages())


@route("POST", "/api/affichage", "action", hub=True)
def h_affichage_set(n, r):
    if "curseur" in r.b:                  # 1.0.12 : écrit dans ydz.conf, comme la rotation, puis relance le kiosque
        n.hub.screen.set_curseur(r.b["curseur"])
    n.hub.maison.regler({k: v for k, v in r.b.items() if k != "curseur"})
    return dict(n.hub.maison.status(n.hub.nodes(n.status())), ok=True, reglages=n.hub.maison.reglages())


# --- réseau de la maison (1.0.10) -----------------------------------------------------------
@route("GET", "/api/appareils", "lecture", hub=True)
def h_appareils(n, r):
    v = n.hub.voisins
    return {"appareils": v.liste(), "maj": (v.res or {}).get("maj"), "nouveaux": v.status()["nouveaux"]}


@route("POST", "/api/appareils/(?P<mac>[0-9a-fA-F:]{17})", "action", hub=True)
def h_appareil(n, r):
    v = n.hub.voisins
    if r.b.get("oublier") is True:
        v.oublier(r.p["mac"])
        return {"ok": True}
    if "nom" not in r.b:
        raise ApiError(400, "Indique « nom » (vide pour effacer) ou « oublier ».", "nom")
    return {"ok": True, "appareil": v.renommer(r.p["mac"], r.b.get("nom"))}


@route("GET", "/api/reseau", "lecture", hub=True)
def h_reseau(n, r):
    return {"reseau_maison": n.hub.reseau_maison(), "pihole": n.hub.reseau.pihole}


@route("POST", "/api/reseau/debit", "action", hub=True)
def h_reseau_debit(n, r):
    return dict(n.hub.reseau.lancer_debit(), ok=True)


@route("POST", "/api/reseau/controle", "action", hub=True)
def h_reseau_controle(n, r):
    """1.0.15 : règle le contrôle réseau horaire, ou en lance un tout de suite (action=lancer)."""
    if r.b.get("action") == "lancer":
        return dict(controle=n.hub.reseau.lancer_controle(), ok=True)
    return dict(controle=n.hub.reseau.regler_controle(r.b), ok=True)


# --- sauvegarde vers le PC (1.0.11) ------------------------------------------------------------------
@route("GET", "/api/sauvegarde", "lecture", hub=True)
def h_sauvegarde(n, r):
    return n.hub.sauvegarde.status()                     # jamais le mot de passe


@route("POST", "/api/sauvegarde", "action", hub=True)
def h_sauvegarde_set(n, r):
    return dict(n.hub.sauvegarde.regler(r.b), ok=True)


@route("POST", "/api/sauvegarde/lancer", "action", hub=True)
def h_sauvegarde_lancer(n, r):
    return dict(n.hub.sauvegarde.lancer(manuel=True), ok=True)


@route("POST", "/api/sauvegarde/preparer-pc", "action", hub=True)
def h_sauvegarde_pc(n, r):
    """Script PowerShell pour le PC : seul endroit où le mot de passe du compte ydz-sauvegarde apparaît."""
    return dict(n.hub.sauvegarde.preparer_pc(r.b.get("nouveau_mot_de_passe") is True), ok=True)


# --- notifications Discord / ntfy (1.0.11) ------------------------------------------------------------
@route("GET", "/api/notifications", "lecture", hub=True)
def h_notifs(n, r):
    return n.hub.notifs.status()                         # adresse du webhook masquée


@route("POST", "/api/notifications", "action", hub=True)
def h_notifs_set(n, r):
    return dict(n.hub.notifs.regler(r.b), ok=True)


@route("POST", "/api/notifications/test", "action", hub=True)
def h_notifs_test(n, r):
    return n.hub.notifs.tester(r.str("canal"))


@route("GET", "/api/minecraft", hub=True)
def h_minecraft(n, r):
    return {"minecraft": n.hub.minecraft.etat}


# --- accès à distance, historique, journal d'activité (1.0.12) -----------------------------------------------
@route("GET", "/api/acces", "lecture", hub=True)
def h_acces(n, r):
    return n.hub.acces.status()


@route("POST", "/api/acces", "action", hub=True)
def h_acces_set(n, r):
    if "https" in r.b:
        return dict(n.hub.acces.regler(r.b["https"]), ok=True)
    n.hub.acces.lire()
    n.hub.acces.appliquer()
    return dict(n.hub.acces.status(), ok=True)


# --- espace visiteurs (1.0.16) : lecture protégée (le lien avec secret n'est rendu qu'à une session) ---
@route("GET", "/api/visiteur", "lecture", hub=True)
def h_visiteur(n, r):
    return n.hub.visiteur.status_complet()


@route("POST", "/api/visiteur", "action", hub=True)
def h_visiteur_set(n, r):
    if "actif" in r.b:
        return dict(n.hub.visiteur.regler(bool(r.b["actif"])), ok=True)
    if "app" in r.b:
        return dict(n.hub.visiteur.regler_adresse(r.str("app"), r.str("adresse")), ok=True)
    return dict(n.hub.visiteur.status_complet(), ok=True)


@route("POST", "/api/visiteur/lien", "action", hub=True)
def h_visiteur_lien(n, r):
    return dict(n.hub.visiteur.roter(), ok=True)


@route("POST", "/api/visiteur/couper", "action", hub=True)
def h_visiteur_couper(n, r):
    return dict(n.hub.visiteur.couper(), ok=True)


# --- agenda du jour (iCloud, lecture seule), rappels, Wi-Fi invités (1.0.18) ------------------------------
@route("GET", "/api/agenda", "lecture", hub=True)
def h_agenda(n, r):
    return n.hub.agenda.status_complet()


@route("POST", "/api/agenda", "action", hub=True)
def h_agenda_set(n, r):
    a = r.b.get("action")
    if a == "ajouter":
        return dict(n.hub.agenda.ajouter(r.str("url"), r.str("nom")), ok=True)
    if a == "retirer":
        return dict(n.hub.agenda.retirer(r.str("id")), ok=True)
    if a == "alerte":
        return dict(n.hub.agenda.regler_alerte(r.b.get("minutes")), ok=True)
    raise ApiError(400, "Action inconnue (ajouter, retirer, alerte).", "action")


@route("GET", "/api/rappels", "lecture", hub=True)
def h_rappels(n, r):
    return n.hub.rappels.status()


@route("POST", "/api/rappels", "action", hub=True)
def h_rappels_set(n, r):
    a = r.b.get("action")
    if a == "ajouter":
        return dict(n.hub.rappels.ajouter(r.str("texte")), ok=True)
    if a == "basculer":
        return dict(n.hub.rappels.basculer(r.str("id")), ok=True)
    if a == "retirer":
        return dict(n.hub.rappels.retirer(r.str("id")), ok=True)
    if a == "vider":
        return dict(n.hub.rappels.vider_faits(), ok=True)
    raise ApiError(400, "Action inconnue (ajouter, basculer, retirer, vider).", "action")


@route("GET", "/api/wifi-invite", "lecture", hub=True)
def h_wifi_invite(n, r):
    return n.hub.wifi_invite.status()                    # jamais le mot de passe, jamais le QR


@route("GET", "/api/wifi-invite/qr", "lecture", hub=True)
def h_wifi_invite_qr(n, r):
    """Le QR encode le mot de passe : « lecture » (écran local, session PIN ou hub signé), jamais anonyme."""
    return n.hub.wifi_invite.qr()


@route("POST", "/api/wifi-invite", "action", hub=True)
def h_wifi_invite_set(n, r):
    return dict(n.hub.wifi_invite.regler(r.b), ok=True)


# --- agents assistants + Canal Claude (1.0.19) -----------------------------------------------------------
@route("GET", "/api/agents", "lecture", hub=True)
def h_agents(n, r):
    """Roster, état des agents, réglages du cerveau et du Canal Claude. Jamais de secret en clair :
    la clé d'API et la clé du canal sont masquées (mises à nu seulement par les routes « action »)."""
    out = n.hub.agents.status()
    # La clé du Canal Claude n'est donnée qu'à l'écran local ou à une session PIN (jamais anonyme).
    if r.ctx.get("local") or r.ctx.get("sig") or n.hub.auth.valid(r.ctx.get("token")):
        out["canal"] = n.hub.agents.canal_status(avec_secret=True)
    return out


@route("POST", "/api/agents", "action", hub=True)
def h_agents_action(n, r):
    a = r.b.get("action")
    ag = n.hub.agents
    if a == "creer":
        return dict(agent=ag.creer(r.str("nom")), ok=True)
    if a == "configurer":
        return dict(agent=ag.configurer(r.str("id"), r.b), ok=True)
    if a == "supprimer":
        return dict(ag.supprimer(r.str("id")), ok=True)
    if a == "tache":
        return dict(ag.assigner(r.str("id"), r.str("tache")), ok=True)
    if a == "annuler":
        return dict(ag.annuler(r.str("id")), ok=True)
    if a == "canal":
        return dict(ag.regler_canal(r.b), ok=True)
    if a == "bureaux":
        return dict(bureaux=ag.regler_bureaux(r.b), ok=True)
    raise ApiError(400, "Action inconnue (creer, configurer, supprimer, tache, annuler, canal, bureaux).", "action")


@route("POST", "/api/agents/cerveau", "action", hub=True)
def h_agents_cerveau(n, r):
    return dict(n.hub.agents.regler_cerveau(r.b), ok=True)


@route("GET", "/api/historique", "lecture", hub=True)
def h_historique(n, r):
    h = n.hub.historique
    jours = 7 if str(r.q.get("jours")) == "7" else 1
    series = [x for x in (r.q.get("serie") or "").split(",") if x][:12]
    if not series:
        return {"disponibles": h.disponibles(), "jours": jours}
    noeud = r.q.get("noeud") or n.name
    if not re.match(r"^[a-z0-9-]{1,32}$", noeud):
        raise ApiError(400, "Nœud invalide.", "noeud")
    out = []
    for s in series:
        d = h.serie(noeud, s, jours)
        if d is None:
            raise ApiError(400, "Série inconnue : %s." % s[:20], "serie")
        out.append(d)
    return out[0] if len(out) == 1 else {"series": out, "noeud": noeud, "jours": jours}


@route("GET", "/api/activite", "action", hub=True)
def h_activite(n, r):
    """Journal d'activité : niveau « action » (il montre des adresses IP et des comptes Tailscale)."""
    filtre = r.q.get("filtre") if r.q.get("filtre") in ("tout", "actions", "auto", "erreurs") else "tout"
    return {"entrees": n.hub.activite.liste(_int(r.q.get("limite"), 200, 1, 2000), filtre), "filtre": filtre}


@route("POST", "/api/ecran/activite", hub=True)
def h_screen_touch(n, r):
    """Appelée par l'interface locale à chaque appui : repousse la veille."""
    if not r.ctx.get("local"):
        return {"ok": True, "reveil": False}
    return {"ok": True, "reveil": n.hub.screen.activity()}


@route("POST", "/api/badges", "action", hub=True)
def h_badges(n, r):
    body = {k: r.b[k] for k in ("luminosite", "accent", "retourne") if k in r.b}
    names = _targets(n, [r.b["noeud"]] if r.b.get("noeud") else None)
    res = []
    for name in names:
        try:
            res.append(n.hub.agent(name, "POST", "/api/agent/badge", body, timeout=5))
        except ApiError as ex:
            res.append({"noeud": name, "ok": False, "erreur": ex.message})
    return {"ok": all(x.get("ok") for x in res), "noeuds": res}


# --- tâches, diagnostic ----------------------------------------------------------------------
@route("GET", "/api/taches", hub=True)
def h_tasks(n, r):
    return {"taches": n.hub.snapshot(n.status())["taches"]}


@route("GET", "/api/taches/(?P<id>[0-9a-f]+)", "lecture", hub=True)
def h_task(n, r):
    return n.hub.agent(r.q.get("noeud"), "GET", "/api/agent/taches/" + quote(r.p["id"]), timeout=5)


@route("GET", "/api/diagnostic", "action", hub=True)
def h_diag(n, r):
    return n.hub.agent(r.q.get("noeud"), "GET", "/api/agent/diagnostic", timeout=40)


# --- réglages généraux (1.0.13) : noms, Wi-Fi, Bluetooth, heure, son ---------------------------------------
@route("GET", "/api/general", "lecture", hub=True)
def h_general(n, r):
    h = n.hub
    nodes = h.nodes(n.status())
    return {"labo": h.noms.status([x["nom"] for x in nodes]), "nom_hote": n.name, "ip": n.status().get("ip"),
            "adresse": "http://%s.local/" % n.name, "heure": h.heure.status(), "son": h.son.status(),
            "ecran": h.screen.status()}


@route("POST", "/api/general/nom", "action", hub=True)
def h_general_nom(n, r):
    from . import noms
    h, out = n.hub, {"ok": True}
    if "labo" in r.b:
        out["labo"] = h.noms.renommer_labo(r.b["labo"])
    if "nom" in r.b:
        cible = r.str("noeud") or n.name
        out["nom"] = h.noms.renommer(cible, r.b["nom"])
    if "hote" in r.b:
        nouveau = r.str("hote")
        err = noms.valider_hote(nouveau)
        if err:
            raise ApiError(400, err, "hote")
        if nouveau != n.name:
            if h.peers.get(nouveau) or nouveau in [p.name for p in h.peers.all()]:
                raise ApiError(409, "Un autre Pi du labo s'appelle déjà « %s » sur le réseau." % nouveau, "hote")
            res = n.sys.run(noms.commande_renommage(n.base, nouveau, role=n.role), timeout=15)
            if not res.ok:
                raise ApiError(500, "Renommage impossible (%s)." % (res.err or "").strip()[:120], "systemd")
            out.update(nom_hote=nouveau, adresse="http://%s.local/" % nouveau, ip=n.status().get("ip"),
                       redemarrage=True)
    out["etat_labo"] = h.noms.status([x["nom"] for x in h.nodes(n.status())])
    return out


@route("GET", "/api/general/fuseaux", hub=True)
def h_fuseaux(n, r):
    return {"fuseaux": n.hub.heure.fuseaux(), "actuel": n.hub.heure.status()["fuseau"]}


@route("POST", "/api/general/heure", "action", hub=True)
def h_heure(n, r):
    return dict(n.hub.heure.regler(r.b.get("fuseau")), ok=True)


@route("POST", "/api/general/son", "action", hub=True)
def h_son(n, r):
    return dict(n.hub.son.regler(r.b.get("volume"), r.b.get("sortie")), ok=True)


@route("GET", "/api/wifi", "lecture", hub=True)
def h_wifi(n, r):
    return n.hub.wifi.status()


@route("POST", "/api/wifi/scan", "action", hub=True)
def h_wifi_scan(n, r):
    n.hub.wifi.scanner(forcer=True)
    return dict(n.hub.wifi.status(), ok=True)


@route("POST", "/api/wifi/connecter", "action", hub=True)
def h_wifi_connecter(n, r):
    """Mot de passe : lu ici, écrit seulement dans le profil NetworkManager (0600), jamais renvoyé."""
    d = n.hub.wifi.connecter(r.str("ssid"), r.b.get("mot_de_passe"), r.str("uuid"))
    return {"ok": True, "ssid": d["ssid"], "ancien": d["ancien"], "en_cours": True, "delai_s": 60}


@route("POST", "/api/wifi/oublier", "action", hub=True)
def h_wifi_oublier(n, r):
    return {"ok": True, "ssid": n.hub.wifi.oublier(r.str("uuid"))}


@route("POST", "/api/wifi/radio", "action", hub=True)
def h_wifi_radio(n, r):
    return {"ok": True, "actif": n.hub.wifi.regler_radio(r.b.get("actif"))}


@route("GET", "/api/bluetooth", "lecture", hub=True)
def h_bt(n, r):
    return n.hub.bluetooth.status()


@route("POST", "/api/bluetooth", "action", hub=True)
def h_bt_set(n, r):
    n.hub.bluetooth.regler(r.b.get("actif"))
    return dict(n.hub.bluetooth.status(), ok=True)


@route("POST", "/api/bluetooth/scan", "action", hub=True)
def h_bt_scan(n, r):
    return {"ok": True, "duree_s": n.hub.bluetooth.rechercher()}


@route("POST", "/api/bluetooth/appareil", "action", hub=True)
def h_bt_appareil(n, r):
    n.hub.bluetooth.action(r.str("mac"), r.str("action"))
    return dict(n.hub.bluetooth.status(), ok=True)


@route("POST", "/api/bluetooth/pin", "action", hub=True)
def h_bt_pin(n, r):
    n.hub.bluetooth.donner_pin(r.str("mac"), r.str("pin"))
    return {"ok": True}


@route("POST", "/api/bluetooth/confirmer", "action", hub=True)
def h_bt_confirmer(n, r):
    """1.0.15 : accepter (ou refuser) explicitement le code à 6 chiffres affiché pendant l'appairage."""
    n.hub.bluetooth.confirmer_appairage(r.str("mac"), r.b.get("accepter") is True)
    return dict(n.hub.bluetooth.status(), ok=True)


# --- iPhone en Bluetooth LE (1.0.20) : notifications (ANCS) + musique (AMS), AFFICHAGE SEUL --------------
# Lecture (voir les notifs/l'état média) : « lecture » (écran local, session PIN ou hub signé), jamais
# anonyme — une notification d'iPhone est privée. Les commandes média : « action » (PIN ou écran local).
@route("GET", "/api/iphone", "lecture", hub=True)
def h_iphone(n, r):
    return n.hub.iphone.status_complet()


@route("POST", "/api/iphone", "action", hub=True)
def h_iphone_set(n, r):
    if "commande" in r.b:
        return dict(n.hub.iphone.commande(r.str("commande")), ok=True)
    if r.b.get("effacer") is True:
        return dict(n.hub.iphone.effacer_notifs(), ok=True)
    return dict(n.hub.iphone.regler(r.b), ok=True)


__all__ = ["Node", "ROUTES", "mask_settings"]
