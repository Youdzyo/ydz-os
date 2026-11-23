"""ydzd : point d'entrée du démon."""
import argparse
import os
import signal
import sys as _sys
import threading
import time

from . import config
from .node import Node
from .server import Server
from .sysapi import System

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def serve(node, port, ui_dir=None, host="0.0.0.0", tries=10, pause=3.0, relais=False):
    # un conteneur Docker peut avoir pris le port avant nous au démarrage : on réessaie un peu
    srv = None
    for i in range(tries):
        try:
            srv = Server((host, port), node, ui_dir, relais=relais)
            break
        except OSError as ex:
            print("ydzd : port %d indisponible (%s), essai %d/%d" % (port, ex, i + 1, tries), flush=True)
            if i + 1 < tries:
                time.sleep(pause)
    if srv is None:
        return None
    threading.Thread(target=srv.serve_forever, name="ydz-http-%d" % port, daemon=True).start()
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ydzd", description="Démon YDZ OS (agent de nœud et hub).")
    ap.add_argument("--conf", default="/etc/ydz/ydz.conf", help="fichier de configuration")
    ap.add_argument("--sim", action="store_true", help="simule une tour de 3 nœuds sur cette machine")
    ap.add_argument("--sim-port", type=int, default=8080, help="port web du hub simulé (défaut 8080)")
    ap.add_argument("--sim-reseau", action="store_true",
                    help="en simulation, traite 127.0.0.1 comme un client du réseau (pour tester le PIN)")
    ap.add_argument("--sim-scenario", choices=("normal", "vierge"), default="normal",
                    help="« vierge » : deux disques vides sur vault, aucune app")
    ap.add_argument("--version", action="store_true")
    a = ap.parse_args(argv)
    if a.version:
        print(config.version())
        return 0
    if a.sim:
        from . import sim
        return sim.run(BASE, a.sim_port, a.sim_reseau, a.sim_scenario)

    s = System()
    conf = config.Config.load(s, a.conf)
    for e in conf.errors:
        print("ydzd : ydz.conf : " + e, flush=True)
    node = Node(s, conf, BASE)
    if serve(node, conf.int("port")) is None:
        print("ydzd : impossible d'ouvrir le port de l'agent : arrêt (systemd réessaiera).", flush=True)
        return 1
    if node.hub:
        ui = os.path.join(BASE, "ui")
        if serve(node, conf.int("port_web"), ui) is None:
            print("ydzd : l'interface web (port %d) n'est pas servie : qui occupe ce port ? (ss -ltnp)"
                  % conf.int("port_web"), flush=True)
        # 1.0.15 : écoute dédiée au relais `tailscale serve` (127.0.0.1 seulement) : X-Forwarded-For y est fiable
        from .acces import PORT_RELAIS
        serve(node, PORT_RELAIS, ui, host="127.0.0.1", relais=True)
    print("ydzd %s : nœud %s, rôle %s%s" % (config.version(), node.name, node.role,
                                             " (hub)" if node.hub else ""), flush=True)

    def stop(signum, frame):
        node.stop.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    node.run()
    r = s.run(["systemctl", "is-system-running"], timeout=5)
    node.shutdown(system_halting=r.out.strip() == "stopping")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
