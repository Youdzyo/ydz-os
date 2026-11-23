"""Disque système (1.0.21) : répartition poste par poste de ce qui occupe « / »,
aperçu et récupération d'espace (« Libérer de l'espace »), et garde-fous.

Tout est lu par sous-processus bornés (df, docker system df, du, journalctl),
en stdlib, et parsé en Python. Les actions destructrices (prune Docker,
suppression de vieilles sauvegardes, apt clean, rotation des journaux) sont
réservées au niveau « action » (écran local ou code PIN) et ne touchent JAMAIS
les images ni les volumes d'une app qui tourne : seules des sous-commandes
Docker fixes sur l'INUTILISÉ sont lancées, et les seules sauvegardes effaçables
sont des sous-dossiers horodatés validés de /var/lib/ydz/sauvegardes/.

Garde-fous :
  - répartition + aperçu disponibles même disque presque plein (ydzd est déjà en
    mémoire, aucune grosse écriture ici) ;
  - réserve d'espace : on garde toujours RESERVE octets libres pour qu'une mise à
    jour OTA puisse s'installer ; une opération YDZ qui passerait sous ce seuil
    est refusée (voir espace_libre / reserve_bloque) ;
  - l'alerte progressive (≥85 % / ≥95 %) réutilise le veilleur existant (notifs.py)
    qui appelle Hub.alerte (relayé vers Discord/ntfy).
"""
import math
import re

from .core import ApiError

SAUVEGARDES = "/var/lib/ydz/sauvegardes"
APT_CACHE = "/var/cache/apt/archives"
RE_HORO = re.compile(r"^\d{8}-\d{6}(-\d+)?$")

RESERVE_OCTETS = 1_500_000_000          # ~1,5 Go gardés libres pour l'OTA
GARDER_SAUV = 3                         # sauvegardes de paquets gardées par défaut
JOURNAUX_PLANCHER = 100_000_000         # on ne descend pas les journaux sous ~100 Mo
SEUIL_WARN, SEUIL_CRIT = 85, 95         # mêmes seuils que le veilleur

POSTES_LIBERABLES = ("docker", "sauvegardes", "apt", "journaux")

# Unités : docker écrit « 1.2GB » (base 1000) ; df -h / journalctl écrivent « 1.2G » (base 1024).
_UNITES = {
    "": 1, "b": 1,
    "k": 1024, "kb": 1000, "kib": 1024,
    "m": 1024**2, "mb": 1000**2, "mib": 1024**2,
    "g": 1024**3, "gb": 1000**3, "gib": 1024**3,
    "t": 1024**4, "tb": 1000**4, "tib": 1024**4,
    "p": 1024**5, "pb": 1000**5, "pib": 1024**5,
}


def taille_octets(texte):
    """« 1.2GB », « 512MB », « 1.5G », « 0B », « 900 kB » -> octets (int). 0 si illisible."""
    if texte is None:
        return 0
    m = re.match(r"^\s*([\d.,]+)\s*([a-zA-Z]*)\s*$", str(texte))
    if not m:
        return 0
    try:
        val = float(m.group(1).replace(",", "."))
    except (ValueError, OverflowError):
        return 0
    # A-21-01 : un nombre assez grand déborde vers inf ; int(round(inf)) lèverait OverflowError.
    if not math.isfinite(val):
        return 0
    return int(round(val * _UNITES.get(m.group(2).lower(), 1)))


def reserve_configuree(state):
    """Réserve d'espace effective (octets) : l'override « disque_reserve_octets » s'il est réglé,
    sinon le défaut RESERVE_OCTETS. Source unique pour DisqueSysteme.reserve() ET apps.install (N-2)."""
    try:
        v = int(state.get("disque_reserve_octets") or 0)
    except (TypeError, ValueError):
        v = 0
    return v if v > 0 else RESERVE_OCTETS


def octets_fr(n):
    """Taille lisible en français (1,2 Go)."""
    n = max(0, int(n or 0))
    for unite, seuil in (("To", 1000**4), ("Go", 1000**3), ("Mo", 1000**2), ("ko", 1000)):
        if n >= seuil:
            v = n / seuil
            return ("%.1f %s" % (v, unite)).replace(".", ",") if v < 100 else "%d %s" % (round(v), unite)
    return "%d o" % n


def parse_df(texte):
    """`df -B1 /` -> {total, utilise, libre, pct}. None si illisible.
    On lit les champs par la DROITE : un nom de système de fichiers long peut
    passer à la ligne, mais les colonnes de taille restent collées au montage."""
    lignes = [l for l in (texte or "").splitlines() if l.strip()]
    if len(lignes) < 2:
        return None
    champs = lignes[-1].split()
    if len(champs) < 5:
        # ligne repliée : le point de montage est seul, les tailles sont au-dessus
        champs = (lignes[-2] + " " + lignes[-1]).split()
    if len(champs) < 5:
        return None
    try:
        total = int(champs[-5])
        utilise = int(champs[-4])
        libre = int(champs[-3])
        pct = int(champs[-2].rstrip("%"))
    except (ValueError, IndexError):
        return None
    if total <= 0:
        return None
    return {"total": total, "utilise": utilise, "libre": libre, "pct": pct}


def parse_docker_df(texte):
    """`docker system df --format '{{.Type}}|{{.Size}}|{{.Reclaimable}}'`
    -> {type: {taille, recuperable}}. Types attendus : images, conteneurs,
    volumes, cache."""
    cles = {"images": "images", "containers": "conteneurs", "local volumes": "volumes",
            "build cache": "cache"}
    out = {}
    for ligne in (texte or "").splitlines():
        parts = ligne.split("|")
        if len(parts) < 3:
            continue
        cle = cles.get(parts[0].strip().lower())
        if not cle:
            continue
        # « 1.2GB (48%) » -> on garde le premier mot
        recup = parts[2].strip().split()[0] if parts[2].strip() else "0"
        out[cle] = {"taille": taille_octets(parts[1]), "recuperable": taille_octets(recup)}
    return out


def parse_ps_size(texte):
    """`docker ps -a --size --format '{{.Names}}|{{.Size}}'` -> {nom: couche_octets}.
    La taille Docker est « 1.2GB (virtual 3.4GB) » : on garde la couche inscriptible
    (le premier nombre), pas la taille virtuelle partagée avec l'image."""
    out = {}
    for ligne in (texte or "").splitlines():
        if "|" not in ligne:
            continue
        nom, taille = ligne.split("|", 1)
        premier = taille.strip().split("(")[0]
        out[nom.strip()] = taille_octets(premier)
    return out


def parse_du(texte):
    """`du -sb <dir> [<dir>...]` -> {chemin: octets}. Tolère la sortie vide."""
    out = {}
    for ligne in (texte or "").splitlines():
        parts = ligne.split("\t") if "\t" in ligne else ligne.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            out[parts[1].strip()] = int(parts[0])
        except ValueError:
            continue
    return out


def parse_journal_usage(texte):
    """`journalctl --disk-usage` -> octets. 0 si illisible.
    « Archived and active journals take up 1.5G in the file system. »"""
    m = re.search(r"take up\s+([\d.,]+\s*[A-Za-z]+)\s+in the file", texte or "")
    if not m:
        m = re.search(r"([\d.,]+\s*[KMGT]i?B?)\b", texte or "")
    return taille_octets(m.group(1)) if m else 0


def parse_reclaimed(texte):
    """Somme des « Total reclaimed space: X » d'une ou plusieurs sorties de prune."""
    total = 0
    for m in re.finditer(r"Total reclaimed space:\s*([\d.,]+\s*[A-Za-z]+)", texte or ""):
        total += taille_octets(m.group(1))
    return total


def _cle_horo(nom):
    base = nom.split("-")
    suffixe = int(base[2]) if len(base) > 2 and base[2].isdigit() else 0
    return (nom[:15], suffixe)


def sauvegardes_triees(sys):
    """Sous-dossiers horodatés de SAUVEGARDES, du plus ancien au plus récent."""
    noms = [n for n in sys.listdir(SAUVEGARDES)
            if RE_HORO.match(n) and sys.exists("%s/%s" % (SAUVEGARDES, n))]
    return sorted(noms, key=_cle_horo)


class DisqueSysteme:
    def __init__(self, sys, state, tasks, apps=None):
        self.sys, self.state, self.tasks, self.apps = sys, state, tasks, apps
        self._cache = None                 # (monotonic, répartition) : évite de relancer du/docker trop souvent

    # --- outils -----------------------------------------------------------
    def _docker(self):
        return bool(self.sys.which("docker"))

    def reserve(self):
        return reserve_configuree(self.state)

    def espace_libre(self):
        """Octets libres sur « / », ou None si illisible."""
        v = self.sys.statvfs("/")
        return v[1] if v else None

    def reserve_bloque(self, octets_requis=0):
        """(bloque, message) : vrai si écrire `octets_requis` ferait passer « / »
        sous la réserve. Sert aux autres opérations YDZ (installer une app…) pour
        ne jamais remplir le disque et garder la place d'une mise à jour OTA."""
        libre = self.espace_libre()
        if libre is None:
            return False, None
        reste = libre - max(0, int(octets_requis or 0))
        if reste < self.reserve():
            return True, ("Le disque système est presque plein : il ne resterait pas les %s à garder libres "
                          "pour les mises à jour. Libère de l'espace dans Stockage d'abord."
                          % octets_fr(self.reserve()))
        return False, None

    # --- lecture : répartition -------------------------------------------
    def _du(self, *chemins):
        chemins = [c for c in chemins if c]
        if not chemins:
            return {}
        r = self.sys.run(["du", "-sb", "--"] + chemins, timeout=60)
        return parse_du(r.out) if r.ok else {}

    def _taille_sauvegardes(self):
        noms = sauvegardes_triees(self.sys)
        if not noms:
            return 0, {}
        tailles = self._du(*["%s/%s" % (SAUVEGARDES, n) for n in noms])
        par_nom = {n: tailles.get("%s/%s" % (SAUVEGARDES, n), 0) for n in noms}
        return sum(par_nom.values()), par_nom

    def repartition(self, conteneurs=None):
        """Poste par poste, ce qui occupe « / ». Résilient : un sous-processus en
        échec (docker absent, du lent) ne casse jamais la vue."""
        now = self.sys.monotonic()
        if self._cache and now - self._cache[0] < 15:
            return self._cache[1]
        df = parse_df(self.sys.run(["df", "-B1", "/"], timeout=10).out) or \
            self._df_statvfs()
        postes = []
        docker_dispo = self._docker()
        connu = 0
        # Docker : par app (couche inscriptible du conteneur) + un poste « images & cache »
        docker_total = 0
        if docker_dispo:
            ddf = parse_docker_df(self.sys.run(
                ["docker", "system", "df", "--format", "{{.Type}}|{{.Size}}|{{.Reclaimable}}"], timeout=20).out)
            docker_total = sum(v["taille"] for v in ddf.values())
            recup_docker = (ddf.get("images", {}).get("recuperable", 0)
                            + ddf.get("cache", {}).get("taille", 0)
                            + ddf.get("volumes", {}).get("recuperable", 0))
            ps = parse_ps_size(self.sys.run(
                ["docker", "ps", "-a", "--size", "--format", "{{.Names}}|{{.Size}}"], timeout=20).out)
            par_app = {}
            for c in (conteneurs or []):
                app = c.get("app")
                if app and c.get("nom") in ps:
                    par_app.setdefault(app, {"octets": 0, "actif": False})
                    par_app[app]["octets"] += ps[c["nom"]]
                    if c.get("etat") == "running":
                        par_app[app]["actif"] = True
            couches = 0
            for app, v in sorted(par_app.items()):
                couches += v["octets"]
                postes.append({"id": "app:" + app, "nom": app, "octets": v["octets"],
                               "recuperable": 0, "actif": v["actif"], "categorie": "docker"})
            # images, volumes et cache Docker : dont la part inutilisée est récupérable
            images_cache = max(0, docker_total - couches)
            postes.append({"id": "docker", "nom": "Images, volumes et cache Docker",
                           "octets": images_cache, "recuperable": min(recup_docker, images_cache),
                           "actif": False, "categorie": "docker",
                           "detail": "Dont ~%s d'inutilisé (images sans conteneur, cache de build, volumes orphelins)."
                                     % octets_fr(min(recup_docker, images_cache))})
            connu += docker_total
        # Sauvegardes de paquets YDZ
        sauv_total, sauv_par_nom = self._taille_sauvegardes()
        noms = sauvegardes_triees(self.sys)
        a_purger = noms[:-GARDER_SAUV] if len(noms) > GARDER_SAUV else []
        recup_sauv = sum(sauv_par_nom.get(n, 0) for n in a_purger)
        postes.append({"id": "sauvegardes", "nom": "Sauvegardes de paquets YDZ", "octets": sauv_total,
                       "recuperable": recup_sauv, "actif": False, "categorie": "systeme",
                       "detail": "%d sauvegarde%s ; on garde les %d plus récentes."
                                 % (len(noms), "s" if len(noms) > 1 else "", GARDER_SAUV)})
        connu += sauv_total
        # Cache apt
        apt = self._du(APT_CACHE).get(APT_CACHE, 0)
        postes.append({"id": "apt", "nom": "Cache des paquets système (apt)", "octets": apt,
                       "recuperable": apt, "actif": False, "categorie": "systeme",
                       "detail": "Fichiers .deb déjà installés : entièrement récupérable."})
        connu += apt
        # Journaux système
        jour = parse_journal_usage(self.sys.run(["journalctl", "--disk-usage"], timeout=10).out)
        recup_jour = max(0, jour - JOURNAUX_PLANCHER)
        postes.append({"id": "journaux", "nom": "Journaux système (journald)", "octets": jour,
                       "recuperable": recup_jour, "actif": False, "categorie": "systeme",
                       "detail": "On garde ~%s de journaux récents." % octets_fr(JOURNAUX_PLANCHER)})
        connu += jour
        # Reste du système
        reste = max(0, df["utilise"] - connu)
        postes.append({"id": "systeme", "nom": "Système et données diverses", "octets": reste,
                       "recuperable": 0, "actif": False, "categorie": "systeme",
                       "detail": "Système d'exploitation, apps hors Docker, données YDZ."})
        recup_total = sum(p["recuperable"] for p in postes)
        reserve = self.reserve()
        res = {
            "total": df["total"], "utilise": df["utilise"], "libre": df["libre"], "pct": df["pct"],
            "docker_dispo": docker_dispo, "docker_total": docker_total,
            "postes": sorted(postes, key=lambda p: -p["octets"]),
            "recuperable_total": recup_total,
            "reserve_octets": reserve, "reserve_ok": df["libre"] >= reserve,
            "seuil_warn": SEUIL_WARN, "seuil_crit": SEUIL_CRIT, "garder_sauvegardes": GARDER_SAUV,
            "niveau": "crit" if df["pct"] >= SEUIL_CRIT else "warn" if df["pct"] >= SEUIL_WARN else "ok",
        }
        self._cache = (now, res)
        return res

    def _df_statvfs(self):
        v = self.sys.statvfs("/") or (0, 0)
        total, libre = v
        utilise = max(0, total - libre)
        return {"total": total, "utilise": utilise, "libre": libre,
                "pct": int(round(100.0 * utilise / total)) if total else 0}

    # --- aperçu de ce qui serait libéré ----------------------------------
    def apercu_liberation(self, garder=None):
        """Pour chaque poste, estimation de l'espace récupérable, SANS rien effacer."""
        garder = self._garder(garder)
        actions = []
        if self._docker():
            ddf = parse_docker_df(self.sys.run(
                ["docker", "system", "df", "--format", "{{.Type}}|{{.Size}}|{{.Reclaimable}}"], timeout=20).out)
            estime = (ddf.get("images", {}).get("recuperable", 0)
                      + ddf.get("cache", {}).get("taille", 0)
                      + ddf.get("volumes", {}).get("recuperable", 0))
            actions.append({"id": "docker", "nom": "Nettoyer Docker (inutilisé seulement)",
                            "octets": estime, "estimation": True,
                            "detail": "Images sans conteneur, cache de build et volumes orphelins. "
                                      "Les images et volumes des apps installées ne sont jamais touchés."})
        noms = sauvegardes_triees(self.sys)
        a_purger = noms[:max(0, len(noms) - garder)]   # N-1 : garder=0 purge bien tout (évite le no-op noms[:-0])
        if a_purger:
            tailles = self._du(*["%s/%s" % (SAUVEGARDES, n) for n in a_purger])
            octets = sum(tailles.get("%s/%s" % (SAUVEGARDES, n), 0) for n in a_purger)
            actions.append({"id": "sauvegardes", "nom": "Supprimer les vieilles sauvegardes de paquets",
                            "octets": octets, "estimation": False, "a_supprimer": a_purger,
                            "detail": "%d sauvegarde%s supprimée%s ; les %d plus récentes sont gardées (pour pouvoir "
                                      "annuler une mise à jour)." % (len(a_purger), "s" if len(a_purger) > 1 else "",
                                                                     "s" if len(a_purger) > 1 else "", garder)})
        apt = self._du(APT_CACHE).get(APT_CACHE, 0)
        if apt:
            actions.append({"id": "apt", "nom": "Vider le cache des paquets système (apt clean)",
                            "octets": apt, "estimation": False,
                            "detail": "Fichiers .deb déjà installés, re-téléchargés au besoin."})
        jour = parse_journal_usage(self.sys.run(["journalctl", "--disk-usage"], timeout=10).out)
        recup_jour = max(0, jour - JOURNAUX_PLANCHER)
        if recup_jour:
            actions.append({"id": "journaux", "nom": "Réduire les journaux système",
                            "octets": recup_jour, "estimation": True,
                            "detail": "On garde ~%s de journaux récents." % octets_fr(JOURNAUX_PLANCHER)})
        libre = self.espace_libre()
        return {"actions": actions, "total_octets": sum(a["octets"] for a in actions),
                "libre_avant": libre, "reserve_octets": self.reserve(), "garder_sauvegardes": garder}

    # --- action : libérer ------------------------------------------------
    def _garder(self, garder):
        try:
            g = int(garder)
        except (TypeError, ValueError):
            return GARDER_SAUV
        return max(0, min(g, 50))

    def liberer(self, postes, garder=None):
        """Lance une tâche qui exécute, pour chaque poste demandé, l'action de
        récupération correspondante. Chaque action dit ce qu'elle a libéré."""
        if not isinstance(postes, list) or not postes:
            raise ApiError(400, "Indique au moins un poste à libérer.", "postes")
        choisis = [p for p in POSTES_LIBERABLES if p in postes]
        inconnus = [p for p in postes if p not in POSTES_LIBERABLES]
        if inconnus:
            raise ApiError(400, "Poste à libérer inconnu : %s." % ", ".join(map(str, inconnus))[:80], "postes")
        if not choisis:
            raise ApiError(400, "Aucun poste connu à libérer.", "postes")
        garder = self._garder(garder)

        def job(task):
            avant = self.espace_libre()
            total = 0
            faits = []
            for poste in choisis:
                libere = getattr(self, "_liberer_" + poste)(task, garder)
                total += libere
                faits.append("%s : %s libéré%s" % (self._nom_poste(poste), octets_fr(libere),
                                                   "s" if libere > 1 else ""))
            self._cache = None
            apres = self.espace_libre()
            if avant is not None and apres is not None:
                task.log("Disque : %s libres avant, %s libres après." % (octets_fr(avant), octets_fr(apres)))
            return "Espace libéré : %s (%s)." % (octets_fr(total), " ; ".join(faits))
        return self.tasks.start("stockage", "Libération d'espace sur le disque système", job, None, "disque:liberer")

    @staticmethod
    def _nom_poste(poste):
        return {"docker": "Docker", "sauvegardes": "Vieilles sauvegardes",
                "apt": "Cache apt", "journaux": "Journaux"}.get(poste, poste)

    def _liberer_docker(self, task, garder):
        """Purge Docker INUTILISÉ seulement : images sans conteneur (dangling),
        cache de build, volumes anonymes orphelins. Les sous-commandes sont fixes
        et n'enlèvent jamais ce qu'utilise une app (conteneur présent, même arrêté)."""
        if not self._docker():
            task.log("Docker absent : poste ignoré.")
            return 0
        sorties = []
        for argv in (["docker", "image", "prune", "-f"],
                     ["docker", "builder", "prune", "-f"],
                     ["docker", "volume", "prune", "-f"]):
            task.log("> " + " ".join(argv))
            r = self.sys.run(argv, timeout=300)
            if r.out:
                task.log(r.out[-500:])
            if not r.ok and r.err:
                task.log(r.err[-300:])
            sorties.append(r.out or "")
        return parse_reclaimed("\n".join(sorties))

    def _liberer_sauvegardes(self, task, garder):
        noms = sauvegardes_triees(self.sys)
        a_purger = noms[:max(0, len(noms) - garder)]   # N-1 : cohérent avec l'aperçu ; garder=0 purge tout
        if not a_purger:
            task.log("Aucune vieille sauvegarde à supprimer.")
            return 0
        tailles = self._du(*["%s/%s" % (SAUVEGARDES, n) for n in a_purger])
        libere = 0
        for nom in a_purger:
            # Garde-fou : nom strictement horodaté (ni « / », ni « .. », ni chemin absolu) ;
            # le chemin ne peut donc pas sortir de SAUVEGARDES. sys.remove ne suit pas les liens.
            if not RE_HORO.match(nom):
                continue
            chemin = "%s/%s" % (SAUVEGARDES, nom)
            if self.sys.remove(chemin):
                t = tailles.get(chemin, 0)
                libere += t
                task.log("Sauvegarde supprimée : %s (%s)" % (nom, octets_fr(t)))
        return libere

    def _liberer_apt(self, task, garder):
        avant = self._du(APT_CACHE).get(APT_CACHE, 0)
        task.log("> apt-get clean")
        self.sys.run(["apt-get", "clean"], timeout=120)
        apres = self._du(APT_CACHE).get(APT_CACHE, 0)
        return max(0, avant - apres)

    def _liberer_journaux(self, task, garder):
        avant = parse_journal_usage(self.sys.run(["journalctl", "--disk-usage"], timeout=10).out)
        taille = "%dM" % max(1, JOURNAUX_PLANCHER // 1_000_000)
        task.log("> journalctl --vacuum-size=%s" % taille)
        self.sys.run(["journalctl", "--vacuum-size=" + taille], timeout=120)
        apres = parse_journal_usage(self.sys.run(["journalctl", "--disk-usage"], timeout=10).out)
        return max(0, avant - apres)
