"""Écran du Pi (1.0.9) : réglages d'affichage, nuit, météo, état du PC, des services et conso électrique.

Tout ce qui touche au réseau (météo, contrôle des services, ping du PC) tourne dans un fil à part,
seulement sur le vrai système : la simulation et les tests n'envoient rien dehors.
La nuit, le hub écrit l'état voulu (« jour », « horloge » ou « eteint ») dans FICHIER_NUIT ; le kiosque
le lit et éteint la sortie HDMI quand il faut (l'interface, elle, dessine l'horloge de nuit).
"""
import calendar
import math
import re
import threading
import time

from .core import ApiError

FICHIER_NUIT = "/run/ydz/ecran-nuit"
RE_HEURE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DEFAUTS = {
    "nuit_actif": True, "nuit_debut": "00:00", "nuit_fin": "07:00", "nuit_mode": "eteint",
    "rotation_s": 30, "meteo_ville": "Toulouse", "meteo_lat": 43.6047, "meteo_lon": 1.4442,
    "prix_kwh": 0.194,
    # 1.0.13 : la nuit peut suivre le soleil (coucher + décalage -> lever + décalage), en minutes
    "nuit_decalage_coucher": 60, "nuit_decalage_lever": 0,
}
DECALAGE_MIN, DECALAGE_MAX = -120, 240

# codes météo WMO (Open-Meteo) -> (texte, icône)
METEO = {
    0: ("Ciel dégagé", "sun"), 1: ("Plutôt dégagé", "sun"), 2: ("Partiellement nuageux", "cloud-sun"),
    3: ("Couvert", "cloud"), 45: ("Brouillard", "cloud-fog"), 48: ("Brouillard givrant", "cloud-fog"),
    51: ("Bruine légère", "cloud-drizzle"), 53: ("Bruine", "cloud-drizzle"), 55: ("Forte bruine", "cloud-drizzle"),
    56: ("Bruine verglaçante", "cloud-drizzle"), 57: ("Bruine verglaçante", "cloud-drizzle"),
    61: ("Pluie faible", "cloud-rain"), 63: ("Pluie", "cloud-rain"), 65: ("Forte pluie", "cloud-rain"),
    66: ("Pluie verglaçante", "cloud-rain"), 67: ("Pluie verglaçante", "cloud-rain"),
    71: ("Neige faible", "cloud-snow"), 73: ("Neige", "cloud-snow"), 75: ("Forte neige", "cloud-snow"),
    77: ("Grains de neige", "cloud-snow"), 80: ("Averses", "cloud-rain"), 81: ("Averses", "cloud-rain"),
    82: ("Fortes averses", "cloud-rain"), 85: ("Averses de neige", "cloud-snow"), 86: ("Averses de neige", "cloud-snow"),
    95: ("Orage", "cloud-lightning"), 96: ("Orage et grêle", "cloud-lightning"), 99: ("Orage et grêle", "cloud-lightning"),
}


def meteo_texte(code, jour=True):
    txt, ico = METEO.get(int(code) if code is not None else -1, ("—", "cloud"))
    if not jour and ico in ("sun", "cloud-sun"):
        ico = "moon" if ico == "sun" else "cloud-moon"
    return txt, ico


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def dans_plage(maintenant_min, debut, fin):
    """Vrai si l'heure (en minutes) est dans [debut, fin[, plage pouvant passer minuit."""
    d, f = minutes(debut), minutes(fin)
    if d == f:
        return False
    return d <= maintenant_min < f if d < f else (maintenant_min >= d or maintenant_min < f)


# --- lever et coucher du soleil (formules NOAA, calcul local, sans réseau) -----------------------------
def _jour_julien(a, m, j):
    if m <= 2:
        a, m = a - 1, m + 12
    s = a // 100
    return int(365.25 * (a + 4716)) + int(30.6001 * (m + 1)) + j + 2 - s + s // 4 - 1524.5


def _soleil_pos(jd):
    """Déclinaison (radians) et équation du temps (minutes) au jour julien `jd`."""
    t = (jd - 2451545.0) / 36525.0
    l0 = math.radians((280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360)
    m = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t)) + math.sin(2 * m) * (0.019993 - 0.000101 * t)
         + math.sin(3 * m) * 0.000289)
    om = math.radians(125.04 - 1934.136 * t)
    lam = math.radians(math.degrees(l0) + c - 0.00569 - 0.00478 * math.sin(om))
    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = math.radians(eps0 + 0.00256 * math.cos(om))
    decl = math.asin(math.sin(eps) * math.sin(lam))
    y = math.tan(eps / 2) ** 2
    eqt = 4 * math.degrees(y * math.sin(2 * l0) - 2 * e * math.sin(m) + 4 * e * y * math.sin(m) * math.cos(2 * l0)
                           - 0.5 * y * y * math.sin(4 * l0) - 1.25 * e * e * math.sin(2 * m))
    return decl, eqt


def lever_coucher_utc(a, m, j, lat, lon):
    """Lever et coucher du soleil du jour (a, m, j) en minutes après 00:00 UTC (longitude est > 0).
    None si le soleil ne se lève ou ne se couche pas (régions polaires)."""
    jd0 = _jour_julien(a, m, j)
    out = []
    for signe in (1, -1):                   # 1 : lever, -1 : coucher
        minutes = 720.0
        for _ in range(3):                  # deux affinages suffisent (écart < 10 s)
            decl, eqt = _soleil_pos(jd0 + minutes / 1440.0)
            phi = math.radians(lat)
            cos_h = math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl)) - math.tan(phi) * math.tan(decl)
            if not -1 <= cos_h <= 1:
                return None
            h = math.degrees(math.acos(cos_h))
            minutes = 720 - 4 * (lon + signe * h) - eqt
        out.append(minutes)
    return out[0], out[1]


def lever_coucher(t, lat, lon):
    """Horodatages (epoch) du lever et du coucher du soleil le jour LOCAL de l'instant t."""
    lt = time.localtime(t)
    r = lever_coucher_utc(lt.tm_year, lt.tm_mon, lt.tm_mday, lat, lon)
    if r is None:
        return None
    minuit_utc = calendar.timegm((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0))
    return int(minuit_utc + r[0] * 60), int(minuit_utc + r[1] * 60)


def plage_soleil(t, lat, lon, dec_coucher=60, dec_lever=0):
    """Nuit « soleil » vue de l'instant t : (en_cours, début, fin, lever du jour, coucher du jour) en epoch.
    La nuit va du coucher (+ décalage) au lever du lendemain (+ décalage) : elle passe minuit."""
    auj = lever_coucher(t, lat, lon)
    if auj is None:
        return None
    hier = lever_coucher(t - 86400, lat, lon) or auj
    dem = lever_coucher(t + 86400, lat, lon) or auj
    fin_matin = auj[0] + dec_lever * 60
    if t < fin_matin:                       # avant le lever (+ décalage) : la nuit a commencé hier soir
        debut, fin = hier[1] + dec_coucher * 60, fin_matin
    else:
        debut, fin = auj[1] + dec_coucher * 60, dem[0] + dec_lever * 60
    return debut <= t < fin, debut, fin, auj[0], auj[1]


def hhmm(t):
    return time.strftime("%H:%M", time.localtime(t))


def lire_pmic(texte):
    """Sortie de `vcgencmd pmic_read_adc` -> puissance du Pi en watts (somme courant x tension par rail)."""
    courants, tensions = {}, {}
    for l in (texte or "").splitlines():
        m = re.match(r"\s*(\S+)_([AV])\s+\S+=([\d.]+)([AV])", l)
        if not m:
            continue
        (courants if m.group(2) == "A" else tensions)[m.group(1)] = float(m.group(3))
    w = sum(courants[k] * tensions[k] for k in courants if k in tensions)
    return round(w, 2) if w > 0 else None


def estimer_watts(noeud):
    """Estimation quand la mesure n'existe pas : Pi 5 ≈ 3 W au repos, ≈ 8 W à fond, + disques."""
    cpu = noeud.get("cpu") or 0
    disques = max(0, len(noeud.get("disques") or []) - 1)
    return round(3.0 + 5.0 * cpu / 100.0 + 1.5 * disques, 1)


class Maison:
    def __init__(self, hub):
        self.hub = hub
        self.sys = hub.sys
        self.state = hub.node.state
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.meteo = None
        self.services = None
        self.pc = None                     # {"nom", "en_ligne", "depuis"}
        self.watts_hub = None
        self._nuit_ecrite = None
        self.test_veille = None            # 1.0.14 : fin (horloge monotone) du test « mise en veille 10 s »

    # --- réglages ------------------------------------------------------------------------
    def reglages(self):
        r = dict(DEFAUTS)
        r.update(self.state.get("affichage", {}) or {})
        if r.get("nuit_plage") not in ("heures", "soleil"):
            # 1.0.13 : qui n'a jamais touché aux heures (00:00 -> 07:00) suit désormais le soleil
            r["nuit_plage"] = "soleil" if (r["nuit_debut"], r["nuit_fin"]) == ("00:00", "07:00") else "heures"
        return r

    def regler(self, b):
        r = self.reglages()
        if "nuit_actif" in b:
            if not isinstance(b["nuit_actif"], bool):
                raise ApiError(400, "Champ « nuit_actif » invalide.", "nuit_actif")
            r["nuit_actif"] = b["nuit_actif"]
        for k in ("nuit_debut", "nuit_fin"):
            if k in b:
                if not isinstance(b[k], str) or not RE_HEURE.match(b[k]):
                    raise ApiError(400, "Heure invalide (forme attendue : 07:30).", k)
                r[k] = b[k]
        if "nuit_plage" in b:
            if b["nuit_plage"] not in ("heures", "soleil"):
                raise ApiError(400, "La nuit suit des « heures » fixes ou le « soleil ».", "nuit_plage")
            r["nuit_plage"] = b["nuit_plage"]
        for k in ("nuit_decalage_coucher", "nuit_decalage_lever"):
            if k in b:
                v = b[k]
                if isinstance(v, bool) or not isinstance(v, int) or not DECALAGE_MIN <= v <= DECALAGE_MAX or v % 15:
                    raise ApiError(400, "Décalage de -120 à +240 minutes, par pas de 15.", k)
                r[k] = v
        if "nuit_mode" in b:
            if b["nuit_mode"] not in ("eteint", "horloge"):
                raise ApiError(400, "La nuit, l'écran peut être « eteint » ou « horloge ».", "nuit_mode")
            r["nuit_mode"] = b["nuit_mode"]
        if "rotation_s" in b:
            v = b["rotation_s"]
            if isinstance(v, bool) or not isinstance(v, int) or not (v == 0 or 10 <= v <= 600):
                raise ApiError(400, "Les pages tournent toutes les 10 à 600 secondes (0 = jamais).", "rotation_s")
            r["rotation_s"] = v
        if "prix_kwh" in b:
            v = b["prix_kwh"]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v < 2:
                raise ApiError(400, "Prix du kWh invalide (en euros, par exemple 0.194).", "prix_kwh")
            r["prix_kwh"] = round(float(v), 4)
        if "meteo_ville" in b:
            v = b["meteo_ville"]
            if not isinstance(v, str) or not 0 < len(v.strip()) <= 60:
                raise ApiError(400, "Nom de ville invalide.", "meteo_ville")
            if v.strip() != r["meteo_ville"]:
                lat, lon, nom = self._geocoder(v.strip())
                r.update(meteo_ville=nom, meteo_lat=lat, meteo_lon=lon)
                self.meteo = None
        self.state.set("affichage", r)
        self._ecrire_nuit()
        if self.reel and "meteo_ville" in b:
            threading.Thread(target=self._meteo_maj, daemon=True).start()
        return self.status()

    def _geocoder(self, ville):
        if not self.reel:
            return DEFAUTS["meteo_lat"], DEFAUTS["meteo_lon"], ville
        from urllib.parse import quote
        code, d = self.sys.http("GET", "https://geocoding-api.open-meteo.com/v1/search?count=1&language=fr&name="
                                + quote(ville), timeout=6)
        res = (d or {}).get("results") or []
        if code != 200 or not res:
            raise ApiError(404, "Ville introuvable : %s." % ville, "meteo_ville")
        return round(res[0]["latitude"], 4), round(res[0]["longitude"], 4), res[0].get("name", ville)

    # --- nuit ----------------------------------------------------------------------------
    def nuit(self):
        r = self.reglages()
        maintenant = self.sys.now()
        t = time.localtime(maintenant)
        out = {"actif": r["nuit_actif"], "debut": r["nuit_debut"], "fin": r["nuit_fin"], "mode": r["nuit_mode"],
               "plage": r["nuit_plage"], "decalage_coucher": r["nuit_decalage_coucher"],
               "decalage_lever": r["nuit_decalage_lever"], "lever": None, "coucher": None}
        sol = plage_soleil(maintenant, r["meteo_lat"], r["meteo_lon"], r["nuit_decalage_coucher"],
                           r["nuit_decalage_lever"])
        if sol:
            out["lever"], out["coucher"] = hhmm(sol[3]), hhmm(sol[4])
        if r["nuit_plage"] == "soleil" and sol:
            en_cours = sol[0]
            out.update(debut_effectif=hhmm(sol[1]), fin_effective=hhmm(sol[2]))
        else:
            en_cours = dans_plage(t.tm_hour * 60 + t.tm_min, r["nuit_debut"], r["nuit_fin"])
            out.update(debut_effectif=r["nuit_debut"], fin_effective=r["nuit_fin"])
        out["en_cours"] = bool(r["nuit_actif"]) and en_cours
        return out

    def _ecrire_nuit(self):
        n = self.nuit()
        v = n["mode"] if n["en_cours"] else "jour"
        if self.test_veille is not None:
            if self.sys.monotonic() < self.test_veille:
                v = "eteint"
            else:
                self.test_veille = None
        if v != self._nuit_ecrite and self.reel:
            try:
                self.sys.write(FICHIER_NUIT, v + "\n", mode=0o644)
                self._nuit_ecrite = v
            except Exception:
                pass

    def tester_veille(self, secondes=10):
        """1.0.14 : « Tester la mise en veille » : « eteint » pendant `secondes`, puis l'état normal revient.
        Le guetteur du kiosque coupe la sortie vidéo : le moniteur doit afficher « pas de signal » puis dormir."""
        if self.test_veille is not None and self.sys.monotonic() < self.test_veille:
            raise ApiError(409, "Un test de mise en veille est déjà en cours.", "occupe")
        self.test_veille = self.sys.monotonic() + secondes
        self._ecrire_nuit()
        if self.reel:
            t = threading.Timer(secondes + 0.5, self._ecrire_nuit)
            t.daemon = True
            t.start()
        return {"ok": True, "secondes": secondes}

    # --- boucle --------------------------------------------------------------------------
    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-maison", daemon=True).start()

    def _boucle(self):
        tours = 0
        self._ecrire_nuit()
        time.sleep(8)
        while not self.stop.is_set():
            for f in (self._ecrire_nuit, self._pc_maj, self._watts_maj):
                try:
                    f()
                except Exception:
                    pass
            if tours % 2 == 0:                     # services : toutes les 2 minutes
                try:
                    self._services_maj()
                except Exception:
                    pass
            if tours % 15 == 0 or self.meteo is None and tours % 3 == 0:   # météo : 15 min
                self._meteo_maj()
            tours += 1
            self.stop.wait(60)

    def _meteo_maj(self):
        r = self.reglages()
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
               "&current=temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m,relative_humidity_2m"
               "&hourly=temperature_2m,weather_code,is_day,precipitation_probability"
               "&daily=temperature_2m_max,temperature_2m_min,weather_code"
               "&timezone=auto&forecast_days=3" % (r["meteo_lat"], r["meteo_lon"]))
        try:
            code, d = self.sys.http("GET", url, timeout=8)
        except Exception:
            return
        if code != 200 or not d or "current" not in d:
            return
        c = d["current"]
        txt, ico = meteo_texte(c.get("weather_code"), c.get("is_day", 1))
        heures = []
        h = d.get("hourly") or {}
        maint = c.get("time", "")
        for i, t in enumerate(h.get("time") or []):
            if t >= maint[:13] and len(heures) < 8:
                _, hi = meteo_texte(h["weather_code"][i], h["is_day"][i])
                heures.append({"h": t[11:13] + "h", "temp": round(h["temperature_2m"][i]), "icone": hi,
                               "pluie": (h.get("precipitation_probability") or [None] * (i + 1))[i]})
        jours = []
        dj = d.get("daily") or {}
        for i, t in enumerate(dj.get("time") or []):
            _, ji = meteo_texte(dj["weather_code"][i])
            jours.append({"date": t, "min": round(dj["temperature_2m_min"][i]),
                          "max": round(dj["temperature_2m_max"][i]), "icone": ji})
        self.meteo = {"ville": r["meteo_ville"], "temp": round(c.get("temperature_2m", 0)),
                      "ressenti": round(c.get("apparent_temperature", 0)), "texte": txt, "icone": ico,
                      "vent": round(c.get("wind_speed_10m", 0)), "humidite": c.get("relative_humidity_2m"),
                      "heures": heures, "jours": jours, "maj": int(self.sys.now())}

    def _pc_maj(self):
        t = self.hub.wol.targets()[0]
        en_ligne = self.hub.wol._online(t.get("ip")) if t.get("ip") else None
        if self.pc is None or self.pc["en_ligne"] != en_ligne:
            self.pc = {"nom": t.get("nom") or "PC", "en_ligne": en_ligne, "depuis": int(self.sys.now())}
        else:
            self.pc["nom"] = t.get("nom") or "PC"

    def _watts_maj(self):
        if self.sys.which("vcgencmd"):
            r = self.sys.run(["vcgencmd", "pmic_read_adc"], timeout=5)
            if r.ok:
                self.watts_hub = lire_pmic(r.out)

    def _services_maj(self):
        out = []
        for node in self.hub.nodes(self.hub.node.status()):
            if not node.get("en_ligne"):
                continue
            for a in node.get("apps", []):
                app = self.hub.node.apps.catalog.get(a["id"], {})
                etat = "ok" if a.get("etat") == "actif" else ("off" if a.get("etat") == "arrete" else "crit")
                web = app.get("web")
                if etat == "ok" and web and node.get("ip"):
                    url = "%s://%s:%d%s" % (web.get("schema", "http"), node["ip"], web["port"], web.get("chemin", "/"))
                    code, _ = self.sys.http("GET", url, timeout=4)
                    if not code or code >= 500:
                        etat = "warn"
                out.append({"id": a["id"], "nom": app.get("nom", a["id"]), "icone": app.get("icone", "box"),
                            "noeud": node["nom"], "etat": etat})
        self.services = sorted(out, key=lambda s: s["nom"].lower())

    # --- conso ---------------------------------------------------------------------------
    def conso(self, nodes):
        prix = self.reglages()["prix_kwh"]
        total, mesure = 0.0, False
        for n in nodes:
            if not n.get("en_ligne"):
                continue
            if n.get("hub") and self.watts_hub:
                total += self.watts_hub + 0.8     # + écran USB/HDMI, Wi-Fi, ventilateur hors PMIC
                mesure = True
            else:
                total += estimer_watts(n)
        kwh_mois = total * 24 * 30 / 1000.0
        return {"watts": round(total, 1), "mesure": mesure, "kwh_mois": round(kwh_mois, 1),
                "euros_mois": round(kwh_mois * prix, 2), "prix_kwh": prix}

    def status(self, nodes=None):
        r = self.reglages()
        return {"nuit": self.nuit(), "rotation_s": r["rotation_s"], "meteo_ville": r["meteo_ville"],
                "meteo": self.meteo, "pc": self.pc, "services": self.services,
                "curseur": self.hub.node.conf.get("curseur") == "oui",
                "conso": self.conso(nodes) if nodes is not None else None}
