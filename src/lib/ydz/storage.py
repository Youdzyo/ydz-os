"""Stockage (surtout VAULT) : disques, SMART, formatage ext4 guidé, miroir,
partages Samba.

Garde-fous du formatage (tous revérifiés au moment de confirmer) :
  1. seul un disque entier /dev/sdX ou /dev/nvmeXnY est accepté ;
  2. jamais le disque qui porte « / », « /boot », « /boot/firmware » ou le swap ;
  3. jamais un disque dont une partition est montée hors de /srv/ydz ;
  4. double confirmation : un jeton à durée courte + une phrase à retaper
     (« EFFACER SDA ») ; le numéro de série et la taille doivent être
     inchangés entre les deux étapes (disque remplacé = refus).
"""
import json
import re
import secrets

from .core import ApiError

RE_DISK = re.compile(r"^/dev/(sd[a-z]{1,2}|nvme\d{1,2}n\d{1,2})$")
RE_NAME = re.compile(r"^[a-z0-9]{1,16}$")
RE_SHARE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$")
SYSTEM_MOUNTS = ("/", "/boot", "/boot/firmware", "/boot/efi", "[SWAP]", "/usr", "/var", "/home")
MOUNT_ROOT = "/srv/ydz"
FSTAB = "/etc/fstab"
SMB_CONF = "/etc/samba/ydz-partages.conf"
SMB_USER = "ydz"
TOKEN_TTL = 120
MIN_SIZE = 1 * 10**9
ACCES = {"prive": "Protégé par mot de passe", "invite_lecture": "Tout le monde, lecture seule",
         "invite_ecriture": "Tout le monde, lecture et écriture"}


def part_path(disk, n=1):
    return "%s%s%d" % (disk, "p" if disk[-1].isdigit() else "", n)


def _mounts(dev):
    out = [m for m in (dev.get("mountpoints") or []) if m]
    if dev.get("mountpoint"):
        out.append(dev["mountpoint"])
    for c in dev.get("children") or []:
        out += _mounts(c)
    return out


def _flat_children(dev):
    out = []
    for c in dev.get("children") or []:
        out.append(c)
        out += _flat_children(c)
    return out


def parse_lsblk(text):
    """JSON de lsblk -> liste de disques normalisés (voir TECHNIQUE.md)."""
    try:
        devs = json.loads(text or "{}").get("blockdevices", [])
    except ValueError:
        return []
    out = []
    for d in devs:
        if d.get("type") != "disk" or str(d.get("name", "")).startswith(("zram", "loop", "ram", "mtd")):
            continue
        path = d.get("path") or "/dev/" + d["name"]
        mounts = _mounts(d)
        system = any(m in SYSTEM_MOUNTS for m in mounts)
        foreign = [m for m in mounts if m not in SYSTEM_MOUNTS and not m.startswith(MOUNT_ROOT + "/")]
        tran = (d.get("tran") or "").lower()
        if path.startswith("/dev/nvme"):
            kind = "nvme"
        elif path.startswith("/dev/mmcblk"):
            kind = "sd"
        elif tran == "usb":
            kind = "usb"
        else:
            kind = "hdd" if d.get("rota") in (True, 1, "1") else "ssd"
        parts = []
        for c in _flat_children(d):
            parts.append({"chemin": c.get("path") or "/dev/" + c["name"], "type": c.get("type"),
                          "taille": int(c.get("size") or 0), "fs": c.get("fstype"),
                          "etiquette": c.get("label"), "uuid": c.get("uuid"),
                          "montage": [m for m in (c.get("mountpoints") or []) if m]})
        ydz_mount = next((m for m in mounts if m.startswith(MOUNT_ROOT + "/")), None)
        raid = any(p["fs"] == "linux_raid_member" or (p["type"] or "").startswith("raid") for p in parts)
        size = int(d.get("size") or 0)
        # disque déjà préparé par YDZ (étiquette ydz-<nom>) mais pas monté : réinstallation de vault
        adopt = next((p["etiquette"][4:] for p in parts if p["fs"] == "ext4" and not p["montage"]
                      and (p["etiquette"] or "").startswith("ydz-") and RE_NAME.match(p["etiquette"][4:])), None)
        reason = None
        if system:
            reason = "C'est le disque du système : il ne peut pas être effacé."
        elif not RE_DISK.match(path):
            reason = "Ce type de périphérique n'est pas géré (seuls les disques SATA et NVMe le sont)."
        elif foreign:
            reason = "Une partition est montée ailleurs (%s). Démonte-la d'abord." % foreign[0]
        elif size < MIN_SIZE:
            reason = "Disque trop petit ou illisible."
        out.append({
            "id": d["name"], "chemin": path, "modele": (d.get("model") or "").strip() or "Disque",
            "serie": (d.get("serial") or "").strip() or None, "taille": size, "type": kind,
            "transport": tran or None, "amovible": d.get("rm") in (True, 1, "1"),
            "systeme": system, "partitions": parts, "montage": ydz_mount,
            "nom": ydz_mount.split("/")[-1] if ydz_mount else None,
            "vide": not parts and not d.get("fstype"), "miroir": raid,
            "a_remonter": None if (system or ydz_mount) else adopt,
            "formatable": reason is None, "raison": reason,
        })
    return out


def parse_mdstat(text):
    out, cur = [], None
    for line in (text or "").splitlines():
        m = re.match(r"^(md\S+)\s*:\s*(\S+)\s+(?:\(\S+\)\s+)?(raid\d+|linear)?\s*(.*)$", line)
        if m:
            cur = {"nom": m.group(1), "niveau": m.group(3) or "?", "actif": m.group(2) == "active",
                   "membres": re.findall(r"([a-z0-9]+)\[\d+\]", m.group(4)), "etat": "sain", "progression": None}
            out.append(cur)
            continue
        if cur:
            u = re.search(r"\[(\d+)/(\d+)\]\s+\[([U_]+)\]", line)
            if u and "_" in u.group(3):
                cur["etat"] = "degrade"
            r = re.search(r"(resync|recovery|check)\s*=\s*([\d.]+)%", line)
            if r:
                cur["etat"] = "reconstruction" if r.group(1) != "check" else cur["etat"]
                cur["progression"] = float(r.group(2))
    return out


def parse_smart(text):
    try:
        d = json.loads(text or "")
    except ValueError:
        return None
    if not isinstance(d, dict) or ("smart_status" not in d and "temperature" not in d):
        return None
    out = {"sain": (d.get("smart_status") or {}).get("passed"),
           "temp": (d.get("temperature") or {}).get("current"),
           "heures": (d.get("power_on_time") or {}).get("hours"),
           "secteurs_realloues": None, "usure_pct": None}
    for a in (d.get("ata_smart_attributes") or {}).get("table", []):
        if a.get("id") == 5:
            out["secteurs_realloues"] = (a.get("raw") or {}).get("value")
    nv = d.get("nvme_smart_health_information_log") or {}
    if "percentage_used" in nv:
        out["usure_pct"] = nv["percentage_used"]
    if out["sain"] and (out["secteurs_realloues"] or 0) > 0:
        out["avis"] = "Des secteurs ont été réalloués : le disque vieillit, pense à le remplacer."
    elif out["sain"] is False:
        out["avis"] = "Le disque annonce une panne proche. Sauvegarde tes données et remplace-le."
    else:
        out["avis"] = None
    return out


def fstab_without(text, mountpoint):
    keep = []
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) >= 2 and not line.lstrip().startswith("#") and p[1] == mountpoint:
            continue
        keep.append(line)
    return "\n".join(keep) + "\n"


def fstab_line(uuid, mountpoint):
    return "UUID=%s %s ext4 defaults,noatime,nofail,x-systemd.device-timeout=15s 0 2\n" % (uuid, mountpoint)


def smb_conf(shares):
    out = ["# Fichier écrit par YDZ OS : ne pas modifier à la main (il sera réécrit).\n"]
    for s in shares:
        guest = s["acces"] != "prive"
        out.append("[%s]\n   path = %s\n   comment = Partage YDZ\n   browseable = yes\n   read only = %s\n"
                   "   guest ok = %s\n%s   force user = %s\n   create mask = 0664\n   directory mask = 0775\n"
                   % (s["nom"], s["chemin"], "yes" if s["acces"] == "invite_lecture" else "no",
                      "yes" if guest else "no", "" if guest else "   valid users = %s\n" % SMB_USER, SMB_USER))
    return "\n".join(out)


class Storage:
    def __init__(self, sys, state, tasks):
        self.sys, self.state, self.tasks = sys, state, tasks
        self.pending = {}
        self._smart = {}

    # --- lecture ---------------------------------------------------------
    def disks(self):
        r = self.sys.run(["lsblk", "-J", "-b", "-o",
                          "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,ROTA,RM,FSTYPE,LABEL,UUID,MOUNTPOINTS"],
                         timeout=15)
        if not r.ok:
            raise ApiError(500, "Impossible de lister les disques (lsblk).", "lsblk")
        return parse_lsblk(r.out)

    def smart(self, path):
        now = self.sys.monotonic()
        c = self._smart.get(path)
        if c and now - c[0] < 600:
            return c[1]
        val = None
        if self.sys.which("smartctl"):
            r = self.sys.run(["smartctl", "-j", "-H", "-A", "-n", "standby", path], timeout=20)
            val = parse_smart(r.out)
        self._smart[path] = (now, val)
        return val

    def shares(self):
        return list(self.state.get("partages", []))

    def overview(self):
        disks = self.disks()
        for d in disks:
            d["smart"] = None if d["type"] == "sd" else self.smart(d["chemin"])
            d["total"] = d["libre"] = None
            if d["montage"]:
                v = self.sys.statvfs(d["montage"])
                if v:
                    d["total"], d["libre"] = v
        mirrors = parse_mdstat(self.sys.read("/proc/mdstat", ""))
        free = [d for d in disks if d["formatable"] and d["vide"]]
        sugg = None
        if len(free) >= 2 and not mirrors:
            a, b = sorted(free, key=lambda d: d["taille"])[-2:]
            if a["taille"] >= 0.9 * b["taille"]:
                sugg = {"type": "miroir", "disques": [a["chemin"], b["chemin"]],
                        "texte": "Deux disques vides de taille proche : en miroir, chaque fichier est écrit "
                                 "sur les deux. Si un disque tombe en panne, rien n'est perdu. "
                                 "Tu disposes de la capacité d'un seul disque."}
        elif len(free) == 1:
            sugg = {"type": "formater", "disques": [free[0]["chemin"]],
                    "texte": "Un disque vide est prêt à être préparé pour YDZ."}
        return {"disques": disks, "miroirs": mirrors, "partages": self.shares(),
                "acces": [{"id": k, "nom": v} for k, v in ACCES.items()],
                "samba": {"installe": bool(self.sys.which("smbd")), "utilisateur": SMB_USER,
                          "mot_de_passe_defini": bool(self.state.get("samba_mdp"))},
                "mdadm": bool(self.sys.which("mdadm")), "suggestion": sugg}

    # --- formatage : étape 1 ---------------------------------------------
    def _check(self, path, disks=None):
        if not isinstance(path, str) or not RE_DISK.match(path):
            raise ApiError(400, "Disque invalide : choisis un disque entier (ex. /dev/sda).", "disque")
        d = next((x for x in (disks or self.disks()) if x["chemin"] == path), None)
        if not d:
            raise ApiError(404, "Le disque %s est introuvable (débranché ?)." % path, "disque")
        if not d["formatable"]:
            raise ApiError(403, d["raison"], "disque_protege")
        return d

    def _check_name(self, name, disks, replacing=()):
        if not isinstance(name, str) or not RE_NAME.match(name):
            raise ApiError(400, "Nom invalide : 1 à 16 lettres minuscules ou chiffres, sans espace.", "nom")
        for d in disks:
            if d["nom"] == name and d["chemin"] not in replacing:
                raise ApiError(409, "Le nom « %s » est déjà utilisé par un autre disque." % name, "nom")

    def prepare(self, paths, name, mirror=False):
        if self.sys.which("mkfs.ext4") is None:
            raise ApiError(503, "mkfs.ext4 est absent (paquet e2fsprogs).", "outil")
        if mirror and (not isinstance(paths, list) or len(paths) != 2 or paths[0] == paths[1]):
            raise ApiError(400, "Un miroir demande exactement deux disques différents.", "disque")
        if mirror and not self.sys.which("mdadm"):
            raise ApiError(503, "mdadm n'est pas installé sur ce nœud.", "outil")
        if not isinstance(paths, list) or not paths or len(paths) > 2:
            raise ApiError(400, "Aucun disque indiqué.", "disque")
        disks = self.disks()
        chosen = [self._check(p, disks) for p in paths]
        self._check_name(name, disks, replacing=paths)
        now = self.sys.monotonic()
        self.pending = {k: v for k, v in self.pending.items() if v["expire"] > now}
        token = secrets.token_urlsafe(16)
        phrase = "EFFACER " + " ".join(d["id"].upper() for d in chosen)
        self.pending[token] = {"disques": [(d["chemin"], d["serie"], d["taille"]) for d in chosen],
                               "nom": name, "miroir": bool(mirror), "phrase": phrase,
                               "expire": now + TOKEN_TTL}
        lost = [p for d in chosen for p in d["partitions"] if p["fs"]]
        return {"jeton": token, "phrase": phrase, "expire_s": TOKEN_TTL, "nom": name, "miroir": bool(mirror),
                "montage": "%s/%s" % (MOUNT_ROOT, name),
                "disques": [{"chemin": d["chemin"], "modele": d["modele"], "serie": d["serie"],
                             "taille": d["taille"]} for d in chosen],
                "donnees_perdues": bool(lost),
                "avertissement": "TOUT le contenu de %s sera effacé définitivement%s."
                                 % (" et ".join(d["chemin"] for d in chosen),
                                    " (des données existent dessus)" if lost else "")}

    # --- formatage : étape 2 ---------------------------------------------
    def confirm(self, token, phrase):
        now = self.sys.monotonic()
        p = self.pending.pop(token, None) if isinstance(token, str) else None
        if not p or p["expire"] <= now:
            raise ApiError(410, "La demande a expiré. Recommence depuis le début.", "jeton")
        if not isinstance(phrase, str) or phrase.strip() != p["phrase"]:
            raise ApiError(400, "La phrase de confirmation ne correspond pas. Rien n'a été effacé.", "phrase")
        disks = self.disks()
        for path, serial, size in p["disques"]:
            d = self._check(path, disks)
            if d["serie"] != serial or d["taille"] != size:
                raise ApiError(409, "Le disque %s a changé depuis la première étape. Rien n'a été effacé."
                               % path, "disque_change")
        self._check_name(p["nom"], disks, replacing=[x[0] for x in p["disques"]])
        paths, name, mirror = [x[0] for x in p["disques"]], p["nom"], p["miroir"]
        title = ("Création du miroir « %s »" if mirror else "Préparation du disque « %s »") % name
        return self.tasks.start("stockage", title, lambda t: self._format(t, paths, name, mirror, disks),
                                name, "stockage")

    def _must(self, task, argv, what, input=None, timeout=600):
        task.log("> " + " ".join(argv))
        r = self.sys.run(argv, input=input, timeout=timeout)
        if not r.ok:
            task.log(r.err or r.out)
            raise ApiError(500, "%s a échoué (%s)." % (what, (r.err or r.out).strip()[:120] or "erreur"), "outil")
        return r

    def _format(self, task, paths, name, mirror, disks):
        s, mp = self.sys, "%s/%s" % (MOUNT_ROOT, name)
        fstab = s.read(FSTAB, "") or ""
        for d in disks:
            if d["chemin"] in paths and d["montage"]:
                s.run(["systemctl", "stop", "srv-ydz-%s.mount" % d["nom"]], timeout=60)
                fstab = fstab_without(fstab, d["montage"])
                self.state.set("partages", [x for x in self.shares() if x["disque"] != d["nom"]])
        fstab = fstab_without(fstab, mp)
        s.write(FSTAB, fstab, mode=0o644)
        self._write_smb()
        parts = []
        for path in paths:
            d = next(x for x in disks if x["chemin"] == path)
            if d["miroir"]:
                for part in d["partitions"]:
                    if (part["type"] or "").startswith("raid"):
                        s.run(["mdadm", "--stop", part["chemin"]], timeout=30)
                for part in d["partitions"]:
                    if part["type"] == "part":
                        s.run(["mdadm", "--zero-superblock", "--force", part["chemin"]], timeout=30)
            for part in d["partitions"]:
                s.run(["wipefs", "-a", "-q", part["chemin"]], timeout=30)
            self._must(task, ["wipefs", "-a", "-q", path], "L'effacement")
            self._must(task, ["sfdisk", "--wipe", "always", "--label", "gpt", path],
                       "Le partitionnement", input="type=%s\n" % ("raid" if mirror else "linux"))
            parts.append(part_path(path))
        s.run(["udevadm", "settle", "--timeout=10"], timeout=15)
        target = parts[0]
        if mirror:
            target = "/dev/md/ydz-" + name
            self._must(task, ["mdadm", "--create", target, "--run", "--level=1", "--raid-devices=2",
                              "--metadata=1.2", "--name=ydz-" + name] + parts, "La création du miroir",
                       input="y\n")
            scan = s.run(["mdadm", "--detail", "--scan", target], timeout=20)
            if scan.ok and scan.out.strip():
                conf = s.read("/etc/mdadm/mdadm.conf", "") or ""
                if "ydz-" + name not in conf:
                    s.write("/etc/mdadm/mdadm.conf", conf.rstrip("\n") + "\n" + scan.out.strip() + "\n")
        self._must(task, ["mkfs.ext4", "-F", "-q", "-m", "0", "-L", "ydz-" + name, target],
                   "La création du système de fichiers", timeout=1800)
        s.run(["udevadm", "settle", "--timeout=10"], timeout=15)
        uuid = self._must(task, ["blkid", "-o", "value", "-s", "UUID", target], "La lecture de l'UUID").out.strip()
        if not re.match(r"^[0-9a-fA-F-]{8,40}$", uuid):
            raise ApiError(500, "UUID illisible après le formatage.", "outil")
        s.makedirs(mp)
        s.write(FSTAB, (s.read(FSTAB, "") or "") + fstab_line(uuid, mp), mode=0o644)
        s.run(["systemctl", "daemon-reload"], timeout=60)
        self._must(task, ["systemctl", "start", "srv-ydz-%s.mount" % name], "Le montage", timeout=90)
        self._smart = {}
        if mirror:
            return ("Miroir « %s » prêt dans %s. La synchronisation des deux disques continue en arrière-plan "
                    "(tu peux déjà t'en servir)." % (name, mp))
        return "Disque « %s » prêt dans %s." % (name, mp)

    def remount(self, path):
        """Remonte, sans rien effacer, un disque déjà préparé par YDZ (après une
        réinstallation du nœud) : ligne fstab par UUID puis montage."""
        disks = self.disks()
        d = next((x for x in disks if x["chemin"] == path), None) if isinstance(path, str) else None
        if not d or not d.get("a_remonter"):
            raise ApiError(409, "Ce disque n'a pas été préparé par YDZ, ou il est déjà monté.", "disque")
        name = d["a_remonter"]
        self._check_name(name, disks, replacing=[path])
        part = next(p for p in d["partitions"] if p["etiquette"] == "ydz-" + name and p["fs"] == "ext4")
        if not part["uuid"] or not re.match(r"^[0-9a-fA-F-]{8,40}$", part["uuid"]):
            raise ApiError(500, "Identifiant du disque illisible.", "outil")
        mp = "%s/%s" % (MOUNT_ROOT, name)
        self.sys.makedirs(mp)
        self.sys.write(FSTAB, fstab_without(self.sys.read(FSTAB, "") or "", mp) + fstab_line(part["uuid"], mp),
                       mode=0o644)
        self.sys.run(["systemctl", "daemon-reload"], timeout=60)
        r = self.sys.run(["systemctl", "start", "srv-ydz-%s.mount" % name], timeout=90)
        if not r.ok:
            raise ApiError(500, "Le montage a échoué (%s)." % (r.err.strip()[:120] or "erreur"), "outil")
        return {"nom": name, "montage": mp}

    # --- partages Samba ---------------------------------------------------
    def _write_smb(self):
        self.sys.write(SMB_CONF, smb_conf(self.shares()), mode=0o644)
        if self.sys.which("smbd"):
            self.sys.run(["systemctl", "reload-or-restart", "smbd"], timeout=30)

    def add_share(self, name, disk, acces="prive"):
        if not self.sys.which("smbd"):
            raise ApiError(503, "Samba n'est pas installé sur ce nœud (il l'est sur vault).", "samba_absent")
        if not isinstance(name, str) or not RE_SHARE.match(name) or name.lower() in ("global", "homes", "printers"):
            raise ApiError(400, "Nom de partage invalide : lettres, chiffres, tiret, 24 caractères au plus.", "nom")
        if acces not in ACCES:
            raise ApiError(400, "Type d'accès inconnu.", "acces")
        if any(s["nom"].lower() == name.lower() for s in self.shares()):
            raise ApiError(409, "Un partage porte déjà ce nom.", "nom")
        d = next((x for x in self.disks() if x["nom"] == disk and x["montage"]), None)
        if not d:
            raise ApiError(409, "Ce disque n'est pas prêt. Prépare-le d'abord.", "disque")
        if acces == "prive" and not self.state.get("samba_mdp"):
            raise ApiError(409, "Choisis d'abord le mot de passe des partages.", "mot_de_passe_requis")
        path = "%s/partages/%s" % (d["montage"], name)
        self.sys.makedirs(path, 0o775)
        self.sys.run(["chown", "%s:%s" % (SMB_USER, SMB_USER), path], timeout=10)
        share = {"nom": name, "disque": disk, "chemin": path, "acces": acces,
                 "adresse": "\\\\%s\\%s" % (self.sys.hostname(), name),
                 "adresse_mac": "smb://%s.local/%s" % (self.sys.hostname(), name)}
        self.state.set("partages", self.shares() + [share])
        self._write_smb()
        return share

    def remove_share(self, name):
        left = [s for s in self.shares() if s["nom"] != name]
        if len(left) == len(self.shares()):
            raise ApiError(404, "Partage introuvable.", "partage")
        self.state.set("partages", left)
        self._write_smb()          # les fichiers restent sur le disque
        return True

    def set_password(self, password):
        if not self.sys.which("smbpasswd"):
            raise ApiError(503, "Samba n'est pas installé sur ce nœud.", "samba_absent")
        if not isinstance(password, str) or len(password) < 8 or len(password) > 64 or "\n" in password:
            raise ApiError(400, "Le mot de passe doit faire au moins 8 caractères.", "mot_de_passe")
        r = self.sys.run(["smbpasswd", "-s", "-a", SMB_USER], input="%s\n%s\n" % (password, password), timeout=20)
        if not r.ok:
            raise ApiError(500, "Samba a refusé le mot de passe (%s)." % (r.err.strip()[:100] or "erreur"), "samba")
        self.state.set("samba_mdp", True)
        return True
