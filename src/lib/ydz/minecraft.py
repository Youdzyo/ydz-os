"""Joueurs Minecraft (1.0.11) : « Server List Ping » du serveur Java, comme la liste des serveurs du jeu.

Toutes les 60 s (vrai système seulement), YDZ demande au serveur Minecraft installé par l'app
« minecraft » combien de joueurs sont connectés, leurs noms, la version et le message d'accueil.
Protocole (wiki.vg, « Server List Ping ») : paquet de poignée de main (état suivant = 1, statut),
puis demande de statut ; la réponse est un JSON. Sockets Python pures, délai de 3 s.
"""
import json
import re
import socket
import struct
import threading
import time

PORT = 25565
DELAI = 3.0


# --- varints (entiers à taille variable du protocole Minecraft) ------------------------------------
def varint(n):
    n &= 0xFFFFFFFF                      # les négatifs (version -1) s'écrivent sur 5 octets
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def lire_varint(lire):
    """`lire(1)` -> 1 octet. Retourne l'entier (signé 32 bits)."""
    n = 0
    for i in range(5):
        b = lire(1)
        if not b:
            raise EOFError("connexion fermée")
        n |= (b[0] & 0x7F) << (7 * i)
        if not b[0] & 0x80:
            return n - (1 << 32) if n & 0x80000000 else n
    raise ValueError("varint trop long")


def chaine(s):
    b = s.encode("utf-8")
    return varint(len(b)) + b


def paquet(ident, corps=b""):
    donnees = varint(ident) + corps
    return varint(len(donnees)) + donnees


def poignee_de_main(hote, port, version=-1):
    return paquet(0x00, varint(version) + chaine(hote) + struct.pack(">H", port) + varint(1))


def demande_statut():
    return paquet(0x00)


# --- réponse ----------------------------------------------------------------------------------------
def texte_motd(d):
    """Le message d'accueil peut être une chaîne ou un composant de texte JSON : on n'en garde que le texte."""
    if isinstance(d, str):
        t = d
    elif isinstance(d, dict):
        t = str(d.get("text", "")) + "".join(texte_motd(x) for x in d.get("extra") or [])
    elif isinstance(d, list):
        t = "".join(texte_motd(x) for x in d)
    else:
        t = ""
    return re.sub(r"§.", "", t)


def resume(statut):
    """JSON de statut du serveur -> ce que YDZ affiche."""
    j = (statut or {}).get("players") or {}
    noms = [p.get("name") for p in j.get("sample") or [] if isinstance(p, dict) and isinstance(p.get("name"), str)]
    # certains serveurs mettent un faux échantillon (« et 12 autres… ») : on garde les vrais pseudos
    noms = [n for n in noms if re.match(r"^[A-Za-z0-9_]{1,16}$", n)]
    return {"en_ligne": True, "joueurs": int(j.get("online") or 0), "max": int(j.get("max") or 0),
            "noms": sorted(noms, key=str.lower), "version": ((statut or {}).get("version") or {}).get("name"),
            "motd": texte_motd((statut or {}).get("description")).strip()[:120]}


def ping(hote, port=PORT, delai=DELAI):
    """Interroge le serveur. Retourne le JSON de statut (dict) ; lève OSError/ValueError sinon."""
    with socket.create_connection((hote, port), timeout=delai) as s:
        s.settimeout(delai)
        s.sendall(poignee_de_main(hote, port) + demande_statut())
        tampon = bytearray()

        def lire(n):
            while len(tampon) < n:
                b = s.recv(4096)
                if not b:
                    break
                tampon.extend(b)
            out = bytes(tampon[:n])
            del tampon[:n]
            return out
        longueur = lire_varint(lire)
        if not 0 < longueur < 1 << 21:
            raise ValueError("réponse de taille anormale")
        corps = lire(longueur)
        if len(corps) < longueur:
            raise EOFError("réponse tronquée")
        pos = [0]

        def lire_corps(n):
            b = corps[pos[0]:pos[0] + n]
            pos[0] += n
            return b
        if lire_varint(lire_corps) != 0x00:
            raise ValueError("paquet inattendu")
        n = lire_varint(lire_corps)
        texte = lire_corps(n).decode("utf-8", "replace")
        return json.loads(texte)


class Minecraft:
    def __init__(self, hub, alerte=None):
        self.hub = hub
        self.sys = hub.sys
        self.alerte = alerte or hub.alerte
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.etat = None                  # None tant que l'app n'est pas installée
        self._noms = None                 # joueurs vus au tour précédent (None = premier tour)

    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-minecraft", daemon=True).start()

    def _boucle(self):
        time.sleep(20)
        while not self.stop.is_set():
            try:
                self.mesurer()
            except Exception:
                pass
            self.stop.wait(60)

    def installation(self):
        """(nœud, ip, état de l'app) du premier serveur Minecraft installé, ou None."""
        own = self.hub.node.name
        for n in self.hub.nodes(self.hub.node.status()):
            if not n.get("en_ligne"):
                continue
            for a in n.get("apps", []):
                if a.get("id") == "minecraft":
                    ip = "127.0.0.1" if n["nom"] == own else (n.get("ip") or n.get("adresse"))
                    return n["nom"], ip, a.get("etat")
        return None

    def mesurer(self, pinger=ping):
        inst = self.installation()
        if not inst:
            self.etat, self._noms = None, None
            return None
        noeud, ip, etat_app = inst
        res = {"en_ligne": False, "joueurs": 0, "max": 0, "noms": [], "version": None, "motd": None}
        if ip and etat_app in ("actif", "partiel"):
            try:
                res = resume(pinger(ip, PORT))
            except (OSError, ValueError, EOFError):
                pass                          # serveur qui démarre, ou arrêté : « hors ligne »
        res.update(noeud=noeud, maj=int(self.sys.now()))
        self._annoncer(res)
        self.etat = res
        return res

    def _annoncer(self, res):
        noms = set(res["noms"]) if res["en_ligne"] else set()
        avant = self._noms
        self._noms = noms
        if avant is None or not res["en_ligne"]:
            return
        for n in sorted(noms - avant, key=str.lower):
            self.alerte("minecraft", "info", "%s a rejoint le serveur Minecraft (%d/%d)" % (n, res["joueurs"], res["max"]),
                        joueur=n)
        anciens = self.etat or {}
        if not noms and res["joueurs"] > (anciens.get("joueurs") or 0) and anciens.get("en_ligne"):
            self.alerte("minecraft", "info", "Un joueur a rejoint le serveur Minecraft (%d/%d)" % (res["joueurs"], res["max"]))
