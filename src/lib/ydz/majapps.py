"""Mises à jour des apps (1.0.12) : une nouvelle image existe-t-elle, sans rien télécharger ?

Pour chaque image d'une app installée, on compare l'empreinte locale (`docker image inspect` ->
RepoDigests) à celle du registre :
  1. `docker buildx imagetools inspect <image> --format '{{json .Manifest}}'` si buildx est là ;
  2. sinon, l'API du registre directement (requête HEAD sur /v2/<dépôt>/manifests/<tag>, jeton anonyme) :
     l'en-tête Docker-Content-Digest donne l'empreinte de la liste multi-architectures, la même que celle
     gardée dans RepoDigests après un `docker pull` par étiquette. Une requête HEAD ne compte pas dans les
     limites de Docker Hub.
Jamais de `docker pull` pour vérifier. Chaque nœud vérifie ses propres apps, chaque jour vers 05:00 et à la
demande ; le résultat part avec l'état du nœud ({maj_disponible, verifie_le}) et le hub prévient une seule
fois par nouvelle empreinte (Hub.alerte, type « maj_apps »).
"""
import json
import re
import threading
import time
from urllib.parse import quote

from .core import ApiError

HEURE = 5
ACCEPT = ", ".join((
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))
RE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def ref_image(image):
    """« pihole/pihole:latest » -> ("registry-1.docker.io", "pihole/pihole", "latest"). None si épinglée."""
    image = (image or "").strip()
    if not image or "@" in image or "$" in image:
        return None
    premier = image.split("/", 1)[0]
    if "/" in image and ("." in premier or ":" in premier or premier == "localhost"):
        registre, reste = premier, image.split("/", 1)[1]
    else:
        registre, reste = "registry-1.docker.io", image
    tag = "latest"
    if ":" in reste.rsplit("/", 1)[-1]:
        reste, tag = reste.rsplit(":", 1)
    if registre == "registry-1.docker.io" and "/" not in reste:
        reste = "library/" + reste
    if registre == "docker.io":
        registre = "registry-1.docker.io"
    return registre, reste, tag


def lire_repo_digests(texte):
    """Sortie de `docker image inspect --format '{{json .RepoDigests}}'` -> ensemble d'empreintes."""
    out = set()
    for ligne in (texte or "").splitlines():
        try:
            for x in json.loads(ligne) or []:
                m = RE_DIGEST.search(str(x))
                if m:
                    out.add(m.group(0))
        except ValueError:
            continue
    return out


def lire_imagetools(texte):
    """Sortie de `docker buildx imagetools inspect --format '{{json .Manifest}}'` -> empreinte."""
    try:
        d = json.loads(texte or "")
    except ValueError:
        return None
    v = d.get("digest") if isinstance(d, dict) else None
    return v if isinstance(v, str) and RE_DIGEST.fullmatch(v) else None


def lire_www_auth(h):
    """« Bearer realm="…",service="…",scope="…" » -> dict."""
    if not h or not h.lower().startswith("bearer "):
        return {}
    return dict(re.findall(r'(\w+)="([^"]*)"', h))


def images_compose(texte, env):
    """Repli sans `docker compose config` : lignes « image: » du compose, variables ${X} remplacées."""
    out = []
    for m in re.finditer(r"^\s*image:\s*['\"]?([^'\"\s#]+)", texte or "", re.M):
        img = re.sub(r"\$\{(\w+)(?::?-([^}]*))?\}", lambda x: env.get(x.group(1)) or x.group(2) or "", m.group(1))
        if img and "$" not in img:
            out.append(img)
    return out


def lire_env(texte):
    env = {}
    for l in (texte or "").splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)='(.*)'$", l)
        if m:
            env[m.group(1)] = m.group(2)
    return env


class MajApps:
    def __init__(self, apps):
        self.apps = apps
        self.sys = apps.sys
        self.state = apps.state
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.en_cours = False
        self._buildx = None

    # --- empreintes ----------------------------------------------------------------------------------
    def images(self, app_id):
        r = self.apps._dc(app_id, "config", "--images", timeout=30)
        imgs = [l.strip() for l in r.out.splitlines() if l.strip()] if r.ok else []
        if not imgs:
            d = "/var/lib/ydz/apps/%s" % app_id
            imgs = images_compose(self.sys.read(d + "/compose.yml", ""), lire_env(self.sys.read(d + "/.env", "")))
        return sorted(set(imgs))

    def local(self, image):
        r = self.sys.run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=20)
        return lire_repo_digests(r.out) if r.ok else set()

    def id_local(self, image):
        r = self.sys.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], timeout=20)
        return r.out.strip() if r.ok and r.out.strip().startswith("sha256:") else None

    def distant(self, image):
        """Empreinte du registre, ou None (registre muet, image privée…)."""
        if self._buildx is None:
            self._buildx = self.sys.run(["docker", "buildx", "version"], timeout=10).ok
        if self._buildx:
            r = self.sys.run(["docker", "buildx", "imagetools", "inspect", image, "--format", "{{json .Manifest}}"],
                             timeout=40)
            d = lire_imagetools(r.out) if r.ok else None
            if d:
                return d
        return self.distant_api(image)

    def distant_api(self, image):
        ref = ref_image(image)
        if not ref:
            return None
        registre, depot, tag = ref
        url = "https://%s/v2/%s/manifests/%s" % (registre, depot, quote(tag))
        h = {"Accept": ACCEPT, "User-Agent": "ydz-os"}
        code, entetes, _ = self.sys.http_entetes("HEAD", url, h, timeout=10)
        if code == 401:
            a = lire_www_auth(entetes.get("www-authenticate"))
            if not a.get("realm", "").startswith("https://"):
                return None
            jeton_url = a["realm"] + "?service=%s&scope=%s" % (quote(a.get("service", "")),
                                                              quote(a.get("scope") or "repository:%s:pull" % depot))
            c2, _, corps = self.sys.http_entetes("GET", jeton_url, {"User-Agent": "ydz-os"}, timeout=10)
            jeton = (corps or {}).get("token") or (corps or {}).get("access_token") if c2 == 200 else None
            if not jeton:
                return None
            h["Authorization"] = "Bearer " + jeton
            code, entetes, _ = self.sys.http_entetes("HEAD", url, h, timeout=10)
        d = entetes.get("docker-content-digest") if code == 200 else None
        return d if d and RE_DIGEST.fullmatch(d) else None

    # --- état ----------------------------------------------------------------------------------------
    def etats(self):
        return dict(self.state.get("apps_maj") or {})

    def info(self, app_id):
        e = self.etats().get(app_id)
        if not e:
            return {"maj_disponible": None, "verifie_le": None}
        return {"maj_disponible": e.get("maj_disponible"), "verifie_le": e.get("verifie_le"),
                "empreinte": e.get("empreinte"), "erreur": e.get("erreur")}

    def verifier_une(self, app_id):
        imgs = self.images(app_id)
        res, maj, inconnu, distants = {}, False, False, []
        for img in imgs:
            loc = self.local(img)
            dist = self.distant(img)
            res[img] = {"local": sorted(loc)[:1], "distant": dist}
            if dist is None or not loc:
                inconnu = True
            elif dist not in loc:
                maj = True
                distants.append(dist)
        e = {"maj_disponible": True if maj else (None if inconnu or not imgs else False),
             "verifie_le": int(self.sys.now()), "images": res,
             "empreinte": ",".join(sorted(distants))[:200] if maj else None,
             "erreur": "Registre injoignable pour une image." if inconnu and not maj else None}
        return e

    def verifier(self, ids=None, task=None):
        installees = self.apps._installed()
        ids = [i for i in (ids or sorted(installees)) if i in installees]
        etats = self.etats()
        trouvees = []
        for app_id in ids:
            nom = self.apps.catalog.get(app_id, {}).get("nom", app_id)
            if task:
                task.log("Vérification de %s…" % nom)
            try:
                etats[app_id] = self.verifier_une(app_id)
            except Exception as ex:
                etats[app_id] = {"maj_disponible": None, "verifie_le": int(self.sys.now()), "erreur": str(ex)[:160]}
            if etats[app_id].get("maj_disponible"):
                trouvees.append(nom)
                if task:
                    task.log("  nouvelle version disponible")
        for k in list(etats):
            if k not in installees:
                etats.pop(k)
        self.state.set("apps_maj", etats)
        return trouvees

    def lancer_verification(self):
        self.apps.compose_cmd()

        def job(task):
            self.en_cours = True
            try:
                t = self.verifier(task=task)
            finally:
                self.en_cours = False
            return ("Mise à jour disponible : %s." % ", ".join(t)) if t else "Toutes les apps sont à jour."
        return self.apps.tasks.start("maj_apps", "Recherche de mises à jour des apps", job, None, "apps:verif")

    def marquer_a_jour(self, app_id):
        etats = self.etats()
        if app_id in etats:
            etats[app_id] = dict(etats[app_id], maj_disponible=False, empreinte=None, verifie_le=int(self.sys.now()))
            self.state.set("apps_maj", etats)

    def a_mettre_a_jour(self):
        inst = self.apps._installed()
        return [k for k, v in self.etats().items() if v.get("maj_disponible") and k in inst]

    # --- chaque jour vers 05:00 ----------------------------------------------------------------------
    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-maj-apps", daemon=True).start()

    def _boucle(self):
        self.stop.wait(600)                       # 10 min après le démarrage : le réseau est là
        while not self.stop.is_set():
            try:
                self.planifier()
            except Exception:
                pass
            self.stop.wait(60)

    def planifier(self):
        if not self.apps._installed():
            return False
        t = time.localtime(self.sys.now())
        jour = time.strftime("%Y-%m-%d", t)
        derniere = self.state.get("apps_maj_jour")
        dates = [v.get("verifie_le") or 0 for v in self.etats().values()]
        vieux = not dates or self.sys.now() - min(dates) > 36 * 3600
        if (t.tm_hour == HEURE and derniere != jour) or (vieux and derniere != jour):
            self.state.set("apps_maj_jour", jour)
            try:
                self.lancer_verification()
            except ApiError:
                pass
            return True
        return False
