"""Serveur HTTP (bibliothèque standard) : API JSON, événements SSE, fichiers
statiques de l'interface (port web du hub uniquement).

1.0.15 (sécurité) :
  - Content-Security-Policy stricte + X-Content-Type-Options, Referrer-Policy, X-Frame-Options sur chaque réponse ;
  - l'en-tête Host doit désigner ce Pi (sinon 421) : plus de « DNS rebinding » ;
  - X-Forwarded-For n'est cru que sur l'écoute dédiée au relais `tailscale serve` (127.0.0.1:PORT_RELAIS) ;
  - l'accès et la taille sont vérifiés AVANT de lire le corps d'un paquet (40 Mo), un délai par connexion coupe
    les connexions traînantes (slowloris), et le nombre de connexions simultanées est borné.
"""
import json
import mimetypes
import os
import queue
import threading
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, parse_qsl, urlsplit

from . import auth as authmod
from .core import ApiError

MAX_BODY = 64 * 1024
MAX_PAQUET = 40 * 1024 * 1024          # paquets de mise à jour (.zip), route /api/maj/paquet
ROUTE_PAQUET = "/api/maj/paquet"
MAX_CONNEXIONS = 64                    # connexions traitées en même temps (le reste attend un peu, puis 503)
MAX_PAR_IP = 16                        # connexions ouvertes par adresse
DELAI_CORPS = 20.0                     # secondes pour recevoir le corps d'une requête (anti-slowloris)
# Content-Security-Policy : l'interface n'a ni script ni gestionnaire en ligne (tout est dans des fichiers .js
# chargés depuis la même origine). Les styles en ligne (largeur des jauges…) sont permis. Le terminal (ttyd,
# port 7681) est affiché dans un cadre.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; frame-src 'self' http://*:7681 https://*:7681; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
ENTETES_SECU = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY", "Content-Security-Policy": CSP}
PLACEHOLDER = ("<!doctype html><meta charset=utf-8><title>YDZ OS</title>"
               "<body style='background:#000;color:#d9713f;font:20px sans-serif;padding:40px'>"
               "YDZ OS fonctionne. L'interface n'est pas encore installée (dossier ui/ vide).<br>"
               "<a style='color:#fff' href='/api/etat'>/api/etat</a></body>").encode()
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/manifest+json", ".webmanifest")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ydzd"
    sys_version = ""
    timeout = 30

    def log_message(self, fmt, *args):      # pas de journal par requête (carte SD)
        pass

    # --- en-têtes communs -------------------------------------------------------------
    def _secu(self):
        for k, v in ENTETES_SECU.items():
            self.send_header(k, v)

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._secu()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _relaye(self):
        """Vrai si la requête vient d'un relais (écoute dédiée au relais, ou en-têtes de relais présents)."""
        return bool(getattr(self.server, "relais", False)) or authmod.relaye(self.headers)

    def _hote_ok(self):
        """L'en-tête Host doit désigner ce Pi (anti « DNS rebinding »)."""
        node = self.server.node
        noms, dns = node.hotes_autorises()
        return authmod.hote_autorise(self.headers.get("Host"), noms, dns)

    def _ctx(self, raw, acces_connu=None):
        node = self.server.node
        pair = self.client_address[0]
        relais_fiable = bool(getattr(self.server, "relais", False))
        relaye = self._relaye()
        client = authmod.client_reel(pair, self.headers, relais=relais_fiable)
        tok = self.headers.get("X-YDZ-Jeton") or ""
        a = self.headers.get("Authorization") or ""
        if a.startswith("Bearer "):
            tok = a[7:].strip()
        sig = False
        if not node.hub:
            sig = authmod.verify_sig(node.state.get("cle_tour"), self.headers.get("X-YDZ-Sig"),
                                     self.command, node.name, self.path, raw, node.sys.now(), node.rejeu)
        local = node.local.trusted(pair, self.headers.get("X-YDZ-Local") or self._cookie(node.local.COOKIE)) \
            and not node.net_only and not relaye and authmod.hote_local(self.headers)
        # identité du visiteur relayé : seulement sur l'écoute dédiée au relais (jamais fabriquée par un local)
        login = (self.headers.get("Tailscale-User-Login") or "")[:120] if relais_fiable else ""
        return {"client": client, "token": tok or None, "sig": bool(sig), "sig_nonce": sig or None,
                "local": local, "relaye": relaye,
                "tailscale": bool(login) or authmod.est_tailscale(client), "tailscale_login": login or None}

    def _cookie(self, name):
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "")
            return c[name].value if name in c else None
        except CookieError:
            return None

    def _api(self, raw=b""):
        node = self.server.node
        u = urlsplit(self.path)
        body = {}
        if u.path == ROUTE_PAQUET and self.command == "POST":
            body = {"_brut": raw}             # zip brut : pas de JSON
        elif raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"erreur": "Corps JSON illisible.", "code": "json"})
            if not isinstance(body, dict):
                return self._send(400, {"erreur": "Un objet JSON est attendu.", "code": "json"})
        ctx = self._ctx(raw)
        if u.path == "/api/events" and self.command == "GET":
            return self._sse(ctx)
        code, data = node.dispatch(self.command, u.path, dict(parse_qsl(u.query)), body, ctx)
        self._send(code, data)

    def _sse(self, ctx):
        node = self.server.node
        if not node.hub:
            return self._send(404, {"erreur": "Ce nœud n'est pas le hub.", "code": "pas_hub"})
        # 1.0.15.1 : le flux temps réel porte le même instantané privé que /api/etat, donc il exige
        # le même accès « lecture » (écran local, signature du hub ou session PIN). Sans quoi un
        # invité anonyme — ou un visiteur relayé par Tailscale — lirait tout sans code et contournerait
        # l'écran de verrouillage. Une requête relayée sans session n'a ni « local » ni « sig » : refusée.
        try:
            node.check_access("lecture", ctx)
        except ApiError as ex:
            return self._send(ex.status, ex.body())
        # l'écran local / une session ont droit à 8 flux, les autres (signature) à 2 au plus
        prioritaire = bool(ctx.get("local") or node.hub.auth.valid(ctx.get("token")))
        q = node.events.subscribe(prioritaire)
        if q is None:
            return self._send(503, {"erreur": "Trop d'écrans connectés en même temps.", "code": "occupe"})
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self._secu()
            self.end_headers()
            self._event("etat", node.hub.snapshot(node.status()))
            while not node.stop.is_set():
                try:
                    kind, data = q.get(timeout=15)
                    self._event(kind, data)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (OSError, ValueError):
            pass
        finally:
            node.events.unsubscribe(q)

    def _event(self, kind, data):
        self.wfile.write(("event: %s\ndata: %s\n\n" % (
            kind, json.dumps(data, ensure_ascii=False, separators=(",", ":")))).encode())
        self.wfile.flush()

    def _ticket(self):
        """/?billet=… : échange un billet à usage unique contre le cookie du kiosque, puis revient à /."""
        node = self.server.node
        q = parse_qs(urlsplit(self.path).query)
        self.send_response(302)
        if authmod.is_local(self.client_address[0]) and not self._relaye() \
                and node.local.redeem((q.get("billet") or [""])[0]):
            # 1.0.15 : le cookie n'est plus le jeton maître mais une valeur dérivée (fuite = pas le jeton CLI)
            self.send_header("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=315360000"
                             % (node.local.COOKIE, node.local.cookie_valeur))
        # on garde le mode d'affichage demandé par le kiosque (?affichage=1)
        self.send_header("Location", "/?affichage=1" if (q.get("affichage") or [""])[0] == "1" else "/")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self._secu()
        self.end_headers()

    def _static(self):
        if "billet=" in urlsplit(self.path).query and urlsplit(self.path).path == "/":
            return self._ticket()
        root = os.path.realpath(self.server.ui_dir)
        rel = urlsplit(self.path).path.lstrip("/") or "index.html"
        full = os.path.realpath(os.path.join(root, rel))
        if not (full == root or full.startswith(root + os.sep)):
            return self._send(403, {"erreur": "Chemin refusé.", "code": "chemin"})
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            if "." in os.path.basename(rel) and rel != "index.html":
                return self._send(404, {"erreur": "Fichier introuvable.", "code": "inconnu"})
            full = os.path.join(root, "index.html")       # routes de l'interface
            if not os.path.isfile(full):
                return self._send(200, PLACEHOLDER, "text/html; charset=utf-8")
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return self._send(404, {"erreur": "Fichier introuvable.", "code": "inconnu"})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        long = full.endswith((".woff2", ".png", ".jpg", ".webp"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400" if long else "no-cache")
        self._secu()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def finish(self):
        try:
            BaseHTTPRequestHandler.finish(self)
        except OSError:                      # client parti (SSE fermé, onglet quitté)
            pass

    # --- garde commune (Host) ---------------------------------------------------------
    def _garde(self):
        if not self._hote_ok():
            self._send(421, {"erreur": "Cette adresse ne correspond pas à la tour.", "code": "hote"})
            self.close_connection = True
            return False
        return True

    def do_GET(self):
        try:
            if not self._garde():
                return
            if self.path.startswith("/api/"):
                return self._api()
            if self.server.ui_dir:
                return self._static()
            self._send(404, {"erreur": "Ici, seule l'API répond (/api/status).", "code": "inconnu"})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    do_HEAD = do_GET

    def _lire_corps(self, n):
        """Lit n octets avec un délai global (slowloris : un filet d'octets ne garde pas un fil éternellement)."""
        import time
        fin = time.monotonic() + DELAI_CORPS
        morceaux, lus = [], 0
        while lus < n:
            self.connection.settimeout(max(0.1, fin - time.monotonic()))
            try:
                b = self.rfile.read(min(262144, n - lus))
            except (OSError, ValueError):
                return None
            if not b:
                return None
            morceaux.append(b)
            lus += len(b)
            if time.monotonic() > fin:
                return None
        return b"".join(morceaux)

    def do_POST(self):
        try:
            if not self._garde():
                return
            if not self.path.startswith("/api/"):
                return self._send(404, {"erreur": "Route inconnue.", "code": "inconnu"})
            # une page d'un autre site ne doit pas pouvoir agir à la place de l'écran local
            origin = self.headers.get("Origin")
            # « Origin: null » (iframe isolée, page locale) est refusé aussi : c'est le cas d'une page piégée
            if origin and urlsplit(origin).netloc != (self.headers.get("Host") or "") \
                    and not self._origine_relayee(origin):
                return self._send(403, {"erreur": "Origine refusée.", "code": "origine"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            paquet = urlsplit(self.path).path == ROUTE_PAQUET
            limite = MAX_PAQUET if paquet else MAX_BODY
            if n < 0 or n > limite:
                self.close_connection = True
                return self._send(413, {"erreur": "Requête trop grosse.", "code": "taille"})
            # 1.0.15 (F-10) : pour un paquet (40 Mo), on vérifie l'accès AVANT de lire le corps.
            if paquet and not self._paquet_autorise():
                self.close_connection = True
                node = self.server.node
                if not (node.hub and node.hub.auth.pin_set):
                    return self._send(403, {"erreur": "Aucun code PIN n'est défini : installe le paquet depuis "
                                                      "l'écran de la tour.", "code": "pin_non_defini"})
                return self._send(401, {"erreur": "Code PIN demandé pour installer un paquet.", "code": "pin_requis"})
            corps = self._lire_corps(n) if n else b""
            if corps is None:
                self.close_connection = True
                return self._send(408, {"erreur": "Corps de requête incomplet.", "code": "delai"})
            self._api(corps)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        finally:
            try:
                self.connection.settimeout(self.timeout)
            except OSError:
                pass

    def _paquet_autorise(self):
        """Écran local ou session PIN valide : seuls eux peuvent envoyer un paquet (jamais un visiteur relayé)."""
        node = self.server.node
        if not node.hub:
            return False
        ctx = self._ctx(b"")
        if ctx.get("relaye"):
            return False
        return bool(ctx.get("local") or node.hub.auth.valid(ctx.get("token")))

    def _origine_relayee(self, origin):
        """Derrière tailscale serve (relais sur la machine), le navigateur parle à forge.tailXXXX.ts.net :
        l'origine est alors comparée à X-Forwarded-Host (qu'aucune page web ne peut fabriquer)."""
        if not (getattr(self.server, "relais", False) and authmod.is_local(self.client_address[0])):
            return False
        fh = (self.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
        u = urlsplit(origin)
        return bool(fh) and u.scheme == "https" and authmod.hote(u.netloc) == authmod.hote(fh) \
            and authmod.hote(fh).endswith(".ts.net")

    def do_OPTIONS(self):
        self._send(405, {"erreur": "Méthode non permise.", "code": "methode"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (OSError, ValueError)):
            return                           # coupure réseau : rien à signaler
        ThreadingHTTPServer.handle_error(self, request, client_address)

    def __init__(self, addr, node, ui_dir=None, relais=False):
        ThreadingHTTPServer.__init__(self, addr, Handler)
        self.node, self.ui_dir, self.relais = node, ui_dir, relais
        self._places = threading.BoundedSemaphore(MAX_CONNEXIONS)
        self._par_ip = {}
        self._lock = threading.Lock()

    # 1.0.15 : borne les connexions simultanées (globale et par IP) contre l'épuisement des fils
    def process_request(self, request, client_address):
        ip = client_address[0]
        if not self._places.acquire(blocking=False):
            self._refuser(request)
            return
        with self._lock:
            if self._par_ip.get(ip, 0) >= MAX_PAR_IP:
                self._places.release()
                self._refuser(request)
                return
            self._par_ip[ip] = self._par_ip.get(ip, 0) + 1
        ThreadingHTTPServer.process_request(self, request, client_address)

    def finish_request(self, request, client_address):
        try:
            ThreadingHTTPServer.finish_request(self, request, client_address)
        finally:
            ip = client_address[0]
            with self._lock:
                n = self._par_ip.get(ip, 0) - 1
                if n > 0:
                    self._par_ip[ip] = n
                else:
                    self._par_ip.pop(ip, None)
            self._places.release()

    def _refuser(self, request):
        try:
            request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                            b"Connection: close\r\n\r\n")
        except OSError:
            pass
        try:
            self.shutdown_request(request)
        except OSError:
            pass
