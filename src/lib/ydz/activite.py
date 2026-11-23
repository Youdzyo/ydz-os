"""Journal d'activité (1.0.12) : qui a fait quoi, quand, d'où, et avec quel résultat.

Chaque route POST d'action du hub est notée (jamais les lectures), avec une phrase en français construite
à partir de champs choisis un par un : le corps des requêtes n'est JAMAIS recopié (codes PIN, mots de passe,
webhooks). Les événements automatiques (sauvegarde de la nuit, test de débit, nouvel appareil, mise à jour
d'app trouvée…) arrivent par Hub.alerte.

Stockage : /var/log/ydz/activite.jsonl, une ligne JSON par événement, ajoutée en fin de fichier (une petite
écriture, pas de réécriture : carte SD). Le fichier est réduit aux 2000 dernières lignes quand il en dépasse
2200. Il révèle des adresses IP : la route de lecture est de niveau « action ».
"""
import json
import re
import threading
from collections import deque

FICHIER = "/var/log/ydz/activite.jsonl"
GARDER = 2000
MARGE = 200
TYPES = ("action", "auto")

# routes POST jamais notées : bruit (appui sur l'écran), échanges internes, routes d'agent
IGNOREES = re.compile(r"^/api/(ecran/activite|ecran/billet|pair|agent/.*|appairage/oublier)$")

RE_URL = re.compile(r"https?://\S+")
RE_JETON = re.compile(r"[A-Za-z0-9_\-]{24,}")
RE_NOM = re.compile(r"^[A-Za-z0-9 _.\-]{1,40}$")
_VERBES = {"installer": "Installation", "demarrer": "Démarrage", "arreter": "Arrêt", "redemarrer": "Redémarrage",
           "mettre-a-jour": "Mise à jour", "desinstaller": "Désinstallation"}


def nettoyer(texte, n=200):
    """Message d'erreur sans adresse (webhooks), sans jeton ni mot de passe recopié."""
    t = RE_URL.sub("[adresse]", str(texte or ""))
    t = RE_JETON.sub("••••••", t)
    return t.replace("\n", " ").strip()[:n]


def _nom_sur(v):
    """Un nom tiré du corps n'est gardé que s'il est court et sans caractère spécial."""
    return v if isinstance(v, str) and RE_NOM.match(v) else None


def _aff(node, hote):
    """1.0.13 : nom d'affichage (« Pi One ») si le hub le connaît."""
    try:
        return node.hub.aff(hote)
    except Exception:
        return str(hote or "").upper()


_NODE = [None]


def _noeud(b):
    v = _nom_sur(b.get("noeud"))
    return " sur %s" % _aff(_NODE[0], v) if v else ""


def libelle(node, path, b, d):
    """Phrase française pour une route POST. `b` : corps (seuls quelques champs sûrs sont lus), `d` : réponse."""
    b = b if isinstance(b, dict) else {}
    d = d if isinstance(d, dict) else {}
    _NODE[0] = node
    cat = node.apps.catalog if node else {}
    m = re.match(r"^/api/(?:noeuds/(?P<nom>[a-z0-9-]+)/)?(?P<a>redemarrer|eteindre)$", path)
    if m:
        nom = _aff(node, m.group("nom") or (node.name if node else "le nœud"))
        return ("Redémarrage de %s" if m.group("a") == "redemarrer" else "Extinction de %s") % nom
    m = re.match(r"^/api/tour/(redemarrer|eteindre)$", path)
    if m:
        return "Redémarrage de la tour" if m.group(1) == "redemarrer" else "Extinction de la tour"
    m = re.match(r"^/api/appairage/([a-z0-9-]+)/(accepter|refuser)$", path)
    if m:
        if m.group(2) == "accepter" and _nom_sur(b.get("remplace")):
            return "%s accepté : remplace %s" % (m.group(1), _aff(node, b["remplace"]))
        return ("%s accepté dans le labo" if m.group(2) == "accepter" else "Demande de %s refusée") % m.group(1)
    if path == "/api/auth/connexion":
        return "Connexion avec le code PIN"
    if path == "/api/auth/deconnexion":
        return "Appareil verrouillé"
    if path == "/api/auth/pin":
        return "Code PIN modifié"
    if path == "/api/ventilo":
        mode = b.get("mode") if b.get("mode") in ("silence", "equilibre", "performance") else None
        return "Ventilateur : mode %s" % {"silence": "Silence", "equilibre": "Équilibré",
                                          "performance": "Performance"}[mode] if mode else "Réglage du ventilateur"
    if path == "/api/ventilo/test":
        return "Test du ventilateur"
    if path == "/api/apps/maj/verifier":
        return "Recherche de mises à jour des apps"
    if path == "/api/apps/maj/tout":
        return "Mise à jour de toutes les apps"
    m = re.match(r"^/api/apps/([a-z0-9-]+)/([a-z-]+)$", path)
    if m:
        nom = cat.get(m.group(1), {}).get("nom", m.group(1))
        if m.group(2) == "reglages":
            return "Réglages de %s modifiés%s" % (nom, _noeud(b))
        return "%s de %s%s" % (_VERBES.get(m.group(2), "Action"), nom, _noeud(b))
    m = re.match(r"^/api/stockage/(.+)$", path)
    if m:
        return {"formater/preparer": "Préparation du formatage d'un disque", "miroir/preparer": "Préparation d'un miroir",
                "confirmer": "Formatage confirmé", "remonter": "Disque remonté",
                "partages": "Partage « %s » créé" % (_nom_sur(b.get("nom")) or "?"),
                "partages/supprimer": "Partage « %s » supprimé" % (_nom_sur(b.get("nom")) or "?"),
                "samba/mot-de-passe": "Mot de passe des partages modifié"}.get(m.group(1), "Action sur le stockage")
    m = re.match(r"^/api/wol(?:/([a-z0-9]+)/(reveiller|eteindre|supprimer))?$", path)
    if m:
        if not m.group(1):
            return "Machine « %s » ajoutée au réveil" % (_nom_sur(b.get("nom")) or "?")
        cible = "le PC"
        try:
            t = next((x for x in node.hub.wol.targets() if x.get("id") == m.group(1)), None)
            cible = _nom_sur(t.get("nom")) or cible if t else cible
        except Exception:
            pass
        return {"reveiller": "Réveil de %s", "eteindre": "Extinction de %s",
                "supprimer": "%s retiré du réveil à distance"}[m.group(2)] % cible
    if path == "/api/maj/verifier":
        return "Recherche de mises à jour du système"
    if path == "/api/maj/appliquer":
        return "Mises à jour du système lancées"
    if path == "/api/maj/paquet":
        nom = d.get("nom") if isinstance(d.get("nom"), str) else None
        v = re.search(r"\d+\.\d+\.\d+", nom or "")
        return "Paquet %s installé" % v.group(0) if v else "Paquet YDZ installé"
    if path == "/api/maj/ydz/verifier":
        return "Recherche de mises à jour de YDZ OS"
    if path == "/api/maj/ydz/installer":
        vs = [x for x in (d.get("versions") or []) if isinstance(x, str) and re.match(r"^\d+\.\d+\.\d+$", x)] \
            if isinstance(d.get("versions"), list) else []
        return "Installation de YDZ OS %s lancée" % ", ".join(vs[:5]) if vs else "Installation des mises à jour de YDZ OS"
    if path == "/api/maj/reglages":
        if b.get("auto_nuit") is True:
            return "Mises à jour de YDZ OS : installation automatique la nuit activée"
        if b.get("auto_nuit") is False:
            return "Mises à jour de YDZ OS : installation automatique la nuit coupée"
        return "Réglages des mises à jour"
    if path == "/api/maj/annuler":
        v = d.get("version_avant") if isinstance(d.get("version_avant"), str) else None
        return "Annulation de paquet%s" % (" (retour à %s)" % v if v and re.match(r"^[\d.]{1,12}$", v) else "")
    if path == "/api/ecran":
        return "Réglages de l'écran"
    if path == "/api/affichage/test-veille":
        return "Test de mise en veille de l'écran (10 s)"
    if path == "/api/affichage":
        return "Réglages de l'écran d'affichage"
    if path.startswith("/api/appareils/"):
        return "Appareil oublié" if b.get("oublier") is True else "Appareil renommé"
    if path == "/api/reseau/debit":
        return "Test de débit lancé"
    if path == "/api/sauvegarde":
        return "Réglages de la sauvegarde"
    if path == "/api/sauvegarde/lancer":
        return "Sauvegarde lancée"
    if path == "/api/sauvegarde/preparer-pc":
        return "Nouveau mot de passe de sauvegarde" if b.get("nouveau_mot_de_passe") is True \
            else "Script « Préparer le PC » affiché"
    if path == "/api/notifications":
        return "Réglages des notifications"
    if path == "/api/notifications/test":
        return "Notification de test"
    if path == "/api/badges":
        return "Réglages des badges"
    if path == "/api/acces":
        if b.get("https") is True:
            return "Adresses HTTPS activées"
        if b.get("https") is False:
            return "Adresses HTTPS désactivées"
        return "Accès à distance réappliqué"
    # 1.0.13 : réglages généraux (jamais le mot de passe Wi-Fi : seul le nom du réseau est lu)
    if path == "/api/wifi/connecter":
        ssid = d.get("ssid") if isinstance(d.get("ssid"), str) else None
        return "Changement de réseau Wi-Fi%s" % (" vers « %s »" % nettoyer(ssid, 40) if ssid else "")
    if path == "/api/wifi/oublier":
        return "Réseau Wi-Fi oublié"
    if path == "/api/wifi/radio":
        return "Wi-Fi %s" % ("allumé" if b.get("actif") is True else "coupé")
    if path == "/api/wifi/scan":
        return "Recherche de réseaux Wi-Fi"
    if path == "/api/bluetooth":
        return "Bluetooth %s" % ("allumé" if b.get("actif") is True else "coupé")
    if path == "/api/bluetooth/scan":
        return "Recherche d'appareils Bluetooth"
    if path == "/api/bluetooth/appareil":
        a = {"appairer": "Appairage", "connecter": "Connexion", "deconnecter": "Déconnexion",
             "oublier": "Oubli"}.get(b.get("action"), "Action")
        return "Bluetooth : %s d'un appareil" % a.lower()
    if path == "/api/bluetooth/pin":
        return "Bluetooth : code d'appairage saisi"
    if path == "/api/general/nom":
        if b.get("hote"):
            return "Nom réseau du Pi changé : %s" % (_nom_sur(b.get("hote")) or "?")
        return "Noms du labo modifiés"
    if path == "/api/general/heure":
        f = b.get("fuseau") if isinstance(b.get("fuseau"), str) and re.match(r"^[A-Za-z_/+-]{1,40}$", b["fuseau"]) else None
        return "Fuseau horaire : %s" % f if f else "Fuseau horaire modifié"
    if path == "/api/general/son":
        return "Réglages du son"
    return "Action %s" % path[len("/api/"):][:60]


def origine(ctx):
    """(origine, qui) : écran du Pi, réseau local (IP), Tailscale (compte ou IP du tailnet)."""
    ctx = ctx or {}
    if ctx.get("local"):
        return "ecran", "Écran du Pi"
    client = str(ctx.get("client") or "?")[:45]
    if ctx.get("tailscale") or ctx.get("tailscale_login"):
        return "tailscale", (ctx.get("tailscale_login") or client)[:120]
    return "reseau", client


class Activite:
    def __init__(self, sys, chemin=FICHIER):
        self.sys, self.chemin = sys, chemin
        self.lock = threading.Lock()
        self.lignes = deque(maxlen=GARDER)
        self.n_fichier = 0
        for l in (sys.read(chemin, "") or "").splitlines():
            try:
                e = json.loads(l)
            except ValueError:
                continue
            if isinstance(e, dict) and isinstance(e.get("t"), (int, float)):
                self.lignes.append(e)
                self.n_fichier += 1

    # --- écriture ------------------------------------------------------------------------------------
    def ajouter(self, e):
        e = {k: v for k, v in e.items() if v is not None}
        with self.lock:
            self.lignes.append(e)
            try:
                self.sys.append(self.chemin, json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n")
                self.n_fichier += 1
                if self.n_fichier > GARDER + MARGE:          # rotation : on garde les 2000 dernières lignes
                    self.sys.write(self.chemin, "".join(json.dumps(x, ensure_ascii=False, separators=(",", ":"))
                                                        + "\n" for x in self.lignes), mode=0o640)
                    self.n_fichier = len(self.lignes)
            except Exception:
                pass                                         # un journal en panne ne bloque jamais une action
        return e

    def action(self, node, path, body, ctx, code, data):
        """Appelé par Node.dispatch après chaque POST."""
        if IGNOREES.match(path) or (ctx or {}).get("client") == "interne" or (ctx or {}).get("sig"):
            return None
        err = None
        if code >= 400:
            c = (data or {}).get("code") if isinstance(data, dict) else None
            if c in ("pin_requis", "pin_non_defini"):
                return None                                  # l'interface redemande le code puis rejoue
            err = nettoyer((data or {}).get("erreur") if isinstance(data, dict) else "Erreur")
        try:
            texte = libelle(node, path, body, data)
        except Exception:
            texte = "Action %s" % path
        o, qui = origine(ctx)
        return self.ajouter({"t": int(self.sys.now()), "type": "action", "texte": texte, "route": path[:80],
                             "origine": o, "qui": qui, "ok": code < 400, "erreur": err})

    def auto(self, texte, ok=True, categorie=None, niveau=None):
        return self.ajouter({"t": int(self.sys.now()), "type": "auto", "texte": nettoyer(texte, 200),
                             "origine": "auto", "qui": "YDZ", "ok": bool(ok), "categorie": categorie,
                             "niveau": niveau})

    def relayer(self, a):
        """Relais de Hub.alerte : chaque alerte devient une ligne « automatique »."""
        if a.get("type") == "test":
            return
        # un nouvel appareil est signalé en « warn » mais n'est pas une erreur ; le reste des warn/crit, si
        ok = a.get("niveau") in ("ok", "info") or a.get("type") == "nouvel_appareil"
        self.auto(a.get("texte"), ok=ok, categorie=a.get("type"),
                  niveau=a.get("niveau"))

    # --- lecture -------------------------------------------------------------------------------------
    def liste(self, limite=200, filtre="tout"):
        with self.lock:
            l = list(self.lignes)
        if filtre == "actions":
            l = [e for e in l if e.get("type") == "action"]
        elif filtre == "auto":
            l = [e for e in l if e.get("type") == "auto"]
        elif filtre == "erreurs":
            l = [e for e in l if not e.get("ok")]
        return l[::-1][:limite]
