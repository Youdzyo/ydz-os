"""Sauvegarde automatique vers le PC (1.0.11).

1.0.12 : par défaut, la sauvegarde part « dès que le PC s'allume » (PC vu en ligne 2 minutes de suite, et
aucune sauvegarde réussie depuis 20 h). Aucune alerte quand le PC est simplement éteint : seulement en cas
d'échec, ou après 3 jours sans sauvegarde réussie (puis tous les 3 jours). Le mode « à heure fixe » reste
possible, avec son ancienne logique d'essais.

À heure fixe (04:00 par défaut), si le PC répond, forge emballe sa configuration (/etc/ydz, /var/lib/ydz,
sans les sauvegardes de paquets, les caches ni, par défaut, les données des apps) dans un
ydz-forge-AAAAMMJJ-HHMM.tar.gz, l'envoie dans le partage Windows « YDZ-Sauvegardes » avec smbclient,
puis ne garde que les N plus récentes sur le PC. PC éteint : nouvel essai toutes les heures jusqu'à
09:00, puis une alerte.

Côté PC, un compte local dédié (ydz-sauvegarde) n'a accès qu'à ce partage ; son mot de passe est tiré
au hasard par le Pi et n'apparaît que dans le script « Préparer le PC » (route protégée par le PIN).
Le mot de passe passe à smbclient par un fichier d'identification en mode 600, jamais en argument.
"""
import json
import os
import re
import secrets
import string
import tarfile
import threading
import time

from . import config
from .core import ApiError

DOSSIER_TMP = "/var/lib/ydz/tmp"
SOURCES = ("/etc/ydz", "/var/lib/ydz")
DONNEES_APPS = "/var/lib/ydz/donnees"
# 1.0.15 (F-04) : secrets re-créables exclus de l'archive (régénérés à la restauration) : jeton de l'écran local,
# clé SSH d'extinction du PC, sauvegardes/caches. La clé de la tour (cle_tour dans etat.json) reste incluse mais
# l'archive est chiffrée.
EXCLUS = ("/var/lib/ydz/sauvegardes", "/var/lib/ydz/tmp", "/var/lib/ydz/cache",
          "/var/lib/ydz/jeton-local", "/var/lib/ydz/cle-pc", "/var/lib/ydz/cle-pc.pub")
DOSSIERS_CACHE = ("cache", ".cache", "__pycache__")
DOSSIER_PC = "C:\\YDZ-Sauvegardes"
REGLE_PC = "YDZ-SMB-forge"
RE_HEURE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
RE_PARTAGE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
RE_UTILISATEUR = re.compile(r"^[a-z0-9_-]{1,20}$")
FIN_ESSAIS = 9 * 60          # PC éteint : on réessaie toutes les heures jusqu'à 09:00
MODES = ("pc_allume", "heure")
PC_STABLE_S = 120            # le PC doit répondre 2 minutes de suite avant qu'on lui envoie quoi que ce soit
REESSAI_ECHEC_S = 3 * 3600   # après un échec, mode « PC allumé » : nouvel essai 3 h plus tard
RETARD_ALERTE_S = 3 * 86400  # alerte après 3 jours sans sauvegarde réussie, puis tous les 3 jours

DEFAUTS = {"actif": False, "hote": None, "partage": "YDZ-Sauvegardes", "utilisateur": "ydz-sauvegarde",
           "mot_de_passe": None, "heure": "04:00", "garder": 7, "inclure_donnees_apps": False,
           "mode": "pc_allume", "intervalle_h": 20}


class ErreurSauvegarde(Exception):
    pass


def mot_de_passe(n=20):
    """Lettres et chiffres seulement (rien à échapper dans PowerShell ni dans smbclient), avec au moins une
    majuscule, une minuscule et un chiffre (règles de complexité de Windows si elles sont actives)."""
    alpha = string.ascii_letters + string.digits
    while True:
        p = "".join(secrets.choice(alpha) for _ in range(n))
        if any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p):
            return p


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def fin_essais(debut):
    """Fin de la fenêtre d'essais : 09:00 pour une sauvegarde de nuit, sinon 5 h après (même jour)."""
    return FIN_ESSAIS if debut < FIN_ESSAIS else min(debut + 300, 24 * 60)


def nom_archive(noeud, t):
    return "ydz-%s-%s.tar.gz" % (noeud, time.strftime("%Y%m%d-%H%M", time.localtime(t)))


def exclu(chemin, inclure_donnees=False):
    """Vrai si ce chemin absolu ne doit pas entrer dans l'archive."""
    for e in EXCLUS + (() if inclure_donnees else (DONNEES_APPS,)):
        if chemin == e or chemin.startswith(e + "/"):
            return True
    return any(p in DOSSIERS_CACHE for p in chemin.split("/"))


def construire_archive(dest, sources, exclure, extra=None):
    """tar.gz de `sources` (chemins absolus) moins ce que `exclure(chemin)` refuse, plus `extra`
    ({nom: octets}). Un fichier qui disparaît ou change pendant la lecture est sauté, pas fatal.
    Retourne (nombre de fichiers, liste des sautés)."""
    n, sautes = 0, []
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with tarfile.open(dest, "w:gz", compresslevel=6) as tar:
        for src in sources:
            if not os.path.isdir(src) or exclure(src):
                continue
            for racine, dossiers, fichiers in os.walk(src):
                dossiers[:] = sorted(d for d in dossiers if not exclure(os.path.join(racine, d)))
                for f in sorted(fichiers):
                    p = os.path.join(racine, f)
                    if exclure(p) or os.path.abspath(p) == os.path.abspath(dest):
                        continue
                    if not (os.path.isfile(p) or os.path.islink(p)):
                        continue                         # sockets, tubes…
                    try:
                        tar.add(p, arcname=p.lstrip("/"), recursive=False)
                        n += 1
                    except OSError:
                        sautes.append(p)
        for nom, data in (extra or {}).items():
            import io
            ti = tarfile.TarInfo(nom)
            ti.size, ti.mtime, ti.mode = len(data), int(time.time()), 0o600
            tar.addfile(ti, io.BytesIO(data))
            n += 1
    return n, sautes


def erreur_smb(texte):
    """Sortie de smbclient -> explication en français."""
    t = texte or ""
    for motif, msg in (
            ("NT_STATUS_LOGON_FAILURE", "Le PC refuse le mot de passe : relance « Préparer le PC »."),
            ("NT_STATUS_ACCOUNT_DISABLED", "Le compte ydz-sauvegarde est désactivé sur le PC : relance « Préparer le PC »."),
            ("NT_STATUS_PASSWORD_EXPIRED", "Le mot de passe a expiré sur le PC : relance « Préparer le PC »."),
            ("NT_STATUS_BAD_NETWORK_NAME", "Le partage n'existe pas sur le PC : lance « Préparer le PC »."),
            ("NT_STATUS_ACCESS_DENIED", "Le PC refuse l'écriture dans le partage : relance « Préparer le PC »."),
            ("NT_STATUS_DISK_FULL", "Le disque du PC est plein."),
            ("NT_STATUS_IO_TIMEOUT", "Le PC ne répond pas sur le port 445 (pare-feu ?) : relance « Préparer le PC »."),
            ("NT_STATUS_CONNECTION_REFUSED", "Le partage de fichiers du PC ne répond pas (port 445 fermé)."),
            ("NT_STATUS_HOST_UNREACHABLE", "Le PC est injoignable."),
            ("NT_STATUS_NETWORK_UNREACHABLE", "Le PC est injoignable."),
            ("NT_STATUS_UNSUCCESSFUL", "Le PC est injoignable sur le port 445 (pare-feu ?).")):
        if motif in t:
            return msg
    m = re.search(r"NT_STATUS_[A-Z_]+", t)
    if m:
        return "Le PC a répondu %s." % m.group(0)
    lignes = [x for x in t.strip().splitlines() if x.strip()]
    return "smbclient : %s" % lignes[-1][:160] if lignes else "Envoi impossible (raison inconnue)."


def lire_ls(texte, prefixe):
    """Sortie de `smbclient -c ls` -> noms des archives YDZ de ce nœud, triées du plus ancien au plus récent.
    1.0.15 : chiffrées (.tar.gz.enc) comme anciennes (.tar.gz) ; tri sur l'horodatage AAAAMMJJ-HHMM."""
    rx = re.compile(r"^\s*(%s-\d{8}-\d{4}\.tar\.gz(?:\.enc)?)\s" % re.escape(prefixe))
    noms = {m.group(1) for m in (rx.match(l) for l in (texte or "").splitlines()) if m}
    return sorted(noms, key=lambda x: re.search(r"(\d{8}-\d{4})", x).group(1))


def octets_fr(n):
    for u, d in (("Go", 1e9), ("Mo", 1e6), ("Ko", 1e3)):
        if n >= d:
            return ("%.1f %s" % (n / d, u)).replace(".", ",")
    return "%d octets" % n


def script_powershell(utilisateur, mdp, partage, ip_pi, dossier=DOSSIER_PC, regle=REGLE_PC):
    """Commandes à coller dans un PowerShell administrateur sur le PC. On peut les relancer :
    elles mettent à jour ce qui existe déjà (mot de passe, droits, partage, pare-feu)."""
    for v, rx in ((utilisateur, RE_UTILISATEUR), (partage, RE_PARTAGE), (mdp, re.compile(r"^[A-Za-z0-9]{12,64}$")),
                  (ip_pi, config._RE_IP)):
        if not isinstance(v, str) or not rx.match(v):
            raise ValueError("valeur refusée dans le script : %r" % (v,))
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$u = '%s'; $p = ConvertTo-SecureString '%s' -AsPlainText -Force" % (utilisateur, mdp),
        "$d = '%s'; $s = '%s'" % (dossier, partage),
        "# 1. compte local dédié (mot de passe sans expiration), caché de l'écran de connexion",
        "if (Get-LocalUser -Name $u -ErrorAction SilentlyContinue) { Set-LocalUser -Name $u -Password $p "
        "-PasswordNeverExpires $true -UserMayChangePassword $false; Enable-LocalUser -Name $u }",
        "else { New-LocalUser -Name $u -Password $p -PasswordNeverExpires -UserMayNotChangePassword "
        "-AccountNeverExpires -Description 'Sauvegardes de la tour YDZ' | Out-Null }",
        "$k = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\\SpecialAccounts\\UserList'",
        "New-Item -Path $k -Force | Out-Null; New-ItemProperty -Path $k -Name $u -Value 0 -PropertyType DWord -Force | Out-Null",
        "# 2. dossier, avec le droit « Modifier » pour ce compte",
        "New-Item -ItemType Directory -Force -Path $d | Out-Null",
        "icacls $d /grant \"${u}:(OI)(CI)M\" | Out-Null",
        "# 3. partage réservé à ce compte",
        "if (Get-SmbShare -Name $s -ErrorAction SilentlyContinue) {",
        "  Grant-SmbShareAccess -Name $s -AccountName $u -AccessRight Full -Force | Out-Null",
        "  Get-SmbShareAccess -Name $s | Where-Object { $_.AccountName -notlike \"*\\$u\" } | "
        "ForEach-Object { Revoke-SmbShareAccess -Name $s -AccountName $_.AccountName -Force | Out-Null }",
        "} else { New-SmbShare -Name $s -Path $d -FullAccess $u -Description 'Sauvegardes YDZ' | Out-Null }",
        "# 4. pare-feu : port 445 ouvert pour forge seulement, quel que soit le profil réseau (Public compris)",
        "Remove-NetFirewallRule -Name '%s' -ErrorAction SilentlyContinue" % regle,
        "New-NetFirewallRule -Name '%s' -DisplayName 'YDZ : sauvegardes de forge (SMB)' -Direction Inbound "
        "-Protocol TCP -LocalPort 445 -RemoteAddress %s -Action Allow -Profile Any | Out-Null" % (regle, ip_pi),
        "Write-Host 'YDZ : le PC est pret a recevoir les sauvegardes.' -ForegroundColor Green",
    ])


class Sauvegarde:
    def __init__(self, hub):
        self.hub = hub
        self.sys = hub.sys
        self.state = hub.node.state
        self.conf = hub.node.conf
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.en_cours = False
        self.etape = None
        self.sources = SOURCES
        self.dossier_tmp = DOSSIER_TMP
        self._jour = None             # jour (AAAA-MM-JJ) déjà traité par la planification
        self._essaye = None           # jour où un essai a eu lieu (PC éteint)
        self._essai = None            # prochain essai (minutes depuis minuit)
        self._pc_depuis = None        # mode « PC allumé » : depuis quand le PC répond (heure)
        self._apres = 0               # mode « PC allumé » : pas de nouvel essai avant (heure), après un échec

    # --- réglages --------------------------------------------------------------------------------------
    def reglages(self):
        r = dict(DEFAUTS)
        r.update(self.state.get("sauvegarde") or {})
        if r.get("mode") not in MODES:          # réglages d'avant 1.0.12 : le nouveau mode par défaut
            r["mode"] = DEFAUTS["mode"]
        if not r.get("hote"):
            r["hote"] = self.conf.get("wol_ip")
        if not r.get("mot_de_passe"):
            r["mot_de_passe"] = mot_de_passe()
            self.state.set("sauvegarde", r)
        if not r.get("phrase"):                       # 1.0.15 : phrase de chiffrement de l'archive (montrée une fois)
            r["phrase"] = mot_de_passe(24)
            self.state.set("sauvegarde", r)
        return r

    def derniere(self):
        return (self.state.get("sauvegarde_etat") or {}).get("derniere")

    def status(self):
        """Pour /api/etat et GET /api/sauvegarde : jamais le mot de passe."""
        r = self.reglages()
        ok = (self.state.get("sauvegarde_etat") or {}).get("dernier_ok")
        return {"actif": r["actif"], "heure": r["heure"], "garder": r["garder"], "mode": r["mode"],
                "intervalle_h": r["intervalle_h"], "fait_aujourdhui": bool(ok) and time.strftime(
                    "%Y-%m-%d", time.localtime(ok)) == time.strftime("%Y-%m-%d", time.localtime(self.sys.now())),
                "pc_en_ligne_depuis": self._pc_depuis,
                "inclure_donnees_apps": r["inclure_donnees_apps"], "hote": r["hote"], "partage": r["partage"],
                "utilisateur": r["utilisateur"], "mot_de_passe_defini": bool(r["mot_de_passe"]),
                "derniere": self.derniere(), "dernier_succes": (self.state.get("sauvegarde_etat") or {}).get("dernier_ok"),
                "prochaine": self.prochaine(r), "en_cours": self.en_cours, "etape": self.etape,
                "phrase_definie": bool(r.get("phrase")), "chiffree": True,
                "smbclient": bool(self.sys.which("smbclient")), "openssl": bool(self.sys.which("openssl"))}

    def regler(self, b):
        r = self.reglages()
        avant_actif = r["actif"]
        for k in ("actif", "inclure_donnees_apps"):
            if k in b:
                if not isinstance(b[k], bool):
                    raise ApiError(400, "Champ « %s » invalide." % k, k)
                r[k] = b[k]
        if "mode" in b:
            if b["mode"] not in MODES:
                raise ApiError(400, "Mode inconnu : « pc_allume » (dès que le PC s'allume) ou « heure ».", "mode")
            r["mode"] = b["mode"]
        if "intervalle_h" in b:
            v = b["intervalle_h"]
            if isinstance(v, bool) or not isinstance(v, int) or not 6 <= v <= 72:
                raise ApiError(400, "Écart entre deux sauvegardes : de 6 à 72 heures.", "intervalle_h")
            r["intervalle_h"] = v
        if "heure" in b:
            if not isinstance(b["heure"], str) or not RE_HEURE.match(b["heure"]):
                raise ApiError(400, "Heure invalide (forme attendue : 04:00).", "heure")
            r["heure"] = b["heure"]
        if "garder" in b:
            v = b["garder"]
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 60:
                raise ApiError(400, "Garde de 1 à 60 sauvegardes sur le PC.", "garder")
            r["garder"] = v
        if "hote" in b:
            if not isinstance(b["hote"], str) or not config._RE_IP.match(b["hote"]):
                raise ApiError(400, "Adresse IP du PC invalide (exemple : 192.168.1.23).", "hote")
            r["hote"] = b["hote"]
        if "partage" in b:
            if not isinstance(b["partage"], str) or not RE_PARTAGE.match(b["partage"]):
                raise ApiError(400, "Nom de partage invalide : lettres, chiffres et tirets.", "partage")
            r["partage"] = b["partage"]
        if b.get("actif") is True and not avant_actif:
            e = dict(self.state.get("sauvegarde_etat") or {})
            e["suivi_depuis"] = int(self.sys.now())      # le compte des 3 jours repart de l'activation
            self.state.set("sauvegarde_etat", e)
        self.state.set("sauvegarde", r)
        self._jour = None             # nouvelle heure : la planification repart de zéro
        self._apres = 0
        return self.status()

    def preparer_pc(self, nouveau=False):
        """Script PowerShell pour le PC. Contient le mot de passe : route « action » seulement."""
        r = self.reglages()
        if nouveau:
            r["mot_de_passe"] = mot_de_passe()
            self.state.set("sauvegarde", r)
        ip = self.sys.primary_ip() or "192.168.1.41"
        return {"script": script_powershell(r["utilisateur"], r["mot_de_passe"], r["partage"], ip),
                "utilisateur": r["utilisateur"], "partage": r["partage"], "hote": r["hote"], "dossier": DOSSIER_PC,
                "ip_pi": ip, "regle": REGLE_PC, "phrase": r["phrase"],      # montrée une fois : à noter de côté
                "restaurer": "openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in ARCHIVE.tar.gz.enc "
                             "-out ydz.tar.gz -pass pass:<phrase>  puis  sudo tar -xzf ydz.tar.gz -C /"}

    # --- planification -----------------------------------------------------------------------------------
    def prochaine(self, r=None):
        r = r or self.reglages()
        if not r["actif"]:
            return None
        now = self.sys.now()
        if r["mode"] == "pc_allume":
            # pas d'heure : « dès que le PC s'allume », mais pas avant la fin de l'écart depuis la dernière réussie
            ok = (self.state.get("sauvegarde_etat") or {}).get("dernier_ok")
            return int(max(now, (ok or 0) + r["intervalle_h"] * 3600, self._apres))
        t = time.localtime(now)
        m = t.tm_hour * 60 + t.tm_min
        debut = minutes(r["heure"])
        fin = fin_essais(debut)
        jour = time.strftime("%Y-%m-%d", t)

        def a(jours, mins):
            base = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + jours, mins // 60, mins % 60, 0, 0, 0, -1))
            return int(base)
        if self._jour == jour or m >= fin:
            return a(1, debut)
        if m < debut:
            return a(0, debut)
        return a(0, self._essai) if self._essai and self._essaye == jour and self._essai > m else int(now)

    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-sauvegarde", daemon=True).start()

    def _boucle(self):
        time.sleep(30)
        while not self.stop.is_set():
            try:
                self.planifier()
            except Exception:
                pass
            self.stop.wait(60)

    def pc_allume(self, r):
        return bool(self.hub.wol._online(r["hote"]))

    def planifier(self):
        r = self.reglages()
        if not r["actif"]:
            self._pc_depuis = None
            return
        self.surveiller_retard()
        if self.en_cours:
            return
        if r["mode"] == "pc_allume":
            return self._planifier_pc(r)
        t = time.localtime(self.sys.now())
        jour = time.strftime("%Y-%m-%d", t)
        m = t.tm_hour * 60 + t.tm_min
        debut = minutes(r["heure"])
        fin = fin_essais(debut)
        if self._jour is None:
            # au démarrage du démon : la sauvegarde du jour est-elle déjà faite ?
            d = self.derniere()
            self._jour = ""
            if d and d.get("t"):
                td = time.localtime(d["t"])
                if time.strftime("%Y-%m-%d", td) == jour and td.tm_hour * 60 + td.tm_min >= debut:
                    self._jour = jour
        if self._jour == jour:
            return
        if debut <= m < fin:
            if self._essaye == jour and self._essai is not None and m < self._essai:
                return
            self._essaye = jour
            if not self.pc_allume(r):
                self._essai = m + 60
                return
            self._jour = jour
            self._essai = None
            try:
                self.lancer(manuel=False)
            except ApiError:
                pass
        elif m >= fin and self._essaye == jour:
            self._jour = jour
            self._essai = None
            self._enregistrer(False, erreur="Le PC est resté éteint jusqu'à %02d:%02d." % (fin // 60, fin % 60))
            self.hub.alerte("sauvegarde", "warn", "Sauvegarde impossible : le PC est éteint")

    def _planifier_pc(self, r):
        """Mode « dès que le PC s'allume » (appelé chaque minute). Aucune alerte quand le PC est éteint."""
        now = self.sys.now()
        if not self.pc_allume(r):
            self._pc_depuis = None
            return
        if self._pc_depuis is None:
            self._pc_depuis = now
            return
        if now - self._pc_depuis < PC_STABLE_S or now < self._apres:
            return
        ok = (self.state.get("sauvegarde_etat") or {}).get("dernier_ok")
        if ok and now - ok < r["intervalle_h"] * 3600:
            return
        self._apres = now + REESSAI_ECHEC_S      # en cas d'échec, on ne réessaie pas avant 3 h
        try:
            self.lancer(manuel=False)
        except ApiError:
            pass

    def surveiller_retard(self):
        """Alerte une fois après 3 jours sans sauvegarde réussie, puis tous les 3 jours (horodatage gardé)."""
        now = self.sys.now()
        e = dict(self.state.get("sauvegarde_etat") or {})
        ref = max(e.get("dernier_ok") or 0, e.get("suivi_depuis") or 0)
        if not ref:
            e["suivi_depuis"] = int(now)          # sauvegarde activée sans réussite connue : on compte d'ici
            self.state.set("sauvegarde_etat", e)
            return
        derniere_alerte = e.get("alerte_retard") or 0
        if now - ref >= RETARD_ALERTE_S and now - max(derniere_alerte, ref) >= RETARD_ALERTE_S:
            e["alerte_retard"] = int(now)
            self.state.set("sauvegarde_etat", e)
            jours = int((now - ref) // 86400)
            self.hub.alerte("sauvegarde", "warn", "Aucune sauvegarde réussie depuis %d jours : allume le PC ou "
                                                  "regarde Réglages > Sauvegarde." % jours)

    # --- exécution ------------------------------------------------------------------------------------
    def lancer(self, manuel=True):
        r = self.reglages()
        with self.lock:
            if self.en_cours:
                raise ApiError(409, "Une sauvegarde est déjà en cours.", "sauvegarde_en_cours")
            if not self.sys.which("smbclient"):
                raise ApiError(409, "smbclient n'est pas installé sur forge : lance « sudo apt install smbclient ».",
                               "smbclient")
            if manuel and not self.pc_allume(r):
                raise ApiError(409, "Le PC est éteint : allume-le (bouton Réveiller dans Réseau) puis recommence.",
                               "pc_eteint")
            self.en_cours, self.etape = True, "Préparation de l'archive"
        if self.reel:
            threading.Thread(target=self._executer, args=(r, manuel), name="ydz-sauvegarde-envoi", daemon=True).start()
        else:
            self._executer(r, manuel)
        return self.status()

    def _smb(self, r, auth, commande, timeout=120, protege=True):
        """1.0.15 (F-04) : SMB3 chiffré et signé (--client-protection=encrypt). Un serveur pirate sans le mot de
        passe ne peut pas établir la session. Repli si smbclient est trop ancien pour l'option."""
        base = ["smbclient", "//%s/%s" % (r["hote"], r["partage"]), "-A", auth, "-m", "SMB3"]
        if protege and not getattr(self, "_sans_chiffrement_smb", False):
            res = self.sys.run(base + ["--client-protection=encrypt", "-c", commande], timeout=timeout)
            sortie = (res.out or "") + (res.err or "")
            if res.rc in (2, 22) or "client-protection" in sortie or "nrecognized" in sortie or "nknown" in sortie:
                self._sans_chiffrement_smb = True        # smbclient trop ancien : on retombe sur SMB3 simple
                if getattr(self, "hub", None):
                    self.hub.alerte("sauvegarde", "info", "smbclient est trop ancien pour le chiffrement SMB3 : "
                                    "la sauvegarde part quand même (l'archive, elle, est chiffrée).", ui=False)
            else:
                return res
        return self.sys.run(base + ["-c", commande], timeout=timeout)

    def _mac_pc_ok(self, r):
        """1.0.15 : avant d'envoyer, vérifie que l'IP du PC répond bien avec la MAC attendue (anti-usurpation
        d'IP quand le PC est éteint). Refuse SEULEMENT sur un désaccord franc ; inconnue -> on laisse passer."""
        attendue = re.sub(r"[^0-9a-f]", "", (self.conf.get("wol_mac") or "").lower())
        if not attendue or not self.reel:
            return True
        arp = self.sys.read("/proc/net/arp", "") or ""
        for ligne in arp.splitlines()[1:]:
            ch = ligne.split()
            if len(ch) >= 4 and ch[0] == r["hote"]:
                vue = re.sub(r"[^0-9a-f]", "", ch[3].lower())
                return not vue or vue == "000000000000" or vue == attendue
        return True

    def _chiffrer(self, src, dest, phrase):
        """Chiffre `src` -> `dest` avec openssl (AES-256-CBC, PBKDF2 200000 itérations). Vrai si réussi.
        La phrase passe par l'environnement (jamais dans la ligne de commande)."""
        if not self.sys.which("openssl"):
            raise ErreurSauvegarde("openssl n'est pas installé : impossible de chiffrer la sauvegarde "
                                   "(sudo apt install openssl).")
        res = self.sys.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt",
                            "-in", src, "-out", dest, "-pass", "env:YDZ_PHRASE"],
                           timeout=600, env={"YDZ_PHRASE": phrase})
        if not res.ok:
            raise ErreurSauvegarde("chiffrement impossible (openssl) : %s" % (res.err or res.out or "").strip()[:120])
        return True

    def _executer(self, r, manuel):
        t0 = self.sys.now()
        nom = nom_archive(self.hub.node.name, t0)
        envoi = nom + ".enc"                    # 1.0.15 : ce qui part sur le PC est chiffré
        archive = os.path.join(self.dossier_tmp, nom)
        chiffree = archive + ".enc"
        auth = os.path.join(self.dossier_tmp, "smb-auth")
        taille, err = None, None
        try:
            if not self._mac_pc_ok(r):
                raise ErreurSauvegarde("L'adresse du PC répond avec une autre carte réseau que prévu : "
                                       "sauvegarde annulée (usurpation d'adresse ?).")
            extra = {"ydz-sauvegarde.json": json.dumps(self._resume(r, t0), ensure_ascii=False, indent=1).encode("utf-8")}
            incl = r["inclure_donnees_apps"]
            try:
                construire_archive(archive, self.sources, lambda p: exclu(p, incl), extra)
                taille = os.path.getsize(archive)
            except OSError as ex:
                raise ErreurSauvegarde("Archive impossible à créer : %s" % ex)
            self.etape = "Chiffrement"
            self._chiffrer(archive, chiffree, r["phrase"])
            try:
                os.remove(archive)             # le clair ne reste jamais sur le disque
            except OSError:
                pass
            self.etape = "Envoi vers le PC"
            self.sys.write(auth, "username = %s\npassword = %s\n" % (r["utilisateur"], r["mot_de_passe"]), mode=0o600)
            res = self._smb(r, auth, 'put "%s" "%s"' % (chiffree, envoi), timeout=3600)
            sortie = res.out + "\n" + res.err
            if not res.ok or "NT_STATUS_" in sortie:
                raise ErreurSauvegarde(erreur_smb(sortie))
            self.etape = "Ménage sur le PC"
            try:
                self._elaguer(r, auth)
            except Exception:
                pass                   # le ménage raté n'annule pas une sauvegarde réussie
        except ErreurSauvegarde as ex:
            err = str(ex)
        except Exception as ex:        # jamais de trace dans le démon : un message clair
            err = "Erreur inattendue : %r" % ex
        finally:
            self.sys.remove(auth)              # le mot de passe ne reste jamais sur le disque
            for f in (archive, chiffree):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except OSError:
                    pass
            self.en_cours, self.etape = False, None
        self._enregistrer(err is None, taille=taille, erreur=err, fichier=envoi, t=int(t0))
        if err:
            self.hub.alerte("sauvegarde", "warn", "Sauvegarde ratée : %s" % err)
        elif manuel:
            self.hub.alerte("sauvegarde", "ok", "Sauvegarde envoyée sur le PC (%s)" % octets_fr(taille or 0))
        elif getattr(self.hub, "activite", None):        # 1.0.12 : la sauvegarde automatique va au journal
            self.hub.activite.auto("Sauvegarde automatique envoyée sur le PC (%s)" % octets_fr(taille or 0),
                                   categorie="sauvegarde")

    def _elaguer(self, r, auth):
        # 1.0.13 : les archives faites sous l'ancien nom du Pi (ydz-forge-…) comptent aussi
        noms = []
        anciens = list(getattr(getattr(self.hub, "noms", None), "anciens", []) or [])
        for h in [self.hub.node.name] + anciens:
            prefixe = "ydz-%s" % h
            res = self._smb(r, auth, "ls %s-*.tar.gz*" % prefixe)   # .tar.gz et .tar.gz.enc
            noms += lire_ls(res.out, prefixe)
        noms.sort(key=lambda x: re.search(r"(\d{8}-\d{4})", x).group(1))  # du plus ancien au plus récent
        trop = noms[:-r["garder"]] if len(noms) > r["garder"] else []
        if trop:
            self._smb(r, auth, "; ".join('del "%s"' % n for n in trop))
        return trop

    def _resume(self, r, t):
        apps = self.state.get("apps") or {}
        return {"ydz": config.version(), "noeud": self.hub.node.name, "date": int(t),
                "contenu": list(self.sources), "donnees_apps": r["inclure_donnees_apps"],
                "apps": {k: {"reglages": v.get("reglages"), "donnees": v.get("donnees"), "installe_le": v.get("installe_le")}
                         for k, v in apps.items()},
                "chiffrement": "openssl aes-256-cbc pbkdf2 200000", "jeton_local": "régénéré au redémarrage",
                "cle_pc": "à recréer dans Réseau > Éteindre le PC", "archive": nom_archive(self.hub.node.name, t) + ".enc",
                "restaurer": "1) openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in %s.enc -out ydz.tar.gz "
                             "-pass pass:<phrase>  2) sudo systemctl stop ydzd  3) sudo tar -xzf ydz.tar.gz -C /"
                             % nom_archive(self.hub.node.name, t)}

    def _enregistrer(self, ok, taille=None, erreur=None, fichier=None, t=None):
        e = dict(self.state.get("sauvegarde_etat") or {})
        e["derniere"] = {"t": int(t or self.sys.now()), "ok": ok, "taille": taille, "erreur": erreur, "fichier": fichier}
        if ok:
            e["dernier_ok"] = e["derniere"]["t"]
        self.state.set("sauvegarde_etat", e)
