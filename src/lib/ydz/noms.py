"""Noms du labo (1.0.13) : « Home Pi Lab » pour l'ensemble, « Pi One », « Pi Two »… pour chaque Raspberry Pi.

YDZ OS reste le nom du logiciel. Les rôles internes (forge, vault, pulse) ne changent pas : seuls les noms
montrés à l'utilisateur changent. Les noms d'affichage sont rangés dans l'état du hub :

  labo = {"nom": "Home Pi Lab", "noeuds": {"pi-one": {"nom": "Pi One", "numero": 1}, …}}

La clé est le nom d'hôte réseau du nœud. Un nœud qui en remplace un autre hérite de son nom, de son numéro
et (si possible) de son nom d'hôte ; l'ancien quitte le labo.

Changer le nom d'hôte se fait hors du bac à sable de ydzd (ProtectHostname) : script bin/ydz-nom-hote lancé
par systemd-run. Il note l'ancien nom dans FICHIER_ANCIENS : le hub s'en sert pour garder l'historique et
pour ne pas prendre son ancien nom pour un autre nœud.
"""
import re
import unicodedata

from .core import ApiError

NOM_LABO = "Home Pi Lab"
FICHIER_ANCIENS = "/var/lib/ydz/noms-hote-anciens"
RE_HOTE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
INTERDITS = {"localhost", "local", "ydz", "broadcasthost"}
CHIFFRES = ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
            "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen", "Twenty"]


def nom_par_numero(n):
    """1 -> « Pi One », 2 -> « Pi Two »… (au-delà de 20 : « Pi 21 »)."""
    return "Pi %s" % (CHIFFRES[n - 1] if 1 <= n <= len(CHIFFRES) else n)


def premier_libre(pris):
    n = 1
    while n in pris:
        n += 1
    return n


def slug(nom):
    """« Pi One » -> « pi-one » ; accents retirés, 32 caractères au plus."""
    t = unicodedata.normalize("NFKD", str(nom or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:32].strip("-")


def valider_hote(h):
    """Message d'erreur (français) ou None si le nom d'hôte est acceptable."""
    if not isinstance(h, str) or not h:
        return "Choisis un nom d'hôte."
    if len(h) > 32:
        return "32 caractères au plus pour le nom d'hôte."
    if not RE_HOTE.match(h):
        return "Nom d'hôte : lettres minuscules sans accent, chiffres et tirets seulement (pas au début ni à la fin)."
    if h in INTERDITS or h.isdigit():
        return "Ce nom d'hôte est réservé, choisis-en un autre."
    return None


def valider_nom(nom, champ="nom", maxi=32):
    if not isinstance(nom, str) or not nom.strip():
        raise ApiError(400, "Le nom ne peut pas être vide.", champ)
    nom = " ".join(nom.split())
    if len(nom) > maxi:
        raise ApiError(400, "%d caractères au plus." % maxi, champ)
    if any(c in nom for c in "<>\"\\") or any(unicodedata.category(c).startswith("C") for c in nom):
        raise ApiError(400, "Ce nom contient un caractère non permis.", champ)
    return nom


def lire_anciens(texte):
    return [l.strip() for l in (texte or "").splitlines() if RE_HOTE.match(l.strip())]


class Noms:
    """Noms d'affichage des nœuds, vus par le hub."""

    def __init__(self, sys, state, moi, anciens=None):
        self.sys, self.state, self.moi = sys, state, moi
        self.anciens = [a for a in (anciens if anciens is not None else
                                    lire_anciens(sys.read(FICHIER_ANCIENS, ""))) if a != moi]
        self._initialiser()

    # --- état -----------------------------------------------------------------------------------
    def _labo(self):
        l = self.state.get("labo")
        return dict(l) if isinstance(l, dict) else {}

    def _initialiser(self):
        """Premier démarrage : « Home Pi Lab », le hub est « Pi One ». Après un renommage du hub, son entrée
        suit son nouveau nom d'hôte."""
        l = self._labo()
        nds = {k: dict(v) for k, v in (l.get("noeuds") or {}).items() if isinstance(v, dict)}
        change = not l
        if self.moi not in nds:
            for a in self.anciens:
                if a in nds:
                    nds[self.moi] = nds.pop(a)
                    change = True
                    break
        if self.moi not in nds:
            pris = {v.get("numero") for v in nds.values()}
            num = 1 if 1 not in pris else premier_libre(pris)
            nds[self.moi] = {"nom": nom_par_numero(num), "numero": num}
            change = True
        if change:
            self.state.set("labo", {"nom": l.get("nom") or NOM_LABO, "noeuds": nds})

    @property
    def labo(self):
        return self._labo().get("nom") or NOM_LABO

    def noeuds(self):
        return {k: dict(v) for k, v in (self._labo().get("noeuds") or {}).items()}

    def _ecrire(self, nds, nom=None):
        self.state.set("labo", {"nom": nom or self.labo, "noeuds": nds})

    def assurer(self, noms_connus):
        """Donne un nom (premier numéro libre) aux nœuds connus qui n'en ont pas encore."""
        nds = self.noeuds()
        nouveaux = [n for n in noms_connus if n not in nds]
        if not nouveaux:
            return nds
        for n in nouveaux:
            num = premier_libre({v.get("numero") for v in nds.values()})
            nds[n] = {"nom": nom_par_numero(num), "numero": num}
        self._ecrire(nds)
        return nds

    def affiche(self, hote):
        """Nom montré à l'utilisateur ; à défaut, le nom d'hôte en majuscules (comme avant 1.0.13)."""
        if not hote:
            return ""
        v = self.noeuds().get(hote)
        if v and v.get("nom"):
            return v["nom"]
        if hote in self.anciens:
            return self.affiche(self.moi)
        return str(hote).upper()

    def provisoires(self, hotes):
        """Noms proposés pour des emplacements libres (pas encore enregistrés)."""
        nds = self.noeuds()
        pris = {v.get("numero") for v in nds.values()}
        out = {}
        ordre = ("forge", "vault", "pulse")       # emplacements prévus : stockage avant réseau
        for h in sorted(hotes, key=lambda x: (ordre.index(x) if x in ordre else 9, x)):
            if h in nds:
                continue
            num = premier_libre(pris)
            pris.add(num)
            out[h] = nom_par_numero(num)
        return out

    def status(self, hotes=()):
        nds = self.noeuds()
        noms = {k: v.get("nom") for k, v in nds.items()}
        noms.update({k: v for k, v in self.provisoires([h for h in hotes if h not in nds]).items()})
        return {"nom": self.labo, "hub": self.moi, "noms": noms,
                "numeros": {k: v.get("numero") for k, v in nds.items()}, "anciens_noms_hote": list(self.anciens)}

    # --- changements ----------------------------------------------------------------------------------
    def renommer_labo(self, nom):
        nom = valider_nom(nom, "labo", 40)
        self._ecrire(self.noeuds(), nom)
        return nom

    def renommer(self, hote, nom):
        nom = valider_nom(nom)
        nds = self.noeuds()
        if hote not in nds:
            raise ApiError(404, "Pi inconnu dans le labo.", "noeud")
        if any(k != hote and (v.get("nom") or "").lower() == nom.lower() for k, v in nds.items()):
            raise ApiError(409, "Un autre Pi s'appelle déjà « %s »." % nom, "nom")
        nds[hote]["nom"] = nom
        self._ecrire(nds)
        return nom

    def remplacer(self, nouveau, ancien):
        """`nouveau` prend le nom et le numéro d'`ancien`, qui quitte le labo."""
        nds = self.noeuds()
        if ancien not in nds:
            raise ApiError(404, "Le Pi à remplacer n'est pas dans le labo.", "remplace")
        if ancien == self.moi:
            raise ApiError(409, "Le Pi qui fait tourner l'interface ne peut pas être remplacé d'ici.", "remplace")
        herite = nds.pop(ancien)
        nds.pop(nouveau, None)
        nds[nouveau] = herite
        self._ecrire(nds)
        return herite

    def deplacer(self, ancien_hote, nouvel_hote):
        """Le nom d'hôte d'un nœud a changé : ses nom et numéro le suivent."""
        nds = self.noeuds()
        if ancien_hote in nds and ancien_hote != nouvel_hote:
            nds[nouvel_hote] = nds.pop(ancien_hote)
            self._ecrire(nds)

    def retirer(self, hote):
        nds = self.noeuds()
        if nds.pop(hote, None) is not None:
            self._ecrire(nds)


def commande_renommage(base, nouveau, role=None, relancer=True):
    """Commande systemd-run du script de renommage (hors bac à sable de ydzd)."""
    err = valider_hote(nouveau)
    if err:
        raise ValueError(err)
    argv = ["systemd-run", "--unit=ydz-nom-hote", "--collect", "--quiet", "--on-active=3",
            "--description=YDZ : nouveau nom d'hôte", base + "/bin/ydz-nom-hote", nouveau]
    if role:
        argv += ["--role", role]
    if not relancer:
        argv.append("--sans-relance")
    return argv
