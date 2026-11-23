"""Agents assistants (1.0.19) : une petite équipe de « mini-Claude » à qui Justin confie une tâche.

Deux parties, et la SÉCURITÉ est la règle n°1 :

  * le « cerveau » d'un agent est un modèle de langage, local par défaut (app Ollama, API sur
    127.0.0.1:11434), ou en ligne au choix (une base d'API + une clé que Justin saisit dans Réglages,
    rangée comme un secret). Un modèle de langage ne peut JAMAIS lancer de commande, écrire un fichier,
    installer ou supprimer quoi que ce soit : pour s'appuyer sur des faits, il ne dispose que d'une
    liste fermée de « capacités » EN LECTURE SEULE (état de la tour, alertes, apps, réseau, journal,
    agenda) exécutées ici, une par une, après vérification du nom dans la liste blanche. Toute demande
    d'un outil inconnu ou dangereux est refusée (jamais exécutée) ;

  * le « Canal Claude » (Partie C) reçoit, via un sujet ntfy secret, les événements signés que l'appli
    Claude publie quand elle fait tourner des agents pour Justin. Le transport public (ntfy.sh) est
    traité comme hostile : chaque événement doit porter une signature HMAC valide (clé partagée), sinon
    il est jeté. Ce canal sert UNIQUEMENT à animer des personnages à l'écran : rien de ce qu'il reçoit
    n'est exécuté ; les textes sont échappés, bornés, débarrassés des caractères de contrôle, et le
    nombre d'agents distants est plafonné.

Rien ne part sur le réseau en simulation ni dans les tests (le fil ntfy et les appels au modèle ne
tournent que sur le vrai système).
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from urllib.parse import urlsplit

from .core import ApiError
from .agenda import hote_public

MASQUE = "••••••"
NTFY_SERVEUR = "https://ntfy.sh"
OLLAMA_URL = "http://127.0.0.1:11434"

# Bornes d'une tâche (un agent ne doit jamais tourner sans fin ni gonfler la mémoire).
MAX_AGENTS = 6                 # agents de l'équipe
MAX_ITER = 4                   # tours modèle -> capacité -> modèle au plus
TACHE_TIMEOUT = 90             # secondes au total pour une tâche
PROMPT_MAX = 4000              # caractères envoyés au modèle (la tâche de Justin)
SORTIE_MODELE_MAX = 2000       # caractères gardés d'une réponse du modèle
CAPACITE_MAX = 1800            # caractères gardés d'une capacité (contexte au modèle)
RESULTAT_MAX = 1200            # caractères du résultat montré à Justin
JETONS_MAX = 512               # jetons de réponse demandés au modèle (num_predict)

# Canal Claude (Partie C).
CANAL_MAX_AGENTS = 8           # agents distants affichés au plus
CANAL_FENETRE = 300            # un événement plus vieux que 5 min est ignoré (anti-rejeu)
CANAL_TTL = 120               # un agent distant disparaît 2 min après son dernier signe de vie
RE_SUJET = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RE_SERVEUR = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?/?$")
RE_COULEUR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Nettoyage de texte : pas de balisage, pas de caractère de contrôle ni de réécriture bidi.
REMPLACE = str.maketrans({"<": "‹", ">": "›", '"': "”", "`": "ˋ", "\\": "/"})
RE_CTRL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")

ETATS = ("en_attente", "travaille", "besoin_aide", "erreur", "termine")
PALETTE = ["#E5484D", "#E8A33D", "#7FBF8E", "#5AA9E6", "#B57BE0", "#4FC3C7"]
ACCESSOIRES = ("aucun", "lunettes", "casquette", "casque")


def nettoyer(texte, maxlen=200):
    """Chaîne sûre à afficher : bornée, sans caractère de contrôle ni de balisage."""
    if not isinstance(texte, str):
        return ""
    return RE_CTRL.sub("", texte[:maxlen]).translate(REMPLACE).strip()


def sujet_aleatoire():
    return "ydz-claude-" + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(14))


def cle_aleatoire():
    return secrets.token_urlsafe(32)


# =====================================================================================================
# Capacités : la liste BLANCHE, en LECTURE SEULE, des faits sur lesquels un agent peut s'appuyer.
# Chaque fonction prend le hub et rend un court texte. Aucune n'écrit, n'installe ni ne lance de commande.
# =====================================================================================================
def _cap_tour(hub):
    own = hub.node.status()
    d = own.get("disques") or []
    sys_disk = next((x for x in d if x.get("point") == "/"), d[0] if d else {})
    return ("État de la tour « %s » (nœud principal %s) :\n"
            "- processeur : %s %%\n- température : %s °C\n- mémoire : %s %% utilisée\n- disque système : %s %% utilisé\n"
            "- allumée depuis : %s heures" % (
                nettoyer(hub.noms.labo(), 40),
                nettoyer(own.get("nom", "forge"), 30),
                round(own.get("cpu") or 0), round(own.get("temp") or 0),
                round((own.get("ram") or {}).get("pct") or 0), round(sys_disk.get("pct") or 0),
                round((own.get("uptime") or 0) / 3600)))


def _cap_noeuds(hub):
    nodes = hub.nodes(hub.node.status())
    lignes = []
    for n in nodes:
        if not n.get("connu"):
            continue
        lignes.append("- %s : %s, %s °C, cpu %s %%" % (
            nettoyer(n.get("nom_affiche") or n.get("nom"), 30),
            "en ligne" if n.get("en_ligne") else "hors ligne",
            round(n.get("temp") or 0), round(n.get("cpu") or 0)))
    return "Nœuds du labo :\n" + "\n".join(lignes[:16])


def _cap_alertes(hub):
    snap = hub.snapshot(hub.node.status())
    al = snap.get("tour", {}).get("alertes") or []
    recentes = list(getattr(hub.notifs, "recentes", []))[::-1][:8]
    out = ["État général : %s." % {"ok": "tout va bien", "warn": "à surveiller", "crit": "attention requise"}.get(
        snap.get("tour", {}).get("etat"), "inconnu")]
    if al:
        out.append("Alertes en cours :")
        out += ["- " + nettoyer(a.get("texte"), 160) for a in al[:8]]
    if recentes:
        out.append("Dernières notifications :")
        out += ["- " + nettoyer(a.get("texte"), 160) for a in recentes]
    if len(out) == 1:
        out.append("Aucune alerte.")
    return "\n".join(out)


def _cap_apps(hub):
    nodes = hub.nodes(hub.node.status())
    lignes = []
    for n in nodes:
        if not n.get("en_ligne"):
            continue
        for a in n.get("apps", []):
            app = hub.node.apps.catalog.get(a.get("id"), {})
            lignes.append("- %s sur %s : %s" % (nettoyer(app.get("nom", a.get("id")), 40),
                                                nettoyer(n.get("nom"), 20), a.get("etat", "?")))
    return ("Applications installées :\n" + "\n".join(lignes[:40])) if lignes else "Aucune application installée."


def _cap_reseau(hub):
    r = hub.reseau_maison()
    debit = (r.get("debit") or {}).get("dernier") or {}
    wifi = r.get("wifi") or {}
    out = ["Réseau de la maison : %s appareils en ligne." % (r.get("appareils") or 0)]
    if debit:
        out.append("Dernier test de débit : réception %s Mbit/s, envoi %s Mbit/s, latence %s ms." % (
            round(debit.get("down_mbps") or 0), round(debit.get("up_mbps") or 0), round(debit.get("ping_ms") or 0)))
    if wifi.get("signal_dbm") is not None:
        out.append("Wi-Fi du Pi : %s dBm (%s %%)." % (wifi.get("signal_dbm"), wifi.get("qualite_pct")))
    elif wifi.get("filaire"):
        out.append("Le Pi est relié par câble Ethernet.")
    return "\n".join(out)


def _cap_journal(hub):
    try:
        entrees = hub.activite.liste(12, "tout")
    except Exception:
        entrees = []
    if not entrees:
        return "Journal d'activité vide."
    return "Dernières actions de la tour :\n" + "\n".join(
        "- " + nettoyer(e.get("texte"), 160) for e in entrees[:12])


def _cap_agenda(hub):
    try:
        ag = hub.agenda.status()
    except Exception:
        ag = None
    if not ag or not ag.get("actif"):
        return "Aucun calendrier relié."
    auj = ag.get("aujourdhui") or []
    if not auj:
        return "Rien de prévu aujourd'hui."
    return "Rendez-vous d'aujourd'hui :\n" + "\n".join(
        "- %s %s" % (nettoyer(e.get("heure", "journée"), 10), nettoyer(e.get("titre"), 80)) for e in auj[:8])


# name -> (libellé pour Justin et le modèle, fonction LECTURE SEULE)
CAPACITES = {
    "etat_tour": ("état actuel de la tour (processeur, température, disque)", _cap_tour),
    "noeuds": ("liste des nœuds du labo et leur état", _cap_noeuds),
    "alertes": ("alertes et notifications récentes", _cap_alertes),
    "apps": ("applications installées et leur état", _cap_apps),
    "reseau": ("réseau de la maison et dernier test de débit", _cap_reseau),
    "journal": ("dernières actions enregistrées", _cap_journal),
    "agenda": ("rendez-vous du jour", _cap_agenda),
}


def liste_capacites():
    return [{"id": k, "nom": v[0]} for k, v in CAPACITES.items()]


# Jeton que le modèle écrit pour demander une capacité : « OUTIL: <nom> ». Le reste est la réponse.
RE_OUTIL = re.compile(r"(?:^|\n)\s*OUTIL\s*[:=]\s*([A-Za-z0-9_]{1,40})", re.IGNORECASE)
RE_AIDE = re.compile(r"(?:^|\n)\s*AIDE\s*[:=]\s*(.*)", re.IGNORECASE)


def analyser_reponse(texte):
    """Lit une réponse du modèle. Rend l'un de :
       {"type": "outil", "nom": <demandé>}  — le modèle veut une capacité (le nom est vérifié ensuite) ;
       {"type": "aide", "texte": ...}       — le modèle a besoin d'une décision humaine ;
       {"type": "final", "texte": ...}      — réponse finale.
    Rien ici n'exécute quoi que ce soit : on ne fait que classer un texte."""
    t = texte or ""
    m = RE_OUTIL.search(t)
    if m:
        return {"type": "outil", "nom": m.group(1).strip().lower()}
    a = RE_AIDE.search(t)
    if a:
        return {"type": "aide", "texte": nettoyer(a.group(1), 400) or "L'agent a besoin d'une décision."}
    return {"type": "final", "texte": nettoyer(t, RESULTAT_MAX)}


SYSTEME = (
    "Tu es un petit agent assistant du système domestique YDZ OS de Justin. Réponds en français, bref et "
    "concret. Tu ne peux RIEN modifier : tu n'exécutes aucune commande, tu n'installes rien, tu n'écris aucun "
    "fichier. Pour t'appuyer sur des faits réels, tu peux demander UNE capacité en lecture seule parmi :\n%s\n"
    "Pour l'utiliser, écris une ligne « OUTIL: <nom> » (et rien d'autre). Tu recevras le résultat puis tu "
    "pourras en demander un autre ou répondre. Quand tu as la réponse, donne-la directement. Si tu ne peux pas "
    "répondre ou qu'une décision humaine est nécessaire, écris « AIDE: <ce qu'il faut décider> »."
)


def invite_systeme():
    return SYSTEME % "\n".join("- %s : %s" % (k, v[0]) for k, v in CAPACITES.items())


# =====================================================================================================
# Cerveau : le client du modèle de langage (local Ollama, ou en ligne au choix). Réseau sur le vrai système.
# =====================================================================================================
class Cerveau:
    def __init__(self, agents):
        self.agents = agents
        self.sys = agents.sys
        self.reel = type(self.sys).__name__ == "System"

    def interroger(self, messages, reglages):
        """Un aller-retour avec le modèle. Rend le texte de la réponse, ou lève ApiError si injoignable.
        `messages` : liste [{role, content}]. Borné (num_predict, timeout)."""
        if reglages.get("mode") == "online" and reglages.get("online_actif"):
            return self._online(messages, reglages)
        return self._ollama(messages, reglages)

    def _ollama(self, messages, reglages):
        url = OLLAMA_URL + "/api/chat"
        corps = {"model": reglages.get("modele") or "llama3.2:3b", "messages": messages, "stream": False,
                 "options": {"num_predict": JETONS_MAX, "temperature": 0.4}}
        code, d = self.sys.http("POST", url, corps, {"Content-Type": "application/json"}, timeout=60)
        if code == 0:
            raise ApiError(503, "Ollama ne répond pas sur ce nœud. Installe l'app Ollama et télécharge un modèle, "
                                "ou bascule sur une API en ligne dans Réglages.", "ollama_absent")
        if code == 404:
            raise ApiError(409, "Le modèle « %s » n'est pas téléchargé. Lance « ollama pull %s » sur la tour."
                           % (reglages.get("modele"), reglages.get("modele")), "modele_absent")
        if code >= 400 or not isinstance(d, dict):
            raise ApiError(502, "Le modèle local a renvoyé une erreur (code %d)." % code, "modele")
        return nettoyer((d.get("message") or {}).get("content") or "", SORTIE_MODELE_MAX)

    def _online(self, messages, reglages):
        base = (reglages.get("online_base") or "").rstrip("/")
        cle = reglages.get("online_cle") or ""
        if not base or not cle:
            raise ApiError(400, "Renseigne l'adresse de l'API et la clé dans Réglages.", "api_incomplete")
        # A-19-01 : on revérifie l'hôte juste avant l'envoi (contre un re-pointage DNS entre
        # l'enregistrement et l'appel), pour ne jamais envoyer la clé « Bearer » vers une adresse interne.
        if self.reel and not hote_public(urlsplit(base).hostname or ""):
            raise ApiError(400, "Adresse d'API refusée : elle vise le réseau local, pas Internet.", "online_base")
        url = base + "/chat/completions"
        corps = {"model": reglages.get("modele") or "gpt-4o-mini", "messages": messages,
                 "max_tokens": JETONS_MAX, "temperature": 0.4}
        code, d = self.sys.http("POST", url, corps, {"Content-Type": "application/json",
                                                     "Authorization": "Bearer " + cle}, timeout=60)
        if code == 0:
            raise ApiError(503, "L'API en ligne ne répond pas. Vérifie l'adresse et ta connexion.", "api_injoignable")
        if code in (401, 403):
            raise ApiError(502, "L'API en ligne refuse la clé. Vérifie-la dans Réglages.", "api_cle")
        if code >= 400 or not isinstance(d, dict):
            raise ApiError(502, "L'API en ligne a renvoyé une erreur (code %d)." % code, "api")
        choix = (d.get("choices") or [{}])[0]
        return nettoyer((choix.get("message") or {}).get("content") or "", SORTIE_MODELE_MAX)


# =====================================================================================================
# Canal Claude (Partie C) : réception d'événements signés via un sujet ntfy. AFFICHAGE SEUL.
# =====================================================================================================
def message_signe(ev):
    """Chaîne signée d'un événement (ordre fixe, faciles à reproduire côté appli et dans claude-statut.sh)."""
    return "\n".join([
        str(ev.get("agent", "")), str(ev.get("couleur", "")), str(ev.get("tache", "")),
        str(ev.get("etape", "")), str(int(ev.get("progression", 0) or 0)),
        "1" if ev.get("termine") else "0", str(int(ev.get("ts", 0) or 0)), str(ev.get("nonce", ""))])


def signer_evenement(cle, ev):
    return hmac.new(str(cle).encode(), message_signe(ev).encode("utf-8"), hashlib.sha256).hexdigest()


class CanalClaude:
    """Reçoit les événements d'agents publiés par l'appli Claude. Le transport (ntfy.sh) n'est pas de
    confiance : seule une signature HMAC valide, fraîche et jamais vue fait apparaître un agent. Jamais
    d'exécution : on ne fait qu'animer des personnages."""

    def __init__(self, agents):
        self.agents = agents
        self.sys = agents.sys
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.distants = {}        # nom d'agent -> état nettoyé pour l'affichage
        self.nonces = {}          # nonce -> expiration (anti-rejeu)
        self.lock = threading.Lock()
        self.erreur = None

    # --- vérification (testable sans réseau) ------------------------------------------------------
    def verifier(self, ev, cle, now):
        """Rend un dict d'agent nettoyé si l'événement est authentique, frais et jamais vu ; sinon None.
        Ne lève jamais : un événement hostile doit juste être ignoré."""
        if not cle or not isinstance(ev, dict):
            return None
        sig = ev.get("sig")
        if not isinstance(sig, str) or not re.match(r"^[0-9a-f]{64}$", sig):
            return None
        try:
            ts = int(ev.get("ts", 0))
        except (TypeError, ValueError):
            return None
        if abs(now - ts) > CANAL_FENETRE:                 # trop vieux ou venu du futur : rejeu probable
            return None
        nonce = ev.get("nonce")
        if not isinstance(nonce, str) or not re.match(r"^[A-Za-z0-9_-]{8,64}$", nonce):
            return None
        attendu = signer_evenement(cle, ev)
        if not hmac.compare_digest(attendu, sig):         # signature fausse : jeté
            return None
        with self.lock:
            self.nonces = {k: e for k, e in self.nonces.items() if e > now}
            if nonce in self.nonces:                      # déjà vu : rejeu, jeté
                return None
            if len(self.nonces) >= 4096:
                return None
            self.nonces[nonce] = now + 2 * CANAL_FENETRE
        nom = nettoyer(ev.get("agent"), 40) or "Claude"
        couleur = ev.get("couleur") if isinstance(ev.get("couleur"), str) and RE_COULEUR.match(ev.get("couleur", "")) else "#B57BE0"
        prog = ev.get("progression")
        prog = int(prog) if isinstance(prog, (int, float)) and not isinstance(prog, bool) and 0 <= prog <= 100 else 0
        return {"nom": nom, "couleur": couleur, "tache": nettoyer(ev.get("tache"), 120),
                "etape": nettoyer(ev.get("etape"), 120), "progression": prog,
                "termine": bool(ev.get("termine")), "distant": True, "ts": ts}

    def traiter(self, ev, cle, now):
        """Vérifie puis range (ou retire) un agent distant. Plafonne le nombre d'agents distants."""
        a = self.verifier(ev, cle, now)
        if a is None:
            return False
        with self.lock:
            if a["termine"]:
                # on garde l'agent « terminé » un court instant puis on l'oublie (TTL)
                a["expire"] = now + 20
            else:
                a["expire"] = now + CANAL_TTL
            if a["nom"] not in self.distants and len(self.distants) >= CANAL_MAX_AGENTS:
                # plafond atteint : on remplace le plus ancien
                vieux = min(self.distants.values(), key=lambda x: x.get("ts", 0))
                self.distants.pop(vieux["nom"], None)
            self.distants[a["nom"]] = a
        return True

    def traiter_ligne(self, ligne, cle, now):
        """Une ligne du flux ntfy /json : {..., "event":"message", "message":"<json de l'événement>"}."""
        try:
            msg = json.loads(ligne)
        except (ValueError, TypeError):
            return False
        if not isinstance(msg, dict) or msg.get("event") != "message":
            return False
        corps = msg.get("message")
        if not isinstance(corps, str) or len(corps) > 4096:
            return False
        try:
            ev = json.loads(corps)
        except (ValueError, TypeError):
            return False
        return self.traiter(ev, cle, now)

    def status(self):
        now = self.sys.now()
        with self.lock:
            self.distants = {k: v for k, v in self.distants.items() if v.get("expire", 0) > now}
            agents = [{k: v[k] for k in ("nom", "couleur", "tache", "etape", "progression", "termine", "distant")}
                      for v in sorted(self.distants.values(), key=lambda x: x.get("ts", 0))]
        return agents

    # --- boucle (vrai système seulement) ----------------------------------------------------------
    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-canal-claude", daemon=True).start()

    def _boucle(self):
        while not self.stop.is_set():
            reg = self.agents.canal_reglages()
            if reg.get("actif") and reg.get("cle") and reg.get("sujet"):
                try:
                    self._tour(reg)
                except Exception as ex:
                    self.erreur = str(ex)[:120]
            self.stop.wait(5)

    def _tour(self, reg):
        url = "%s/%s/json?poll=1&since=%ds" % (reg["serveur"].rstrip("/"), reg["sujet"], CANAL_FENETRE)
        # http_brut renvoie (code, entêtes, corps) : on déballe bien les 3 (corrige « too many values to unpack »)
        code, _ent, brut = self.sys.http_brut("GET", url, {"User-Agent": "YDZ-OS/1.0"}, timeout=8,
                                              max_octets=256 * 1024)
        if code != 200 or not brut:
            return
        now = self.sys.now()
        for ligne in brut.decode("utf-8", "replace").splitlines():
            if ligne.strip():
                self.traiter_ligne(ligne, reg["cle"], now)


# =====================================================================================================
# Agents : l'équipe, le cerveau, l'exécution bornée, le Canal Claude.
# =====================================================================================================
class Agents:
    def __init__(self, hub):
        self.hub = hub
        self.sys = hub.sys
        self.state = hub.node.state
        self.reel = type(self.sys).__name__ == "System"
        self.cerveau = Cerveau(self)
        self.canal = CanalClaude(self)
        self.runs = {}            # id d'agent -> état de la tâche en cours (en mémoire, non persisté)
        self.lock = threading.Lock()

    def start(self):
        self.assurer_roster()
        self.canal.start()

    # --- roster -----------------------------------------------------------------------------------
    def assurer_roster(self):
        if not self.state.get("agents"):
            self.state.set("agents", [self._defaut(0, "Docteur"), self._defaut(1, "Scout"), self._defaut(2, "Pixel")])
        return self.state.get("agents")

    def _defaut(self, i, nom):
        return {"id": secrets.token_hex(4), "nom": nom, "couleur": PALETTE[i % len(PALETTE)],
                "perso": {"peau": "#E8B98A", "cheveux": "#5A3A22", "vetements": PALETTE[i % len(PALETTE)],
                          "accessoire": ACCESSOIRES[(i + 1) % len(ACCESSOIRES)]},
                "bureau": {"decor": "plante" if i % 2 else "ecran"},
                "personnalite": "", "phrases": []}

    def roster(self):
        return list(self.assurer_roster())

    def _trouver(self, agent_id):
        for a in self.roster():
            if a["id"] == agent_id:
                return a
        return None

    def creer(self, nom):
        liste = self.roster()
        if len(liste) >= MAX_AGENTS:
            raise ApiError(409, "L'équipe est au complet (%d agents au plus)." % MAX_AGENTS, "trop")
        nom = nettoyer(nom, 24) or "Agent"
        a = self._defaut(len(liste), nom)
        liste.append(a)
        self.state.set("agents", liste)
        return a

    def configurer(self, agent_id, champs):
        liste = self.roster()
        a = next((x for x in liste if x["id"] == agent_id), None)
        if not a:
            raise ApiError(404, "Agent inconnu.", "agent")
        if "nom" in champs:
            a["nom"] = nettoyer(champs["nom"], 24) or a["nom"]
        if "couleur" in champs and RE_COULEUR.match(str(champs.get("couleur", ""))):
            a["couleur"] = champs["couleur"]
        if "personnalite" in champs:
            a["personnalite"] = nettoyer(champs["personnalite"], 280)
        if isinstance(champs.get("phrases"), list):
            a["phrases"] = [nettoyer(p, 80) for p in champs["phrases"][:8] if isinstance(p, str) and p.strip()]
        if isinstance(champs.get("perso"), dict):
            p = dict(a.get("perso") or {})
            for k in ("peau", "cheveux", "vetements"):
                if RE_COULEUR.match(str(champs["perso"].get(k, ""))):
                    p[k] = champs["perso"][k]
            if champs["perso"].get("accessoire") in ACCESSOIRES:
                p["accessoire"] = champs["perso"]["accessoire"]
            a["perso"] = p
        if isinstance(champs.get("bureau"), dict):
            b = dict(a.get("bureau") or {})
            if isinstance(champs["bureau"].get("decor"), str):
                b["decor"] = nettoyer(champs["bureau"]["decor"], 20)
            a["bureau"] = b
        self.state.set("agents", liste)
        return a

    def supprimer(self, agent_id):
        liste = [a for a in self.roster() if a["id"] != agent_id]
        self.state.set("agents", liste)
        self.runs.pop(agent_id, None)
        return {"ok": True}

    # --- exécution bornée d'une tâche -------------------------------------------------------------
    def executer_capacite(self, nom):
        """Exécute UNE capacité de la liste blanche, en lecture seule. Rend le texte, ou None si le nom
        n'est pas une capacité connue (demande refusée : rien n'est exécuté)."""
        cap = CAPACITES.get(nom)
        if cap is None:
            return None
        try:
            return nettoyer(cap[1](self.hub), CAPACITE_MAX)
        except Exception:
            return "(capacité momentanément indisponible)"

    def _poser_run(self, agent_id, **kw):
        with self.lock:
            run = self.runs.setdefault(agent_id, {})
            run.update(kw)
            if "etape" in kw:
                run.setdefault("etapes", []).append(nettoyer(kw["etape"], 120))
                run["etapes"] = run["etapes"][-8:]
        return run

    def executer_tache(self, agent_id, prompt, cerveau=None):
        """Boucle bornée : le modèle réfléchit, demande au plus MAX_ITER capacités (lecture seule), puis
        répond. Met à jour l'état de l'agent (travaille -> termine / besoin_aide / erreur). Rend le run.
        `cerveau` : injectable pour les tests (objet avec .interroger(messages, reglages))."""
        agent = self._trouver(agent_id)
        if not agent:
            raise ApiError(404, "Agent inconnu.", "agent")
        prompt = nettoyer(prompt, PROMPT_MAX)
        if not prompt:
            raise ApiError(400, "Écris une tâche pour l'agent.", "tache")
        brain = cerveau or self.cerveau
        reg = self.cerveau_reglages_clair()
        debut = self.sys.monotonic()
        self._poser_run(agent_id, etat="travaille", tache=prompt, progression=5, resultat=None,
                        etapes=[], outils=[], debut=int(self.sys.now()), fin=None, erreur=None)
        messages = [{"role": "system", "content": invite_systeme()}, {"role": "user", "content": prompt}]
        try:
            for i in range(MAX_ITER):
                if self.sys.monotonic() - debut > TACHE_TIMEOUT:
                    return self._fin(agent_id, "erreur", resultat="La tâche a pris trop de temps : arrêtée.")
                self._poser_run(agent_id, progression=min(90, 20 + i * 20))
                reponse = brain.interroger(messages, reg)
                messages.append({"role": "assistant", "content": reponse})
                acte = analyser_reponse(reponse)
                if acte["type"] == "final":
                    return self._fin(agent_id, "termine", resultat=acte["texte"] or "Terminé.")
                if acte["type"] == "aide":
                    return self._fin(agent_id, "besoin_aide", resultat=acte["texte"])
                # type == "outil" : le nom DOIT être dans la liste blanche, sinon refus (jamais exécuté)
                nom = acte["nom"]
                resultat = self.executer_capacite(nom)
                if resultat is None:
                    with self.lock:
                        self.runs.setdefault(agent_id, {}).setdefault("outils", []).append({"nom": nom, "refuse": True})
                    self._poser_run(agent_id, etape="outil refusé : « %s »" % nom)
                    return self._fin(agent_id, "besoin_aide",
                                     resultat="L'agent a demandé un outil non autorisé (« %s ») : refusé. "
                                              "Une décision humaine est nécessaire." % nettoyer(nom, 40))
                with self.lock:
                    self.runs.setdefault(agent_id, {}).setdefault("outils", []).append({"nom": nom, "refuse": False})
                self._poser_run(agent_id, etape="consulte : %s" % CAPACITES[nom][0])
                messages.append({"role": "user", "content": "Résultat de « %s » :\n%s" % (nom, resultat)})
            return self._fin(agent_id, "besoin_aide",
                             resultat="L'agent n'a pas abouti après plusieurs essais : à toi de décider.")
        except ApiError as ex:
            return self._fin(agent_id, "erreur", resultat=ex.message)
        except Exception as ex:
            return self._fin(agent_id, "erreur", resultat="Erreur interne : %s" % str(ex)[:120])

    def _fin(self, agent_id, etat, resultat):
        return self._poser_run(agent_id, etat=etat, progression=100 if etat == "termine" else 100,
                               resultat=nettoyer(resultat, RESULTAT_MAX), fin=int(self.sys.now()))

    def assigner(self, agent_id, prompt):
        """Lance la tâche. Sur le vrai système, dans un fil (l'appel au modèle est lent) ; sinon tout de
        suite (simulation, tests : rien ne sort, le cerveau factice répond)."""
        agent = self._trouver(agent_id)
        if not agent:
            raise ApiError(404, "Agent inconnu.", "agent")
        with self.lock:
            en_cours = self.runs.get(agent_id, {}).get("etat") in ("travaille",)
        if en_cours:
            raise ApiError(409, "Cet agent travaille déjà. Attends qu'il ait fini.", "occupe")
        prompt = nettoyer(prompt, PROMPT_MAX)
        self._poser_run(agent_id, etat="travaille", tache=prompt, progression=2, resultat=None, etapes=[],
                        outils=[], debut=int(self.sys.now()), fin=None, erreur=None)
        if self.reel:
            threading.Thread(target=self.executer_tache, args=(agent_id, prompt), name="ydz-agent",
                             daemon=True).start()
        else:
            try:
                self.executer_tache(agent_id, prompt)
            except ApiError:
                raise
        return {"ok": True, "agent": agent_id, "etat": self.runs.get(agent_id, {}).get("etat")}

    def annuler(self, agent_id):
        self._poser_run(agent_id, etat="en_attente", tache=None, progression=0, resultat=None, fin=None)
        return {"ok": True}

    # --- réglages du cerveau ----------------------------------------------------------------------
    def cerveau_reglages(self):
        r = dict(self.state.get("agents_cerveau") or {})
        r.setdefault("mode", "local")
        r.setdefault("modele", "llama3.2:3b")
        r.setdefault("online_base", "")
        r.setdefault("online_cle", "")
        r.setdefault("online_actif", False)
        return r

    def cerveau_reglages_clair(self):
        return self.cerveau_reglages()          # usage interne : avec la clé en clair

    def regler_cerveau(self, b):
        r = self.cerveau_reglages()
        if "mode" in b:
            if b["mode"] not in ("local", "online"):
                raise ApiError(400, "Le cerveau est « local » (Ollama) ou « online » (API).", "mode")
            r["mode"] = b["mode"]
        if "modele" in b:
            v = nettoyer(b["modele"], 60)
            if v and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,58}$", v):
                raise ApiError(400, "Nom de modèle invalide.", "modele")
            r["modele"] = v or r["modele"]
        if "online_base" in b:
            v = str(b["online_base"] or "").strip().rstrip("/")
            if v and not re.match(r"^https://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._/-]*)?$", v):
                raise ApiError(400, "Adresse d'API invalide (elle commence par https://).", "online_base")
            # A-19-01 : l'API en ligne doit être sur Internet, jamais une adresse interne (sinon on pourrait
            # faire envoyer la clé d'API vers le réseau local / le loopback / une métadonnée).
            if v and self.reel and not hote_public(urlsplit(v).hostname or ""):
                raise ApiError(400, "Adresse refusée : l'API doit être sur Internet, pas sur le réseau local.",
                               "online_base")
            r["online_base"] = v
        if "online_cle" in b:
            v = b["online_cle"]
            if v in (None, ""):
                r["online_cle"], r["online_actif"] = "", False
            elif v != MASQUE:
                if not isinstance(v, str) or not 8 <= len(v) <= 200:
                    raise ApiError(400, "Clé d'API invalide.", "online_cle")
                r["online_cle"] = v.strip()
        if "online_actif" in b:
            if not isinstance(b["online_actif"], bool):
                raise ApiError(400, "Champ « online_actif » invalide.", "online_actif")
            r["online_actif"] = b["online_actif"]
        if r["online_actif"] and (not r["online_base"] or not r["online_cle"]):
            raise ApiError(400, "Renseigne l'adresse de l'API et la clé avant de l'activer.", "online_cle")
        self.state.set("agents_cerveau", r)
        return self.cerveau_status()

    def ollama_installe(self):
        """Détecte si l'app Ollama est posée sur un nœud (sans jamais la lancer ni l'installer).
        Résultat mis en cache ~30 s : /api/etat est interrogé souvent, inutile de reparcourir les nœuds à chaque fois."""
        now = self.sys.monotonic()
        cache = getattr(self, "_ollama_cache", None)
        if cache is not None and now < cache[1]:
            return cache[0]
        trouve = False
        try:
            for n in self.hub.nodes(self.hub.node.status()):
                if any(a.get("id") == "ollama" for a in n.get("apps", [])):
                    trouve = True
                    break
        except Exception:
            pass
        self._ollama_cache = (trouve, now + 30)
        return trouve

    def cerveau_status(self):
        r = self.cerveau_reglages()
        return {"mode": r["mode"], "modele": r["modele"], "online_base": r["online_base"],
                "online_cle_definie": bool(r["online_cle"]), "online_actif": r["online_actif"],
                "ollama_installe": self.ollama_installe()}

    # --- réglages du Canal Claude -----------------------------------------------------------------
    def canal_reglages(self):
        r = dict(self.state.get("agents_canal") or {})
        if not r.get("sujet"):
            r["sujet"] = sujet_aleatoire()
        if not r.get("cle"):
            r["cle"] = cle_aleatoire()
        r.setdefault("serveur", NTFY_SERVEUR)
        r.setdefault("actif", False)
        if "sujet" not in (self.state.get("agents_canal") or {}) or "cle" not in (self.state.get("agents_canal") or {}):
            self.state.set("agents_canal", r)
        return r

    def regler_canal(self, b):
        r = self.canal_reglages()
        if "actif" in b:
            if not isinstance(b["actif"], bool):
                raise ApiError(400, "Champ « actif » invalide.", "actif")
            r["actif"] = b["actif"]
        if b.get("rotation") is True:                 # nouvelle clé (et nouveau sujet) : on invalide l'ancienne
            r["cle"] = cle_aleatoire()
            r["sujet"] = sujet_aleatoire()
            with self.canal.lock:
                self.canal.distants.clear()
                self.canal.nonces.clear()
        if "serveur" in b:
            v = str(b["serveur"] or NTFY_SERVEUR).strip()
            if not RE_SERVEUR.match(v):
                raise ApiError(400, "Adresse du serveur ntfy invalide (exemple : https://ntfy.sh).", "serveur")
            r["serveur"] = v.rstrip("/")
        self.state.set("agents_canal", r)
        return self.canal_status()

    def canal_status(self, avec_secret=False):
        r = self.canal_reglages()
        out = {"actif": r["actif"], "sujet": r["sujet"], "serveur": r["serveur"],
               "cle_definie": bool(r["cle"]), "erreur": self.canal.erreur}
        if avec_secret:                               # rendu seulement à une session (jamais anonyme)
            out["cle"] = r["cle"]
        return out

    # --- réglages des bureaux (thème, boîte de dialogue, panneau) ---------------------------------
    def bureaux_reglages(self):
        r = dict(self.state.get("agents_bureaux") or {})
        r.setdefault("theme", "auto")         # auto (jour/nuit réels) | jour | nuit
        r.setdefault("dialogue_vitesse", 45)  # caractères/s de la boîte de dialogue
        r.setdefault("dialogue_taille", 20)   # px
        r.setdefault("panneau", True)         # panneau « Équipe d'agents »
        r.setdefault("boite", True)           # boîte de dialogue RPG
        return r

    def regler_bureaux(self, b):
        r = self.bureaux_reglages()
        if b.get("theme") in ("auto", "jour", "nuit"):
            r["theme"] = b["theme"]
        if isinstance(b.get("dialogue_vitesse"), (int, float)) and not isinstance(b.get("dialogue_vitesse"), bool):
            r["dialogue_vitesse"] = int(max(5, min(120, b["dialogue_vitesse"])))
        if isinstance(b.get("dialogue_taille"), (int, float)) and not isinstance(b.get("dialogue_taille"), bool):
            r["dialogue_taille"] = int(max(12, min(40, b["dialogue_taille"])))
        for k in ("panneau", "boite"):
            if isinstance(b.get(k), bool):
                r[k] = b[k]
        self.state.set("agents_bureaux", r)
        return r

    # --- instantané pour /api/etat et /api/agents -------------------------------------------------
    def _run_public(self, agent_id):
        with self.lock:
            run = dict(self.runs.get(agent_id) or {})
        return {"etat": run.get("etat", "en_attente"), "tache": run.get("tache"),
                "progression": run.get("progression", 0), "resultat": run.get("resultat"),
                "etapes": list(run.get("etapes") or [])[-4:], "fin": run.get("fin")}

    def snapshot(self):
        """Ce que /api/etat porte sous « agents » : jamais de secret (clés masquées)."""
        c = self.cerveau_reglages()
        equipe = [{"id": a["id"], "nom": nettoyer(a["nom"], 24), "couleur": a["couleur"],
                   "perso": a.get("perso"), "bureau": a.get("bureau"),
                   "run": self._run_public(a["id"])} for a in self.roster()]
        return {"equipe": equipe, "distants": self.canal.status(), "bureaux": self.bureaux_reglages(),
                "cerveau": {"mode": c["mode"], "modele": c["modele"],
                            "en_ligne_actif": c["online_actif"], "ollama_installe": self.ollama_installe()},
                "canal_actif": bool((self.state.get("agents_canal") or {}).get("actif"))}

    def status(self):
        """GET /api/agents : roster + état + réglages (secrets masqués)."""
        return {"equipe": [dict(a, run=self._run_public(a["id"])) for a in self.roster()],
                "distants": self.canal.status(), "cerveau": self.cerveau_status(),
                "canal": self.canal_status(), "bureaux": self.bureaux_reglages(),
                "capacites": liste_capacites()}
