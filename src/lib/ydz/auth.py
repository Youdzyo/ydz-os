"""Code PIN (PBKDF2), sessions par jeton (verrou après inactivité), limitation des essais, signature hub -> nœud.

1.0.15 (sécurité) :
  - nouveaux codes : 6 à 8 chiffres (un ancien code à 4 ou 5 chiffres marche encore, l'interface demande
    de le changer) ;
  - chaque essai raté compte pour le client ET pour un compteur global : 20 ratés en 15 min verrouillent la
    connexion par PIN pour tout le monde pendant 15 min (l'écran de la tour, lui, n'a pas besoin du code) ;
  - une session se referme après N minutes sans action de l'utilisateur (réglage, 15 min par défaut) : les
    lectures automatiques (/api/etat, flux) ne la prolongent pas ;
  - signatures v2 : méthode, nœud visé, chemin, heure DU HUB, nonce à usage unique (cache anti-rejeu), corps.
"""
import hashlib
import hmac
import os
import re
import secrets
import threading

PBKDF2_ITER = 200_000
SESSION_TTL = 12 * 3600
MAX_TRIES = 5
LOCK_BASE = 30
LOCK_MAX = 900
GLOBAL_MAX = 20            # 20 essais ratés (tous clients confondus)…
GLOBAL_FENETRE = 900       # … en 15 minutes…
GLOBAL_VERROU = 900        # … verrouillent la connexion par PIN 15 minutes
SIG_SKEW = 45              # fenêtre des signatures (horloges NTP des Pi)
PIN_MIN = 6                # longueur minimale d'un NOUVEAU code
PIN_MAX = 8
VERROU_CHOIX = (5, 15, 30, 60)          # minutes sans action avant que la session se referme
VERROU_FOND_CHOIX = (1, 5, 15, 30)      # minutes en arrière-plan (app fermée/cachée) avant de reverrouiller
VERROU_DEFAUT, VERROU_FOND_DEFAUT = 15, 5


def hash_pin(pin, salt=None, iterations=PBKDF2_ITER):
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, salt.hex(), dk.hex())


def check_pin(pin, stored):
    try:
        algo, it, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), dk)
    except (ValueError, AttributeError):
        return False


def valid_pin(pin):
    """Règle des NOUVEAUX codes : 6 à 8 chiffres."""
    return isinstance(pin, str) and bool(re.match(r"^\d{%d,%d}$" % (PIN_MIN, PIN_MAX), pin))


def pin_saisi_valide(pin):
    """Ce qu'on accepte de vérifier à la connexion (un ancien code peut faire 4 chiffres)."""
    return isinstance(pin, str) and bool(re.match(r"^\d{4,%d}$" % PIN_MAX, pin))


def weak_pin(pin):
    suites = "01234567890", "09876543210"
    return len(set(pin)) == 1 or any(pin in s for s in suites) or pin in ("123123", "121212", "112233", "456456")


class RateLimiter:
    """Par client : 5 essais, puis verrou de 30 s qui double à chaque nouvelle série ratée (15 min au plus).
    Global (clé « * ») : 20 ratés en 15 min, tous clients confondus -> 15 min de verrou pour tout le monde."""

    def __init__(self, sys):
        self.sys = sys
        self.d = {}
        self.globaux = []          # instants (monotonic) des derniers essais ratés
        self.verrou_global = 0
        self.lock = threading.Lock()

    def wait(self, key):
        with self.lock:
            now = self.sys.monotonic()
            if key == "*":
                return max(0, int(self.verrou_global - now + 0.999))
            e = self.d.get(key)
            if not e:
                return 0
            return max(0, int(e["until"] - now + 0.999))

    def fail(self, key):
        """Un essai raté pour `key` ; compte aussi dans le compteur global. Renvoie les essais restants."""
        with self.lock:
            now = self.sys.monotonic()
            self.globaux = [t for t in self.globaux if now - t < GLOBAL_FENETRE] + [now]
            if len(self.globaux) >= GLOBAL_MAX and self.verrou_global <= now:
                self.verrou_global = now + GLOBAL_VERROU
                self.globaux = []
                self.alerte_globale = True
            if len(self.d) > 4096:                      # pas de croissance sans fin (adresses fabriquées)
                self.d = {k: v for k, v in self.d.items() if v["until"] > now or v["n"]}
                while len(self.d) > 4096:
                    self.d.pop(next(iter(self.d)))
            e = self.d.setdefault(key, {"n": 0, "series": 0, "until": 0})
            e["n"] += 1
            if e["n"] >= MAX_TRIES:
                e["n"] = 0
                e["series"] += 1
                e["until"] = now + min(LOCK_BASE * 2 ** (e["series"] - 1), LOCK_MAX)
            return MAX_TRIES - e["n"] if e["n"] else 0

    def prendre_alerte(self):
        """Vrai une fois quand le verrou global vient de se fermer (pour une alerte)."""
        with self.lock:
            a = getattr(self, "alerte_globale", False)
            self.alerte_globale = False
            return a

    def success(self, key):
        with self.lock:
            self.d.pop(key, None)


class Auth:
    def __init__(self, sys, state):
        self.sys, self.state = sys, state
        self.sessions = {}         # jeton -> {"fin": monotonic absolu, "vu": dernière action}
        self.limiter = RateLimiter(sys)
        self.lock = threading.Lock()
        self.sur_verrou_global = None   # fonction appelée quand le verrou global se ferme (alerte)

    @property
    def pin_set(self):
        return bool(self.state.get("pin"))

    @property
    def pin_court(self):
        """Vrai si le code actuel a moins de 6 chiffres (connu à la 1re connexion réussie, ou au réglage)."""
        return self.pin_set and bool(self.state.get("pin_court"))

    # --- réglages du verrou --------------------------------------------------------------------------
    def verrou(self):
        v = self.state.get("verrou") or {}
        m = v.get("inactivite_min") if v.get("inactivite_min") in VERROU_CHOIX else VERROU_DEFAUT
        f = v.get("fond_min") if v.get("fond_min") in VERROU_FOND_CHOIX else VERROU_FOND_DEFAUT
        return {"inactivite_min": m, "fond_min": f}

    def regler_verrou(self, inactivite_min=None, fond_min=None):
        v = self.verrou()
        if inactivite_min is not None:
            if inactivite_min not in VERROU_CHOIX or isinstance(inactivite_min, bool):
                raise ValueError("Durée d'inactivité : 5, 15, 30 ou 60 minutes.")
            v["inactivite_min"] = inactivite_min
        if fond_min is not None:
            if fond_min not in VERROU_FOND_CHOIX or isinstance(fond_min, bool):
                raise ValueError("Durée en arrière-plan : 1, 5, 15 ou 30 minutes.")
            v["fond_min"] = fond_min
        self.state.set("verrou", v)
        return v

    def set_pin(self, pin):
        if not valid_pin(pin):
            raise ValueError("Le code doit faire %d à %d chiffres." % (PIN_MIN, PIN_MAX))
        if weak_pin(pin):
            raise ValueError("Ce code est trop facile à deviner. Choisis-en un autre.")
        self.state.set("pin", hash_pin(pin))
        self.state.set("pin_court", False)
        with self.lock:
            self.sessions.clear()

    def attente(self, client):
        return max(self.limiter.wait(client), self.limiter.wait("*"))

    def login(self, pin, client):
        """Retourne (jeton|None, message, attente_s)."""
        for key in (client, "*"):
            w = self.limiter.wait(key)
            if w:
                return None, ("Trop d'essais. Réessaie dans %d s." % w if key != "*" else
                              "Trop d'essais ratés sur la tour : la connexion par code est bloquée %d min."
                              % max(1, (w + 59) // 60)), w
        if not self.pin_set:
            return None, "Aucun code PIN n'est encore défini.", 0
        if not (pin_saisi_valide(pin) and check_pin(pin, self.state.get("pin"))):
            left = self.limiter.fail(client)
            if self.limiter.prendre_alerte() and self.sur_verrou_global:
                try:
                    self.sur_verrou_global()
                except Exception:
                    pass
            w = self.attente(client)
            if w:
                return None, "Trop d'essais. Réessaie dans %d s." % w, w
            return None, "Code incorrect. Encore %d essai%s." % (left, "s" if left > 1 else ""), 0
        self.limiter.success(client)
        if len(pin) < PIN_MIN and not self.state.get("pin_court"):
            self.state.set("pin_court", True)           # ancien code à 4 chiffres : l'interface demandera d'en changer
        tok = secrets.token_urlsafe(32)
        with self.lock:
            now = self.sys.monotonic()
            self.sessions = {t: e for t, e in self.sessions.items() if self._vivante(e, now)}
            if len(self.sessions) > 64:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[tok] = {"fin": now + SESSION_TTL, "vu": now}
        return tok, "ok", 0

    def _vivante(self, e, now):
        return e["fin"] > now and now - e["vu"] < self.verrou()["inactivite_min"] * 60

    def valid(self, token, activite=False):
        """Session ouverte ? `activite` : une action de l'utilisateur repousse le verrou d'inactivité."""
        if not token or not isinstance(token, str):
            return False
        with self.lock:
            e = self.sessions.get(token)
            now = self.sys.monotonic()
            if not e or not self._vivante(e, now):
                if e:
                    self.sessions.pop(token, None)
                return False
            if activite:
                e["vu"] = now
            return True

    def reste(self, token):
        """Secondes avant que la session se referme faute d'action (0 si fermée)."""
        with self.lock:
            e = self.sessions.get(token) if isinstance(token, str) else None
            if not e:
                return 0
            now = self.sys.monotonic()
            return max(0, int(min(e["fin"] - now, self.verrou()["inactivite_min"] * 60 - (now - e["vu"]))))

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def logout_partout(self):
        with self.lock:
            self.sessions.clear()


def is_local(addr):
    return addr in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


# --- requêtes relayées (1.0.12 : `tailscale serve`) ------------------------------------------------------
# tailscaled relaie les visiteurs du tailnet vers http://127.0.0.1:80 : vue du démon, la requête vient de
# 127.0.0.1, comme l'écran de la tour. Elle ne doit JAMAIS être prise pour l'écran local. Un relais ajoute
# toujours au moins un de ces en-têtes (Tailscale : Tailscale-User-*, X-Forwarded-*), et son Host est le nom
# public du tailnet (forge.tailXXXX.ts.net). Le kiosque et la CLI, eux, appellent http://127.0.0.1/ en direct.
EN_TETES_RELAIS = ("Tailscale-User-Login", "Tailscale-User-Name", "Tailscale-User-Profile-Pic",
                   "Tailscale-Headers-Info", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
                   "Forwarded", "X-Real-IP", "Via")
HOTES_LOCAUX = ("127.0.0.1", "localhost", "::1", "")


def hote(host):
    """« forge.tail1234.ts.net:443 » -> « forge.tail1234.ts.net » ; « [::1]:80 » -> « ::1 »."""
    h = (host or "").strip().lower()
    if h.startswith("["):
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:
        h = h.split(":", 1)[0]
    return h.rstrip(".")


def relaye(headers):
    """Vrai si la requête est passée par un relais (tailscale serve ou autre proxy)."""
    if any(headers.get(k) for k in EN_TETES_RELAIS):
        return True
    h = hote(headers.get("Host"))
    return h.endswith(".ts.net") or h.endswith(".beta.tailscale.net")


def hote_local(headers):
    """L'écran local n'appelle que 127.0.0.1 / localhost (ou sans Host, HTTP/1.0)."""
    return hote(headers.get("Host")) in HOTES_LOCAUX


def client_reel(pair, headers, relais=False):
    """Adresse du vrai visiteur (clé de la limitation des essais).
    1.0.15 : X-Forwarded-For n'est cru QUE sur l'écoute réservée au relais `tailscale serve` (127.0.0.1:8790,
    que rien d'autre n'utilise) ; on prend alors le DERNIER saut (celui ajouté par tailscaled). Sur le port 80,
    n'importe quel programme local pourrait fabriquer l'en-tête : il est ignoré."""
    if relais and is_local(pair):
        xff = (headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
        if xff and len(xff) <= 45 and all(c in "0123456789abcdefABCDEF.:" for c in xff):
            return xff
        return "relais"
    return pair


def est_tailscale(ip):
    """Adresse du tailnet (100.64.0.0/10 ou fd7a:115c:a1e0::/48)."""
    import ipaddress
    try:
        a = ipaddress.ip_address((ip or "").replace("::ffff:", ""))
    except ValueError:
        return False
    return a in ipaddress.ip_network("100.64.0.0/10") or a in ipaddress.ip_network("fd7a:115c:a1e0::/48")


def est_ip(h):
    import ipaddress
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def hote_autorise(host, noms, nom_dns=None):
    """1.0.15 (anti « DNS rebinding ») : l'en-tête Host doit désigner ce Pi.
    Acceptés : une adresse IP (une page piégée passe toujours par un NOM qu'elle contrôle), localhost,
    `noms` (nom d'hôte, anciens noms) avec ou sans .local, tout nom en .local (résolu seulement sur le réseau
    de la maison), le nom MagicDNS du tailnet et <nom>.<tailnet>.ts.net. Host absent (HTTP/1.0) : accepté."""
    h = hote(host)
    if not h:
        return True
    if est_ip(h) or h in ("localhost", "localhost.localdomain"):
        return True
    if h.endswith(".local") and len(h) > 6:
        return True
    noms = {n.lower() for n in noms if n}
    if h in noms:
        return True
    if nom_dns and h == nom_dns.lower().rstrip("."):
        return True
    if h.endswith(".ts.net") and h.split(".", 1)[0] in noms:
        return True
    return False


class LocalToken:
    """Confiance « écran local » : la preuve n'est pas l'adresse 127.0.0.1 (un conteneur en réseau hôte
    y arrive aussi) mais un jeton que seuls root (CLI) et le groupe du kiosque savent lire.
    Le navigateur du kiosque l'échange contre un cookie via un billet à usage unique (le jeton ne
    passe donc jamais dans la ligne de commande de Chromium).
    1.0.15 : la copie du kiosque vit dans /run/ydz-kiosk (dossier de root, groupe ydz-kiosk, 0750 ; fichier 0440),
    écrite sans jamais suivre un lien symbolique ; le cookie n'est plus le jeton lui-même mais une valeur dérivée."""
    FILE = "/var/lib/ydz/jeton-local"
    KIOSK_FILE = "/run/ydz-kiosk/jeton-local"
    ANCIEN_KIOSK_FILE = "/var/lib/ydz-kiosk/jeton-local"
    KIOSK_USER = "ydz-kiosk"
    BILLET_TTL = 120
    COOKIE = "ydz_local"

    def __init__(self, sys, path=FILE, kiosk_path=KIOSK_FILE, kiosk_user=KIOSK_USER):
        self.sys, self.path, self.kiosk_path, self.kiosk_user = sys, path, kiosk_path, kiosk_user
        self.open = False                    # simulation seulement : 127.0.0.1 est cru sur parole
        self.billets = {}
        self.lock = threading.Lock()
        tok = (sys.read(path, "") or "").strip()
        if len(tok) < 32:
            tok = secrets.token_urlsafe(32)
            sys.write(path, tok + "\n", mode=0o600)
        self.token = tok
        self.cookie_valeur = hmac.new(tok.encode(), b"ydz:cookie-kiosque", hashlib.sha256).hexdigest()
        self.publish()

    def publish(self):
        """Copie lisible par le seul groupe du kiosque (s'il existe), dans un dossier de root."""
        publier = getattr(self.sys, "publier_secret", None)
        if publier is None:
            return False
        try:
            return publier(self.kiosk_path, self.token + "\n", self.kiosk_user)
        except OSError:
            return False

    def check(self, candidate):
        return bool(candidate) and isinstance(candidate, str) and (
            hmac.compare_digest(candidate, self.token) or hmac.compare_digest(candidate, self.cookie_valeur))

    def trusted(self, client, candidate):
        return is_local(client) and (self.open or self.check(candidate))

    def make_billet(self):
        now = self.sys.monotonic()
        with self.lock:
            self.billets = {b: e for b, e in self.billets.items() if e > now}
            b = secrets.token_urlsafe(24)
            self.billets[b] = now + self.BILLET_TTL
        return b

    def redeem(self, billet):
        now = self.sys.monotonic()
        with self.lock:
            e = self.billets.pop(billet, None) if isinstance(billet, str) else None
        return bool(e and e > now)


# --- signature des ordres hub -> nœud (v2, 1.0.15) ------------------------------------------------------
# En-tête : X-YDZ-Sig: v2:<heure du hub>:<nonce>:<hmac>. Message signé :
#   v2 \n MÉTHODE \n nœud visé \n chemin \n heure \n nonce \n sha256(corps)
# avec une clé propre à chaque nœud, dérivée de la clé de la tour : HMAC(cle_tour, "noeud:<nom>").
# Le nœud refuse une signature pour un autre nœud, hors de ±45 s autour de SON heure, ou dont le nonce a déjà
# servi (cache anti-rejeu). Le hub signe toujours avec sa propre horloge (jamais celle annoncée par le pair).
RE_NONCE = re.compile(r"^[0-9a-f]{16,64}$")


def cle_noeud(cle_tour, noeud):
    return hmac.new(str(cle_tour).encode(), ("noeud:" + str(noeud)).encode(), hashlib.sha256).digest()


def sign(cle_tour, method, noeud, path, ts, nonce, body=b""):
    msg = "\n".join(["v2", method.upper(), noeud, path, str(int(ts)), nonce,
                     hashlib.sha256(body or b"").hexdigest()]).encode()
    return hmac.new(cle_noeud(cle_tour, noeud), msg, hashlib.sha256).hexdigest()


def nonce():
    return secrets.token_hex(12)


def sig_header(cle_tour, method, noeud, path, ts, body=b"", nonce_=None):
    n = nonce_ or nonce()
    return "v2:%d:%s:%s" % (int(ts), n, sign(cle_tour, method, noeud, path, ts, n, body))


def lire_sig(header):
    """« v2:ts:nonce:hmac » -> (ts, nonce, hmac) ou None."""
    if not isinstance(header, str) or len(header) > 200:
        return None
    p = header.split(":")
    if len(p) != 4 or p[0] != "v2" or not RE_NONCE.match(p[2]) or not re.match(r"^[0-9a-f]{64}$", p[3]):
        return None
    try:
        return int(p[1]), p[2], p[3]
    except ValueError:
        return None


class AntiRejeu:
    """Nonces vus pendant 2 x SIG_SKEW. Borné : plein (attaque), on refuse plutôt que d'oublier."""
    MAX = 4096

    def __init__(self):
        self.vus = {}
        self.lock = threading.Lock()

    def premiere_fois(self, n, now):
        with self.lock:
            if len(self.vus) >= self.MAX:
                self.vus = {k: e for k, e in self.vus.items() if e > now}
                if len(self.vus) >= self.MAX:
                    return False
            if n in self.vus and self.vus[n] > now:
                return False
            self.vus[n] = now + 2 * SIG_SKEW + 5
            return True


def verify_sig(cle_tour, header, method, noeud, path, body, now, rejeu=None):
    """Vrai si la signature est valable pour CE nœud, maintenant, et jamais vue (si `rejeu` est donné).
    Renvoie le nonce (chaîne vraie) en cas de succès, sinon False."""
    if not cle_tour or not noeud:
        return False
    p = lire_sig(header)
    if not p:
        return False
    ts, n, mac = p
    if abs(now - ts) > SIG_SKEW:
        return False
    if not hmac.compare_digest(sign(cle_tour, method, noeud, path, ts, n, body), mac):
        return False
    if rejeu is not None and not rejeu.premiere_fois(n, now):
        return False
    return n


# --- preuve d'identité dans /api/status (1.0.15) -----------------------------------------------------------
# Le hub interroge /api/status avec une signature (nonce frais). Le nœud répond avec « preuve » =
# HMAC(clé du nœud, "status\n" + nonce + "\n" + sha256(JSON canonique du reste)). Sans preuve valable, le
# hub ne fusionne rien de ce que le pair raconte (noms, tâches, apps…).
def json_canonique(d):
    import json
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def preuve_status(cle_tour, noeud, nonce_, st):
    corps = {k: v for k, v in st.items() if k != "preuve"}
    msg = b"status\n" + nonce_.encode() + b"\n" + hashlib.sha256(json_canonique(corps)).hexdigest().encode()
    return hmac.new(cle_noeud(cle_tour, noeud), msg, hashlib.sha256).hexdigest()


def verifier_preuve(cle_tour, noeud, nonce_, st):
    p = st.get("preuve") if isinstance(st, dict) else None
    if not cle_tour or not isinstance(p, str):
        return False
    try:
        return hmac.compare_digest(preuve_status(cle_tour, noeud, nonce_, st), p)
    except (TypeError, ValueError):
        return False
