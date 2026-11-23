"""Générateur de QR code en Python pur (1.0.18), bibliothèque standard seule.

Sert à fabriquer le QR code « Wi-Fi invités » : un petit carré que les amis de Justin scannent pour
rejoindre le Wi-Fi sans taper le mot de passe. Le QR est rendu en SVG en ligne (aucun script, aucune
ressource externe : compatible avec la politique CSP de l'interface).

Mode « octet » (byte), niveau de correction M, versions 1 à 10 (largement assez pour une charge Wi-Fi).
Masquage automatique (8 masques, pénalité standard), informations de format et de version (≥ V7) calculées
par BCH. L'implémentation suit la norme ISO/IEC 18004.
"""

# --- corps de Galois GF(256), polynôme primitif 0x11d -------------------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11d
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _poly_generateur(n):
    """Polynôme générateur de Reed-Solomon pour `n` symboles de correction."""
    g = [1]
    for i in range(n):
        g2 = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            g2[j] ^= c
            g2[j + 1] ^= _mul(c, _EXP[i])
        g = g2
    return g


def _reed_solomon(data, n):
    """Codewords de correction (`n` octets) pour la liste d'octets `data`."""
    g = _poly_generateur(n)
    reste = [0] * n
    for d in data:
        facteur = d ^ reste[0]
        reste = reste[1:] + [0]
        if facteur:
            for i in range(n):
                reste[i] ^= _mul(g[i + 1], facteur)
    return reste


# --- tables de la norme (niveau M) -------------------------------------------------------------------
# version -> (codewords de correction par bloc, [(nb de blocs, codewords de données par bloc), ...])
_EC_M = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
    5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)]),
}
# centres des motifs d'alignement par version
_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}
# informations de version (V7..V10), 18 bits (pré-calculées par BCH, chaîne la plus significative d'abord)
_VERSION_INFO = {
    7: 0x07C94, 8: 0x085BC, 9: 0x09A99, 10: 0x0A4D3,
}


def _codewords_donnees(version):
    return sum(nb * cw for nb, cw in _EC_M[version][1])


def _bits_comptage(version):
    return 8 if version <= 9 else 16


def _choisir_version(n_octets):
    """Plus petite version (niveau M, mode octet) qui contient `n_octets`."""
    for v in range(1, 11):
        dispo = _codewords_donnees(v) * 8
        besoin = 4 + _bits_comptage(v) + 8 * n_octets
        if besoin <= dispo:
            return v
    raise ValueError("Charge trop grande pour un QR code (max %d octets)." % 213)


# --- encodage des données (mode octet) ---------------------------------------------------------------
def _bits_vers_octets(bits):
    return [int("".join(bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]


def _codewords(data, version):
    total = _codewords_donnees(version)
    bits = ["0", "1", "0", "0"]                                  # indicateur de mode : octet
    bits += list(format(len(data), "0%db" % _bits_comptage(version)))
    for o in data:
        bits += list(format(o, "08b"))
    # terminateur (jusqu'à 4 zéros), puis alignement sur l'octet
    bits += ["0"] * min(4, total * 8 - len(bits))
    while len(bits) % 8:
        bits.append("0")
    octets = _bits_vers_octets(bits)
    pad = [0xEC, 0x11]
    i = 0
    while len(octets) < total:
        octets.append(pad[i % 2])
        i += 1
    return octets


def _entrelacer(octets, version):
    ec_par_bloc, groupes = _EC_M[version]
    blocs_d, blocs_ec, pos = [], [], 0
    for nb, cw in groupes:
        for _ in range(nb):
            bloc = octets[pos:pos + cw]
            pos += cw
            blocs_d.append(bloc)
            blocs_ec.append(_reed_solomon(bloc, ec_par_bloc))
    out = []
    for i in range(max(len(b) for b in blocs_d)):
        for b in blocs_d:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_par_bloc):
        for b in blocs_ec:
            out.append(b[i])
    return out


# --- matrice ------------------------------------------------------------------------------------------
class _Matrice:
    def __init__(self, taille):
        self.n = taille
        self.m = [[None] * taille for _ in range(taille)]       # None = libre, 0/1 posé
        self.reserve = [[False] * taille for _ in range(taille)]

    def poser(self, x, y, v, fonction=True):
        self.m[y][x] = v
        if fonction:
            self.reserve[y][x] = True


def _motif_detection(mat, x0, y0):
    for dy in range(-1, 8):
        for dx in range(-1, 8):
            x, y = x0 + dx, y0 + dy
            if not (0 <= x < mat.n and 0 <= y < mat.n):
                continue
            if dx in (-1, 7) or dy in (-1, 7):
                v = 0                                            # séparateur
            elif dx in (0, 6) or dy in (0, 6):
                v = 1
            elif 2 <= dx <= 4 and 2 <= dy <= 4:
                v = 1
            else:
                v = 0
            mat.poser(x, y, v)


def _construire(donnees, version):
    n = 17 + 4 * version
    mat = _Matrice(n)
    # motifs de détection de position (3 coins)
    _motif_detection(mat, 0, 0)
    _motif_detection(mat, n - 7, 0)
    _motif_detection(mat, 0, n - 7)
    # motifs de synchronisation (timing)
    for i in range(8, n - 8):
        v = 1 - (i % 2)
        if mat.m[6][i] is None:
            mat.poser(i, 6, v)
        if mat.m[i][6] is None:
            mat.poser(6, i, v)
    # motifs d'alignement
    centres = _ALIGN[version]
    for cy in centres:
        for cx in centres:
            if (cx, cy) in ((6, 6), (6, n - 7), (n - 7, 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    bord = max(abs(dx), abs(dy))
                    mat.poser(cx + dx, cy + dy, 1 if bord != 1 else 0)
    # module sombre
    mat.poser(8, 4 * version + 9, 1)
    # zones réservées : format (autour des coins) et version (V≥7)
    for i in range(9):
        for (x, y) in ((i, 8), (8, i)):
            if mat.m[y][x] is None:
                mat.reserve[y][x] = True
    for i in range(8):
        mat.reserve[8][n - 1 - i] = True
        mat.reserve[n - 1 - i][8] = True
    if version >= 7:
        for i in range(6):
            for j in range(3):
                mat.reserve[n - 11 + j][i] = True
                mat.reserve[i][n - 11 + j] = True
    # placement des données en zigzag (de bas à droite vers le haut)
    bits = []
    for o in donnees:
        bits += [int(b) for b in format(o, "08b")]
    idx = 0
    col = n - 1
    montant = True
    while col > 0:
        if col == 6:
            col -= 1                                             # saute la colonne de synchronisation
        for i in range(n):
            y = (n - 1 - i) if montant else i
            for dx in (0, 1):
                x = col - dx
                if mat.m[y][x] is None and not mat.reserve[y][x]:
                    mat.m[y][x] = bits[idx] if idx < len(bits) else 0
                    idx += 1
        col -= 2
        montant = not montant
    return mat


# --- masquage -----------------------------------------------------------------------------------------
_MASQUES = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (y // 2 + x // 3) % 2 == 0,
    lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
    lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
]


def _appliquer_masque(mat, num):
    n = mat.n
    out = [ligne[:] for ligne in mat.m]
    f = _MASQUES[num]
    for y in range(n):
        for x in range(n):
            if not mat.reserve[y][x] and f(x, y):
                out[y][x] ^= 1
    return out


def _penalite(m):
    n = len(m)
    p = 0
    # règle 1 : suites de 5 modules ou plus de même couleur
    for ligne in (m, list(zip(*m))):
        for r in ligne:
            compte, prec = 1, None
            for v in r:
                if v == prec:
                    compte += 1
                else:
                    if compte >= 5:
                        p += 3 + (compte - 5)
                    compte, prec = 1, v
            if compte >= 5:
                p += 3 + (compte - 5)
    # règle 2 : blocs 2x2 de même couleur
    for y in range(n - 1):
        for x in range(n - 1):
            v = m[y][x]
            if v == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                p += 3
    # règle 3 : motif 1:1:3:1:1 (comme un motif de détection)
    motif = [1, 0, 1, 1, 1, 0, 1]
    for r in (m + [list(c) for c in zip(*m)]):
        for i in range(n - 10):
            seg = r[i:i + 7]
            if seg == motif:
                avant = r[i - 4:i] if i >= 4 else [1, 1, 1, 1]
                apres = r[i + 7:i + 11]
                if (i >= 4 and all(v == 0 for v in avant)) or all(v == 0 for v in apres):
                    p += 40
    # règle 4 : proportion de modules sombres (écart à 50 %, par tranches de 5 %)
    sombres = sum(sum(r) for r in m)
    ratio = sombres * 100.0 / (n * n)
    p += (int(abs(ratio - 50) // 5)) * 10
    return p


# --- informations de format (niveau M = 00) et de version --------------------------------------------
def _bits_format(masque):
    donnees = (0b00 << 3) | masque                              # niveau M (00) + numéro de masque
    reste = donnees << 10
    for i in range(14, 9, -1):
        if reste & (1 << i):
            reste ^= 0b10100110111 << (i - 10)
    bits = ((donnees << 10) | reste) ^ 0b101010000010010
    return bits


def _poser_format(m, reserve, masque):
    n = len(m)
    bits = _bits_format(masque)
    b = [(bits >> i) & 1 for i in range(14, -1, -1)]            # b[0] = bit de poids fort (F14)
    # copie 1 (coin haut-gauche), ordre normalisé (ligne 8 puis colonne 8)
    coords1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
               (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    for k, (y, x) in enumerate(coords1):
        m[y][x] = b[k]
    # copie 2 : F14..F8 en colonne 8 (du bas vers le haut), F7..F0 en ligne 8 (de gauche à droite)
    coords2 = [(n - 1 - i, 8) for i in range(7)] + [(8, n - 8 + i) for i in range(8)]
    for k, (y, x) in enumerate(coords2):
        m[y][x] = b[k]


def _poser_version(m, version):
    if version < 7:
        return
    n = len(m)
    bits = _VERSION_INFO[version]
    for i in range(18):
        b = (bits >> i) & 1
        a, c = i // 3, i % 3
        m[n - 11 + c][a] = b
        m[a][n - 11 + c] = b


# --- interface publique -------------------------------------------------------------------------------
def matrice(payload):
    """Retourne la matrice (liste de listes de 0/1) du QR code encodant `payload` (texte)."""
    data = payload.encode("utf-8")
    version = _choisir_version(len(data))
    octets = _entrelacer(_codewords(data, version), version)
    mat = _construire(octets, version)
    meilleur, score = None, None
    for num in range(8):
        masquee = _appliquer_masque(mat, num)
        _poser_format(masquee, mat.reserve, num)
        _poser_version(masquee, version)
        s = _penalite(masquee)
        if score is None or s < score:
            meilleur, score = masquee, s
    return meilleur


def svg_de_matrice(m, marge=4, module=None, taille=None, titre="QR code Wi-Fi"):
    """Rend une matrice en SVG en ligne (aucun script). `taille` fixe la largeur totale en pixels."""
    n = len(m)
    total_mod = n + 2 * marge
    if taille is not None:
        total = taille
        unite = taille / total_mod
    else:
        unite = module or 8
        total = total_mod * unite
    chemins = []
    for y in range(n):
        x = 0
        while x < n:
            if m[y][x]:
                debut = x
                while x < n and m[y][x]:
                    x += 1
                px = (debut + marge) * unite
                py = (y + marge) * unite
                larg = (x - debut) * unite
                chemins.append("M%s %sh%sv%sh%sz" % (_num(px), _num(py), _num(larg), _num(unite), _num(-larg)))
            else:
                x += 1
    d = "".join(chemins)
    return ('<svg class="qr-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" '
            'width="%s" height="%s" role="img" aria-label="%s" shape-rendering="crispEdges">'
            '<rect width="%s" height="%s" fill="#ffffff"/>'
            '<path d="%s" fill="#000000"/></svg>') % (
        _num(total), _num(total), _num(total), _num(total), _echap(titre),
        _num(total), _num(total), d)


def _num(v):
    i = round(v, 3)
    return str(int(i)) if i == int(i) else ("%.3f" % i).rstrip("0").rstrip(".")


def _echap(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def svg(payload, **kw):
    """Fabrique directement le SVG du QR code pour `payload`."""
    return svg_de_matrice(matrice(payload), **kw)


# --- charge Wi-Fi -------------------------------------------------------------------------------------
def echapper_wifi(v):
    """Échappe un champ pour la charge WIFI: (caractères spéciaux \\ ; , : " précédés d'un \\)."""
    out = []
    for c in str(v):
        if c in "\\;,:\"":
            out.append("\\")
        out.append(c)
    return "".join(out)


def charge_wifi(ssid, mdp, securite="WPA"):
    """Charge standard d'un QR Wi-Fi : WIFI:T:WPA;S:<ssid>;P:<mdp>;;
    `securite` : WPA (WPA/WPA2/WPA3), nopass (réseau ouvert)."""
    t = "nopass" if securite == "nopass" or not mdp else "WPA"
    corps = "WIFI:T:%s;S:%s;" % (t, echapper_wifi(ssid))
    if t != "nopass":
        corps += "P:%s;" % echapper_wifi(mdp)
    return corps + ";"
