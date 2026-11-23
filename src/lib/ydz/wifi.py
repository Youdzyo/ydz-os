"""Wi-Fi du Pi (1.0.13) : réseaux à portée, réseaux enregistrés, changement de réseau SÛR.

Le Pi n'a ni clavier ni écran tactile et on le joint par ce même Wi-Fi : changer de réseau peut couper la
branche sur laquelle on est assis. Le changement est donc une transaction détachée (unité systemd
« ydz-wifi-bascule », elle survit à un redémarrage de ydzd) :
  1. on note la connexion Wi-Fi en cours ;
  2. on active la nouvelle avec nmcli ;
  3. dans les 60 s : la passerelle répond au ping ET le DNS (ou HTTP) marche ;
  4. sinon on réactive l'ancienne et on efface le profil s'il vient d'être créé.
Le résultat est écrit dans FICHIER_ETAT (des mots-clés seulement), que ydzd lit et montre dans l'interface.

Le mot de passe n'apparaît JAMAIS dans une ligne de commande ni dans un journal : ydzd écrit le profil
NetworkManager directement (fichier keyfile 0600 dans /etc/NetworkManager/system-connections), le script ne
fait que « nmcli connection up uuid … ».
Le profil Ethernet « ydz-pc » (et tout profil non Wi-Fi) n'est jamais touché.
"""
import json
import re
import shlex
import threading
import uuid as uuidmod

from .core import ApiError

DEV = "wlan0"
UNITE = "ydz-wifi-bascule"
FICHIER_ETAT = "/run/ydz/wifi-bascule.etat"
FICHIER_DEMANDE = "/run/ydz/wifi-bascule.json"
SCRIPT = "/run/ydz/wifi-bascule.sh"
DOSSIER_NM = "/etc/NetworkManager/system-connections"
PROFILS_PROTEGES = {"ydz-pc"}
DELAI_VERIF = 60
TYPE_WIFI = "802-11-wireless"

RAISONS = {
    "mot_de_passe": "mot de passe incorrect ?",
    "introuvable": "réseau introuvable (trop loin ?)",
    "delai": "le réseau n'a pas répondu à temps",
    "sans_internet": "connecté, mais ni la box ni Internet ne répondent",
    "activation": "connexion refusée",
}


# --- lecture de nmcli -------------------------------------------------------------------------------
def decouper(ligne):
    """Une ligne `nmcli -t` -> champs. Les « : » et « \\ » des valeurs sont échappés par un « \\ »."""
    champs, cur, i = [], [], 0
    while i < len(ligne):
        c = ligne[i]
        if c == "\\" and i + 1 < len(ligne):
            cur.append(ligne[i + 1])
            i += 2
            continue
        if c == ":":
            champs.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    champs.append("".join(cur))
    return champs


def securite(texte):
    """Champ SECURITY de nmcli -> (clé de gestion, libellé). None si non pris en charge (entreprise, WEP)."""
    t = (texte or "").upper()
    if not t or t == "--":
        return "aucune", "Ouvert"
    if "802.1X" in t or "EAP" in t:
        return None, "Entreprise"
    if "WEP" in t:
        return None, "WEP"
    if "WPA3" in t and "WPA2" not in t and "WPA1" not in t and "WPA " not in t + " ":
        return "sae", "WPA3"
    return "wpa-psk", "WPA3" if "WPA3" in t else "WPA2" if "WPA2" in t else "WPA"


def lire_scan(texte):
    """`nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY,FREQ dev wifi list` -> réseaux uniques, le plus fort d'abord.
    Les SSID cachés (vides) sont ignorés."""
    vus = {}
    for ligne in (texte or "").splitlines():
        if not ligne.strip():
            continue
        f = decouper(ligne)
        if len(f) < 5:
            continue
        en_cours, ssid, signal, sec, freq = f[0].strip() == "*", f[1], f[2], f[3], f[4]
        if not ssid or ssid == "--" or not ssid.strip("\x00"):
            continue
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        m = re.match(r"(\d+)", freq or "")
        mhz = int(m.group(1)) if m else None
        cle, lib = securite(sec)
        r = {"ssid": ssid, "signal": sig, "securite": lib, "pris_en_charge": cle is not None,
             "cle": cle, "frequence_mhz": mhz, "bande": "5 GHz" if mhz and mhz >= 4900 else "2,4 GHz" if mhz else "",
             "en_cours": en_cours}
        a = vus.get(ssid)
        if a is None or en_cours or (sig > a["signal"] and not a["en_cours"]):
            if a and a["en_cours"]:
                r["en_cours"] = True
            vus[ssid] = r
    return sorted(vus.values(), key=lambda x: (not x["en_cours"], -x["signal"], x["ssid"].lower()))


def lire_connexions(texte):
    """`nmcli -t -f NAME,UUID,TYPE,DEVICE,AUTOCONNECT connection show` -> [{nom, uuid, type, device}]."""
    out = []
    for ligne in (texte or "").splitlines():
        f = decouper(ligne)
        if len(f) >= 4 and f[1]:
            out.append({"nom": f[0], "uuid": f[1], "type": f[2], "device": f[3] if f[3] != "--" else "",
                        "auto": (f[4] if len(f) > 4 else "yes") == "yes"})
    return out


def lire_etat_bascule(texte):
    """FICHIER_ETAT : « etat raison horodatage » (mots-clés seulement)."""
    p = (texte or "").split()
    if len(p) < 2:
        return None
    try:
        t = int(p[2]) if len(p) > 2 else 0
    except ValueError:
        t = 0
    return {"etat": p[0], "raison": p[1], "t": t}


# --- profil keyfile (mot de passe écrit seulement dans ce fichier root 0600) ------------------------------
def _echapper_valeur(v):
    v = v.replace("\\", "\\\\")
    if v.startswith(" "):
        v = "\\s" + v[1:]
    return v


def _ssid_keyfile(ssid):
    b = ssid.encode("utf-8")
    if re.match(r"^[A-Za-z0-9 _.@#()!+&'-]+$", ssid) and not ssid.startswith(" ") and not ssid.endswith(" "):
        return ssid
    return ";".join(str(x) for x in b) + ";"


def valider_mdp(mdp, cle):
    if cle == "aucune":
        return None
    if not isinstance(mdp, str):
        return "Mot de passe manquant."
    if re.match(r"^[0-9A-Fa-f]{64}$", mdp):
        return None
    if not 8 <= len(mdp) <= 63:
        return "Le mot de passe Wi-Fi fait entre 8 et 63 caractères."
    if any(ord(c) < 32 or ord(c) > 126 for c in mdp):
        return "Le mot de passe contient un caractère non permis (accents, emoji)."
    return None


def profil_keyfile(ssid, cle, mdp, uid):
    lignes = ["[connection]", "id=%s" % _echapper_valeur(ssid.replace("\n", " ")), "uuid=%s" % uid, "type=wifi",
              "interface-name=%s" % DEV, "autoconnect=true", "autoconnect-priority=10", "",
              "[wifi]", "mode=infrastructure", "ssid=%s" % _ssid_keyfile(ssid), ""]
    if cle != "aucune":
        lignes += ["[wifi-security]", "key-mgmt=%s" % cle, "psk=%s" % _echapper_valeur(mdp), ""]
    lignes += ["[ipv4]", "method=auto", "", "[ipv6]", "addr-gen-mode=default", "method=auto", ""]
    return "\n".join(lignes)


# --- script de la transaction --------------------------------------------------------------------------
def script_bascule(cible_uuid, ancien_uuid, cree, profil="", dev=DEV, delai=DELAI_VERIF):
    """Script bash de la bascule. Aucun secret dedans : seulement des UUID et un chemin de fichier."""
    for u in (cible_uuid, ancien_uuid):
        if u and not re.match(r"^[0-9a-fA-F-]{8,40}$", u):
            raise ValueError("uuid invalide")
    q = shlex.quote
    return """#!/bin/bash
# YDZ OS : bascule Wi-Fi sûre (générée par ydzd, sans aucun mot de passe). Retour automatique en cas d'échec.
set -u
ETAT=%(etat)s
DEV=%(dev)s
CIBLE=%(cible)s
ANCIEN=%(ancien)s
CREE=%(cree)d
PROFIL=%(profil)s
etat() { printf '%%s %%s %%s\\n' "$1" "$2" "$(date +%%s)" > "$ETAT.tmp" && mv -f "$ETAT.tmp" "$ETAT"; }
raison_de() {
    case "$1" in
        *[Ss]ecrets*|*[Pp]assword*|*psk*|*802-1x*|*"mot de passe"*) echo mot_de_passe ;;
        *"No network with SSID"*|*"not found"*|*"introuvable"*) echo introuvable ;;
        *[Tt]imeout*|*"timed out"*) echo delai ;;
        *) echo activation ;;
    esac
}
verifier() {   # passerelle + (DNS ou HTTP), pendant %(delai)d s au plus
    local fin=$((SECONDS + %(delai)d)) gw
    while [ $SECONDS -lt $fin ]; do
        gw=$(ip -4 route show default dev "$DEV" 2>/dev/null | awk '{print $3; exit}')
        if [ -n "$gw" ] && ping -c 1 -W 2 "$gw" >/dev/null 2>&1; then
            if getent hosts deb.debian.org >/dev/null 2>&1 || curl -fsS -m 5 -o /dev/null http://deb.debian.org/ 2>/dev/null; then
                return 0
            fi
        fi
        sleep 3
    done
    return 1
}
retour() {   # retour sur l'ancien réseau, effacement du profil neuf
    etat retour "$1"
    echo "ydz-wifi : échec ($1), retour sur l'ancien réseau"
    if [ -n "$ANCIEN" ]; then
        nmcli --wait 40 connection up uuid "$ANCIEN" ifname "$DEV" >/dev/null 2>&1 \\
            || { sleep 5; nmcli --wait 40 connection up uuid "$ANCIEN" ifname "$DEV" >/dev/null 2>&1; } \\
            || echo "ydz-wifi : l'ancien réseau ne répond pas, NetworkManager reprendra la main"
    fi
    if [ "$CREE" = 1 ]; then
        nmcli connection delete uuid "$CIBLE" >/dev/null 2>&1 || { [ -n "$PROFIL" ] && rm -f "$PROFIL"; nmcli connection reload; }
    fi
    etat echec "$1"
    exit 0
}
etat en_cours activation
echo "ydz-wifi : bascule vers $CIBLE (ancien : ${ANCIEN:-aucun})"
nmcli connection reload >/dev/null 2>&1
ERR=$(timeout 50 nmcli --wait 45 connection up uuid "$CIBLE" ifname "$DEV" 2>&1 >/dev/null)
RC=$?
if [ $RC -ne 0 ]; then
    [ $RC -eq 124 ] && retour delai
    retour "$(raison_de "$ERR")"
fi
etat en_cours verification
if verifier; then
    etat ok verifie
    echo "ydz-wifi : nouveau réseau vérifié"
else
    retour sans_internet
fi
""" % {"etat": q(FICHIER_ETAT), "dev": q(dev), "cible": q(cible_uuid), "ancien": q(ancien_uuid or ""),
       "cree": 1 if cree else 0, "profil": q(profil or ""), "delai": delai}


class Wifi:
    def __init__(self, sys, state, events=None):
        self.sys, self.state, self.events = sys, state, events
        self.lock = threading.Lock()
        self._scan = (None, [])          # (monotonic, réseaux)
        self._ssid = {}                  # uuid -> ssid (cache)
        self._annonce = None             # dernier résultat déjà notifié

    # --- lecture ----------------------------------------------------------------------------------
    def present(self):
        return bool(self.sys.which("nmcli")) and self.sys.exists("/sys/class/net/%s" % DEV)

    def connexions(self):
        r = self.sys.run(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE,AUTOCONNECT", "connection", "show"], timeout=10)
        return lire_connexions(r.out) if r.ok else []

    def ssid_de(self, uid, nom):
        if uid not in self._ssid:
            r = self.sys.run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "uuid", uid], timeout=8)
            self._ssid[uid] = (r.out.strip().replace("\\:", ":") if r.ok and r.out.strip() else nom)
        return self._ssid[uid]

    def enregistres(self):
        out = []
        for c in self.connexions():
            if c["type"] != TYPE_WIFI or c["nom"] in PROFILS_PROTEGES:
                continue
            out.append({"nom": c["nom"], "uuid": c["uuid"], "ssid": self.ssid_de(c["uuid"], c["nom"]),
                        "actif": c["device"] == DEV, "auto": c["auto"]})
        return out

    def actuelle(self, liste=None):
        for c in liste if liste is not None else self.enregistres():
            if c["actif"]:
                return c
        return None

    def scanner(self, forcer=False):
        now = self.sys.monotonic()
        t, res = self._scan
        if not forcer and t is not None and now - t < 15:
            return res
        r = self.sys.run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,FREQ", "dev", "wifi", "list",
                          "--rescan", "yes" if forcer else "auto"], timeout=25)
        res = lire_scan(r.out) if r.ok else res
        self._scan = (now, res)
        return res

    def radio(self):
        r = self.sys.run(["nmcli", "radio", "wifi"], timeout=5)
        return r.out.strip() != "disabled" if r.ok else True

    def lien_filaire(self):
        """Vrai si un autre lien (Ethernet avec porteuse ET route par défaut) permet de joindre le Pi."""
        r = self.sys.run(["ip", "-4", "route", "show", "default"], timeout=5)
        for l in (r.out or "").splitlines():
            m = re.search(r"\bdev (\S+)", l)
            if m and m.group(1) != DEV and not m.group(1).startswith(("lo", "docker", "br-", "veth", "tailscale")):
                if (self.sys.read("/sys/class/net/%s/carrier" % m.group(1), "") or "").strip() == "1":
                    return True
        return False

    def bascule(self):
        """Dernière bascule : demande (sans mot de passe) + état écrit par le script."""
        try:
            d = json.loads(self.sys.read(FICHIER_DEMANDE, "") or "null")
        except ValueError:
            d = None
        if not isinstance(d, dict):
            return None
        e = lire_etat_bascule(self.sys.read(FICHIER_ETAT, ""))
        if not e or e["t"] < d.get("t", 0) - 5:
            e = {"etat": "en_cours", "raison": "attente", "t": d.get("t", 0)}
        out = {"ssid": d.get("ssid"), "ancien": d.get("ancien"), "t": d.get("t"), "etat": e["etat"],
               "etape": e["raison"] if e["etat"] == "en_cours" else None,
               "raison": e["raison"] if e["etat"] in ("echec", "retour") else None,
               "raison_texte": RAISONS.get(e["raison"]) if e["etat"] in ("echec", "retour") else None}
        if e["etat"] == "ok":
            out["message"] = "Connecté à %s" % d.get("ssid")
        elif e["etat"] in ("echec", "retour"):
            out["message"] = "Échec, retour sur %s : %s" % (d.get("ancien") or "l'ancien réseau",
                                                              RAISONS.get(e["raison"], "connexion refusée"))
        else:
            out["message"] = "Connexion à %s en cours…" % d.get("ssid")
        return out

    def annoncer(self):
        """Notification unique quand une bascule se termine (appelée par le hub)."""
        b = self.bascule()
        if not b or b["etat"] not in ("ok", "echec") or not self.events:
            return
        cle = (b["t"], b["etat"])
        if self._annonce == cle:
            return
        premier = self._annonce is None
        self._annonce = cle
        if not premier or self.sys.now() - (b["t"] or 0) < 300:
            self.events.publish("notification", {"niveau": "ok" if b["etat"] == "ok" else "warn", "texte": b["message"]})

    def status(self, scan=True):
        if not self.present():
            return {"present": False}
        enr = self.enregistres()
        act = self.actuelle(enr)
        filaire = self.lien_filaire()
        reseaux = self.scanner() if scan else self._scan[1]
        connus = {c["ssid"] for c in enr}
        return {"present": True, "actif": self.radio(), "connecte": act["ssid"] if act else None,
                "connexion": act["nom"] if act else None,
                "reseaux": [dict(x, enregistre=x["ssid"] in connus) for x in reseaux],
                "enregistres": enr, "filaire": filaire,
                "coupure_possible": filaire,
                "coupure_raison": None if filaire else
                "Le Wi-Fi est le seul lien de ce Pi avec la maison : le couper le rendrait injoignable.",
                "bascule": self.bascule(), "bascule_en_cours": self.en_cours()}

    # --- actions ----------------------------------------------------------------------------------
    def en_cours(self):
        r = self.sys.run(["systemctl", "is-active", UNITE + ".service", UNITE + ".timer"], timeout=5)
        if any(x.strip() in ("active", "activating") for x in r.out.splitlines()):
            return True
        b = self.bascule()            # entre la demande et le démarrage de l'unité (--on-active=2)
        return bool(b and b["etat"] == "en_cours" and self.sys.now() - (b["t"] or 0) < 200)

    def connecter(self, ssid=None, mdp=None, uid=None):
        """Lance la transaction. Retourne la demande (sans mot de passe)."""
        with self.lock:
            if self.en_cours():
                raise ApiError(409, "Un changement de réseau est déjà en cours.", "occupe")
            enr = self.enregistres()
            act = self.actuelle(enr)
            cree, profil = False, ""
            if uid:
                c = next((x for x in enr if x["uuid"] == uid), None)
                if not c:
                    raise ApiError(404, "Réseau enregistré inconnu.", "uuid")
                ssid = c["ssid"]
                if act and act["uuid"] == uid:
                    raise ApiError(409, "Le Pi est déjà connecté à %s." % ssid, "deja")
            else:
                if not isinstance(ssid, str) or not 0 < len(ssid.encode("utf-8")) <= 32:
                    raise ApiError(400, "Nom de réseau (SSID) invalide.", "ssid")
                if act and act["ssid"] == ssid:
                    raise ApiError(409, "Le Pi est déjà connecté à %s." % ssid, "deja")
                vu = next((x for x in self.scanner() if x["ssid"] == ssid), None)
                cle = vu["cle"] if vu else ("wpa-psk" if mdp else "aucune")
                if vu and not vu["pris_en_charge"]:
                    raise ApiError(400, "Ce réseau (%s) n'est pas pris en charge." % vu["securite"], "securite")
                err = valider_mdp(mdp, cle)
                if err:
                    raise ApiError(400, err, "mot_de_passe")
                existant = next((x for x in enr if x["ssid"] == ssid), None)
                if existant and not mdp:
                    uid = existant["uuid"]
                else:
                    uid = str(uuidmod.uuid4())
                    profil = "%s/ydz-%s.nmconnection" % (DOSSIER_NM, uid[:8])
                    self.sys.write(profil, profil_keyfile(ssid, cle, mdp or "", uid), mode=0o600)
                    cree = True
            demande = {"ssid": ssid, "ancien": act["ssid"] if act else None, "t": int(self.sys.now()),
                       "cree": cree}
            self.sys.write(FICHIER_DEMANDE, json.dumps(demande, ensure_ascii=False), mode=0o600)
            self.sys.write(FICHIER_ETAT, "en_cours attente %d\n" % demande["t"], mode=0o644)
            self.sys.write(SCRIPT, script_bascule(uid, act["uuid"] if act else "", cree, profil), mode=0o700)
            r = self.sys.run(["systemd-run", "--unit=" + UNITE, "--collect", "--quiet",
                              "--description=YDZ : changement de réseau Wi-Fi", "--property=TimeoutStartSec=240",
                              "--on-active=2", "/bin/bash", SCRIPT], timeout=15)
            if not r.ok:
                self.sys.write(FICHIER_ETAT, "echec activation %d\n" % demande["t"], mode=0o644)
                if cree:
                    self.sys.remove(profil)
                raise ApiError(500, "Le changement de réseau n'a pas pu démarrer (%s)." % (r.err or "").strip()[:120],
                               "systemd")
            self._scan = (None, self._scan[1])
            self._ssid.pop(uid, None)
            return dict(demande, en_cours=True)

    def oublier(self, uid):
        c = next((x for x in self.enregistres() if x["uuid"] == uid), None)
        if not c:
            raise ApiError(404, "Réseau enregistré inconnu.", "uuid")
        if c["actif"]:
            raise ApiError(409, "C'est le réseau utilisé en ce moment : connecte d'abord le Pi à un autre réseau.",
                           "actif")
        r = self.sys.run(["nmcli", "connection", "delete", "uuid", uid], timeout=15)
        if not r.ok:
            raise ApiError(500, "NetworkManager a refusé : %s" % (r.err or "").strip()[:120], "nmcli")
        self._ssid.pop(uid, None)
        return c["ssid"]

    def regler_radio(self, actif):
        if not isinstance(actif, bool):
            raise ApiError(400, "Champ « actif » invalide (vrai ou faux).", "actif")
        if not actif and not self.lien_filaire():
            raise ApiError(409, "Le Wi-Fi est le seul lien de ce Pi avec la maison : le couper le rendrait "
                                "injoignable. Branche d'abord un câble Ethernet relié à la box.", "seul_lien")
        r = self.sys.run(["nmcli", "radio", "wifi", "on" if actif else "off"], timeout=10)
        if not r.ok:
            raise ApiError(500, "NetworkManager a refusé : %s" % (r.err or "").strip()[:120], "nmcli")
        return actif
