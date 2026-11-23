"""Mises à jour automatiques de YDZ OS, publiées comme FICHIERS du dépôt GitHub (1.0.15).

Source : le dépôt PUBLIC Youdzyo/ydz-os (clé maj_depot= de ydz.conf pour en changer, forme propriétaire/dépôt
seulement). Les Releases GitHub ne sont pas créables depuis la session de publication : chaque version est donc
publiée comme fichiers dans le dépôt, sur la branche « main » :
  - paquets/index.json = {"depot", "format": 1, "versions": [{"version", "titre", "fichier", "taille", "sha256", "notes"}]} ;
  - paquets/ydz-1.0.X.zip (le paquet habituel) ;
  - paquets/notes/1.0.X.md (notes de version détaillées, facultatives).

  1. Vérification : toutes les 6 h (première ~5 min après le démarrage, avec un peu de hasard) et à la demande.
     Une seule requête HTTPS vers raw.githubusercontent.com/<dépôt>/main/paquets/index.json ; l'en-tête ETag est
     gardé et renvoyé (If-None-Match) : si rien n'a changé, GitHub répond 304 sans renvoyer l'index.
  2. Téléchargement automatique de toutes les versions en attente, dans l'ordre, vers /var/lib/ydz/maj/ :
     taille bornée (paquet.TAILLE_MAX), taille et SHA-256 comparées à l'index, paquet relu par paquet.lire et
     version de YDZ-PAQUET.json = version annoncée. Téléchargement uniquement en HTTPS depuis
     raw.githubusercontent.com ; chaque redirection est vérifiée (jamais suivie vers un autre hôte). Écriture en
     .part puis renommage : une coupure ne laisse jamais un paquet à moitié écrit passer pour prêt.
  3. Une notification « maj_ydz » par version prête (Hub.alerte : interface, journal d'activité, téléphone).
  4. Installation : une « chaîne » détachée (systemd-run, unité ydz-maj-chaine, script bin/ydz-maj-chaine)
     installe chaque version prête, dans l'ordre, par paquet.installer (comme un dépôt manuel : sauvegarde et
     manifeste pour chacune, « Annuler » marche une version à la fois), attend la fin de son apres.sh et le retour
     de ydzd, puis se relance elle-même avec le code tout juste installé pour la version suivante. Elle s'arrête
     au premier échec. Son avancement est dans /var/lib/ydz/maj/chaine.json, que ydzd lit (jamais l'inverse,
     sauf pour marquer une chaîne coupée par un redémarrage du Pi).
  5. Option « Installer automatiquement la nuit » : à 04:00 (heure du Pi), les versions prêtes s'installent.

Rien ne part sur le réseau en simulation ni dans les tests (fil lancé seulement sur le vrai système).
"""
import hashlib
import json
import os
import random
import re
import threading
import time
from urllib.parse import urljoin, urlsplit

from . import config, paquet
from .core import ApiError

DEPOT_DEFAUT = "Youdzyo/ydz-os"
RE_DEPOT = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
# 1.0.15 : les paquets sont publiés comme FICHIERS du dépôt (les Releases GitHub ne sont pas créables depuis
# la session de publication). Tout est lu sur raw.githubusercontent.com en HTTPS ; confiance = HTTPS + SHA-256.
RAW = "https://raw.githubusercontent.com"
BRANCHE = "main"
HOTES = ("raw.githubusercontent.com",)
HOTES_API = HOTES            # compat : le même hôte sert l'index et les fichiers
HOTES_TELECHARGEMENT = HOTES
ENTETES = {"Accept": "application/json", "User-Agent": "YDZ-OS"}
DOSSIER = "/var/lib/ydz/maj"
CHAINE = "chaine.json"
UNITE = "ydz-maj-chaine"
INTERVALLE = 6 * 3600             # une vérification toutes les 6 h
PREMIERE = 300                    # la première, ~5 min après le démarrage
HASARD = 900                      # jusqu'à 15 min de décalage (tous les Pi du monde ne frappent pas en même temps)
PAUSE_MANUELLE = 20               # deux « Rechercher » coup sur coup : une seule requête
HEURE_NUIT = 4                    # installation automatique à 04:00
MAX_REDIRECTIONS = 5
MAX_LISTE = 2 * 1024 * 1024
NOTES_MAX = 2000
RE_TAG = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?$")
RE_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
RE_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")
RE_BALISE = re.compile(r"<[^>\n]{0,400}>")
RE_SCRIPT = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)


class ErreurMaj(Exception):
    pass


# =============================================================================================
# Outils purs (testés un par un)
# =============================================================================================
def version_tuple(v):
    """« 1.0.14 » -> (1, 0, 14) ; « 1.0.16.1 » (correctif) -> (1, 0, 16, 1). Tout le reste -> None.
    Les tuples se comparent élément par élément : (1,0,16) < (1,0,16,1) < (1,0,17)."""
    m = RE_TAG.match(v.strip()) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups() if x is not None) if m else None


def plus_recente(a, b):
    """Vrai si la version a est strictement plus récente que b (1.0.9 < 1.0.10 < 1.0.14)."""
    ta, tb = version_tuple(a), version_tuple(b)
    return ta is not None and (tb is None or ta > tb)


def hote_autorise(url, hotes):
    """HTTPS, hôte exactement dans la liste, port 443 ou absent, sans identifiants dans l'adresse."""
    try:
        u = urlsplit(url)
        port = u.port
    except (ValueError, TypeError, AttributeError):
        return False
    return (u.scheme == "https" and (u.hostname or "").lower() in hotes and port in (None, 443)
            and not u.username and not u.password and "@" not in u.netloc)


def valider_depot(depot):
    return isinstance(depot, str) and bool(RE_DEPOT.match(depot)) and ".." not in depot


def nettoyer_notes(texte, n=NOTES_MAX):
    """Notes de version : texte simple (markdown léger gardé), sans balise HTML ni caractère de contrôle.
    L'interface échappe tout de même chaque caractère avant d'afficher."""
    t = str(texte or "").replace("\r\n", "\n").replace("\r", "\n")
    t = RE_BALISE.sub("", RE_SCRIPT.sub("", t))
    t = RE_CTRL.sub("", t).replace("\t", "  ")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > n:
        t = t[:n].rsplit("\n", 1)[0].rstrip() + "\n…"
    return t


def resume_notes(notes, lignes=3, n=220):
    """Premières lignes utiles des notes, sans marques markdown : pour la notification."""
    out = []
    for l in (notes or "").splitlines():
        l = re.sub(r"^\s*(?:#{1,6}\s*|[-*+]\s+|\d+[.)]\s+)", "", l)
        l = l.replace("**", "").replace("__", "").replace("`", "").strip()
        if l and l != "…":
            out.append(l.rstrip(".;:"))
        if len(out) >= lignes:
            break
    t = " · ".join(out)
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def url_index(depot):
    return "%s/%s/%s/paquets/index.json" % (RAW, depot, BRANCHE)


def url_fichier(depot, fichier):
    return "%s/%s/%s/paquets/%s" % (RAW, depot, BRANCHE, fichier)


def url_notes(depot, version):
    return "%s/%s/%s/paquets/notes/%s.md" % (RAW, depot, BRANCHE, version)


def filtrer_index(d, installee, depot=DEPOT_DEFAUT):
    """index.json -> versions plus récentes que `installee`, de la plus ancienne à la plus récente.
    index.json : {"depot", "format": 1, "versions": [{"version", "titre", "fichier", "taille", "sha256", "notes"}]}.
    Écartées : forme hors X.Y.Z, fichier différent de « ydz-X.Y.Z.zip », taille/empreinte invalides, trop gros."""
    if not isinstance(d, dict) or not isinstance(d.get("versions"), list):
        return []
    out = {}
    for r in d["versions"]:
        if not isinstance(r, dict):
            continue
        v = r.get("version")
        if version_tuple(v) is None or not plus_recente(v, installee) or v in out:
            continue
        fichier = r.get("fichier") or ("ydz-%s.zip" % v)
        if fichier != "ydz-%s.zip" % v:              # nom imposé : pas de chemin ni d'autre fichier
            continue
        taille = r.get("taille")
        if not isinstance(taille, int) or isinstance(taille, bool) or taille <= 0 or taille > paquet.TAILLE_MAX:
            continue
        sha = r.get("sha256")
        if not isinstance(sha, str) or not RE_HEX.match(sha):
            continue
        titre = r.get("titre") if isinstance(r.get("titre"), str) and r.get("titre").strip() else "YDZ OS %s" % v
        out[v] = {"version": v, "nom": nettoyer_notes(titre, 80).replace("\n", " "), "fichier": fichier,
                  "taille": taille, "sha256": sha.lower(), "notes": nettoyer_notes(r.get("notes") or "")}
    return [out[k] for k in sorted(out, key=version_tuple)]


def heure_txt(t):
    return time.strftime("%H:%M", time.localtime(t))


# =============================================================================================
# Fichiers de /var/lib/ydz/maj
# =============================================================================================
def _chemin(nom, dossier=None):
    return os.path.join(dossier or DOSSIER, nom)


def _ecrire_json(chemin, d):
    tmp = chemin + ".ydz-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, chemin)


def _lire_json(chemin):
    try:
        with open(chemin, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def sha256_fichier(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fiche(version, dossier=None):
    """Fiche d'un paquet téléchargé et vérifié (ydz-1.0.X.json), ou None s'il n'est pas prêt."""
    if version_tuple(version) is None:
        return None
    f = _lire_json(_chemin("ydz-%s.json" % version, dossier))
    z = _chemin("ydz-%s.zip" % version, dossier)
    try:
        taille = os.path.getsize(z)
    except OSError:
        return None
    if not f or f.get("version") != version or f.get("taille") != taille or not RE_HEX.match(str(f.get("sha256"))):
        return None
    return f


def lire_chaine(dossier=None):
    return _lire_json(_chemin(CHAINE, dossier))


def ecrire_chaine(ch, dossier=None):
    os.makedirs(dossier or DOSSIER, mode=0o700, exist_ok=True)
    _ecrire_json(_chemin(CHAINE, dossier), ch)


def effacer_version(version, dossier=None):
    for ext in (".zip", ".zip.part", ".json"):
        try:
            os.remove(_chemin("ydz-%s%s" % (version, ext), dossier))
        except OSError:
            pass


# =============================================================================================
# La chaîne d'installation (exécutée par bin/ydz-maj-chaine, HORS de ydzd)
# =============================================================================================
class OutilsChaine:
    """Ce dont la chaîne a besoin du système ; remplacé par un faux dans les tests."""

    def __init__(self, base):
        self.base = base

    def now(self):
        return time.time()

    def installer(self, brut):
        return paquet.installer(self.base, brut, lambda: None)      # ydzd est relancé par la chaîne, plus bas

    def version(self):
        try:
            with open(os.path.join(self.base, "VERSION"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return "0.0.0"

    def position_journal(self):
        try:
            return os.path.getsize(paquet.JOURNAL)
        except OSError:
            return 0

    def code_apres(self, depuis):
        """Code de sortie de apres.sh, lu dans paquets.log après la position `depuis` (None si absent)."""
        try:
            with open(paquet.JOURNAL, "rb") as f:
                f.seek(depuis)
                texte = f.read().decode("utf-8", "replace")
        except OSError:
            return None
        m = re.findall(r"apres\.sh : fin \(code (\d+)\)", texte)
        return int(m[-1]) if m else None

    def _systemctl(self, *args):
        import subprocess
        try:
            return subprocess.run(["systemctl"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=60).stdout.decode("utf-8", "replace").strip()
        except Exception:
            return ""

    def attendre_unite(self, unite, delai):
        """Attend qu'une unité systemd (apres.sh) se termine. Faux si elle tourne encore après `delai`."""
        fin = time.time() + delai
        while time.time() < fin:
            if self._systemctl("is-active", unite) not in ("active", "activating", "deactivating", "reloading"):
                return True
            time.sleep(3)
        return False

    def redemarrer_demon(self):
        self._systemctl("restart", "ydzd")

    def attendre_demon(self, version, delai):
        """ydzd répond-il sur /api/ping avec la bonne version ? (port de ydz.conf, 8787 par défaut)"""
        import urllib.request
        port = 8787
        try:
            vals, _ = config.parse_conf(open("/etc/ydz/ydz.conf", encoding="utf-8").read())
            port = int(vals.get("port", port))
        except (OSError, ValueError):
            pass
        fin = time.time() + delai
        time.sleep(4)
        while time.time() < fin:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=4) as r:
                    d = json.loads(r.read().decode("utf-8", "replace"))
                if d.get("version") == version:
                    time.sleep(3)          # le temps que ses fils démarrent
                    return True
            except Exception:
                pass
            time.sleep(3)
        return False

    def annuler(self):
        return paquet.annuler(self.base, None, paquet.relancer_apres_annulation)

    def journal(self, texte):
        paquet._journal("chaîne de mises à jour : " + texte)


def etape_chaine(outils, dossier=None, attente_apres=3600, attente_demon=300):
    """Installe LA version suivante de la chaîne. Renvoie « continuer » (le script se relance alors avec le code
    neuf), « fini » ou « echec ». Toute l'histoire est dans chaine.json."""
    ch = lire_chaine(dossier)
    if not ch or ch.get("etat") != "en_cours":
        return "fini"
    versions = [v for v in ch.get("versions") or [] if version_tuple(v)]
    i = int(ch.get("index") or 0)
    total = len(versions)

    def sauver(**k):
        ch.update(k, maj=int(outils.now()))
        ecrire_chaine(ch, dossier)

    def echec(v, msg):
        outils.journal("ÉCHEC sur %s : %s" % (v, msg))
        sauver(etat="echec", erreur="%s : %s" % (v, msg), version=v, texte="Échec sur %s" % v, fin=int(outils.now()))
        return "echec"

    if i >= total:
        sauver(etat="fini", fin=int(outils.now()), texte="Terminé")
        outils.journal("terminée (%s)" % (", ".join(x["version"] for x in ch.get("installees", [])) or "rien"))
        return "fini"
    v = versions[i]
    if not plus_recente(v, outils.version()):        # déjà là (reprise après coupure, ou version annulée puis…)
        outils.journal("%s déjà installée : suivante" % v)
        sauver(index=i + 1)
        return "continuer"
    texte = "Installation %d/%d : %s…" % (i + 1, total, v)
    sauver(version=v, texte=texte, etape="verification")
    outils.journal(texte)
    f = fiche(v, dossier)
    z = _chemin("ydz-%s.zip" % v, dossier)
    if not f:
        return echec(v, "paquet absent ou incomplet (relance « Rechercher des mises à jour »).")
    try:
        if sha256_fichier(z) != f["sha256"].lower():
            effacer_version(v, dossier)
            return echec(v, "empreinte SHA-256 différente : le fichier a été effacé, il sera retéléchargé.")
        with open(z, "rb") as fh:
            brut = fh.read()
        infos, _, _ = paquet.lire(brut)
        if infos.get("version") != v:
            return echec(v, "le paquet annonce la version %s." % str(infos.get("version"))[:20])
    except (OSError, paquet.ErreurPaquet) as ex:
        return echec(v, str(ex))
    pos = outils.position_journal()
    sauver(etape="installation")
    try:
        res = outils.installer(brut)
    except (OSError, paquet.ErreurPaquet) as ex:
        return echec(v, "installation impossible : %s" % ex)
    horo = os.path.basename(str(res.get("sauvegarde") or ""))
    ch.setdefault("installees", []).append({"version": v, "sauvegarde": horo, "t": int(outils.now())})
    sauver(etape="apres")
    if res.get("script"):
        if not outils.attendre_unite("ydz-paquet-" + horo, attente_apres):
            return echec(v, "son script d'installation tourne encore après %d min : la chaîne s'arrête là."
                         % (attente_apres // 60))
        code = outils.code_apres(pos)
        if code not in (None, 0):
            return echec(v, "son script d'installation a échoué (code %d). Détail : /var/log/ydz/paquets.log." % code)
    elif res.get("redemarrage_demon"):
        outils.redemarrer_demon()
    sauver(etape="redemarrage")
    if not outils.attendre_demon(v, attente_demon):
        # YDZ ne revient pas avec cette version : on la retire tout de suite (personne n'est peut-être devant)
        msg = "YDZ ne répondait plus après l'installation"
        try:
            outils.annuler()
            ch["installees"] = ch["installees"][:-1]
            msg += " : la version a été annulée automatiquement."
        except Exception as ex:
            msg += " (annulation automatique impossible : %s)." % str(ex)[:120]
        return echec(v, msg)
    effacer_version(v, dossier)
    outils.journal("%s installée" % v)
    sauver(index=i + 1, etape=None)
    if i + 1 >= total:
        sauver(etat="fini", fin=int(outils.now()), texte="Terminé")
        outils.journal("terminée : %s" % ", ".join(x["version"] for x in ch["installees"]))
        return "fini"
    return "continuer"


# =============================================================================================
# Côté ydzd : vérification, téléchargement, notifications, lancement de la chaîne
# =============================================================================================
class MajYdz:
    def __init__(self, hub):
        self.hub = hub
        self.node = hub.node
        self.sys = hub.sys
        self.state = hub.node.state
        self.reel = type(self.sys).__name__ == "System"
        d = self.node.conf.vals.get("maj_depot") or DEPOT_DEFAUT
        self.depot = d if valider_depot(d) else DEPOT_DEFAUT
        self.version_fn = config.version
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.verification = False
        self.telechargement = None          # version en cours de téléchargement
        self.prochaine = None
        self._chaine = (None, None)         # (mtime, contenu) : chaine.json relu seulement s'il a changé
        self._derniere_manuelle = 0

    # --- état persistant (une écriture par vérification au plus : carte SD) ------------------------
    def _etat(self):
        e = self.state.get("maj_ydz")
        return dict(e) if isinstance(e, dict) else {}

    def _regler(self, **k):
        e = self._etat()
        e.update(k)
        self.state.set("maj_ydz", e)
        return e

    def installee(self):
        return self.version_fn()

    def en_attente(self):
        """Versions publiées plus récentes que celle installée (liste gardée de la dernière vérification)."""
        inst = self.installee()
        return [r for r in self._etat().get("releases") or [] if isinstance(r, dict) and plus_recente(r.get("version"), inst)]

    def pretes(self):
        """Les versions prêtes à installer, dans l'ordre, sans trou (une version manquante arrête la liste :
        les apres.sh de chaque version doivent tous passer)."""
        out = []
        for r in self.en_attente():
            if not fiche(r["version"]):
                break
            out.append(r["version"])
        return out

    # --- requêtes GitHub (redirections vérifiées une à une) ------------------------------------------
    def _requete(self, url, hotes, entetes=None, max_octets=MAX_LISTE, timeout=20):
        for _ in range(MAX_REDIRECTIONS + 1):
            if not hote_autorise(url, hotes):
                raise ErreurMaj("Adresse refusée (%s) : seuls %s sont autorisés en HTTPS."
                                % ((urlsplit(url).hostname or "?")[:60] if "://" in str(url) else "?", ", ".join(hotes)))
            code, ent, corps = self.sys.http_brut("GET", url, dict(entetes or {}), timeout=timeout, max_octets=max_octets)
            if code in (301, 302, 303, 307, 308):
                loc = ent.get("location")
                if not loc:
                    raise ErreurMaj("Redirection sans adresse.")
                url = urljoin(url, loc)
                entetes = {k: v for k, v in (entetes or {}).items() if k.lower() not in ("if-none-match", "authorization")}
                continue
            return code, ent, corps
        raise ErreurMaj("Trop de redirections.")

    def _telecharger(self, url, max_octets, timeout=300):
        code, ent, corps = self._requete(url, HOTES_TELECHARGEMENT, {"User-Agent": "YDZ-OS",
                                                                      "Accept": "application/octet-stream"},
                                         max_octets=max_octets, timeout=timeout)
        if code == -1:
            raise ErreurMaj("fichier trop gros (%d Mo au maximum)." % (max_octets // (1024 * 1024) or 1))
        if code == 0:
            raise ErreurMaj("GitHub injoignable pendant le téléchargement.")
        if code != 200 or corps is None:
            raise ErreurMaj("GitHub a répondu %d au téléchargement." % code)
        return corps

    # --- 1. vérification -------------------------------------------------------------------------------
    def verifier(self, manuel=False, task=None):
        """Interroge GitHub puis télécharge ce qui manque. Renvoie l'état (dict de status())."""
        with self.lock:
            if self.verification:
                return self.status()
            self.verification = True
        try:
            self._verifier(manuel, task)
        finally:
            self.verification = False
            self.telechargement = None
        return self.status()

    def _verifier(self, manuel, task):
        now = self.sys.now()
        e = self._etat()
        attente = e.get("reessai_apres") or 0
        if attente > now:
            self._planifier(attente + random.randint(30, 300))
            if task:
                task.log(e.get("erreur") or "GitHub demande d'attendre.")
            return
        inst = self.installee()
        entetes = dict(ENTETES)
        cache = e.get("releases") if isinstance(e.get("releases"), list) else None
        # le cache ne vaut que pour une version installée au moins aussi récente qu'à sa création (après un
        # « Annuler », d'anciennes versions redeviennent intéressantes : liste complète)
        if e.get("etag") and cache is not None and not plus_recente(e.get("base") or "0.0.0", inst):
            entetes["If-None-Match"] = e["etag"]
        url = url_index(self.depot)
        if task:
            task.log("Lecture de l'index des mises à jour (%s)…" % self.depot)
        try:
            code, ent, corps = self._requete(url, HOTES, entetes)
        except ErreurMaj as ex:
            return self._fin_verif(now, erreur=str(ex))
        if code == 304:
            releases = cache
            etag = e.get("etag")
        elif code == 200:
            try:
                index = json.loads((corps or b"").decode("utf-8", "replace"))
            except ValueError:
                return self._fin_verif(now, erreur="Index des mises à jour illisible.")
            if not isinstance(index, dict) or index.get("format") != 1:
                return self._fin_verif(now, erreur="Index des mises à jour au format inattendu.")
            releases = filtrer_index(index, inst, self.depot)
            etag = ent.get("etag")
        elif code == 404:
            return self._fin_verif(now, erreur="Index des mises à jour introuvable sur GitHub (%s)." % self.depot)
        elif code == 0:
            return self._fin_verif(now, erreur="GitHub injoignable : pas d'Internet pour l'instant ?")
        else:
            return self._fin_verif(now, erreur="GitHub a répondu %d. Nouvel essai plus tard." % code)
        releases = [r for r in releases or [] if plus_recente(r.get("version"), inst)]
        self._regler(releases=releases, etag=etag if isinstance(etag, str) and len(etag) < 200 else None, base=inst)
        if task:
            task.log("%d version%s plus récente%s que %s." % (len(releases), "s" * (len(releases) > 1),
                                                              "s" * (len(releases) > 1), inst) if releases
                     else "YDZ OS %s est la dernière version." % inst)
        erreur = self.telecharger_tout(releases, task)
        self._fin_verif(now, erreur=erreur)

    def _fin_verif(self, now, erreur=None, reessai=None):
        self._regler(derniere=int(now), erreur=erreur, reessai_apres=reessai)
        if not reessai:                                     # sinon : déjà planifié à l'heure donnée par GitHub
            self._planifier(now + INTERVALLE + random.randint(0, HASARD))
        return erreur

    def _planifier(self, t_mur):
        self.prochaine = self.sys.monotonic() + max(60, t_mur - self.sys.now())

    # --- 2. téléchargement ---------------------------------------------------------------------------
    def telecharger_tout(self, releases, task=None):
        """Télécharge et vérifie, dans l'ordre, ce qui n'est pas encore prêt. S'arrête à la première erreur
        (les suivantes attendront : on installe toujours dans l'ordre). Renvoie l'erreur ou None."""
        self.nettoyer({r["version"] for r in releases})
        for r in releases:
            if fiche(r["version"]):
                continue
            self.telechargement = r["version"]
            if task:
                task.log("Téléchargement de YDZ OS %s…" % r["version"])
            try:
                self.telecharger_une(r)
            except (ErreurMaj, paquet.ErreurPaquet) as ex:
                effacer_version(r["version"])
                msg = "YDZ OS %s : %s" % (r["version"], ex)
                paquet._journal("mises à jour : téléchargement refusé, " + msg)
                return msg
            except OSError as ex:
                effacer_version(r["version"])
                return "YDZ OS %s : écriture impossible dans %s (%s)." % (r["version"], DOSSIER, ex.strerror or ex)
            finally:
                self.telechargement = None
            if task:
                task.log("  %s vérifiée, prête à installer." % r["version"])
            self.annoncer(r)
        return None

    def telecharger_une(self, r):
        """Télécharge le zip du dépôt, vérifie taille + SHA-256 (de l'index) + version (paquet.lire)."""
        v = r["version"]
        nom = "ydz-%s.zip" % v
        brut = self._telecharger(url_fichier(self.depot, r["fichier"]), paquet.TAILLE_MAX)
        if len(brut) != r["taille"]:
            raise ErreurMaj("taille différente de celle annoncée par l'index (téléchargement coupé ?).")
        obtenu = hashlib.sha256(brut).hexdigest()
        if obtenu != r["sha256"]:
            raise ErreurMaj("empreinte SHA-256 différente de celle publiée : fichier refusé.")
        infos, _, _ = paquet.lire(brut)
        if infos.get("version") != v:
            raise ErreurMaj("le paquet annonce la version « %s » au lieu de %s : refusé."
                            % (str(infos.get("version"))[:20], v))
        notes = r.get("notes") or ""
        try:                                 # notes détaillées (paquets/notes/X.md) : jolies mais facultatives
            code, _, corps = self._requete(url_notes(self.depot, v), HOTES, {"User-Agent": "YDZ-OS"},
                                           max_octets=64 * 1024, timeout=15)
            if code == 200 and corps:
                notes = nettoyer_notes(corps.decode("utf-8", "replace")) or notes
        except ErreurMaj:
            pass
        os.makedirs(DOSSIER, mode=0o700, exist_ok=True)
        part = _chemin(nom + ".part")
        with open(part, "wb") as f:
            f.write(brut)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(part, 0o600)
        os.replace(part, _chemin(nom))
        r["notes"] = notes                   # mémorisé pour la notification et l'interface
        _ecrire_json(_chemin("ydz-%s.json" % v), {"version": v, "nom": r.get("nom"), "taille": len(brut),
                                                    "sha256": obtenu, "notes": notes, "verifie_le": int(self.sys.now())})

    def nettoyer(self, gardees=None):
        """Efface les fichiers des versions déjà installées (ou retirées de GitHub) et les .part abandonnés."""
        inst = self.installee()
        try:
            noms = os.listdir(DOSSIER)
        except OSError:
            return
        for n in noms:
            m = re.match(r"^ydz-(\d+\.\d+\.\d+)\.(zip|zip\.part|json)$", n)
            if not m:
                continue
            v = m.group(1)
            if m.group(2) == "zip.part" or not plus_recente(v, inst) or (gardees is not None and v not in gardees):
                try:
                    os.remove(_chemin(n))
                except OSError:
                    pass

    # --- 3. notification -------------------------------------------------------------------------------
    def annoncer(self, r):
        """Une notification par version prête, une seule fois."""
        e = self._etat()
        vues = [v for v in e.get("notifiees") or [] if plus_recente(v, self.installee())]
        if r["version"] in vues:
            return False
        vues.append(r["version"])
        self._regler(notifiees=vues[-20:])
        res = resume_notes(r.get("notes"))
        self.hub.alerte("maj_ydz", "info", "YDZ OS %s est prête à installer%s" % (
            r["version"], " : " + res if res else "."), version=r["version"])
        return True

    # --- 4. chaîne d'installation -----------------------------------------------------------------------
    def chaine(self):
        try:
            mt = os.stat(_chemin(CHAINE)).st_mtime
        except OSError:
            self._chaine = (None, None)
            return None
        if self._chaine[0] != mt:
            self._chaine = (mt, lire_chaine())
        return self._chaine[1]

    def chaine_active(self):
        ch = self.chaine()
        return bool(ch and ch.get("etat") == "en_cours")

    def installer(self, origine="manuel"):
        """Lance la chaîne détachée pour toutes les versions prêtes."""
        if self.chaine_active():
            raise ApiError(409, "Une installation de mises à jour est déjà en cours.", "occupe")
        if self.verification and self.telechargement:
            raise ApiError(409, "Téléchargement de YDZ OS %s en cours : réessaie dans un instant." % self.telechargement,
                           "occupe")
        versions = self.pretes()
        if not versions:
            raise ApiError(409, "Aucune mise à jour prête à installer.", "rien")
        horo = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.sys.now()))
        ch = {"id": horo, "origine": origine, "versions": versions, "index": 0, "etat": "en_cours",
              "debut": int(self.sys.now()), "maj": int(self.sys.now()), "version_depart": self.installee(),
              "texte": "Installation 1/%d : %s…" % (len(versions), versions[0]), "installees": [], "erreur": None}
        ecrire_chaine(ch)
        self._chaine = (None, None)
        paquet._journal("chaîne de mises à jour (%s) : %s" % (origine, ", ".join(versions)))
        script = os.path.join(self.node.base, "bin", "ydz-maj-chaine")
        if self.reel:
            import subprocess
            cmd = ["systemd-run", "--quiet", "--collect", "--no-block", "--unit=" + UNITE,
                   "--setenv=PYTHONDONTWRITEBYTECODE=1",
                   "--property=StandardOutput=append:" + paquet.JOURNAL,
                   "--property=StandardError=append:" + paquet.JOURNAL,
                   "/usr/bin/python3", script]
            try:
                r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
                ok = r.returncode == 0
                detail = r.stdout.decode("utf-8", "replace").strip()[:160]
            except (OSError, subprocess.TimeoutExpired) as ex:
                ok, detail = False, str(ex)
            if not ok:
                ch.update(etat="echec", erreur="Lancement impossible : %s" % detail, fin=int(self.sys.now()))
                ecrire_chaine(ch)
                raise ApiError(500, "L'installation n'a pas pu démarrer (%s)." % detail, "lancement")
        else:
            self.sys.spawn(["ydz-maj-chaine", horo])
        return ch

    def observer_chaine(self):
        """Fin de chaîne -> notification et journal (une fois). Chaîne coupée par un redémarrage -> marquée."""
        ch = self.chaine()
        if not ch or not ch.get("id"):
            return None
        if ch.get("etat") == "en_cours":
            if (self.sys.now() - (ch.get("maj") or 0) > 120 and self._unite_finie()):
                ch.update(etat="interrompue", fin=int(self.sys.now()),
                          erreur="L'installation s'est arrêtée en route (redémarrage du Pi ?). Les versions "
                                 "restantes sont toujours prêtes : relance « Installer ».")
                ecrire_chaine(ch)
                self._chaine = (None, None)
            else:
                return None
        if self._etat().get("chaine_vue") == ch["id"]:
            return None
        self._regler(chaine_vue=ch["id"])
        faites = [x.get("version") for x in ch.get("installees") or [] if isinstance(x, dict)]
        nuit = ch.get("origine") == "nuit"
        if faites:
            self.hub.alerte("maj_ydz", "ok", "Mise à jour installée%s : %s%s. En cas de souci : Réglages > "
                            "Mises à jour > Annuler ce paquet." % ("s" if len(faites) > 1 else "", ", ".join(faites),
                                                                  " (cette nuit)" if nuit else ""), versions=faites)
        if ch.get("etat") in ("echec", "interrompue"):
            self.hub.alerte("maj_ydz", "warn", "Mise à jour de YDZ OS arrêtée : %s" % (ch.get("erreur") or "raison inconnue"))
        return ch

    def _unite_finie(self):
        r = self.sys.run(["systemctl", "is-active", UNITE], timeout=10)
        return r.out.strip() not in ("active", "activating", "deactivating", "reloading")

    # --- 5. la nuit --------------------------------------------------------------------------------------
    def planifier_nuit(self):
        """À 04:00 (heure du Pi), si l'option est cochée et que des versions sont prêtes : installation."""
        e = self._etat()
        if not e.get("auto_nuit"):
            return False
        t = time.localtime(self.sys.now())
        jour = time.strftime("%Y-%m-%d", t)
        if t.tm_hour != HEURE_NUIT or e.get("nuit_jour") == jour or self.chaine_active() or self.verification:
            return False
        if not self.pretes():
            return False
        self._regler(nuit_jour=jour)
        try:
            self.installer("nuit")
        except ApiError:
            return False
        return True

    def regler(self, b):
        if "auto_nuit" in b:
            if not isinstance(b["auto_nuit"], bool):
                raise ApiError(400, "« auto_nuit » doit valoir vrai ou faux.", "auto_nuit")
            self._regler(auto_nuit=b["auto_nuit"])
        return self.status()

    # --- boucle (vrai système seulement) -----------------------------------------------------------------
    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-maj-ydz", daemon=True).start()

    def premiere_echeance(self):
        """Secondes avant la première vérification : ~5 min, ou plus si la dernière date de moins de 6 h
        (ydzd redémarre à chaque paquet : on ne réinterroge pas GitHub à chaque fois)."""
        now = self.sys.now()
        derniere = self._etat().get("derniere") or 0
        attente = max(PREMIERE + random.randint(0, 120), derniere + INTERVALLE - now)
        reessai = self._etat().get("reessai_apres") or 0
        return max(attente, reessai - now)

    def _boucle(self):
        self.prochaine = self.sys.monotonic() + self.premiere_echeance()
        try:
            self.nettoyer()
        except Exception:
            pass
        while not self.stop.is_set():
            try:
                self.observer_chaine()
                self.planifier_nuit()
                if self.sys.monotonic() >= self.prochaine and not self.chaine_active():
                    self.prochaine = self.sys.monotonic() + INTERVALLE      # garde-fou si verifier() plante
                    self.verifier()
            except Exception as ex:
                print("ydzd : mises à jour : %r" % ex, flush=True)
            self.stop.wait(30)

    def lancer_verification(self):
        """« Rechercher des mises à jour » : une tâche (deux appuis rapprochés = une seule requête)."""
        if self.chaine_active():
            raise ApiError(409, "Une installation est en cours : attends qu'elle se termine.", "occupe")
        if self.sys.monotonic() - self._derniere_manuelle < PAUSE_MANUELLE:
            return None
        self._derniere_manuelle = self.sys.monotonic()

        def job(task):
            self.verifier(True, task)
            e = self._etat()
            if e.get("erreur"):
                raise ApiError(502, e["erreur"], "maj_ydz")
            p = self.pretes()
            return ("Prête%s à installer : %s." % ("s" if len(p) > 1 else "", ", ".join(p))) if p \
                else "Tu es à jour (YDZ OS %s)." % self.installee()
        return self.node.tasks.start("maj_ydz", "Recherche de mises à jour de YDZ OS", job, None, "ydz:maj")

    # --- pour /api/etat -------------------------------------------------------------------------------------
    def status(self):
        e = self._etat()
        ch = self.chaine()
        en_cours = bool(ch and ch.get("etat") == "en_cours")
        dispo = []
        for r in self.en_attente():
            f = fiche(r["version"])
            dispo.append({"version": r["version"], "nom": r.get("nom"), "notes": r.get("notes") or "",
                          "taille": (f or {}).get("taille") or r.get("zip", {}).get("taille"), "prete": bool(f),
                          "publiee": r.get("publiee"),
                          "telechargement": self.telechargement == r["version"]})
        prog = None
        if ch and ch.get("id"):
            vs = ch.get("versions") or []
            prog = {"id": ch.get("id"), "etat": ch.get("etat"), "origine": ch.get("origine"), "total": len(vs),
                    "index": min(int(ch.get("index") or 0), len(vs)), "version": ch.get("version"),
                    "texte": ch.get("texte"), "etape": ch.get("etape"), "erreur": ch.get("erreur"),
                    "installees": [x.get("version") for x in ch.get("installees") or [] if isinstance(x, dict)],
                    "debut": ch.get("debut"), "fin": ch.get("fin")}
        return {"version_installee": self.installee(), "depot": self.depot, "disponibles": dispo,
                "derniere_verification": e.get("derniere"), "erreur": e.get("erreur"),
                "auto_nuit": bool(e.get("auto_nuit")), "heure_nuit": "%02d:00" % HEURE_NUIT,
                "en_cours": en_cours, "verification": self.verification, "progression": prog}
