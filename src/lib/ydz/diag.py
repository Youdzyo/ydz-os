"""`ydz diagnostic` : contrôles matériels et logiciels, avec un conseil par
problème. Tout ce qui n'a pas pu être vérifié sans matériel remonte ici."""
import re

BOOT_CONFIG = "/boot/firmware/config.txt"


def _c(cid, nom, etat, detail, conseil=None):
    return {"id": cid, "nom": nom, "etat": etat, "detail": detail, "conseil": conseil}


def run(node):
    s, conf = node.sys, node.conf
    role = node.role
    out = []
    cfg = s.read(BOOT_CONFIG, "") or ""
    active = [ln.strip() for ln in cfg.splitlines() if ln.strip() and not ln.strip().startswith("#")]

    # configuration
    if conf.errors:
        out.append(_c("config", "Fichier ydz.conf", "warn", " ; ".join(conf.errors),
                      "Corrige /etc/ydz/ydz.conf puis lance « sudo systemctl restart ydzd »."))
    else:
        out.append(_c("config", "Fichier ydz.conf", "ok", "rôle %s, nom d'hôte %s" % (role, node.name)))
    if node.name != role and conf.get("nom") == "auto":
        out.append(_c("nom", "Nom d'hôte", "warn", "le nœud s'appelle « %s » mais son rôle est « %s »"
                      % (node.name, role), "Les autres nœuds le cherchent sous « %s.local ». Relance "
                      "« sudo bash /opt/ydz/install.sh »." % role))

    # alimentation
    r = s.run(["vcgencmd", "get_throttled"], timeout=5)
    if not r.ok:
        out.append(_c("alim", "Alimentation", "info", "vcgencmd indisponible (pas un Raspberry Pi ?)"))
    else:
        val = r.out.strip().split("=")[-1]
        try:
            bits = int(val, 16)
        except ValueError:
            bits = 0
        if bits & 0x1:
            out.append(_c("alim", "Alimentation", "err", "sous-tension en ce moment (%s)" % val,
                          "Vérifie le convertisseur 5 V 5 A et le câble USB-C de ce nœud."))
        elif bits & 0x10000:
            out.append(_c("alim", "Alimentation", "warn", "une sous-tension a eu lieu depuis le démarrage (%s)" % val,
                          "Si cela revient : câble USB-C plus court ou plus gros, bornes Wago à resserrer."))
        else:
            out.append(_c("alim", "Alimentation", "ok", "aucune sous-tension (%s)" % val))
    if "usb_max_current_enable=1" not in active:
        out.append(_c("usb", "Courant USB", "warn", "usb_max_current_enable=1 absent de config.txt",
                      "Sans cela les ports USB sont limités à 600 mA. Relance l'installeur."))
    ee = s.run(["rpi-eeprom-config"], timeout=10)
    if ee.ok:
        m = re.search(r"^PSU_MAX_CURRENT=(\d+)", ee.out, re.M)
        b = re.search(r"^BOOT_ORDER=(\S+)", ee.out, re.M)
        ok = bool(m and m.group(1) == "5000")
        out.append(_c("eeprom", "EEPROM", "ok" if ok else "warn",
                      "PSU_MAX_CURRENT=%s, BOOT_ORDER=%s" % (m.group(1) if m else "absent",
                                                             b.group(1) if b else "défaut"),
                      None if ok else "Relance l'installeur puis redémarre : le réglage s'applique au démarrage."))
    else:
        out.append(_c("eeprom", "EEPROM", "info", "rpi-eeprom-config indisponible"))

    # ventilateur (hub)
    if node.hub:
        chips = []
        for chip in s.glob("/sys/class/pwm/pwmchip*"):
            chips.append("%s -> %s (npwm=%s)" % (chip.split("/")[-1], s.realpath(chip).split("/devices/")[-1],
                                                  (s.read(chip + "/npwm", "?") or "?").strip()))
        fan = node.hub.fan
        line = any(ln.startswith("dtoverlay=pwm") for ln in active)
        if fan.pwm.available:
            out.append(_c("ventilo", "Ventilateur (PWM GPIO%d)" % fan.pwm.gpio, "ok",
                          "canal %s, %d %% ; %s" % (fan.pwm.chan, getattr(fan, "applied", 100), " | ".join(chips))))
        else:
            out.append(_c("ventilo", "Ventilateur (PWM GPIO%d)" % fan.pwm.gpio, "err",
                          "%s ; overlay %s ; puces vues : %s" % (fan.pwm.error, "présent" if line else "ABSENT",
                                                                 " | ".join(chips) or "aucune"),
                          "Le ventilateur tourne à 100 % (sécurité matérielle). Vérifie la ligne "
                          "« dtoverlay=pwm,pin=18,func=2 » de config.txt, redémarre, puis relance ce diagnostic."))
        # écran
        dsi = [p for p in s.glob("/sys/class/drm/card*-DSI-*")]
        st = [(s.read(p + "/status", "") or "").strip() for p in dsi]
        bl = s.glob("/sys/class/backlight/*")
        if conf.get("kiosk") == "non":
            out.append(_c("ecran", "Écran", "info", "kiosque désactivé (kiosk=non)"))
        elif "connected" in st and bl:
            ks = s.run(["systemctl", "is-active", "ydz-kiosk.service"], timeout=5).out.strip()
            out.append(_c("ecran", "Écran DSI", "ok" if ks == "active" else "warn",
                          "écran détecté, rétroéclairage %s, kiosque %s, rotation %s°"
                          % (bl[0].split("/")[-1], ks or "?", conf.get("rotation")),
                          None if ks == "active" else "Regarde « ydz journal kiosque »."))
        else:
            out.append(_c("ecran", "Écran DSI", "err", "écran non détecté (DSI : %s ; rétroéclairage : %s)"
                          % (", ".join(st) or "aucun connecteur", "oui" if bl else "non"),
                          "Vérifie la nappe DSI (sens, verrou) et les 2 fils 5 V/GND. Si l'écran reste noir, "
                          "mets « ecran_overlay=force » dans /etc/ydz/ydz.conf et relance l'installeur."))
        # pairs
        for p in node.hub.peers.all():
            on = node.hub.peers.online(p)
            if p.status is None:
                out.append(_c("pair-" + p.name, "Nœud " + p.name, "info", "jamais vu (normal s'il n'est pas monté)"))
            else:
                out.append(_c("pair-" + p.name, "Nœud " + p.name, "ok" if on and not p.bad_key else "warn",
                              "%s, adresse %s%s" % ("en ligne" if on else "HORS LIGNE", p.addr or "?",
                                                    ", clé refusée" if p.bad_key else ""),
                              None if on and not p.bad_key else
                              "Vérifie son câble réseau ; si la clé est refusée, lance « sudo ydz appairer » dessus."))
    elif not node.state.get("cle_tour"):
        out.append(_c("appairage", "Appairage avec le hub", "info",
                      "pas encore appairé : le hub (forge) le fera tout seul dès qu'il verra ce nœud"))

    # Docker
    if conf.get("docker") != "non":
        if not s.which("docker"):
            out.append(_c("docker", "Docker", "err", "docker absent", "Relance l'installeur avec Internet branché."))
        else:
            ok = s.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
            comp = s.run(["docker", "compose", "version", "--short"], timeout=10)
            c2 = comp.out.strip() if comp.ok else ("docker-compose (v1)" if s.which("docker-compose") else None)
            out.append(_c("docker", "Docker", "ok" if ok.ok and c2 else "err",
                          "moteur %s, compose %s" % (ok.out.strip() or "arrêté", c2 or "ABSENT"),
                          None if ok.ok and c2 else "« sudo systemctl status docker » pour voir l'erreur."))

    # badge
    b = node.badge.status()
    if not b["actif"]:
        out.append(_c("badge", "Badge", "info", "désactivé (badge=non)"))
    elif b["present"]:
        out.append(_c("badge", "Badge", "ok", "%s, firmware %s" % (b["port"], b["fw"])))
    else:
        out.append(_c("badge", "Badge", "warn", b["erreur"] or "absent",
                      "Branche le badge en USB sur CE nœud, puis « sudo ydz badge flasher »."))

    # stockage
    if role == "vault":
        sata = [p for p in s.glob("/sys/bus/pci/devices/*/class")
                if (s.read(p, "") or "").strip().startswith("0x0106")]
        if sata:
            n = len([d for d in s.glob("/sys/block/sd*")])
            out.append(_c("sata", "Penta SATA HAT", "ok", "contrôleur SATA vu sur le PCIe, %d disque(s)" % n))
        else:
            out.append(_c("sata", "Penta SATA HAT", "err",
                          "aucun contrôleur SATA sur le PCIe (dtparam=pciex1 %s dans config.txt)"
                          % ("présent" if "dtparam=pciex1" in active else "ABSENT"),
                          "Vérifie la nappe PCIe et le 12 V du HAT. Si tout est branché : mets « sata_dma32=oui » "
                          "dans /etc/ydz/ydz.conf, relance l'installeur, redémarre."))
        smb = s.run(["systemctl", "is-active", "smbd"], timeout=5).out.strip()
        out.append(_c("samba", "Partages Samba", "ok" if smb == "active" else "warn", "service smbd : %s" % (smb or "?"),
                      None if smb == "active" else "« sudo systemctl status smbd »."))
    if role == "forge":
        root = next((ln.split()[0] for ln in (s.read("/proc/mounts", "") or "").splitlines()
                     if len(ln.split()) > 1 and ln.split()[1] == "/" and ln.startswith("/dev/")), "?")
        nvme = s.exists("/sys/block/nvme0n1")
        if root.startswith("/dev/nvme"):
            out.append(_c("nvme", "SSD NVMe", "ok", "le système tourne sur le SSD (%s)" % root))
        elif nvme:
            out.append(_c("nvme", "SSD NVMe", "info", "SSD présent, mais le système tourne encore sur %s" % root,
                          "Quand tu veux : « sudo ydz migrer-nvme »."))
        else:
            out.append(_c("nvme", "SSD NVMe", "info", "pas de SSD NVMe détecté (système sur %s)" % root,
                          "Normal sans M.2 HAT+. Sinon vérifie la nappe PCIe."))

    # système
    swaps = s.read("/proc/swaps", "") or ""
    out.append(_c("zram", "Mémoire compressée (zram)", "ok" if "zram" in swaps else "warn",
                  "active" if "zram" in swaps else "inactive",
                  None if "zram" in swaps else "Relance l'installeur."))
    ntp = s.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout=5).out.strip()
    out.append(_c("heure", "Horloge", "ok" if ntp == "yes" else "warn",
                  "synchronisée" if ntp == "yes" else "non synchronisée",
                  None if ntp == "yes" else "Sans Internet, l'heure peut dériver ; les ordres du hub restent acceptés."))
    for unit, nom in (("avahi-daemon", "Découverte réseau (avahi)"), ("unattended-upgrades", "Mises à jour de sécurité")):
        a = s.run(["systemctl", "is-active", unit], timeout=5).out.strip()
        out.append(_c(unit, nom, "ok" if a == "active" else "warn", a or "?",
                      None if a == "active" else "« sudo systemctl status %s »." % unit))
    log = s.read("/var/log/ydz/install.log", "") or ""
    fails = [ln for ln in log.splitlines() if "ÉCHEC" in ln][-5:]
    if fails:
        out.append(_c("install", "Installation", "warn", " ; ".join(f[-110:] for f in fails),
                      "Relance « sudo bash /opt/ydz/install.sh » avec Internet branché."))
    elif log:
        out.append(_c("install", "Installation", "ok", "aucune étape en échec"))
    for e in node.apps.errors:
        out.append(_c("catalogue", "Catalogue d'apps", "warn", e))
    return out
