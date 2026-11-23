"""ydz : commande d'administration de YDZ OS (en français)."""
import argparse
import getpass
import json
import os
import subprocess
import sys as _sys
import time

from . import auth as authmod
from . import badge as badgemod
from . import config
from .sysapi import System

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
C = {"ok": "\033[32m", "warn": "\033[33m", "err": "\033[31m", "crit": "\033[31m", "info": "\033[36m",
     "acc": "\033[38;5;173m", "dim": "\033[2m", "0": "\033[0m"}
if not _sys.stdout.isatty():
    C = {k: "" for k in C}
MARK = {"ok": "OK  ", "warn": "ATT ", "err": "ERR ", "crit": "ERR ", "info": "INFO", "halt": "ARR "}


class Api:
    def __init__(self, port):
        self.s, self.base = System(), "http://127.0.0.1:%d" % port
        # preuve « je suis l'opérateur local » : jeton lisible par root seulement (voir auth.LocalToken)
        self.hdr = {}
        try:
            with open(authmod.LocalToken.FILE) as f:
                self.hdr["X-YDZ-Local"] = f.read().strip()
        except OSError:
            pass

    def call(self, method, path, body=None, timeout=30):
        code, data = self.s.http(method, self.base + path, body if body is not None or method == "GET" else {},
                                 self.hdr, timeout=timeout)
        if code == 0:
            fail("Le service YDZ ne répond pas sur ce nœud. Essaie : sudo systemctl restart ydzd ; ydz journal")
        if code >= 400:
            fail((data or {}).get("erreur", "Erreur %d." % code))
        return data or {}

    def get(self, path, timeout=30):
        return self.call("GET", path, timeout=timeout)

    def post(self, path, body=None, timeout=30):
        return self.call("POST", path, body or {}, timeout=timeout)


def fail(msg, code=1):
    print(C["err"] + "Erreur : " + C["0"] + msg, file=_sys.stderr)
    _sys.exit(code)


def need_root():
    if os.geteuid() != 0:
        fail("Cette commande demande les droits administrateur : relance-la avec « sudo ».")


def size(n):
    if n is None:
        return "?"
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1000 or unit == "To":
            return ("%.0f %s" if unit in ("o", "Ko", "Mo") else "%.1f %s") % (n, unit)
        n /= 1000.0


def dur(s):
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return ("%d j %d h" % (d, h)) if d else ("%d h %02d" % (h, m))


def col(state, text):
    return C.get(state, "") + text + C["0"]


def node_line(n):
    if not n.get("en_ligne", True):
        return "  %-7s %s" % (n["nom"].upper(), col("err" if n.get("connu") else "dim",
                                                     "hors ligne" if n.get("connu") else "pas encore vu"))
    ram = n.get("ram", {})
    temp = "%5.1f °C" % n["temp"] if n.get("temp") is not None else "   ? °C"
    disk = "%s %%" % n["disk"] if n.get("disk") is not None else "?"
    return "  %-7s %s  %s  CPU %3.0f %%  RAM %3.0f %%  disque %s  %-15s  allumé depuis %s" % (
        n["nom"].upper(), col(n.get("etat", "ok"), MARK.get(n.get("etat"), "?   ")), temp,
        n.get("cpu", 0), ram.get("pct", 0), disk, n.get("ip", ""), dur(n.get("uptime", 0)))


def wait_task(api, res, hub):
    t = res.get("tache")
    if not t:
        return
    node = res.get("noeud", t.get("noeud"))
    shown = 0
    while True:
        path = ("/api/taches/%s?noeud=%s" % (t["id"], node)) if hub else "/api/agent/taches/" + t["id"]
        t = api.get(path)["tache"]
        for line in t["journal"][shown:]:
            print(C["dim"] + "   " + line + C["0"])
        shown = len(t["journal"])
        if t["etat"] != "en_cours":
            break
        time.sleep(1)
    print(col("ok" if t["etat"] == "ok" else "err", t["message"]))
    if t["etat"] != "ok":
        _sys.exit(1)


# --- commandes -------------------------------------------------------------------
def cmd_etat(api, a, hub):
    if not hub:
        n = api.get("/api/status")
        print(node_line(n))
        for al in n.get("alertes", []):
            print("    " + col("warn", "! " + al))
        return
    e = api.get("/api/etat")
    print("%sYDZ OS %s%s, tour : %s" % (C["acc"], e["version"], C["0"], col(e["tour"]["etat"], {
        "ok": "tout va bien", "warn": "à surveiller", "crit": "PROBLÈME"}[e["tour"]["etat"]])))
    for n in e["noeuds"]:
        print(node_line(n))
    v = e["ventilo"]
    print("  Ventilateur : %d %%, mode %s%s" % (v["vitesse"], v["mode_nom"],
                                                "" if v["pwm"] else col("warn", "  (non piloté : 100 % par sécurité)")))
    for al in e["tour"]["alertes"]:
        print("  " + col("warn", "! %s" % al["texte"]))
    if a.court:
        return
    for n in e["noeuds"]:
        for app in n.get("apps", []):
            print("  %-7s app %-14s %s" % (n["nom"].upper(), app["id"], app["etat"]))


def cmd_apps(api, a, hub):
    act = a.action or "liste"
    if act == "liste":
        if hub:
            d = api.get("/api/apps")
            for app in d["catalogue"]:
                inst = ", ".join("%s sur %s" % (i["etat"], i["noeud"]) for i in app["installations"]) or "non installée"
                print("  %-14s %-16s %s%s%s" % (app["id"], app["nom"], C["dim"], inst, C["0"]))
        else:
            for app in api.get("/api/agent/apps")["apps"]:
                print("  %-14s %s" % (app["id"], app["etat"]))
        return
    if not a.app:
        fail("Indique l'application. Exemple : ydz apps %s minecraft" % act)
    if act == "journal":
        path = ("/api/apps/%s/journal?lignes=%d%s" % (a.app, a.lignes, "&noeud=" + a.noeud if a.noeud else "")) \
            if hub else "/api/agent/apps/%s/journal?lignes=%d" % (a.app, a.lignes)
        print("\n".join(api.get(path)["lignes"]))
        return
    body = {}
    if a.noeud:
        body["noeud"] = a.noeud
    if act in ("installer", "reglages"):
        body["reglages"] = {}
        for kv in a.reglage:
            if "=" not in kv:
                fail("Réglage attendu sous la forme CLE=valeur (reçu : %s)" % kv)
            k, v = kv.split("=", 1)
            body["reglages"][k] = v
    if act == "desinstaller":
        body["supprimer_donnees"] = a.supprimer_donnees
    prefix = "/api/apps/" if hub else "/api/agent/apps/"
    wait_task(api, api.post("%s%s/%s" % (prefix, a.app, act), body), hub)


def cmd_maj(api, a, hub):
    if a.depuis:
        need_root()
        inst = os.path.join(a.depuis, "install.sh")
        if not os.path.isfile(inst):
            fail("Pas de install.sh dans %s." % a.depuis)
        os.execvp("bash", ["bash", inst, "--maj"])
    if a.appliquer:
        need_root()
        if not a.oui and input("Mettre à jour le système de ce nœud maintenant ? [o/N] ").strip().lower() != "o":
            return
        os.makedirs("/var/log/ydz", exist_ok=True)
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        with open("/var/log/ydz/maj.log", "a") as log:
            log.write("=== %s : mise à jour ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            log.flush()
            rc = 0
            for cmd in (["apt-get", "update", "-q"],
                        ["apt-get", "-y", "-q", "-o", "Dpkg::Options::=--force-confdef",
                         "-o", "Dpkg::Options::=--force-confold", "full-upgrade"],
                        ["apt-get", "-y", "-q", "autoremove"]):
                print("> " + " ".join(cmd))
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
                for line in p.stdout:
                    log.write(line)
                    if _sys.stdout.isatty():
                        _sys.stdout.write(line)
                rc = p.wait() or rc
            log.write("=== fin (code %d) ===\n" % rc)
        s = System()
        st = config.State(s)
        st.set("maj", dict(st.get("maj", {}), paquets=0, liste=[], fait=int(time.time())) if rc == 0 else st.get("maj", {}))
        print(col("ok", "Système à jour.") if rc == 0 else col("err", "La mise à jour a échoué : ydz journal maj"))
        if os.path.exists("/run/reboot-required"):
            print(col("warn", "Un redémarrage est nécessaire : sudo ydz redemarrer"))
        return rc
    if a.verifier:
        wait_task(api, api.post("/api/agent/maj/verifier"), False)
    m = api.get("/api/agent/maj")
    print("YDZ OS %s. Paquets à mettre à jour : %s. Redémarrage requis : %s." % (
        m["version"], "?" if m["paquets"] is None else m["paquets"], "oui" if m["redemarrage_requis"] else "non"))
    if m["paquets"] is None:
        print("Lance « ydz maj --verifier » pour interroger les dépôts.")
    elif m["paquets"]:
        print("Pour les installer : sudo ydz maj --appliquer")


def cmd_journal(api, a, hub):
    files = {"install": "/var/log/ydz/install.log", "maj": "/var/log/ydz/maj.log"}
    units = {"ydzd": "ydzd", "kiosque": "ydz-kiosk", "docker": "docker", "samba": "smbd"}
    if a.quoi in files:
        os.execvp("tail", ["tail", "-n", str(a.n)] + (["-f"] if a.suivre else []) + [files[a.quoi]])
    os.execvp("journalctl", ["journalctl", "-u", units[a.quoi], "-n", str(a.n), "--no-pager"]
              + (["-f"] if a.suivre else []))


def cmd_ventilo(api, a, hub):
    if not hub:
        fail("Le ventilateur est piloté par forge. Lance cette commande sur forge.")
    if a.mode == "test":
        api.post("/api/ventilo/test", {"secondes": 8})
        print("Ventilateur à 100 % pendant 8 secondes : tu dois l'entendre accélérer.")
        return
    if a.mode:
        api.post("/api/ventilo", {"mode": a.mode})
    v = api.get("/api/ventilo")
    print("Mode %s, vitesse %d %%, nœud le plus chaud : %s (%s °C)." % (
        v["mode_nom"], v["vitesse"], (v["noeud"] or "?").upper(), v["temp"]))
    if not v["pwm"]:
        print(col("warn", "PWM indisponible : " + (v["erreur"] or "")))


def _probe(port):
    """(info, lignes_étrangères) : info est None si l'appareil ne répond pas comme un badge YDZ."""
    s = System()
    other = []
    try:
        ser = s.open_serial(port)
    except OSError:
        return None, other
    try:
        return badgemod.probe(ser, seen=other), other
    except OSError:
        return None, other
    finally:
        ser.close()


def cmd_badge(api, a, hub):
    act = a.action or "etat"
    if act == "etat":
        b = api.get("/api/status")["badge"]
        if b["present"]:
            print(col("ok", "Badge présent") + " sur %s, firmware %s, luminosité %d %%." % (b["port"], b["fw"], b["luminosite"]))
        else:
            print(col("warn", "Badge absent : ") + (b["erreur"] or "désactivé"))
        return
    if act == "luminosite":
        if a.valeur is None or not a.valeur.isdigit():
            fail("Exemple : ydz badge luminosite 60")
        api.post("/api/agent/badge", {"luminosite": int(a.valeur)})
        return
    need_root()
    import glob
    sysx = System()
    ports = [a.valeur] if a.valeur else sorted(glob.glob("/dev/ttyACM*"))
    if not a.valeur and act != "auto":      # « flasher » sans port : seulement les cartes Espressif, jamais une clé Zigbee
        ports = [p for p in ports if sysx.usb_vendor(p) == badgemod.ESPRESSIF]
    if not ports:
        fail("Aucun badge (carte Espressif) branché en USB.")
    flash = os.path.join(BASE, "badge", "flash.sh")
    if not os.path.isfile(flash):
        fail("Firmware du badge absent (%s)." % flash)
    os.makedirs("/run/ydz", exist_ok=True)
    pause = badgemod.PAUSE_FILE
    stamp = "/var/lib/ydz/badge-flash.json"
    try:
        with open(stamp) as f:
            tries = json.load(f)
    except (OSError, ValueError):
        tries = {}
    rc = 0
    try:
        open(pause, "w").close()
        time.sleep(3)                       # le démon lâche le port
        for port in ports:
            info, other = _probe(port)
            ident = sysx.usb_ident(port) or port
            vendor = sysx.usb_vendor(port)
            if info:
                badgemod.mark_vus(sysx, ident, "ydz")
            elif other:                     # il parle, mais pas YDZ
                badgemod.mark_vus(sysx, ident, "autre")
            go, why = badgemod.decide_flash(act, vendor, info, badgemod.load_vus(sysx).get(ident), tries, time.time())
            if not go:
                print("%s : %s." % (port, why))
                if vendor == badgemod.ESPRESSIF and not info:
                    badgemod.log_flash(sysx, "REFUS %s %s : %s" % (port, ident, why))
                continue
            if act == "auto":
                fresh = time.time() - tries.get("t", 0) < 86400
                tries = {"n": tries.get("n", 0) + 1 if fresh else 1, "t": time.time()}
                with open(stamp, "w") as f:
                    json.dump(tries, f)
            print("Flash du badge sur %s…" % port)
            r = subprocess.call(["bash", flash, port])
            badgemod.log_flash(sysx, "FLASH %s %s (%s) : %s" % (port, ident, why, "réussi" if r == 0 else "échec code %d" % r))
            if r == 0:
                badgemod.mark_vus(sysx, ident, "ydz")
            print(col("ok", "Badge prêt.") if r == 0 else col("err", "Échec du flash sur %s. Maintiens le bouton BOOT "
                      "du badge en le branchant, puis relance : sudo ydz badge flasher" % port))
            rc = rc or r
    finally:
        try:
            os.remove(pause)
        except OSError:
            pass
    return rc


def cmd_diagnostic(api, a, hub):
    d = api.get("/api/agent/diagnostic", timeout=60)
    bad = 0
    for c in d["controles"]:
        print("%s %-28s %s" % (col(c["etat"], MARK[c["etat"]]), c["nom"], c["detail"]))
        if c["conseil"] and c["etat"] != "ok":
            print("       %s-> %s%s" % (C["acc"], c["conseil"], C["0"]))
        bad += c["etat"] in ("err", "warn")
    print("\n%s" % (col("ok", "Aucun problème détecté.") if not bad else col("warn", "%d point(s) à regarder." % bad)))
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    return 1 if any(c["etat"] == "err" for c in d["controles"]) else 0


def cmd_migrer(api, a, hub):
    need_root()
    os.execvp("bash", ["bash", os.path.join(BASE, "outils", "migrer-nvme.sh")] + (["--dry-run"] if a.dry_run else []))


def cmd_appairer(api, a, hub):
    if hub:
        # forge : accepter la demande d'un nœud (à défaut d'écran tactile)
        need_root()
        demandes = api.get("/api/appairage")["demandes"]
        if not a.nom:
            if not demandes:
                print("Aucune demande en attente. Un nœud neuf se présente dans les secondes qui suivent son démarrage.")
            for d in demandes:
                print("%s (%s) demande à rejoindre la tour, expire dans %d s : sudo ydz appairer %s"
                      % (d["nom"].upper(), d["ip"], d["expire_s"], d["nom"]))
            return
        api.post("/api/appairage/%s/accepter" % a.nom.lower())
        print(col("ok", "%s a rejoint la tour." % a.nom.upper()))
        return
    api.post("/api/appairage/oublier")
    print("Clé oubliée. Le hub (forge) va appairer ce nœud de nouveau dans les secondes qui viennent.")


def cmd_pin(api, a, hub):
    if not hub:
        fail("Le code PIN se règle sur forge.")
    need_root()
    p1 = getpass.getpass("Nouveau code PIN (4 à 8 chiffres) : ")
    if p1 != getpass.getpass("Encore une fois : "):
        fail("Les deux saisies sont différentes.")
    api.post("/api/auth/pin", {"pin": p1})
    print(col("ok", "Code PIN enregistré."))


def cmd_power(api, a, hub):
    need_root()
    if a.tour:
        if not hub:
            fail("« --tour » se lance depuis forge.")
        api.post("/api/tour/" + a.cmd)
        print("Toute la tour va %s." % ("s'éteindre" if a.cmd == "eteindre" else "redémarrer"))
    else:
        api.post("/api/" + a.cmd)
        print("Ce nœud va %s." % ("s'éteindre" if a.cmd == "eteindre" else "redémarrer"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ydz", description="Outil d'administration de YDZ OS.",
                                 epilog="Exemples : ydz etat | sudo ydz diagnostic | ydz apps installer minecraft")
    ap.add_argument("--port", type=int, default=None, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", metavar="commande")
    p = sub.add_parser("etat", help="état du nœud (sur forge : de toute la tour)")
    p.add_argument("--court", action="store_true", help="résumé seul")
    p = sub.add_parser("apps", help="applications : liste, installer, demarrer, arreter, journal…")
    p.add_argument("action", nargs="?", choices=["liste", "installer", "demarrer", "arreter", "redemarrer",
                                                 "mettre-a-jour", "desinstaller", "reglages", "journal"])
    p.add_argument("app", nargs="?")
    p.add_argument("reglage", nargs="*", help="CLE=valeur")
    p.add_argument("--noeud", help="nœud cible (défaut : celui conseillé par l'app)")
    p.add_argument("--supprimer-donnees", action="store_true")
    p.add_argument("--lignes", type=int, default=100)
    p = sub.add_parser("maj", help="mises à jour du système")
    p.add_argument("--verifier", action="store_true", help="interroger les dépôts")
    p.add_argument("--appliquer", action="store_true", help="installer les mises à jour (sudo)")
    p.add_argument("--oui", action="store_true", help="ne pas demander de confirmation")
    p.add_argument("--depuis", metavar="DOSSIER", help="mettre à jour YDZ OS depuis un dossier ydz-os")
    p = sub.add_parser("journal", help="journaux : ydzd, kiosque, install, maj, docker, samba")
    p.add_argument("quoi", nargs="?", default="ydzd", choices=["ydzd", "kiosque", "install", "maj", "docker", "samba"])
    p.add_argument("-n", type=int, default=60)
    p.add_argument("-f", "--suivre", action="store_true")
    p = sub.add_parser("ventilo", help="ventilateur : état, mode, test")
    p.add_argument("mode", nargs="?", choices=["silence", "equilibre", "performance", "test"])
    p = sub.add_parser("badge", help="badge USB : etat, flasher, luminosite")
    p.add_argument("action", nargs="?", choices=["etat", "flasher", "auto", "luminosite"])
    p.add_argument("valeur", nargs="?")
    p = sub.add_parser("diagnostic", help="vérifie le matériel et les services, avec des conseils")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("migrer-nvme", help="copie le système de la carte SD vers le SSD NVMe (forge)")
    p.add_argument("--dry-run", action="store_true", help="montre ce qui serait fait, sans rien écrire")
    sp = sub.add_parser("appairer", help="forge : accepte la demande d'un nœud ; vault/pulse : oublie le hub")
    sp.add_argument("nom", nargs="?", help="(forge) nom du nœud à accepter, ex. vault")
    sub.add_parser("pin", help="(forge) choisit le code PIN de l'interface")
    for name, hlp in (("eteindre", "éteint ce nœud (--tour : toute la tour)"),
                      ("redemarrer", "redémarre ce nœud (--tour : toute la tour)")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--tour", action="store_true")
    sub.add_parser("version", help="version de YDZ OS")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    if a.cmd == "version":
        print(config.version())
        return 0
    s = System()
    conf = config.Config.load(s)
    api = Api(a.port or conf.int("port"))
    direct = (a.cmd == "journal" or a.cmd == "migrer-nvme" or (a.cmd == "maj" and (a.appliquer or a.depuis))
              or (a.cmd == "badge" and a.action in ("flasher", "auto")))
    hub = False if direct else bool(api.get("/api/ping").get("hub"))
    fn = {"etat": cmd_etat, "apps": cmd_apps, "maj": cmd_maj, "journal": cmd_journal, "ventilo": cmd_ventilo,
          "badge": cmd_badge, "diagnostic": cmd_diagnostic, "migrer-nvme": cmd_migrer, "appairer": cmd_appairer,
          "pin": cmd_pin, "eteindre": cmd_power, "redemarrer": cmd_power}[a.cmd]
    try:
        return fn(api, a, hub) or 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
