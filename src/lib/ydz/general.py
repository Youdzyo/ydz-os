"""Réglages généraux du Pi (1.0.13) : fuseau horaire et son.

Heure : `timedatectl` (systemd-timedated fait le changement : rien à écrire nous-mêmes).
Son   : PipeWire (`wpctl`) si une session PipeWire tourne, sinon ALSA (`aplay -l`, `amixer`). Sur Pi OS Lite,
        il n'y a en général que l'ALSA des sorties HDMI (sans réglage de volume matériel).
"""
import re
import time

from .core import ApiError

FUSEAU_DEFAUT = "Europe/Paris"
RE_FUSEAU = re.compile(r"^(UTC|[A-Z][A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2})$")
ASOUND = "/etc/asound.conf"
MARQUE_ASOUND = "# YDZ OS : sortie son choisie dans Réglages > Général"
CONTROLES = ("Master", "PCM", "Speaker", "Headphone", "Digital", "HDMI")


# --- heure -------------------------------------------------------------------------------------------
class Heure:
    def __init__(self, sys):
        self.sys = sys
        self._liste = None

    def fuseau(self):
        r = self.sys.run(["timedatectl", "show", "-p", "Timezone", "--value"], timeout=6)
        v = r.out.strip() if r.ok else ""
        if not v:
            v = (self.sys.read("/etc/timezone", "") or "").strip()
        return v if v and RE_FUSEAU.match(v) else None

    def fuseaux(self):
        if self._liste is None:
            r = self.sys.run(["timedatectl", "list-timezones", "--no-pager"], timeout=10)
            l = [x.strip() for x in r.out.splitlines() if RE_FUSEAU.match(x.strip())] if r.ok else []
            self._liste = l or None
        return self._liste or []

    def status(self):
        f = self.fuseau()
        t = self.sys.now()
        return {"fuseau": f or FUSEAU_DEFAUT, "fuseau_defini": bool(f), "defaut": FUSEAU_DEFAUT,
                "heure": time.strftime("%H:%M", time.localtime(t)), "decalage": time.strftime("%z", time.localtime(t))}

    def regler(self, fuseau):
        if not isinstance(fuseau, str) or not RE_FUSEAU.match(fuseau):
            raise ApiError(400, "Fuseau horaire invalide (forme attendue : Europe/Paris).", "fuseau")
        liste = self.fuseaux()
        if liste and fuseau not in liste:
            raise ApiError(400, "Fuseau horaire inconnu : %s." % fuseau, "fuseau")
        r = self.sys.run(["timedatectl", "set-timezone", fuseau], timeout=15)
        if not r.ok:
            raise ApiError(500, "Le fuseau n'a pas pu être changé (%s)." % (r.err or "").strip()[:120], "timedatectl")
        if type(self.sys).__name__ == "System":
            try:
                time.tzset()          # le démon suit tout de suite la nouvelle heure locale
            except AttributeError:
                pass
        return self.status()


# --- son ---------------------------------------------------------------------------------------------
def lire_aplay(texte):
    """`aplay -l` -> [{carte, device, nom}] (une entrée par carte)."""
    out, vus = [], set()
    for l in (texte or "").splitlines():
        m = re.match(r"^card (\d+): (\S+) \[(.*?)\], device (\d+): .*?\[(.*?)\]", l)
        if m and m.group(1) not in vus:
            vus.add(m.group(1))
            nom = m.group(3)
            if "hdmi" in m.group(2).lower():
                n = re.search(r"(\d+)$", m.group(2))
                nom = "HDMI %d" % (int(n.group(1)) + 1) if n else "HDMI"
            elif "headphones" in nom.lower():
                nom = "Prise casque"
            out.append({"id": "alsa:%s" % m.group(1), "carte": int(m.group(1)), "nom": nom})
    return out


def lire_amixer(texte):
    """`amixer sget X` -> volume en % (moyenne des canaux), ou None."""
    v = [int(x) for x in re.findall(r"\[(\d{1,3})%\]", texte or "")]
    return round(sum(v) / len(v)) if v else None


def lire_wpctl(texte):
    """`wpctl status` -> sorties (Sinks) de la partie Audio : [{id, nom, defaut, volume}]."""
    out, audio, sinks = [], False, False
    for l in (texte or "").splitlines():
        s = l.strip(" │├└─\t")
        if s in ("Audio", "Video", "Settings"):
            audio, sinks = s == "Audio", False
            continue
        if not audio:
            continue
        if s.startswith("Sinks:"):
            sinks = True
            continue
        if s.endswith(":") and not s.startswith("*"):
            sinks = False
            continue
        if sinks:
            m = re.match(r"^(\*)?\s*(\d+)\.\s+(.*?)(?:\s+\[vol:\s*([\d.]+)(\s+MUTED)?\])?$", s)
            if m:
                out.append({"id": "pw:%s" % m.group(2), "nom": m.group(3).strip(), "defaut": bool(m.group(1)),
                            "volume": round(float(m.group(4)) * 100) if m.group(4) else None})
    return out


class Son:
    def __init__(self, sys):
        self.sys = sys

    def _pipewire(self):
        if not self.sys.which("wpctl"):
            return None
        for sock in self.sys.glob("/run/user/*/pipewire-0"):
            return {"XDG_RUNTIME_DIR": sock.rsplit("/", 1)[0]}
        return None

    def _alsa_controle(self, carte):
        r = self.sys.run(["amixer", "-c", str(carte), "scontrols"], timeout=5)
        noms = re.findall(r"'([^']+)'", r.out or "")
        for c in CONTROLES:
            if c in noms:
                return c
        return None

    def _alsa_defaut(self):
        m = re.search(r"defaults\.pcm\.card\s+(\d+)", self.sys.read(ASOUND, "") or "")
        return int(m.group(1)) if m else 0

    def status(self):
        env = self._pipewire()
        if env:
            r = self.sys.run(["wpctl", "status"], timeout=6, env=env)
            sorties = lire_wpctl(r.out) if r.ok else []
            if sorties:
                d = next((s for s in sorties if s["defaut"]), sorties[0])
                return {"present": True, "systeme": "pipewire", "sorties": sorties, "sortie": d["id"],
                        "volume": d["volume"], "volume_reglable": d["volume"] is not None}
        if not self.sys.which("aplay"):
            return {"present": False}
        sorties = lire_aplay(self.sys.run(["aplay", "-l"], timeout=6).out)
        if not sorties:
            return {"present": False}
        defaut = self._alsa_defaut()
        d = next((s for s in sorties if s["carte"] == defaut), sorties[0])
        ctrl = self._alsa_controle(d["carte"]) if self.sys.which("amixer") else None
        vol = lire_amixer(self.sys.run(["amixer", "-c", str(d["carte"]), "sget", ctrl], timeout=5).out) if ctrl else None
        for s in sorties:
            s["defaut"] = s is d
        return {"present": True, "systeme": "alsa", "sorties": sorties, "sortie": d["id"], "volume": vol,
                "volume_reglable": vol is not None}

    def regler(self, volume=None, sortie=None):
        st = self.status()
        if not st.get("present"):
            raise ApiError(404, "Aucune sortie son sur ce Pi.", "son")
        if sortie is not None:
            s = next((x for x in st["sorties"] if x["id"] == sortie), None)
            if not s:
                raise ApiError(400, "Sortie son inconnue.", "sortie")
            if st["systeme"] == "pipewire":
                self.sys.run(["wpctl", "set-default", sortie.split(":", 1)[1]], timeout=6, env=self._pipewire())
            else:
                actuel = self.sys.read(ASOUND, "") or ""
                if actuel.strip() and MARQUE_ASOUND not in actuel:
                    raise ApiError(409, "/etc/asound.conf a été écrit à la main : YDZ n'y touche pas.", "asound")
                self.sys.write(ASOUND, "%s\ndefaults.pcm.card %d\ndefaults.ctl.card %d\n"
                               % (MARQUE_ASOUND, s["carte"], s["carte"]), mode=0o644)
            st = self.status()
        if volume is not None:
            if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
                raise ApiError(400, "Le volume va de 0 à 100.", "volume")
            if not st.get("volume_reglable"):
                raise ApiError(409, "Le volume de cette sortie ne se règle pas ici (réglage sur l'écran ou l'enceinte).",
                               "volume")
            if st["systeme"] == "pipewire":
                self.sys.run(["wpctl", "set-volume", st["sortie"].split(":", 1)[1], "%.2f" % (volume / 100)],
                             timeout=6, env=self._pipewire())
            else:
                carte = int(st["sortie"].split(":", 1)[1])
                ctrl = self._alsa_controle(carte)
                self.sys.run(["amixer", "-q", "-c", str(carte), "sset", ctrl, "%d%%" % volume], timeout=5)
                self.sys.run(["alsactl", "store"], timeout=10)
            st = self.status()
        return st
