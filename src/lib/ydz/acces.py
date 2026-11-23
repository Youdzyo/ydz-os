"""Accès à distance (1.0.12) : de vraies adresses HTTPS dans le tailnet, avec `tailscale serve`.

  https://forge.tailXXXX.ts.net/         -> YDZ (http://127.0.0.1:80)
  https://forge.tailXXXX.ts.net:8123/    -> Home Assistant, même port que sur le réseau local

Uniquement `tailscale serve` (visible des appareils du tailnet de Justin) : JAMAIS `tailscale funnel`, rien
n'est publié sur Internet. YDZ ne touche qu'aux ports qu'il a lui-même configurés (liste gardée dans l'état) :
une configuration `serve` faite à la main sur d'autres ports reste en place.

`tailscale serve` ne relaie que vers 127.0.0.1 : pour une app installée sur un autre nœud (vault, pulse), un
petit relais TCP écoute sur 127.0.0.1:47100+ (jamais sur le réseau) et passe les octets au nœud.

Prérequis dans la console Tailscale (DNS) : MagicDNS et « HTTPS Certificates ». Sans eux, `tailscale serve`
attendrait une confirmation interactive : on vérifie donc `CertDomains` dans `tailscale status --json`
avant de lancer quoi que ce soit.
"""
import json
import select
import socket
import threading

from .core import ApiError

CONSOLE_DNS = "https://login.tailscale.com/admin/dns"
PORT_YDZ = 443
PORT_RELAIS = 8790   # 1.0.15 : écoute dédiée (127.0.0.1) où `tailscale serve` pointe ; X-Forwarded-For y est fiable
RELAIS_BASE = 47100
PORTS_INTERDITS = {22, 53, 80, 445, 7681, 8787}

# 1.0.16 : espace visiteurs publié sur Internet via `tailscale funnel`. Le funnel ne doit JAMAIS viser
# ydzd (port 80), le port des nœuds (8787), le terminal (7681) ni une app : seulement le petit serveur
# « visiteur », isolé, en lecture seule, sur 127.0.0.1:8090. Le port public est 8443 (un des trois ports
# autorisés par Tailscale pour un funnel : 443, 8443, 10000) et jamais le 443 d'YDZ.
PORT_VISITEUR = 8090
PORT_FUNNEL = 8443
FUNNEL_CIBLE = "http://127.0.0.1:%d" % PORT_VISITEUR
# Ports qu'un funnel ne doit jamais viser (ydzd, nœuds, terminal). Les ports d'app sont refusés en plus
# par la liste blanche : la seule cible permise est FUNNEL_CIBLE, donc tout autre port est écarté d'office.
PORTS_FUNNEL_INTERDITS = {80, 443, 8787, 7681}


def _port_de_cible(cible):
    """Extrait le port d'une cible « http://127.0.0.1:PORT » ; None si illisible."""
    try:
        return int(str(cible).rsplit(":", 1)[1].split("/", 1)[0])
    except (IndexError, ValueError):
        return None


def funnel_autorise(https_port, cible):
    """Vrai seulement pour l'unique funnel permis : 8443 (public) -> le serveur visiteur en loopback.
    Tout le reste est refusé — en particulier un funnel qui viserait :80, :8787, :7681 ou un port d'app
    (aucun d'eux n'égale le port du serveur visiteur, donc la liste blanche les écarte)."""
    if https_port != PORT_FUNNEL:
        return False
    if _port_de_cible(cible) in PORTS_FUNNEL_INTERDITS:
        return False
    return cible == FUNNEL_CIBLE


def commande_funnel(actif):
    """Commande `tailscale funnel` d'activation (en arrière-plan) ou de coupure, sur le seul port permis."""
    if actif:
        return ["tailscale", "funnel", "--bg", "--https=%d" % PORT_FUNNEL, FUNNEL_CIBLE]
    return ["tailscale", "funnel", "--https=%d" % PORT_FUNNEL, "off"]


def lire_funnel(texte):
    """`tailscale funnel status --json` -> True si le funnel permis (8443 -> visiteur) est bien en place."""
    try:
        d = json.loads(texte or "") or {}
    except ValueError:
        return False
    for hote, web in (d.get("Web") or {}).items():
        try:
            port = int(hote.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        if port != PORT_FUNNEL:
            continue
        for h in ((web or {}).get("Handlers") or {}).values():
            if h.get("Proxy") == FUNNEL_CIBLE:
                return True
    return False


def lire_statut(texte):
    """`tailscale status --json` -> faits utiles (sans les pairs)."""
    try:
        d = json.loads(texte or "")
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    moi = d.get("Self") or {}
    nom = (moi.get("DNSName") or "").rstrip(".").lower()
    tn = d.get("CurrentTailnet") or {}
    magic = tn.get("MagicDNSEnabled")
    if magic is None:
        magic = bool(d.get("MagicDNSSuffix"))
    certs = [c.rstrip(".").lower() for c in (d.get("CertDomains") or []) if isinstance(c, str)]
    ips = [ip for ip in (moi.get("TailscaleIPs") or []) if isinstance(ip, str)]
    return {"etat": d.get("BackendState"), "nom_dns": nom or None, "magicdns": bool(magic),
            "certificats": bool(certs) and (not nom or nom in certs), "ip": ips[0] if ips else None,
            "tailnet": tn.get("Name")}


def lire_serve(texte):
    """`tailscale serve status --json` -> {port: cible} pour la racine « / » de chaque port HTTPS."""
    try:
        d = json.loads(texte or "") or {}
    except ValueError:
        return {}
    out = {}
    for hote, web in (d.get("Web") or {}).items():
        try:
            port = int(hote.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        h = ((web or {}).get("Handlers") or {}).get("/") or {}
        if h.get("Proxy"):
            out[port] = h["Proxy"]
    return out


def commande_serve(port, cible):
    """Commande d'ajout (tailnet seulement, en arrière-plan, persistante)."""
    if not (isinstance(port, int) and 0 < port < 65536) or port in PORTS_INTERDITS:
        raise ValueError("port refusé : %r" % (port,))
    if not (cible.startswith("http://127.0.0.1:") or cible.startswith("https+insecure://127.0.0.1:")):
        raise ValueError("cible refusée : %r" % cible)
    return ["tailscale", "serve", "--bg", "--https=%d" % port, cible]


def commande_off(port):
    return ["tailscale", "serve", "--https=%d" % port, "off"]


def raison(st, installe=True):
    if not installe:
        return "Tailscale n'est pas installé sur ce Pi."
    if not st:
        return "Tailscale ne répond pas sur ce Pi (sudo systemctl status tailscaled)."
    if st["etat"] != "Running":
        return "Tailscale est déconnecté sur ce Pi (sudo tailscale up)."
    if not st["magicdns"] or not st["nom_dns"]:
        return "MagicDNS est désactivé dans la console Tailscale."
    if not st["certificats"]:
        return "Les certificats HTTPS ne sont pas activés dans la console Tailscale."
    return None


class Relais:
    """Relais TCP 127.0.0.1:port_local -> ip:port (une app d'un autre nœud). Jamais sur le réseau."""

    def __init__(self, port_local, ip, port):
        self.port_local, self.ip, self.port = port_local, ip, port
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port_local))
        self.srv.listen(16)
        self.ouvert = True
        threading.Thread(target=self._accepter, name="ydz-relais-%d" % port_local, daemon=True).start()

    def _accepter(self):
        while self.ouvert:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._pomper, args=(c,), daemon=True).start()

    def _pomper(self, c):
        try:
            d = socket.create_connection((self.ip, self.port), timeout=5)
        except OSError:
            c.close()
            return
        d.settimeout(None)
        try:
            paires = {c: d, d: c}
            while self.ouvert:
                prets, _, _ = select.select(list(paires), [], [], 300)
                if not prets:
                    break                             # 5 min sans un octet : on ferme
                for s in prets:
                    data = s.recv(65536)
                    if not data:
                        return
                    paires[s].sendall(data)
        except OSError:
            pass
        finally:
            c.close()
            d.close()

    def fermer(self):
        self.ouvert = False
        try:
            self.srv.close()
        except OSError:
            pass


class Acces:
    def __init__(self, hub):
        self.hub = hub
        self.sys = hub.sys
        self.state = hub.node.state
        self.reel = type(self.sys).__name__ == "System"
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.st = None
        self.installe = None
        self.erreur = None
        self.applique = {}                # port -> cible réellement en place (dernière lecture)
        self.relais = {}                  # port local -> Relais
        self._empreinte = None

    # --- réglages ------------------------------------------------------------------------------------
    @property
    def actif(self):
        return bool(self.state.get("acces_https"))

    def _ports_geres(self):
        return {int(p) for p in (self.state.get("acces_ports") or [])}

    def _port_relais(self, cle):
        m = dict(self.state.get("acces_relais") or {})
        if cle not in m:
            pris = set(m.values())
            p = RELAIS_BASE
            while p in pris:
                p += 1
            m[cle] = p
            self.state.set("acces_relais", m)
        return m[cle]

    # --- lecture -------------------------------------------------------------------------------------
    def lire(self):
        self.installe = bool(self.sys.which("tailscale"))
        if not self.installe:
            self.st = None
            return None
        r = self.sys.run(["tailscale", "status", "--json", "--peers=false"], timeout=8)
        self.st = lire_statut(r.out) if r.ok else None
        return self.st

    def voulus(self, nodes=None):
        """Liste des correspondances voulues : [{port, cible, nom, app, noeud, relais?}]. YDZ d'abord."""
        node = self.hub.node
        # 1.0.15 : la racine YDZ pointe vers l'écoute dédiée au relais (127.0.0.1:PORT_RELAIS) et non le port 80 :
        # c'est là, et là seulement, que X-Forwarded-For (le vrai visiteur du tailnet) est digne de confiance.
        out = [{"port": PORT_YDZ, "cible": "http://127.0.0.1:%d" % PORT_RELAIS, "nom": "YDZ OS",
                "app": "ydz", "noeud": node.name, "chemin": "/"}]
        pris = {PORT_YDZ}
        nodes = nodes if nodes is not None else self.hub.nodes(node.status())
        for n in nodes:                    # le hub est le premier de la liste : ses apps passent en priorité
            if not n.get("en_ligne"):
                continue
            for a in n.get("apps", []):
                app = node.apps.catalog.get(a["id"]) or {}
                web = app.get("web")
                if not web or web.get("port") in pris or web.get("port") in PORTS_INTERDITS:
                    continue
                port = web["port"]
                schema = "https+insecure" if web.get("schema") == "https" else "http"
                if n["nom"] == node.name:
                    cible, rel = "%s://127.0.0.1:%d" % (schema, port), None
                else:
                    ip = n.get("ip") or n.get("adresse")
                    if not ip:
                        continue
                    lp = self._port_relais("%s:%s" % (n["nom"], a["id"]))
                    cible, rel = "%s://127.0.0.1:%d" % (schema, lp), (lp, ip, port)
                pris.add(port)
                out.append({"port": port, "cible": cible, "nom": app.get("nom", a["id"]), "app": a["id"],
                            "noeud": n["nom"], "chemin": web.get("chemin", "/"), "relais": rel,
                            "icone": app.get("icone")})
        return out

    def liens(self, voulus=None):
        if not self.st or not self.st.get("nom_dns"):
            return []
        nom = self.st["nom_dns"]
        out = []
        for v in voulus if voulus is not None else self.voulus():
            url = "https://%s%s%s" % (nom, "" if v["port"] == 443 else ":%d" % v["port"], v.get("chemin") or "/")
            out.append({"app": v["app"], "nom": v["nom"], "noeud": v["noeud"], "url": url,
                        "actif": self.applique.get(v["port"]) == v["cible"], "icone": v.get("icone")})
        return out

    def url_app(self, app_id, noeud):
        """Adresse HTTPS d'une app (pour /api/apps), si l'accès HTTPS est en place."""
        if not self.actif or not self.st or not self.st.get("nom_dns"):
            return None
        for l in self.liens(self._dernier_voulus or []):
            if l["app"] == app_id and l["noeud"] == noeud:
                return l["url"]
        return None

    _dernier_voulus = None

    def status(self):
        st = self.st or {}
        r = raison(self.st, self.installe is not False) if self.installe is not None else None
        voulus = self._dernier_voulus or []
        return {"disponible": self.installe is not None and r is None, "installe": self.installe,
                "connecte": st.get("etat") == "Running", "nom_dns": st.get("nom_dns"), "ip": st.get("ip"),
                "magicdns": st.get("magicdns"), "certificats": st.get("certificats"),
                "https_actif": self.actif, "raison_si_indispo": r, "erreur": self.erreur,
                "liens": self.liens(voulus) if self.actif else [], "console": CONSOLE_DNS}

    # --- action --------------------------------------------------------------------------------------
    def regler(self, actif):
        if not isinstance(actif, bool):
            raise ApiError(400, "Champ « https » invalide (vrai ou faux).", "https")
        if actif:
            self.lire()
            r = raison(self.st, self.installe)
            if r:
                raise ApiError(409, r + " Suis les 3 étapes ci-dessous, puis réessaie.", "https_indispo")
        self.state.set("acces_https", actif)
        self._empreinte = None
        self.appliquer()
        return self.status()

    def appliquer(self, nodes=None):
        """Met `tailscale serve` en accord avec ce qui est voulu (idempotent). Retourne le nombre de commandes."""
        with self.lock:
            voulus = self.voulus(nodes)
            self._dernier_voulus = voulus
            if self.st is None:
                self.lire()
            if not self.installe:
                return 0
            r = self.sys.run(["tailscale", "serve", "status", "--json"], timeout=8)
            courant = lire_serve(r.out) if r.ok else {}
            geres = self._ports_geres()
            n, erreurs = 0, []
            dispo = raison(self.st, self.installe) is None
            cibles = {v["port"]: v for v in voulus} if self.actif and dispo else {}
            # 1. retirer ce que YDZ avait mis et qui n'est plus voulu (jamais les ports réglés à la main)
            for port in sorted(geres):
                if port not in cibles and port in courant:
                    self.sys.run(commande_off(port), timeout=20)
                    courant.pop(port, None)
                    n += 1
            # 2. relais TCP vers les autres nœuds (vrai système seulement)
            self._relais(cibles)
            # 3. ajouter / corriger
            for port, v in sorted(cibles.items()):
                if courant.get(port) == v["cible"]:
                    continue
                if port in courant and port not in geres:
                    erreurs.append("Le port %d sert déjà autre chose dans Tailscale : %s laissé de côté." % (port, v["nom"]))
                    continue
                res = self.sys.run(commande_serve(port, v["cible"]), timeout=20)
                n += 1
                if res.ok:
                    courant[port] = v["cible"]
                else:
                    erreurs.append("%s : %s" % (v["nom"], (res.err or res.out or "refusé").strip().splitlines()[-1][:160]
                                                if (res.err or res.out) else "refusé"))
            nouveaux = sorted(p for p in cibles if courant.get(p) == cibles[p]["cible"])
            if set(nouveaux) != geres:
                self.state.set("acces_ports", nouveaux)
            self.applique = courant
            self.erreur = " · ".join(erreurs) or None
            return n

    def _relais(self, cibles):
        if not self.reel:
            return
        voulus = {v["relais"][0]: v["relais"] for v in cibles.values() if v.get("relais")}
        for lp in list(self.relais):
            r = self.relais[lp]
            if lp not in voulus or (r.ip, r.port) != voulus[lp][1:]:
                r.fermer()
                del self.relais[lp]
        for lp, (_, ip, port) in voulus.items():
            if lp not in self.relais:
                try:
                    self.relais[lp] = Relais(lp, ip, port)
                except OSError:
                    pass

    # --- boucle --------------------------------------------------------------------------------------
    def start(self):
        if self.reel:
            threading.Thread(target=self._boucle, name="ydz-acces", daemon=True).start()
        else:
            self.lire()

    def _boucle(self):
        self.stop.wait(20)                         # laisse tailscaled et les pairs se présenter
        tours = 0
        while not self.stop.is_set():
            try:
                self.lire()
                voulus = self.voulus()
                empreinte = json.dumps([self.actif, (self.st or {}).get("nom_dns"),
                                        [(v["port"], v["cible"]) for v in voulus]])
                # au démarrage, quand une app arrive ou part, et toutes les 30 min au cas où
                if empreinte != self._empreinte or tours % 30 == 0:
                    self.appliquer()
                    self._empreinte = empreinte
                self._dernier_voulus = voulus
            except Exception:
                pass
            tours += 1
            self.stop.wait(60)
