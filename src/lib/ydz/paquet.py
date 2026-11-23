"""Paquets de mise à jour YDZ (.zip) déposés depuis l'interface (Réglages > Mises à jour).

Format d'un paquet :
  YDZ-PAQUET.json   obligatoire : {"nom": "...", "description": "...", "version": "1.0.1" (facultatif)}
  ui/... lib/... bin/... apps/... (même arborescence que /opt/ydz) : fichiers copiés tels quels
  apres.sh          facultatif : script lancé en root après la copie, hors du bac à sable du démon

Sécurité : la route exige le code PIN (accès « action »). Chemins absolus, « .. », liens et
dossiers hors liste sont refusés. Chaque fichier remplacé est d'abord sauvegardé dans
/var/lib/ydz/sauvegardes/<horodatage>/, avec (depuis 1.0.11) un manifeste.json qui liste les fichiers
remplacés ET les fichiers ajoutés : « Annuler ce paquet » remet les premiers et efface les seconds.
Seul le dernier paquet non annulé peut être annulé (on remonte l'historique un paquet à la fois).
"""
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile

TAILLE_MAX = 40 * 1024 * 1024
DOSSIERS = ("ui", "ui-visiteur", "lib", "bin", "apps", "kiosk", "plymouth", "outils", "systemd", "installer", "presets", "payload")
FICHIERS_RACINE = ("VERSION", "YDZ-PAQUET.json", "apres.sh")
SAUVEGARDES = "/var/lib/ydz/sauvegardes"
JOURNAL = "/var/log/ydz/paquets.log"
MANIFESTE = "manifeste.json"
GARDER = 10                       # sauvegardes gardées après une installation réussie
RE_HORO = re.compile(r"^\d{8}-\d{6}(-\d+)?$")
RE_VERSION = re.compile(r"\b(\d+\.\d+\.\d+)\b")


class ErreurPaquet(Exception):
    pass


def _journal(texte):
    try:
        os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + texte.rstrip() + "\n")
    except OSError:
        pass


def _chemin_sur(nom):
    """Renvoie le chemin relatif nettoyé, ou None si le membre doit être refusé."""
    nom = nom.replace("\\", "/")
    if nom.startswith("/") or ":" in nom.split("/")[0]:
        return None
    parts = [p for p in nom.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    if len(parts) == 1:
        return parts[0] if parts[0] in FICHIERS_RACINE else None
    return "/".join(parts) if parts[0] in DOSSIERS else None


def lire(brut):
    """Vérifie le zip et renvoie (infos, [(chemin_relatif, octets, executable)], script_apres)."""
    if not brut:
        raise ErreurPaquet("Fichier vide.")
    if len(brut) > TAILLE_MAX:
        raise ErreurPaquet("Paquet trop gros (40 Mo au maximum).")
    try:
        z = zipfile.ZipFile(io.BytesIO(brut))
    except zipfile.BadZipFile:
        raise ErreurPaquet("Ce fichier n'est pas un .zip valide.")
    noms = [i for i in z.infolist() if not i.is_dir()]
    # un zip créé depuis un dossier « paquet/ » : on retire ce dossier commun
    prefixe = ""
    tetes = {i.filename.replace("\\", "/").split("/")[0] for i in noms}
    if len(tetes) == 1 and not any(i.filename.replace("\\", "/").count("/") == 0 for i in noms):
        prefixe = tetes.pop() + "/"
    fichiers, infos, apres, total = [], None, None, 0
    for i in noms:
        nom = i.filename.replace("\\", "/")
        if prefixe and nom.startswith(prefixe):
            nom = nom[len(prefixe):]
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            raise ErreurPaquet("Lien symbolique refusé : %s" % nom)
        rel = _chemin_sur(nom)
        if rel is None:
            raise ErreurPaquet("Chemin refusé dans le paquet : %s" % nom)
        total += i.file_size
        if total > 4 * TAILLE_MAX:
            raise ErreurPaquet("Paquet trop gros une fois décompressé.")
        data = z.read(i)
        if rel == "YDZ-PAQUET.json":
            try:
                infos = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ErreurPaquet("YDZ-PAQUET.json illisible.")
            if not isinstance(infos, dict) or not isinstance(infos.get("nom"), str):
                raise ErreurPaquet("YDZ-PAQUET.json doit contenir au moins « nom ».")
            continue
        if rel == "apres.sh":
            apres = data
            continue
        exe = bool((i.external_attr >> 16) & 0o111) or rel.startswith(("bin/", "kiosk/", "outils/", "installer/")) or rel.endswith(".sh")
        fichiers.append((rel, data, exe))
    if infos is None:
        raise ErreurPaquet("Ce n'est pas un paquet YDZ (YDZ-PAQUET.json manquant).")
    if not fichiers and apres is None:
        raise ErreurPaquet("Le paquet ne contient rien à installer.")
    return infos, fichiers, apres


def _lire_version(base):
    try:
        with open(os.path.join(base, "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _ecrire_json(chemin, d):
    tmp = chemin + ".ydz-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, chemin)


def _nouvelle_sauvegarde():
    """Dossier de sauvegarde neuf (suffixe -2, -3… si deux paquets tombent dans la même seconde)."""
    horo = time.strftime("%Y%m%d-%H%M%S")
    nom, n = horo, 1
    while os.path.exists(os.path.join(SAUVEGARDES, nom)):
        n += 1
        nom = "%s-%d" % (horo, n)
    os.makedirs(os.path.join(SAUVEGARDES, nom))
    return nom


def installer(base, brut, redemarrer_demon):
    """Copie les fichiers dans base (/opt/ydz), sauvegarde l'ancien, lance apres.sh. Renvoie un résumé."""
    infos, fichiers, apres = lire(brut)
    horo = _nouvelle_sauvegarde()
    sauve = os.path.join(SAUVEGARDES, horo)
    avant = _lire_version(base)
    remplaces, nouveaux = [], []
    manifeste = {"format": 1, "nom": infos.get("nom"), "version": infos.get("version"), "version_avant": avant,
                 "date": int(time.time()), "remplaces": remplaces, "nouveaux": nouveaux, "annulee": None,
                 "en_cours": True}
    # le manifeste est écrit AVANT la copie (marqué « en_cours ») : une coupure en plein milieu laisse
    # quand même de quoi revenir en arrière
    _ecrire_json(os.path.join(sauve, MANIFESTE), manifeste)
    for rel, data, exe in fichiers:
        cible = os.path.join(base, rel)
        if os.path.isfile(cible):
            dest = os.path.join(sauve, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(cible, dest)
            remplaces.append(rel)
        else:
            nouveaux.append(rel)
        os.makedirs(os.path.dirname(cible), exist_ok=True)
        tmp = cible + ".ydz-tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o755 if exe else 0o644)
        os.replace(tmp, cible)
    manifeste["en_cours"] = False
    _ecrire_json(os.path.join(sauve, MANIFESTE), manifeste)
    _journal("paquet « %s » : %d fichier(s) remplacé(s), %d nouveau(x), sauvegarde %s"
             % (infos.get("nom"), len(remplaces), len(nouveaux), sauve))
    try:
        nettoyer()
    except OSError:
        pass
    script = None
    if apres is not None:
        script = os.path.join(sauve, "apres.sh")
        with open(script, "wb") as f:
            f.write(apres)
        os.chmod(script, 0o700)
    touche_demon = any(rel.startswith(("lib/", "bin/")) for rel, _, _ in fichiers)

    if script:
        # apres.sh tourne dans une unité systemd DÉTACHÉE (--no-block) : ni la fin de la requête, ni
        # un redémarrage de ydzd ne peuvent le couper. Sa sortie va dans le journal des paquets, et
        # c'est lui qui redémarre ydzd à la fin si le paquet a changé le démon.
        enveloppe = ('bash "$1"; c=$?; echo "$(date "+%Y-%m-%d %H:%M:%S") apres.sh : fin (code $c)"; '
                     '[ "$2" = 1 ] && systemctl restart ydzd; exit $c')
        cmd = ["systemd-run", "--quiet", "--collect", "--no-block",
               "--unit=ydz-paquet-" + horo,
               "--property=StandardOutput=append:" + JOURNAL,
               "--property=StandardError=append:" + JOURNAL,
               "/bin/bash", "-c", enveloppe, "ydz-paquet", script, "1" if touche_demon else "0"]
        _journal("apres.sh : lancé en tâche de fond (unité ydz-paquet-%s)" % horo)
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as ex:
            _journal("apres.sh : lancement impossible %r" % ex)
    elif touche_demon:
        def suite():
            time.sleep(1.5)
            redemarrer_demon()
        threading.Thread(target=suite, daemon=True).start()
    return {
        "ok": True,
        "nom": infos.get("nom"),
        "description": infos.get("description", ""),
        "fichiers": len(fichiers),
        "remplaces": len(remplaces),
        "nouveaux": len(nouveaux),
        "script": bool(script),
        "redemarrage_demon": touche_demon,
        "sauvegarde": sauve,
    }


# =============================================================================================
# Historique et annulation (1.0.11)
# =============================================================================================
def nettoyer(garder=GARDER):
    """Ne garde que les `garder` sauvegardes les plus récentes (dossiers horodatés seulement)."""
    noms = sorted((n for n in os.listdir(SAUVEGARDES) if RE_HORO.match(n)
                   and os.path.isdir(os.path.join(SAUVEGARDES, n))), key=_cle_horo)
    for n in noms[:-garder] if len(noms) > garder else []:
        shutil.rmtree(os.path.join(SAUVEGARDES, n), ignore_errors=True)


def _cle_horo(nom):
    p = nom.split("-")
    return (p[0], p[1], int(p[2]) if len(p) > 2 else 1)


def _fichiers_sauves(dossier):
    """Fichiers d'une sauvegarde (chemins relatifs), sans le manifeste ni apres.sh."""
    out = []
    for racine, _, noms in os.walk(dossier):
        for n in noms:
            rel = os.path.relpath(os.path.join(racine, n), dossier).replace(os.sep, "/")
            if rel in (MANIFESTE, "apres.sh") or rel.endswith(".ydz-tmp"):
                continue
            out.append(rel)
    return sorted(out)


def _nom_dans_journal(dossier):
    """Pour les sauvegardes d'avant 1.0.11 : le nom du paquet se retrouve dans paquets.log."""
    try:
        with open(JOURNAL, encoding="utf-8", errors="replace") as f:
            for ligne in f:
                if ligne.rstrip().endswith("sauvegarde " + dossier):
                    m = re.search(r"paquet « (.*) » :", ligne)
                    if m:
                        return m.group(1)
    except OSError:
        pass
    return None


def _lire_sauvegarde(nom):
    dossier = os.path.join(SAUVEGARDES, nom)
    man = None
    try:
        with open(os.path.join(dossier, MANIFESTE), encoding="utf-8") as f:
            man = json.load(f)
        if not isinstance(man, dict):
            man = None
    except (OSError, ValueError):
        man = None
    complet = bool(man) and isinstance(man.get("remplaces"), list) and isinstance(man.get("nouveaux"), list)
    if complet:
        remplaces, nouveaux = list(man["remplaces"]), list(man["nouveaux"])
    else:
        remplaces, nouveaux = _fichiers_sauves(dossier), []
    nom_paquet = (man or {}).get("nom") or _nom_dans_journal(dossier)
    version = (man or {}).get("version")
    if not version and nom_paquet:
        m = RE_VERSION.search(nom_paquet)
        version = m.group(1) if m else None
    avant = (man or {}).get("version_avant")
    if not avant and "VERSION" in remplaces:
        try:
            with open(os.path.join(dossier, "VERSION"), encoding="utf-8") as f:
                avant = f.read().strip() or None
        except OSError:
            pass
    try:
        date = int((man or {}).get("date") or time.mktime(time.strptime(nom[:15], "%Y%m%d-%H%M%S")))
    except (ValueError, OverflowError):
        date = None
    return {"id": nom, "date": date, "nom": nom_paquet, "version": version, "version_avant": avant,
            "remplaces": remplaces, "nouveaux": nouveaux, "complet": complet,
            "annulee": (man or {}).get("annulee"), "manifeste": man}


def historique():
    """Sauvegardes, la plus récente d'abord. Seule la plus récente non annulée est « annulable »."""
    try:
        noms = [n for n in os.listdir(SAUVEGARDES) if RE_HORO.match(n) and os.path.isdir(os.path.join(SAUVEGARDES, n))]
    except OSError:
        noms = []
    out, trouvee = [], False
    for n in sorted(noms, key=_cle_horo, reverse=True):
        s = _lire_sauvegarde(n)
        annulable = not trouvee and not s["annulee"] and bool(s["remplaces"] or s["nouveaux"])
        if not s["annulee"]:
            trouvee = True
        out.append({"id": s["id"], "date": s["date"], "nom": s["nom"], "version": s["version"],
                    "version_avant": s["version_avant"], "fichiers": len(s["remplaces"]) + len(s["nouveaux"]),
                    "remplaces": len(s["remplaces"]), "nouveaux": len(s["nouveaux"]), "complet": s["complet"],
                    "annulee": s["annulee"], "annulable": annulable})
    return out


def _sous(base, rel):
    """Chemin absolu de `rel` sous `base`, ou None si `rel` est douteux ou sort de `base`."""
    if not isinstance(rel, str) or _chemin_sur(rel) != rel:
        return None
    base_r = os.path.realpath(base)
    cible = os.path.join(base_r, rel)
    parent = os.path.realpath(os.path.dirname(cible))
    if parent != base_r and not parent.startswith(base_r + os.sep):
        return None
    return cible


def annuler(base, sauvegarde=None, relancer=None):
    """Remet le paquet d'avant : fichiers remplacés restaurés, fichiers ajoutés effacés (seulement ceux
    du manifeste, seulement sous base), VERSION remise. `relancer()` redémarre ydzd et l'écran."""
    liste = historique()
    possible = next((x for x in liste if x["annulable"]), None)
    if not liste:
        raise ErreurPaquet("Aucune sauvegarde de paquet sur cette tour.")
    if sauvegarde is None:
        sauvegarde = possible["id"] if possible else liste[0]["id"]
    if not isinstance(sauvegarde, str) or not RE_HORO.match(sauvegarde) or \
            not any(x["id"] == sauvegarde for x in liste):
        raise ErreurPaquet("Sauvegarde inconnue.")
    if not possible or possible["id"] != sauvegarde:
        raise ErreurPaquet("Seul le dernier paquet installé (et pas encore annulé) peut être annulé. "
                           "Annule d'abord les plus récents, un par un.")
    s = _lire_sauvegarde(sauvegarde)
    dossier = os.path.join(SAUVEGARDES, sauvegarde)
    restaures, effaces, refuses = 0, 0, []
    for rel in s["remplaces"]:
        src, cible = os.path.join(dossier, rel), _sous(base, rel)
        if cible is None or _chemin_sur(rel) != rel or not os.path.isfile(src):
            refuses.append(rel)
            continue
        os.makedirs(os.path.dirname(cible), exist_ok=True)
        tmp = cible + ".ydz-tmp"
        shutil.copy2(src, tmp)
        os.replace(tmp, cible)
        restaures += 1
    for rel in s["nouveaux"]:
        cible = _sous(base, rel)
        if cible is None:
            refuses.append(rel)
            continue
        if rel in s["remplaces"]:
            continue
        if os.path.isfile(cible) and not os.path.islink(cible):
            os.remove(cible)
            effaces += 1
            # dossiers devenus vides (jamais base elle-même)
            d = os.path.dirname(cible)
            base_r = os.path.realpath(base)
            while d.startswith(base_r + os.sep):
                try:
                    os.rmdir(d)
                except OSError:
                    break
                d = os.path.dirname(d)
    if "VERSION" not in s["remplaces"] and s["version_avant"]:
        with open(os.path.join(base, "VERSION"), "w", encoding="utf-8") as f:
            f.write(s["version_avant"] + "\n")
    man = s["manifeste"] if s["complet"] else {"format": 1, "nom": s["nom"], "version": s["version"],
                                                 "version_avant": s["version_avant"], "ancien": True}
    man["annulee"] = int(time.time())
    _ecrire_json(os.path.join(dossier, MANIFESTE), man)
    _journal("annulation du paquet « %s » (sauvegarde %s) : %d fichier(s) remis, %d effacé(s)%s, retour en %s"
             % (s["nom"] or "?", dossier, restaures, effaces,
                ", %d refusé(s)" % len(refuses) if refuses else "", s["version_avant"] or "?"))
    if relancer:
        relancer(sauvegarde)
    return {"ok": True, "sauvegarde": sauvegarde, "nom": s["nom"], "version_avant": s["version_avant"],
            "restaures": restaures, "effaces": effaces, "refuses": refuses, "complet": s["complet"]}


def relancer_apres_annulation(horo):
    """Comme pour un paquet : ydzd redémarre dans une unité systemd détachée, puis l'écran 25 s après."""
    cmds = [["systemd-run", "--quiet", "--collect", "--no-block", "--unit=ydz-annulation-" + horo,
             "--on-active=2", "/bin/systemctl", "restart", "ydzd"],
            ["systemd-run", "--quiet", "--collect", "--no-block", "--on-active=25",
             "/bin/systemctl", "restart", "ydz-kiosk"]]
    for c in cmds:
        try:
            subprocess.run(c, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as ex:
            _journal("annulation : relance impossible %r" % ex)
