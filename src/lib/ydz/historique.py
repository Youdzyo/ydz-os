"""Historique sur 7 jours (1.0.12) : une moyenne toutes les 5 minutes, pour chaque nœud.

Séries par nœud : cpu, temp, ram, disque, rx, tx ; sur le hub en plus : ventilo, watts (conso de la tour,
maison.conso) et wifi (signal du Pi, en dBm). Le hub mesure toutes les 2 s : chaque valeur est additionnée
puis la moyenne des 5 minutes est rangée (2016 points = 7 jours par série), en mémoire.

Carte SD : le fichier /var/lib/ydz/historique.json n'est écrit qu'au plus une fois toutes les 30 minutes,
et à l'arrêt propre du démon ; il est relu au démarrage (points de plus de 7 jours jetés).
"""
import json
import threading
from collections import deque

FICHIER = "/var/lib/ydz/historique.json"
PAS_S = 300
POINTS = 7 * 24 * 3600 // PAS_S          # 2016
ECRITURE_S = 30 * 60
MAX_POINTS_REPONSE = 300

SERIES = {
    "cpu": ("Processeur", "%"), "temp": ("Température", "°C"), "ram": ("Mémoire", "%"),
    "disque": ("Disque système", "%"), "rx": ("Réception", "bit/s"), "tx": ("Envoi", "bit/s"),
    "ventilo": ("Ventilateur", "%"), "watts": ("Consommation", "W"), "wifi": ("Signal Wi-Fi", "dBm"),
}


def mesures(n):
    """Valeurs d'un nœud (dict de /api/etat) -> {série: nombre}."""
    out = {}
    for k, v in (("cpu", n.get("cpu")), ("temp", n.get("temp")), ("ram", (n.get("ram") or {}).get("pct")),
                 ("disque", n.get("disk")), ("rx", (n.get("reseau") or {}).get("rx_bps")),
                 ("tx", (n.get("reseau") or {}).get("tx_bps"))):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = float(v)
    return out


def reduire(points, maxi=MAX_POINTS_REPONSE):
    """Moyenne par paquets pour ne rendre qu'au plus `maxi` points (le temps est celui du milieu)."""
    if len(points) <= maxi:
        return [list(p) for p in points]
    taille = len(points) / float(maxi)
    out = []
    for i in range(maxi):
        a, b = int(i * taille), int((i + 1) * taille)
        tranche = points[a:max(b, a + 1)]
        out.append([int(sum(p[0] for p in tranche) / len(tranche)),
                    round(sum(p[1] for p in tranche) / len(tranche), 1)])
    return out


def arrondi(serie, v):
    return int(round(v)) if serie in ("rx", "tx") else round(v, 1)


class Historique:
    def __init__(self, sys, chemin=FICHIER):
        self.sys, self.chemin = sys, chemin
        self.lock = threading.Lock()
        self.series = {}                   # (noeud, série) -> deque([t, v])
        self.cumul = {}                    # (noeud, série) -> [somme, nombre]
        self.debut = None                  # monotonic du début de la fenêtre de 5 min
        self.ecrit_a = None                # monotonic de la dernière écriture
        self.modifie = False
        self.charger()

    # --- persistance ---------------------------------------------------------------------------------
    def charger(self):
        try:
            d = json.loads(self.sys.read(self.chemin, "") or "{}")
        except ValueError:
            d = {}
        limite = self.sys.now() - POINTS * PAS_S
        for cle, pts in (d.get("series") or {}).items():
            if "/" not in cle or not isinstance(pts, list):
                continue
            noeud, serie = cle.split("/", 1)
            if serie not in SERIES:
                continue
            q = deque(maxlen=POINTS)
            for p in pts:
                if isinstance(p, list) and len(p) == 2 and all(isinstance(x, (int, float)) for x in p) and p[0] >= limite:
                    q.append([int(p[0]), p[1]])
            if q:
                self.series[(noeud, serie)] = q

    def renommer(self, ancien, nouveau):
        """1.0.13 : le nœud a changé de nom d'hôte ; ses séries le suivent (sans écraser des séries plus neuves)."""
        with self.lock:
            for (n, serie) in list(self.series):
                if n == ancien:
                    q = self.series.pop((n, serie))
                    if (nouveau, serie) in self.series:
                        neuf = self.series[(nouveau, serie)]
                        fus = deque(sorted(list(q) + list(neuf)), maxlen=POINTS)
                        self.series[(nouveau, serie)] = fus
                    else:
                        self.series[(nouveau, serie)] = q
                    self.modifie = True
                    c = self.cumul.pop((n, serie), None)
                    if c and (nouveau, serie) not in self.cumul:
                        self.cumul[(nouveau, serie)] = c

    def sauver(self, force=False):
        """Écrit au plus une fois toutes les 30 min (ou tout de suite si force : arrêt propre)."""
        now = self.sys.monotonic()
        if not self.modifie or (not force and self.ecrit_a is not None and now - self.ecrit_a < ECRITURE_S):
            return False
        with self.lock:
            d = {"v": 1, "pas": PAS_S, "series": {"%s/%s" % k: list(q) for k, q in self.series.items()}}
        try:
            self.sys.write(self.chemin, json.dumps(d, separators=(",", ":")), mode=0o644)
        except Exception:
            return False
        self.ecrit_a, self.modifie = now, False
        return True

    # --- mesure --------------------------------------------------------------------------------------
    def observer(self, nodes, extra=None):
        """Appelé à chaque tour du hub (2 s). `extra` : {(noeud, série): valeur} (watts, wifi, ventilo)."""
        now = self.sys.monotonic()
        if self.ecrit_a is None:
            self.ecrit_a = now               # première écriture au plus tôt 30 min après le démarrage
        vals = {}
        for n in nodes:
            if n.get("en_ligne") and n.get("connu", True):
                for k, v in mesures(n).items():
                    vals[(n["nom"], k)] = v
        for k, v in (extra or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals[k] = float(v)
        with self.lock:
            if self.debut is None:
                self.debut = now
            for k, v in vals.items():
                c = self.cumul.setdefault(k, [0.0, 0])
                c[0] += v
                c[1] += 1
            if now - self.debut >= PAS_S:
                self._ranger()
                self.debut = now
        self.sauver()

    def _ranger(self):
        t = int(self.sys.now())
        for k, (somme, nb) in self.cumul.items():
            if nb:
                self.series.setdefault(k, deque(maxlen=POINTS)).append([t, arrondi(k[1], somme / nb)])
        self.cumul = {}
        self.modifie = True

    # --- lecture -------------------------------------------------------------------------------------
    def disponibles(self):
        with self.lock:
            cles = sorted(self.series)
        out = {}
        for noeud, serie in cles:
            out.setdefault(noeud, []).append(serie)
        return [{"noeud": k, "series": v} for k, v in sorted(out.items())]

    def serie(self, noeud, serie, jours=1):
        if serie not in SERIES:
            return None
        depuis = self.sys.now() - jours * 86400
        with self.lock:
            pts = [p for p in self.series.get((noeud, serie), ()) if p[0] >= depuis]
        nom, unite = SERIES[serie]
        vals = [p[1] for p in pts]
        return {"noeud": noeud, "serie": serie, "nom": nom, "unite": unite, "jours": jours, "pas_s": PAS_S,
                "points": reduire(pts), "nombre": len(pts),
                "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                "moy": round(sum(vals) / len(vals), 1) if vals else None, "derniere": vals[-1] if vals else None}
