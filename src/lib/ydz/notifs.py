"""Notifications vers le téléphone (1.0.11) : Discord (webhook) et ntfy, plus le « veilleur » qui
transforme les changements d'état de la tour en alertes.

Chaque alerte passe par Hub.alerte() : notification dans l'interface, puis les relais enregistrés
dans Hub.relais. Les relais d'ici filtrent par type (cases à cocher dans Réglages), limitent le
débit (même alerte une fois par heure au plus, 20 par heure en tout) et envoient dans un fil à part,
avec un délai court : un service injoignable ne bloque jamais le démon.
Rien ne part sur le réseau en simulation ou dans les tests.
"""
import base64
import collections
import queue
import re
import secrets
import string
import threading

from .core import ApiError

MASQUE = "••••••"
NTFY_SERVEUR = "https://ntfy.sh"
RE_DISCORD = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d{5,30}/[A-Za-z0-9_-]{20,120}/?$")
RE_SUJET = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RE_SERVEUR = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?/?$")
ENTETES = {"User-Agent": "YDZ-OS/1.0"}

# Types d'événements : (id, nom, description, coché par défaut). L'interface les affiche tels quels.
TYPES = [
    ("noeud", "Nœud hors ligne", "Un Pi de la tour ne répond plus, puis revient.", True),
    ("temperature", "Température", "Un Pi chauffe : 75 °C (attention), 80 °C (critique).", True),
    ("tour", "Tour en alerte", "La tour passe en état critique.", True),
    ("app", "Application en panne", "Une application s'arrête ou redémarre en boucle.", True),
    ("disque", "Disque presque plein", "Un disque dépasse 90 %.", True),
    ("nouvel_appareil", "Nouvel appareil", "Un appareil jamais vu arrive sur le Wi-Fi.", True),
    ("sauvegarde", "Sauvegarde", "La sauvegarde vers le PC échoue (ou réussit, quand tu la lances).", True),
    ("debit", "Test de débit", "Le test de débit Internet échoue.", True),
    ("minecraft", "Joueurs Minecraft", "Quelqu'un rejoint le serveur Minecraft.", False),
    ("maj_apps", "Mises à jour des apps", "Une nouvelle version d'une application est disponible.", True),
    ("maj_ydz", "Mises à jour de YDZ OS", "Une nouvelle version de YDZ OS est prête à installer, ou vient d'être installée.", True),
]
IDS_TYPES = [t[0] for t in TYPES]
NIVEAUX = {
    # niveau -> (couleur Discord, priorité ntfy, étiquette ntfy, titre)
    "crit": (0xE5484D, 5, "rotating_light", "YDZ : critique"),
    "warn": (0xE8A33D, 4, "warning", "YDZ : attention"),
    "ok": (0x7FBF8E, 3, "white_check_mark", "YDZ : tout va bien"),
    "info": (0x5AA9E6, 3, "information_source", "YDZ"),
}
TITRES = {
    "noeud": "Nœud", "temperature": "Température", "tour": "Tour", "app": "Application", "disque": "Disque",
    "nouvel_appareil": "Nouvel appareil", "sauvegarde": "Sauvegarde", "debit": "Débit Internet",
    "minecraft": "Minecraft", "test": "Test", "maj_apps": "Mises à jour des apps", "maj_ydz": "YDZ OS",
}


def discord_valide(url):
    return isinstance(url, str) and bool(RE_DISCORD.match(url.strip()))


def sujet_aleatoire():
    return "ydz-" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(16))


def masquer_discord(url):
    """https://discord.com/api/webhooks/123456/abcdef… -> https://discord.com/api/webhooks/12…56/••••••"""
    if not url:
        return None
    m = re.match(r"^(https://[^/]+/api/webhooks/)(\d+)/", url)
    if not m:
        return MASQUE
    ident = m.group(2)
    return "%s%s…%s/%s" % (m.group(1), ident[:2], ident[-2:], MASQUE)


def entete_utf8(texte):
    """Valeur d'en-tête HTTP : ASCII tel quel, sinon mot encodé RFC 2047 (ntfy le décode)."""
    try:
        texte.encode("ascii")
        return texte
    except UnicodeEncodeError:
        return "=?UTF-8?B?%s?=" % base64.b64encode(texte.encode("utf-8")).decode("ascii")


class Limiteur:
    """Même (type, texte) au plus une fois par `fenetre` secondes ; `max_total` envois par fenêtre en tout."""

    def __init__(self, fenetre=3600, max_total=20):
        self.fenetre, self.max_total = fenetre, max_total
        self.derniers = {}
        self.envois = collections.deque()
        self.lock = threading.Lock()

    def autoriser(self, cle, t):
        with self.lock:
            while self.envois and t - self.envois[0] >= self.fenetre:
                self.envois.popleft()
            d = self.derniers.get(cle)
            if d is not None and t - d < self.fenetre:
                return False
            if len(self.envois) >= self.max_total:
                return False
            self.derniers[cle] = t
            self.envois.append(t)
            if len(self.derniers) > 500:            # ménage : on oublie les vieilles clés
                for k in [k for k, v in self.derniers.items() if t - v >= self.fenetre]:
                    del self.derniers[k]
            return True


class Notifications:
    def __init__(self, hub):
        self.hub = hub
        self.sys = hub.sys
        self.state = hub.node.state
        self.reel = type(self.sys).__name__ == "System"
        self.limiteur = Limiteur()
        self.file = queue.Queue(maxsize=50)
        self.fil = None
        self.recentes = collections.deque(maxlen=15)   # dernières alertes (en mémoire), pour l'interface
        self.erreur = {}                                # canal -> dernière erreur d'envoi

    # --- réglages --------------------------------------------------------------------------------
    def reglages(self):
        r = dict(self.state.get("notifications") or {})
        if not r.get("ntfy_sujet"):
            r["ntfy_sujet"] = sujet_aleatoire()
            self.state.set("notifications", r)
        r.setdefault("ntfy_serveur", NTFY_SERVEUR)
        r.setdefault("ntfy_actif", False)
        r.setdefault("discord", "")
        r.setdefault("discord_actif", bool(r.get("discord")))
        types = dict((t[0], t[3]) for t in TYPES)
        types.update({k: bool(v) for k, v in (r.get("types") or {}).items() if k in types})
        r["types"] = types
        return r

    def status(self):
        """Ce que les routes GET renvoient : jamais l'adresse complète du webhook."""
        r = self.reglages()
        return {
            "discord": {"defini": bool(r["discord"]), "actif": bool(r["discord"]) and r["discord_actif"],
                        "apercu": masquer_discord(r["discord"]), "erreur": self.erreur.get("discord")},
            "ntfy": {"actif": r["ntfy_actif"], "sujet": r["ntfy_sujet"], "serveur": r["ntfy_serveur"],
                     "erreur": self.erreur.get("ntfy")},
            "types": [{"id": i, "nom": n, "description": d, "actif": r["types"][i]} for i, n, d, _ in TYPES],
            "recentes": list(self.recentes)[::-1],
        }

    def regler(self, b):
        r = self.reglages()
        if "discord" in b:
            v = b["discord"]
            if v in (None, ""):
                r["discord"], r["discord_actif"] = "", False
            elif v != MASQUE:
                if not discord_valide(v):
                    raise ApiError(400, "Adresse de webhook Discord invalide. Elle commence par "
                                        "https://discord.com/api/webhooks/…", "discord")
                r["discord"], r["discord_actif"] = v.strip(), True
                self.erreur.pop("discord", None)
        for k in ("discord_actif", "ntfy_actif"):
            if k in b:
                if not isinstance(b[k], bool):
                    raise ApiError(400, "Champ « %s » invalide." % k, k)
                r[k] = b[k]
        if r["discord_actif"] and not r["discord"]:
            raise ApiError(400, "Colle d'abord l'adresse du webhook Discord.", "discord")
        if "ntfy_sujet" in b:
            v = b["ntfy_sujet"]
            if v in (None, ""):
                v = sujet_aleatoire()
            if not isinstance(v, str) or not RE_SUJET.match(v.strip()):
                raise ApiError(400, "Sujet ntfy invalide : lettres, chiffres, tirets, 64 caractères au plus.",
                               "ntfy_sujet")
            r["ntfy_sujet"] = v.strip()
        if "ntfy_serveur" in b:
            v = b["ntfy_serveur"] or NTFY_SERVEUR
            if not isinstance(v, str) or not RE_SERVEUR.match(v.strip()):
                raise ApiError(400, "Adresse du serveur ntfy invalide (exemple : https://ntfy.sh).", "ntfy_serveur")
            r["ntfy_serveur"] = v.strip().rstrip("/")
        if "types" in b:
            if not isinstance(b["types"], dict) or any(k not in IDS_TYPES or not isinstance(v, bool)
                                                      for k, v in b["types"].items()):
                raise ApiError(400, "Types d'alertes invalides.", "types")
            r["types"].update(b["types"])
        self.state.set("notifications", r)
        return self.status()

    # --- relais (appelé par Hub.alerte pour chaque alerte) ------------------------------------------
    def relayer(self, a):
        self.recentes.append({"t": a.get("heure"), "type": a.get("type"), "niveau": a.get("niveau"),
                              "texte": a.get("texte")})
        r = self.reglages()
        canaux = self._canaux(r)
        if not canaux or not r["types"].get(a.get("type"), a.get("type") == "test"):
            return False
        if not self.limiteur.autoriser((a.get("type"), a.get("texte")), self.sys.monotonic()):
            return False
        if not self.reel:                     # simulation, tests : rien ne sort
            return True
        self._demarrer()
        try:
            self.file.put_nowait((a, canaux))
        except queue.Full:
            return False
        return True

    def _canaux(self, r):
        c = []
        if r["discord"] and r["discord_actif"]:
            c.append("discord")
        if r["ntfy_actif"] and r["ntfy_sujet"]:
            c.append("ntfy")
        return c

    def _demarrer(self):
        if self.fil is None or not self.fil.is_alive():
            self.fil = threading.Thread(target=self._boucle, name="ydz-notifs", daemon=True)
            self.fil.start()

    def _boucle(self):
        while True:
            a, canaux = self.file.get()
            for c in canaux:
                try:
                    self.envoyer(c, a)
                except Exception:
                    pass

    # --- envoi -------------------------------------------------------------------------------------
    def envoyer(self, canal, a, r=None):
        """Envoie une alerte sur un canal. Retourne None si c'est parti, sinon le message d'erreur."""
        r = r or self.reglages()
        niv = NIVEAUX.get(a.get("niveau"), NIVEAUX["info"])
        titre = "%s · %s" % (niv[3], TITRES.get(a.get("type"), "Alerte")) if a.get("type") != "test" else "YDZ : test"
        if canal == "discord":
            corps = {"username": "YDZ", "embeds": [{"title": titre, "description": a.get("texte", "")[:2000],
                                                    "color": niv[0]}]}
            code, _ = self.sys.http("POST", r["discord"], corps, dict(ENTETES), timeout=8)
            err = None if 200 <= code < 300 else self._message(code, "Discord")
        elif canal == "ntfy":
            h = dict(ENTETES, Title=entete_utf8(titre), Priority=str(niv[1]), Tags=niv[2])
            h["Content-Type"] = "text/plain; charset=utf-8"
            url = "%s/%s" % (r["ntfy_serveur"].rstrip("/"), r["ntfy_sujet"])
            code, _ = self.sys.http("POST", url, a.get("texte", "").encode("utf-8"), h, timeout=8)
            err = None if 200 <= code < 300 else self._message(code, "ntfy")
        else:
            return "Canal inconnu."
        if err:
            self.erreur[canal] = err
        else:
            self.erreur.pop(canal, None)
        return err

    @staticmethod
    def _message(code, qui):
        if code == 0:
            return "%s ne répond pas (Internet coupé ?)." % qui
        if code in (401, 403, 404):
            return "%s refuse l'envoi (code %d) : vérifie l'adresse." % (qui, code)
        if code == 429:
            return "%s demande de ralentir (trop d'envois)." % qui
        return "%s a répondu par une erreur (code %d)." % (qui, code)

    def tester(self, canal=None):
        """Envoi immédiat (dans la requête) d'un message de test, sans limite de débit."""
        r = self.reglages()
        canaux = [canal] if canal else self._canaux(r)
        if canal == "discord" and not r["discord"]:
            raise ApiError(400, "Colle d'abord l'adresse du webhook Discord.", "discord")
        if canal not in (None, "discord", "ntfy"):
            raise ApiError(400, "Canal inconnu (discord ou ntfy).", "canal")
        if not canaux:
            raise ApiError(400, "Active d'abord Discord ou ntfy.", "canal")
        a = {"type": "test", "niveau": "ok", "texte": "Ceci est un test : les alertes de ta tour YDZ arriveront ici.",
             "heure": int(self.sys.now())}
        res = {}
        for c in canaux:
            res[c] = self.envoyer(c, a, r) if self.reel else None
        return {"ok": all(v is None for v in res.values()),
                "resultats": {c: {"ok": v is None, "erreur": v} for c, v in res.items()}}


# =====================================================================================================
# Veilleur : compare l'état d'un tour de boucle au précédent et lève des alertes sur les changements.
# =====================================================================================================
TEMP_WARN, TEMP_CRIT, TEMP_MARGE = 75, 80, 3
DISQUE_WARN, DISQUE_CRIT, DISQUE_MARGE = 85, 95, 3   # 1.0.21 : alerte progressive (avertissement / alerte forte)


class Veilleur:
    def __init__(self, alerte, now=None, nom=None):
        self.alerte = alerte
        self.now = now or (lambda: 0)
        self.nom = nom or (lambda x: x.upper())     # 1.0.13 : « Pi One » plutôt que « FORGE »
        self.vu = False
        self.en_ligne = {}       # nœud -> bool
        self.chaleur = {}        # nœud -> 0 (ok), 1 (warn), 2 (crit)
        self.tour_crit = False
        self.conteneurs = {}     # (nœud, conteneur) -> état docker
        self.disques = {}        # (nœud, point) -> plein ?

    def observer(self, noeuds):
        premier = not self.vu
        self.vu = True
        crit = []
        for n in noeuds:
            if not n.get("connu", True):
                continue
            nom = n["nom"]
            NOM = self.nom(nom)
            ligne = bool(n.get("en_ligne"))
            avant = self.en_ligne.get(nom)
            self.en_ligne[nom] = ligne
            if avant is not None and avant != ligne and not premier:
                if ligne:
                    self.alerte("noeud", "ok", "%s est de nouveau en ligne" % NOM, noeud=nom)
                else:
                    self.alerte("noeud", "warn", "%s ne répond plus" % NOM, noeud=nom)
            if not ligne:
                continue
            # température (avec une marge à la descente : pas d'alerte en rafale autour du seuil)
            t = n.get("temp")
            if t is not None:
                ancien = self.chaleur.get(nom, 0)
                niv = 2 if t >= TEMP_CRIT else 1 if t >= TEMP_WARN else 0
                if niv < ancien:
                    seuil = TEMP_CRIT if ancien == 2 else TEMP_WARN
                    if t > seuil - TEMP_MARGE:
                        niv = ancien
                self.chaleur[nom] = niv
                if niv > ancien and not premier:
                    self.alerte("temperature", "crit" if niv == 2 else "warn",
                                "%s chauffe : %d °C" % (NOM, round(t)), noeud=nom, temp=t)
            if n.get("etat") == "crit":
                crit += ["%s : %s" % (NOM, x) for x in (n.get("alertes") or [])] or ["%s en alerte" % NOM]
            # applications : un conteneur qui tournait et qui s'arrête tout seul
            taches = n.get("taches") or []
            for c in (n.get("docker") or {}).get("conteneurs", []):
                cle = (nom, c.get("nom"))
                ancien, etat = self.conteneurs.get(cle), c.get("etat")
                self.conteneurs[cle] = etat
                if premier or ancien != "running" or etat not in ("exited", "restarting", "dead"):
                    continue
                app = c.get("app")
                voulu = any(x.get("type") == "app" and x.get("cible") == app and
                            (x.get("etat") == "en_cours" or (x.get("fin") or 0) > self.now() - 180) for x in taches)
                if app and not voulu:
                    self.alerte("app", "warn", "%s : %s %s" % (NOM, app, "redémarre en boucle" if etat == "restarting"
                                                               else "s'est arrêtée"), noeud=nom, app=app)
            # disques : alerte progressive avec marge à la descente (pas de rafale autour du seuil)
            for d in n.get("disques") or []:
                cle = (nom, d.get("point"))
                p = d.get("pct") or 0
                ancien = self.disques.get(cle, 0)
                niv = 2 if p >= DISQUE_CRIT else 1 if p >= DISQUE_WARN else 0
                if niv < ancien:
                    seuil = DISQUE_CRIT if ancien == 2 else DISQUE_WARN
                    if p > seuil - DISQUE_MARGE:
                        niv = ancien
                self.disques[cle] = niv
                if niv > ancien and not premier:
                    self.alerte("disque", "crit" if niv == 2 else "warn",
                                "%s : disque « %s » %s à %d %%" % (NOM, d.get("nom") or d.get("point"),
                                "presque plein" if niv == 2 else "se remplit", p), noeud=nom)
        est_crit = bool(crit)
        if est_crit and not self.tour_crit and not premier:
            self.alerte("tour", "crit", "Tour en alerte : %s" % crit[0])
        self.tour_crit = est_crit
