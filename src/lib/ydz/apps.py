"""Applications : catalogue déclaratif (apps/<id>/app.json + compose.yml) et
cycle de vie sur un nœud, via Docker Compose.

Le nœud n'exécute QUE les fichiers compose de son propre catalogue (installé
avec YDZ OS) : le hub ne lui envoie que l'identifiant de l'app et les réglages,
validés ici un par un. Aucun YAML ne voyage sur le réseau.
"""
import json
import re
import secrets

from .core import ApiError

RE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
RE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")
TYPES = ("texte", "nombre", "choix", "booleen", "secret")
APPS_DIR = "/var/lib/ydz/apps"
DATA_DIR = "/var/lib/ydz/donnees"
MASK = "••••••"


DIFFICULTES = ("facile", "moyen", "avancé")


def load_catalog(sys, base):
    """Lit apps/*/app.json (+ details.json optionnel). Retourne (dict id -> app, liste d'erreurs)."""
    cat, errs = {}, []
    for name in sys.listdir(base):
        raw = sys.read("%s/%s/app.json" % (base, name))
        if raw is None:
            continue
        try:
            app = json.loads(raw)
            validate_app(app, name)
        except (ValueError, KeyError, TypeError) as ex:
            errs.append("app « %s » ignorée : %s" % (name, ex))
            continue
        compose = sys.read("%s/%s/compose.yml" % (base, name))
        if compose is None:
            errs.append("app « %s » ignorée : compose.yml absent" % name)
            continue
        # 1.0.17 : fiche détaillée optionnelle (details.json) et drapeaux calculés.
        app["details"] = read_details(sys.read("%s/%s/details.json" % (base, name)), app)
        app["drapeaux"] = flags(app, compose)
        cat[name] = app
    return cat, errs


def read_details(raw, app):
    """Lit details.json (nouveau en 1.0.17). Tolérant : champ manquant ou invalide → défaut.
    Ne lève jamais : une fiche incomplète ne doit pas écarter l'app du catalogue."""
    d = {}
    if raw:
        try:
            d = json.loads(raw)
            if not isinstance(d, dict):
                d = {}
        except ValueError:
            d = {}

    def chaine(cle):
        v = d.get(cle)
        return v.strip() if isinstance(v, str) else ""

    dif = d.get("difficulte")
    dif = dif if dif in DIFFICULTES else "moyen"
    site = chaine("site")
    if not re.match(r"^https?://[^\s'\"<>]+$", site):     # seulement une vraie URL http(s), sinon rien
        site = ""
    ram = d.get("ram_mo")
    if not isinstance(ram, int) or isinstance(ram, bool) or ram < 0:
        ram = app.get("ram_mo")
    return {"a_quoi_ca_sert": chaine("a_quoi_ca_sert"), "pour_qui": chaine("pour_qui"),
            "difficulte": dif, "ram_mo": ram, "alternative_a": chaine("alternative_a"), "site": site}


def flags(app, compose):
    """Drapeaux d'avertissement d'une app, déduits de ses champs et de son compose.
    Codes : socket_docker, reseau_hote, usb, lourd, privilegie (droits étendus hors socket)."""
    c = compose or ""
    out = []
    socket = "docker.sock" in c
    if socket:
        out.append("socket_docker")
    if app.get("reseau_hote") or re.search(r"network_mode:\s*[\"']?host", c):
        out.append("reseau_hote")
    if "devices:" in c or "/dev/tty" in c or "/dev/serial" in c or "/dev/bus/usb" in c:
        out.append("usb")
    if (app.get("ram_mo") or 0) >= 1500:
        out.append("lourd")
    # 1.0.17.1 (A-03) : les droits étendus sont détectés dans le compose lui-même, pas seulement via le champ
    # déclaré — sinon une app comme photoprism (seccomp/apparmor unconfined) ou un « privileged: true » glissé
    # dans un compose passait sans avertissement.
    etendu = bool(app.get("privilegie")) or bool(re.search(
        r"(privileged:\s*true|cap_add:|seccomp:unconfined|apparmor:unconfined|pid:\s*[\"']?host|/proc|/sys:)", c))
    if etendu and not socket:                              # droits étendus distincts du socket Docker
        out.append("privilegie")
    return out


def validate_app(app, dirname):
    if app.get("id") != dirname or not RE_ID.match(dirname):
        raise ValueError("« id » doit valoir le nom du dossier")
    for k in ("nom", "description", "categorie", "icone"):
        if not isinstance(app.get(k), str) or not app[k]:
            raise ValueError("champ « %s » manquant" % k)
    for p in app.get("ports", []):
        if not (isinstance(p.get("port"), int) and 0 < p["port"] < 65536 and p.get("proto") in ("tcp", "udp")):
            raise ValueError("port invalide : %r" % (p,))
    for s in app.get("reglages", []):
        if not RE_KEY.match(s.get("cle", "")) or s.get("type") not in TYPES or not s.get("nom"):
            raise ValueError("réglage invalide : %r" % (s.get("cle"),))
        if s["cle"].startswith("YDZ_"):
            raise ValueError("le préfixe YDZ_ est réservé")
        if s["type"] == "choix" and not s.get("choix"):
            raise ValueError("réglage %s : « choix » vide" % s["cle"])


def check_settings(app, given, previous=None):
    """Valide les réglages fournis ; complète avec l'existant puis les défauts.
    Retourne le dict complet clé -> chaîne. Lève ApiError(400)."""
    given = given or {}
    if not isinstance(given, dict):
        raise ApiError(400, "« reglages » doit être un objet.", "reglages")
    known = {s["cle"] for s in app.get("reglages", [])}
    unknown = sorted(set(given) - known)
    if unknown:
        raise ApiError(400, "Réglage inconnu : %s" % ", ".join(unknown), "reglages")
    out = {}
    for s in app.get("reglages", []):
        k, typ = s["cle"], s["type"]
        v = given.get(k)
        if typ == "secret" and v == MASK:
            v = None                      # valeur masquée renvoyée telle quelle : inchangée
        if v is None or v == "":
            v = (previous or {}).get(k)
        if v is None or v == "":
            v = s.get("defaut", "")
            if typ == "secret" and not v and s.get("genere"):
                v = secrets.token_urlsafe(18)
        if typ == "booleen":
            v = "true" if v in (True, "true", "oui", "1", 1) else "false"
        v = str(v)
        nom = s["nom"]
        if s.get("requis") and not v:
            raise ApiError(400, "« %s » est obligatoire." % nom, "reglages", cle=k)
        if any(c in v for c in "\n\r\x00'") or len(v) > 256:
            raise ApiError(400, "« %s » contient un caractère interdit (apostrophe, retour à la ligne) "
                                "ou est trop long." % nom, "reglages", cle=k)
        if v and typ == "choix" and v not in [str(c["valeur"] if isinstance(c, dict) else c) for c in s["choix"]]:
            raise ApiError(400, "Valeur non proposée pour « %s »." % nom, "reglages", cle=k)
        if v and typ == "nombre":
            try:
                n = int(v)
            except ValueError:
                raise ApiError(400, "« %s » doit être un nombre." % nom, "reglages", cle=k)
            if n < s.get("min", -2**31) or n > s.get("max", 2**31):
                raise ApiError(400, "« %s » doit être entre %s et %s." % (nom, s.get("min"), s.get("max")),
                               "reglages", cle=k)
        if v and s.get("motif") and not re.match(s["motif"], v):
            raise ApiError(400, "« %s » : format invalide. %s" % (nom, s.get("aide", "")), "reglages", cle=k)
        # 1.0.15 (F-13) / 1.0.17.1 (A-01) : un réglage qui sert de chemin monté dans un conteneur doit rester
        # sous /srv/ydz, sans « . » ni « .. » (sinon « /srv/ydz/.. » ou « /srv/.. » monterait tout le disque de
        # l'hôte en lecture-écriture). Le contrôle s'applique à TOUT réglage de type chemin : marqué « chemin »,
        # dont le motif vise un point de montage (/srv /mnt /media), OU dont la valeur est un chemin absolu
        # (hors périphérique /dev, qui a son propre motif précis). On ne se fie plus au seul motif de l'app.
        chemin_probable = (s.get("chemin")
                           or re.search(r"/(srv|mnt|media)\b", s.get("motif") or "")
                           or (v.startswith("/") and not v.startswith("/dev/")))
        if v and chemin_probable:
            import os as _os
            norm = _os.path.normpath(v)
            if v != norm or ".." in v.split("/") or not (norm == "/srv/ydz" or norm.startswith("/srv/ydz/")):
                raise ApiError(400, "« %s » doit être un dossier sous /srv/ydz, sans « .. »." % nom, "reglages", cle=k)
        if v and typ == "secret" and len(v) < s.get("min", 0):
            raise ApiError(400, "« %s » : %d caractères au minimum." % (nom, s["min"]), "reglages", cle=k)
        out[k] = v
    return out


def env_text(env):
    return "".join("%s='%s'\n" % (k, env[k]) for k in sorted(env))


def mask_settings(app, settings):
    secret = {s["cle"] for s in app.get("reglages", []) if s["type"] == "secret"}
    return {k: (MASK if k in secret and v else v) for k, v in (settings or {}).items()}


class Apps:
    def __init__(self, sys, state, tasks, base, collector=None):
        self.sys, self.state, self.tasks, self.base = sys, state, tasks, base
        self.collector = collector
        self.catalog, self.errors = load_catalog(sys, base)
        self._compose = None
        from .majapps import MajApps
        self.maj = MajApps(self)                 # 1.0.12 : nouvelles versions des images

    # --- Docker ----------------------------------------------------------
    def compose_cmd(self):
        if self._compose is None:
            if self.sys.which("docker") and self.sys.run(["docker", "compose", "version"], timeout=10).ok:
                self._compose = ["docker", "compose"]
            elif self.sys.which("docker-compose"):
                self._compose = ["docker-compose"]
            else:
                raise ApiError(503, "Docker n'est pas installé sur ce nœud. Lance « sudo ydz diagnostic ».",
                               "docker_absent")
        return self._compose

    def _dc(self, app_id, *args, timeout=120):
        d = "%s/%s" % (APPS_DIR, app_id)
        return self.sys.run(self.compose_cmd() + ["-p", "ydz-" + app_id, "--project-directory", d,
                                                  "-f", d + "/compose.yml", "--env-file", d + "/.env"]
                            + list(args), timeout=timeout)

    def _installed(self):
        return dict(self.state.get("apps", {}))

    def _app(self, app_id, installed=None):
        app = self.catalog.get(app_id) if RE_ID.match(app_id or "") else None
        if not app:
            raise ApiError(404, "Application inconnue.", "app_inconnue")
        if installed is True and app_id not in self._installed():
            raise ApiError(409, "%s n'est pas installée sur ce nœud." % app["nom"], "non_installee")
        if installed is False and app_id in self._installed():
            raise ApiError(409, "%s est déjà installée sur ce nœud." % app["nom"], "deja_installee")
        return app

    # --- lecture ---------------------------------------------------------
    def list(self, containers, secrets_ok=False):
        """État des apps installées, d'après `docker ps` (déjà collecté)."""
        out = []
        for app_id, inst in sorted(self._installed().items()):
            app = self.catalog.get(app_id)
            cs = [c for c in containers if c.get("app") == app_id]
            run = [c for c in cs if c["etat"] == "running"]
            etat = "actif" if cs and len(run) == len(cs) else "arrete" if not run else "partiel"
            if any(c["etat"] in ("restarting", "dead") for c in cs):
                etat = "erreur"
            regl = inst.get("reglages", {})
            out.append({"id": app_id, "etat": etat, "conteneurs": cs,
                        "reglages": regl if secrets_ok or not app else mask_settings(app, regl),
                        "donnees": inst.get("donnees"), "installe_le": inst.get("installe_le"),
                        "au_catalogue": app is not None, "maj": self.maj.info(app_id)})
        return out

    def _data_root(self, emplacement):
        if not emplacement:
            return DATA_DIR
        if not re.match(r"^/srv/ydz/[a-z0-9][a-z0-9-]{0,15}$", emplacement):
            raise ApiError(400, "Emplacement de données invalide.", "emplacement")
        mounts = [ln.split()[1] for ln in (self.sys.read("/proc/mounts", "") or "").splitlines()
                  if len(ln.split()) > 1]
        if emplacement not in mounts:
            raise ApiError(409, "Le disque %s n'est pas monté." % emplacement, "emplacement")
        return emplacement + "/apps"

    def _ports_conflict(self, app):
        mine = {(p["port"], p["proto"]) for p in app.get("ports", [])}
        for other_id in self._installed():
            other = self.catalog.get(other_id)
            if not other or other_id == app["id"]:
                continue
            clash = mine & {(p["port"], p["proto"]) for p in other.get("ports", [])}
            if clash:
                port = sorted(clash)[0]
                raise ApiError(409, "Le port %d est déjà pris par %s sur ce nœud. Installe %s sur un autre "
                                    "nœud ou désinstalle %s." % (port[0], other["nom"], app["nom"], other["nom"]),
                               "port_pris", port=port[0])

    def _write(self, app, settings, data):
        d = "%s/%s" % (APPS_DIR, app["id"])
        self.sys.makedirs(d, 0o700)
        self.sys.makedirs(data, 0o755)
        for sub in app.get("donnees", []):
            if re.match(r"^[a-z0-9][a-z0-9/_-]*$", sub) and ".." not in sub:
                self.sys.makedirs("%s/%s" % (data, sub), 0o755)
        env = dict(settings)
        env.update({"YDZ_DATA": data, "YDZ_HOTE": self.sys.hostname(), "YDZ_IP": self.sys.primary_ip(),
                    "YDZ_TZ": (self.sys.read("/etc/timezone", "") or "Europe/Paris").strip() or "Europe/Paris"})
        self.sys.write(d + "/compose.yml", self.sys.read("%s/%s/compose.yml" % (self.base, app["id"])))
        self.sys.write(d + "/.env", env_text(env), mode=0o600)

    def _refresh(self):
        if self.collector:
            self.collector.refresh_docker()

    def _step(self, task, res, what):
        task.log((res.out + "\n" + res.err)[-1500:])
        if not res.ok:
            self._refresh()
            raise ApiError(500, "%s a échoué. Ouvre le journal de la tâche pour le détail." % what, "docker")

    # --- actions (retournent une tâche) -----------------------------------
    def install(self, app_id, reglages=None, emplacement=None):
        app = self._app(app_id, installed=False)
        self.compose_cmd()
        settings = check_settings(app, reglages)
        self._ports_conflict(app)
        need = app.get("ram_mo", 0) * 1024 * 1024
        total = 0
        m = re.search(r"MemTotal:\s+(\d+)", self.sys.read("/proc/meminfo", "") or "")
        if m:
            total = int(m.group(1)) * 1024
        if need and total and need > total * 0.9:
            raise ApiError(409, "%s demande environ %d Mo de mémoire : ce nœud n'en a pas assez."
                           % (app["nom"], app["ram_mo"]), "memoire")
        # 1.0.21 : garde-fou réserve d'espace — ne jamais remplir « / » à 100 % (une app qui gonfle
        # casserait tout) et garder la place d'une mise à jour OTA. On refuse tôt si le disque système
        # est déjà sous la réserve ; la place est à récupérer dans Stockage > Libérer de l'espace.
        from .disque import reserve_configuree, octets_fr
        reserve = reserve_configuree(self.state)   # N-2 : honore l'override configuré, pas seulement le défaut
        v = self.sys.statvfs("/")
        if v and v[1] < reserve:
            raise ApiError(507, "Le disque système est presque plein (moins de %s libres). Libère de l'espace "
                                "dans Stockage avant d'installer une app." % octets_fr(reserve), "disque_plein")
        data = "%s/%s" % (self._data_root(emplacement), app_id)

        def job(task):
            self._write(app, settings, data)
            task.log("Téléchargement de %s…" % app["nom"])
            self._step(task, self._dc(app_id, "pull", timeout=1800), "Le téléchargement")
            task.log("Démarrage…")
            self._step(task, self._dc(app_id, "up", "-d", "--remove-orphans", timeout=600), "Le démarrage")
            apps = self._installed()
            apps[app_id] = {"reglages": settings, "donnees": data, "installe_le": int(self.sys.now())}
            self.state.set("apps", apps)
            self._refresh()
            return "%s est installée." % app["nom"]
        return self.tasks.start("app", "Installation de %s" % app["nom"], job, app_id, "app:" + app_id)

    def configure(self, app_id, reglages):
        app = self._app(app_id, installed=True)
        inst = self._installed()[app_id]
        settings = check_settings(app, reglages, inst.get("reglages"))

        def job(task):
            self._write(app, settings, inst["donnees"])
            self._step(task, self._dc(app_id, "up", "-d", "--remove-orphans", timeout=600), "Le redémarrage")
            apps = self._installed()
            apps[app_id] = dict(inst, reglages=settings)
            self.state.set("apps", apps)
            self._refresh()
            return "Réglages de %s appliqués." % app["nom"]
        return self.tasks.start("app", "Réglages de %s" % app["nom"], job, app_id, "app:" + app_id)

    def _simple(self, app_id, verb, title, done, *args):
        app = self._app(app_id, installed=True)

        def job(task):
            self._step(task, self._dc(app_id, *args, timeout=300), title)
            self._refresh()
            return done % app["nom"]
        return self.tasks.start("app", "%s de %s" % (title, app["nom"]), job, app_id, "app:" + app_id)

    def start(self, app_id):
        return self._simple(app_id, "up", "Démarrage", "%s est démarrée.", "up", "-d")

    def stop(self, app_id):
        return self._simple(app_id, "stop", "Arrêt", "%s est arrêtée.", "stop")

    def restart(self, app_id):
        return self._simple(app_id, "restart", "Redémarrage", "%s a redémarré.", "restart")

    def _mettre_a_jour(self, task, app_id, app):
        """pull + up -d (réglages et volumes gardés), puis retrait des anciennes images de cette app."""
        imgs = self.maj.images(app_id)
        avant = {i: self.maj.id_local(i) for i in imgs}
        task.log("Téléchargement de la nouvelle version de %s…" % app["nom"])
        self._step(task, self._dc(app_id, "pull", timeout=1800), "Le téléchargement")
        task.log("Redémarrage de %s…" % app["nom"])
        self._step(task, self._dc(app_id, "up", "-d", "--remove-orphans", timeout=600), "Le redémarrage")
        for img, ancien in avant.items():
            if ancien and ancien != self.maj.id_local(img):
                r = self.sys.run(["docker", "image", "rm", ancien], timeout=60)
                task.log("Ancienne image retirée (%s)" % img if r.ok else "Ancienne image gardée (encore utilisée)")
        self.maj.marquer_a_jour(app_id)

    def update(self, app_id):
        app = self._app(app_id, installed=True)
        self.compose_cmd()

        def job(task):
            self._mettre_a_jour(task, app_id, app)
            self._refresh()
            return "%s est à jour." % app["nom"]
        return self.tasks.start("app", "Mise à jour de %s" % app["nom"], job, app_id, "app:" + app_id)

    def update_all(self):
        """« Tout mettre à jour » : les apps de ce nœud qui ont une nouvelle version, l'une après l'autre."""
        ids = self.maj.a_mettre_a_jour()
        if not ids:
            raise ApiError(409, "Aucune mise à jour d'app en attente sur ce nœud.", "rien_a_faire")
        self.compose_cmd()

        def job(task):
            faites, ratees = [], []
            for app_id in ids:
                app = self.catalog.get(app_id, {"nom": app_id})
                try:
                    self._mettre_a_jour(task, app_id, app)
                    faites.append(app["nom"])
                except ApiError:
                    ratees.append(app["nom"])
            self._refresh()
            if ratees:
                raise ApiError(500, "Échec pour %s%s." % (", ".join(ratees),
                                                         " (%s à jour)" % ", ".join(faites) if faites else ""), "docker")
            return "%s à jour." % ", ".join(faites)
        return self.tasks.start("app", "Mise à jour de %d app%s" % (len(ids), "s" if len(ids) > 1 else ""), job,
                                None, "apps:maj")

    def uninstall(self, app_id, supprimer_donnees=False):
        if not RE_ID.match(app_id or "") or app_id not in self._installed():
            raise ApiError(409, "Cette application n'est pas installée sur ce nœud.", "non_installee")
        inst = self._installed()[app_id]
        nom = self.catalog.get(app_id, {}).get("nom", app_id)

        def job(task):
            d = "%s/%s" % (APPS_DIR, app_id)
            if self.sys.exists(d + "/compose.yml"):
                self._step(task, self._dc(app_id, "down", "--remove-orphans", "--volumes", timeout=300),
                           "L'arrêt")
            self.sys.remove(d)
            data = inst.get("donnees") or ""
            # garde-fou : on ne supprime qu'un dossier « …/<id> » sous une racine YDZ
            ok_root = data.startswith(DATA_DIR + "/") or re.match(r"^/srv/ydz/[a-z0-9-]+/apps/", data)
            if supprimer_donnees and ok_root and data.endswith("/" + app_id) and ".." not in data:
                self.sys.remove(data)
                task.log("Données supprimées : %s" % data)
            apps = self._installed()
            apps.pop(app_id, None)
            self.state.set("apps", apps)
            self._refresh()
            return "%s est désinstallée%s." % (nom, "" if supprimer_donnees else " (données conservées)")
        return self.tasks.start("app", "Désinstallation de %s" % nom, job, app_id, "app:" + app_id)

    def logs(self, app_id, lines=200):
        self._app(app_id, installed=True)
        lines = max(10, min(int(lines), 1000))
        r = self._dc(app_id, "logs", "--no-color", "--tail", str(lines), timeout=20)
        text = r.out if r.ok else (r.err or r.out)
        return {"id": app_id, "lignes": text.splitlines()[-lines:], "ok": r.ok}
