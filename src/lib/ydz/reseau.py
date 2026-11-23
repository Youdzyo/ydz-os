"""Réseau de la maison (1.0.10) : qualité du Wi-Fi du Pi, test de débit la nuit, pubs bloquées par Pi-hole.

Tout tourne dans un fil à part, seulement sur le vrai système : la simulation et les tests
n'envoient rien dehors. Le test de débit (les seuls octets qui sortent vers Internet) utilise
les points de mesure publics de Cloudflare, une fois par nuit à 03:30 ou à la demande.
Pi-hole est interrogé en local (sur le nœud où l'app est installée), avec le mot de passe que
YDZ a enregistré pour l'app ; la session obtenue est gardée tant qu'elle est valable.
"""
import re
import threading
import time

from .core import ApiError

URL_DOWN = "https://speed.cloudflare.com/__down?bytes=25000000"
URL_PING = "https://speed.cloudflare.com/__down?bytes=0"
URL_UP = "https://speed.cloudflare.com/__up"
OCTETS_UP = 10_000_000
ENTETES = {"User-Agent": "YDZ-OS"}
HEURE_DEBIT = (3, 30)
DEBITS_MAX = 30
PIHOLE_PORT = 8081
PIHOLE_PERIODE = 300

# --- contrôle réseau horaire (1.0.15) -------------------------------------------------------
URL_DOWN_LEGER = "https://speed.cloudflare.com/__down?bytes=10000000"   # ~10 Mo
OCTETS_UP_LEGER = 5_000_000                                             # ~5 Mo
URL_DNS = "https://speed.cloudflare.com/__down?bytes=0"                 # test DNS + joignabilité
HOTE_DNS = "one.one.one.one"
CONTROLE_INTERVALLES = (0, 30, 60, 180)        # minutes (0 = désactivé)
CONTROLE_DEFAUT = 60
SEUIL_DEFAUT = 50                              # Mbit/s : alerte si le débit descend sous ce seuil
CONTROLES_JOURS = 7
OCTETS_PAR_CONTROLE = 15_000_000               # ~10 Mo descendants + 5 Mo montants


# --- Wi-Fi ---------------------------------------------------------------------------------
def qualite(dbm):
    """-90 dBm -> 0 %, -50 dBm -> 100 % (linéaire entre les deux)."""
    if dbm is None:
        return None
    return int(round(max(0.0, min(100.0, (dbm + 90) * 2.5))))


def niveau(pct):
    if pct is None:
        return None
    return "excellent" if pct >= 75 else "bon" if pct >= 50 else "moyen" if pct >= 25 else "faible"


def lire_iw(texte):
    """Sortie de `iw dev wlan0 link` -> {ssid, signal_dbm, qualite_pct, debit_mbps, frequence_mhz, niveau}
    ou None si le Wi-Fi n'est pas connecté."""
    t = texte or ""
    if "Connected to" not in t:
        return None

    def cherche(motif, conv=str):
        m = re.search(motif, t, re.M)
        try:
            return conv(m.group(1).strip()) if m else None
        except ValueError:
            return None
    dbm = cherche(r"^\s*signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", float)
    dbm = int(round(dbm)) if dbm is not None else None
    debit = cherche(r"^\s*tx bitrate:\s*([\d.]+)\s*MBit/s", float)
    freq = cherche(r"^\s*freq:\s*([\d.]+)", float)
    pct = qualite(dbm)
    return {"ssid": cherche(r"^\s*SSID:\s*(.+)$"), "signal_dbm": dbm, "qualite_pct": pct,
            "debit_mbps": round(debit) if debit is not None else None,
            "frequence_mhz": int(freq) if freq is not None else None, "niveau": niveau(pct)}


def lire_proc_wireless(texte, dev="wlan0"):
    """Repli sans `iw` : /proc/net/wireless (niveau du signal seulement)."""
    for ligne in (texte or "").splitlines():
        if ligne.strip().startswith(dev + ":"):
            c = ligne.split()
            try:
                dbm = int(float(c[3].rstrip(".")))
            except (IndexError, ValueError):
                return None
            if dbm >= 0:
                return None
            pct = qualite(dbm)
            return {"ssid": None, "signal_dbm": dbm, "qualite_pct": pct, "debit_mbps": None,
                    "frequence_mhz": None, "niveau": niveau(pct)}
    return None


# --- débit ---------------------------------------------------------------------------------
def ajouter_debit(liste, res, garder=DEBITS_MAX):
    """Ajoute un résultat à l'historique et ne garde que les `garder` derniers."""
    out = [x for x in (liste or []) if isinstance(x, dict)] + [res]
    return out[-garder:]


def mbps(octets, secondes):
    return round(octets * 8 / secondes / 1e6, 1) if octets and secondes else None


def mediane(v):
    v = sorted(v)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def mesurer_debit(sys):
    """Mesure réelle (2 téléchargements de 25 Mo, garde le meilleur ; 10 Mo envoyés ; latence médiane de 5)."""
    lat = sys.http_latence(URL_PING, 5, headers=ENTETES)
    downs = []
    for _ in range(2):
        r = sys.http_download_time(URL_DOWN, timeout=60, headers=ENTETES)
        if r and r[0] > 1_000_000:
            downs.append(mbps(*r))
    up = sys.http_upload_time(URL_UP, OCTETS_UP, timeout=60, headers=ENTETES)
    if not downs:
        return None
    p = mediane(lat)
    return {"t": int(sys.now()), "down_mbps": max(downs), "up_mbps": mbps(*up) if up else None,
            "ping_ms": round(p, 1) if p is not None else None}


def gigue(valeurs):
    """Gigue (jitter) : moyenne des écarts absolus entre latences successives (ms)."""
    v = [x for x in (valeurs or []) if x is not None]
    if len(v) < 2:
        return None
    return round(sum(abs(v[i] - v[i - 1]) for i in range(1, len(v))) / (len(v) - 1), 1)


def mesurer_controle(sys, n_sondes=8):
    """Contrôle léger (1.0.15) : ~10 Mo descendants, ~5 Mo montants, latence/gigue/perte (n petites requêtes),
    passerelle joignable, DNS qui répond, état du Wi-Fi du Pi. Renvoie un dict compact."""
    t = int(sys.now())
    lat = sys.http_sondes(URL_PING, n_sondes, headers=ENTETES)
    perte = round(100.0 * (n_sondes - len(lat)) / n_sondes, 1)
    ping = mediane(lat)
    down = sys.http_download_time(URL_DOWN_LEGER, timeout=40, headers=ENTETES)
    up = sys.http_upload_time(URL_UP, OCTETS_UP_LEGER, timeout=40, headers=ENTETES)
    down_mbps = mbps(*down) if down and down[0] > 500_000 else None
    gw = sys.passerelle()
    passerelle_ok = bool(gw) and sys.run(["ping", "-c", "1", "-W", "1", gw], timeout=3).ok if gw else None
    dns_ok = bool(sys.resolve(HOTE_DNS))
    internet = down_mbps is not None or bool(lat)
    return {"t": t, "down_mbps": down_mbps, "up_mbps": mbps(*up) if up else None,
            "ping_ms": round(ping, 1) if ping is not None else None, "gigue_ms": gigue(lat),
            "perte_pct": perte, "passerelle": passerelle_ok, "dns": dns_ok, "internet": internet}


def elaguer_controles(liste, maintenant, jours=CONTROLES_JOURS, maxi=400):
    """Garde les contrôles des `jours` derniers jours (et au plus `maxi`)."""
    limite = maintenant - jours * 86400
    out = [x for x in (liste or []) if isinstance(x, dict) and (x.get("t") or 0) >= limite]
    return out[-maxi:]


# --- Pi-hole -------------------------------------------------------------------------------
def resume_pihole(d):
    """Réponse de GET /api/stats/summary (Pi-hole v6) -> ce que YDZ affiche."""
    if not isinstance(d, dict) or not isinstance(d.get("queries"), dict):
        return None
    q, g = d["queries"], d.get("gravity") or {}

    def entier(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    pct = q.get("percent_blocked")
    try:
        pct = round(float(pct), 1)
    except (TypeError, ValueError):
        pct = None
    return {"bloquees": entier(q.get("blocked")), "requetes": entier(q.get("total")), "pourcentage": pct,
            "domaines_liste": entier(g.get("domains_being_blocked"))}


class Reseau:
    def __init__(self, hub):
        self.hub = hub
        self.sys = hub.sys
        self.state = hub.node.state
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.wifi = None
        self.pihole = None
        self.debit_en_cours = False
        self.debit_erreur = None
        self.fil_debit = None
        self._debit_lock = threading.Lock()
        self._nuit_faite = None
        self._sid = None              # session Pi-hole (jamais exposée)
        self._sid_fin = 0
        self._sid_hote = None
        # 1.0.15 : contrôle réseau horaire
        self.controle_en_cours = False
        self._controle_lock = threading.Lock()
        self._prochain_controle = None   # monotonic de la prochaine mesure
        self._net_coupe_depuis = None    # horloge murale du début de la coupure Internet (alerte « rétabli »)
        self._sous_seuil = 0             # nombre de contrôles d'affilée sous le seuil de débit

    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-reseau", daemon=True).start()

    # --- contrôle horaire : réglages + état ------------------------------------------------------
    def controle_reglages(self):
        r = self.state.get("reseau_controle") or {}
        inter = r.get("intervalle_min")
        seuil = r.get("seuil_mbps")
        return {"intervalle_min": inter if inter in CONTROLE_INTERVALLES else CONTROLE_DEFAUT,
                "seuil_mbps": seuil if isinstance(seuil, int) and not isinstance(seuil, bool)
                and 1 <= seuil <= 10000 else SEUIL_DEFAUT}

    def controles(self):
        return [x for x in (self.state.get("controles") or []) if isinstance(x, dict)]

    def regler_controle(self, b):
        r = self.controle_reglages()
        if "intervalle_min" in b:
            v = b["intervalle_min"]
            if v not in CONTROLE_INTERVALLES or isinstance(v, bool):
                raise ApiError(400, "Fréquence : 30 min, 1 h, 3 h ou « jamais ».", "intervalle_min")
            r["intervalle_min"] = v
        if "seuil_mbps" in b:
            v = b["seuil_mbps"]
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 10000:
                raise ApiError(400, "Seuil de débit : de 1 à 10000 Mbit/s.", "seuil_mbps")
            r["seuil_mbps"] = v
        self.state.set("reseau_controle", r)
        self._prochain_controle = None       # nouvelle fréquence : on replanifie
        return self.status_controle()

    def status_controle(self):
        r = self.controle_reglages()
        h = self.controles()
        par_jour = (24 * 60 / r["intervalle_min"]) if r["intervalle_min"] else 0
        return {"intervalle_min": r["intervalle_min"], "seuil_mbps": r["seuil_mbps"],
                "actif": r["intervalle_min"] > 0, "en_cours": self.controle_en_cours,
                "dernier": h[-1] if h else None, "historique": h,
                "internet_coupe": self._net_coupe_depuis is not None,
                "conso_jour_mo": round(par_jour * OCTETS_PAR_CONTROLE / 1e6)}

    # --- état ----------------------------------------------------------------------------------
    def debits(self):
        return [x for x in (self.state.get("debits") or []) if isinstance(x, dict)]

    def status_debit(self):
        h = self.debits()
        return {"en_cours": self.debit_en_cours, "dernier": h[-1] if h else None, "historique": h,
                "erreur": self.debit_erreur, "heure_nuit": "%02d:%02d" % HEURE_DEBIT}

    # --- boucle -------------------------------------------------------------------------------
    def _boucle(self):
        time.sleep(15)
        tours = 0
        while not self.stop.is_set():
            try:
                self.wifi = self.mesure_wifi()
            except Exception:
                pass
            if tours % (PIHOLE_PERIODE // 60) == 0:
                try:
                    self.pihole = self.mesure_pihole()
                except Exception:
                    self.pihole = None
            try:
                self._nuit()
            except Exception:
                pass
            try:
                self._controle_horaire()
            except Exception:
                pass
            tours += 1
            self.stop.wait(60)

    # --- contrôle réseau horaire -----------------------------------------------------------------
    def _controle_horaire(self):
        r = self.controle_reglages()
        if r["intervalle_min"] <= 0:
            self._prochain_controle = None
            return
        mono = self.sys.monotonic()
        if self._prochain_controle is None:
            # première échéance : après l'écart choisi depuis le dernier contrôle connu (ou ~1 min après le démarrage)
            h = self.controles()
            depuis = (self.sys.now() - h[-1]["t"]) if h else 1e9
            self._prochain_controle = mono + max(45, r["intervalle_min"] * 60 - depuis)
            return
        if mono < self._prochain_controle or self.debit_en_cours or self.controle_en_cours:
            return
        self._prochain_controle = mono + r["intervalle_min"] * 60
        self.lancer_controle()

    def lancer_controle(self, attendre=False):
        with self._controle_lock:
            if self.controle_en_cours:
                return self.status_controle()
            self.controle_en_cours = True
        if self.reel and not attendre:
            threading.Thread(target=self._controle, name="ydz-controle", daemon=True).start()
        else:
            self._controle()
        return self.status_controle()

    def _controle(self):
        try:
            res = mesurer_controle(self.sys)
        except Exception:
            res = {"t": int(self.sys.now()), "down_mbps": None, "up_mbps": None, "ping_ms": None,
                   "gigue_ms": None, "perte_pct": 100.0, "passerelle": None, "dns": False, "internet": False}
        try:
            self.state.set("controles", elaguer_controles(self.controles() + [res], int(self.sys.now())))
            self._alerter_controle(res)
        finally:
            self.controle_en_cours = False
        return res

    def _alerter_controle(self, res):
        r = self.controle_reglages()
        now = self.sys.now()
        if not res.get("internet"):
            if self._net_coupe_depuis is None:
                self._net_coupe_depuis = now
                self.hub.alerte("reseau", "crit", "Internet est coupé.")
            self._sous_seuil = 0
            return
        if self._net_coupe_depuis is not None:            # rétabli : on dit depuis combien de temps
            duree = int(now - self._net_coupe_depuis)
            self._net_coupe_depuis = None
            self.hub.alerte("reseau", "ok", "Internet est rétabli (coupure de %s)." % _duree_fr(duree))
        d = res.get("down_mbps")
        if d is not None and d < r["seuil_mbps"]:
            self._sous_seuil += 1
            if self._sous_seuil == 3:                     # 3 contrôles d'affilée sous le seuil : une alerte
                self.hub.alerte("reseau", "warn", "Débit faible : %s Mbit/s depuis 3 contrôles (seuil %d)."
                                % (_fr(d), r["seuil_mbps"]))
        else:
            self._sous_seuil = 0

    # --- Wi-Fi -------------------------------------------------------------------------------
    def mesure_wifi(self, dev="wlan0"):
        if not self.sys.exists("/sys/class/net/%s" % dev):
            return {"filaire": True}
        if self.sys.which("iw"):
            r = self.sys.run(["iw", "dev", dev, "link"], timeout=5)
            if r.ok:
                w = lire_iw(r.out)
                return w if w else {"connecte": False}
        w = lire_proc_wireless(self.sys.read("/proc/net/wireless", ""), dev)
        return w if w else {"connecte": False}

    # --- débit -------------------------------------------------------------------------------
    def _nuit(self):
        t = time.localtime(self.sys.now())
        jour = time.strftime("%Y-%m-%d", t)
        if self._nuit_faite is None:          # au démarrage : la mesure de cette nuit est-elle déjà faite ?
            h = self.debits()
            self._nuit_faite = time.strftime("%Y-%m-%d", time.localtime(h[-1]["t"])) if h else ""
            if h:
                d = time.localtime(h[-1]["t"])
                if (d.tm_hour, d.tm_min) < HEURE_DEBIT:
                    self._nuit_faite = ""
        minutes = t.tm_hour * 60 + t.tm_min
        debut = HEURE_DEBIT[0] * 60 + HEURE_DEBIT[1]
        if debut <= minutes < debut + 60 and self._nuit_faite != jour:
            self._nuit_faite = jour
            try:
                self.lancer_debit(auto=True)
            except ApiError:
                pass

    def lancer_debit(self, auto=False):
        with self._debit_lock:
            if self.debit_en_cours:
                raise ApiError(409, "Un test de débit est déjà en cours. Il dure moins d'une minute.", "debit_en_cours")
            self.debit_en_cours = True
            self.debit_erreur = None
        self.fil_debit = threading.Thread(target=self._debit, args=(auto,), name="ydz-debit", daemon=True)
        self.fil_debit.start()
        return self.status_debit()

    def _debit(self, auto):
        try:
            res = mesurer_debit(self.sys)
        except Exception:
            res = None
        if res is None:
            self.debit_erreur = "Mesure impossible : Internet ne répond pas."
            # 1.0.11 : l'échec part aussi vers Discord/ntfy (la nuit, sans bulle dans l'interface)
            self.hub.alerte("debit", "warn", "Test de débit impossible : Internet ne répond pas.", ui=not auto)
        else:
            self.state.set("debits", ajouter_debit(self.debits(), res))
            if auto and getattr(self.hub, "activite", None):     # 1.0.12 : journal d'activité
                self.hub.activite.auto("Test de débit de la nuit : ↓ %s Mbit/s" % _fr(res["down_mbps"]),
                                       categorie="debit")
            if not auto:
                txt = "Test de débit : ↓ %s Mbit/s" % _fr(res["down_mbps"])
                if res["up_mbps"] is not None:
                    txt += " · ↑ %s Mbit/s" % _fr(res["up_mbps"])
                if res["ping_ms"] is not None:
                    txt += " · %s ms" % _fr(round(res["ping_ms"]))
                self.hub.node.events.publish("notification", {"niveau": "ok", "texte": txt})
        self.debit_en_cours = False

    # --- Pi-hole -------------------------------------------------------------------------------
    def _ou_pihole(self):
        """(hôte, mot de passe) de l'installation Pi-hole connue de YDZ, ou None."""
        node = self.hub.node
        inst = (node.state.get("apps") or {}).get("pihole")
        if inst is not None:
            return "127.0.0.1", (inst.get("reglages") or {}).get("MOT_DE_PASSE", "")
        for n in self.hub.nodes(node.status())[1:]:
            if n.get("en_ligne") and any(a.get("id") == "pihole" for a in n.get("apps", [])):
                try:
                    full = self.hub.agent(n["nom"], "GET", "/api/agent/apps", timeout=5)["apps"]
                except (ApiError, KeyError, TypeError):
                    return None
                regl = next((a.get("reglages") or {} for a in full if a.get("id") == "pihole"), {})
                ip = n.get("ip") or n.get("adresse")
                return (ip, regl.get("MOT_DE_PASSE", "")) if ip else None
        return None

    def _session(self, base, mdp, neuve=False):
        mono = self.sys.monotonic()
        if not neuve and self._sid_hote == base and mono < self._sid_fin:
            return self._sid
        self._sid, self._sid_fin, self._sid_hote = None, 0, None
        code, d = self.sys.http("POST", base + "/api/auth", {"password": mdp or ""}, timeout=5)
        s = (d or {}).get("session") or {}
        if code != 200 or not s.get("valid"):
            return False
        self._sid, self._sid_hote = s.get("sid"), base
        self._sid_fin = mono + max(60, int(s.get("validity") or 300) - 60)
        return self._sid

    def mesure_pihole(self):
        ou = self._ou_pihole()
        if not ou:
            self._sid = None
            return None
        hote, mdp = ou
        base = "http://%s:%d" % (hote, PIHOLE_PORT)
        for essai in range(2):
            sid = self._session(base, mdp, neuve=essai > 0)
            if sid is False:
                return None
            code, d = self.sys.http("GET", base + "/api/stats/summary", None,
                                    {"X-FTL-SID": sid} if sid else None, timeout=5)
            if code == 401:
                continue                      # session expirée côté Pi-hole : on en ouvre une neuve
            if code != 200:
                return None
            r = resume_pihole(d)
            if r is not None:
                r["maj"] = int(self.sys.now())
            return r
        return None


def _fr(v):
    return str(v).replace(".", ",")


def _duree_fr(s):
    s = int(s or 0)
    if s < 60:
        return "%d s" % s
    if s < 3600:
        return "%d min" % (s // 60)
    h, m = s // 3600, (s % 3600) // 60
    return "%d h %02d" % (h, m) if m else "%d h" % h
