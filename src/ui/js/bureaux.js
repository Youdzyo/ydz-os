// YDZ — « Les Bureaux de Claude » : une scène pixel-art, dessinée entièrement en code (aucun asset
// externe, rien de © Pokémon) sur un seul <canvas> basse résolution agrandi avec image-rendering:
// pixelated. ~10 images/s, en pause quand la page n'est pas affichée, chemins précalculés sur une
// grille (A* simple), personnages composés par calques + échange de palette et mis en cache.
//
// Perf (Pi 5) : seul le canvas bouge (pas de DOM par image) ; le décor fixe est dessiné une fois dans
// un canvas hors écran et recopié ; chaque image d'un personnage est pré-rendue une fois par apparence.
// prefers-reduced-motion : la scène se fige (une image fixe, pas d'animation).

const LARGEUR = 384, HAUTEUR = 216;      // résolution logique (×5 = 1920×1080)
const CELL = 12;                          // grille de déplacement : 32×18 cellules
const COLS = LARGEUR / CELL, LIGNES = HAUTEUR / CELL;
const IPS = 10;                           // images par seconde

const sansMouvement = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// ------------------------------------------------------------------- thèmes (jour/nuit + saison)
function theme(reglages) {
  const d = new Date();
  const h = d.getHours();
  const noel = d.getMonth() === 11;                       // décembre : thème de Noël
  const nuit = h >= 20 || h < 7;
  const aube = h >= 7 && h < 9, soir = h >= 18 && h < 20;
  let sol, mur, lumiere;
  if (nuit) { sol = "#20222e"; mur = "#171922"; lumiere = "#2b2e3d"; }
  else if (aube || soir) { sol = "#3a3242"; mur = "#2c2634"; lumiere = "#4a3f4e"; }
  else { sol = "#34384a"; mur = "#262a38"; lumiere = "#3d4256"; }
  return { nuit, noel, sol, mur, lumiere,
           saison: noel ? "noel" : d.getMonth() >= 9 ? "automne" : d.getMonth() >= 5 ? "ete" : d.getMonth() >= 2 ? "printemps" : "hiver",
           forcer: reglages?.theme };
}

// ------------------------------------------------------------------- disposition (postes, murs…)
// Cases bloquées (obstacles) : bureaux, serveurs, murs, bureau du directeur. Tout le reste est marchable.
function disposition(nbAgents) {
  const postes = [
    { x: 150, y: 70 }, { x: 214, y: 70 }, { x: 150, y: 130 }, { x: 214, y: 130 },
    { x: 150, y: 184 }, { x: 214, y: 184 },
  ].slice(0, Math.max(1, Math.min(6, nbAgents)));
  return {
    postes,
    mur: { tache: { x: 14, y: 26, l: 74, h: 86 }, fait: { x: 14, y: 128, l: 74, h: 74 } },
    serveur: { x: 312, y: 84, l: 60, h: 120 },
    cafe: { x: 24, y: 150 },                               // machine à café
    directeur: { bureau: { x: 292, y: 10, l: 84, h: 58 }, poste: { x: 318, y: 50 }, sortie: { x: 300, y: 76 } },
    plantes: [{ x: 108, y: 54 }, { x: 270, y: 150 }, { x: 108, y: 170 }, { x: 264, y: 46 }],
  };
}

// Grille des obstacles pour A*.
function grilleObstacles(disp) {
  const bloque = new Set();
  const marque = (x, y, l, h) => {
    for (let cy = Math.floor(y / CELL); cy <= Math.floor((y + h) / CELL); cy++)
      for (let cx = Math.floor(x / CELL); cx <= Math.floor((x + l) / CELL); cx++)
        bloque.add(cy * COLS + cx);
  };
  marque(0, 0, LARGEUR, 40);                               // mur du fond
  marque(disp.serveur.x, disp.serveur.y, disp.serveur.l, disp.serveur.h);
  marque(disp.directeur.bureau.x, disp.directeur.bureau.y, disp.directeur.bureau.l, disp.directeur.bureau.h);
  for (const p of disp.postes) marque(p.x - 10, p.y - 4, 28, 14);  // le bureau lui-même
  return bloque;
}

// ------------------------------------------------------------------- A* (chemins précalculés)
function astar(bloque, depart, but) {
  const cle = (c) => c.y * COLS + c.x;
  const marchable = (x, y) => x >= 0 && y >= 4 && x < COLS && y < LIGNES && !bloque.has(y * COLS + x);
  if (!marchable(but.x, but.y)) {                          // but dans un obstacle : on vise la case libre la plus proche
    let best = null, bd = 1e9;
    for (let y = 4; y < LIGNES; y++) for (let x = 0; x < COLS; x++) {
      if (!marchable(x, y)) continue;
      const d = Math.abs(x - but.x) + Math.abs(y - but.y);
      if (d < bd) { bd = d; best = { x, y }; }
    }
    but = best || depart;
  }
  const ouvert = [{ ...depart, g: 0, f: 0, p: null }];
  const vus = new Map();
  while (ouvert.length) {
    ouvert.sort((a, b) => a.f - b.f);
    const n = ouvert.shift();
    if (n.x === but.x && n.y === but.y) {
      const ch = []; let c = n; while (c) { ch.unshift({ x: c.x * CELL + CELL / 2, y: c.y * CELL + CELL / 2 }); c = c.p; }
      return ch;
    }
    if (vus.has(cle(n)) && vus.get(cle(n)) <= n.g) continue;
    vus.set(cle(n), n.g);
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = n.x + dx, ny = n.y + dy;
      if (!marchable(nx, ny)) continue;
      const g = n.g + 1, h = Math.abs(nx - but.x) + Math.abs(ny - but.y);
      ouvert.push({ x: nx, y: ny, g, f: g + h, p: n });
    }
  }
  return [{ x: depart.x * CELL + CELL / 2, y: depart.y * CELL + CELL / 2 }];
}

const enCase = (px) => ({ x: Math.max(0, Math.min(COLS - 1, Math.floor(px.x / CELL))), y: Math.max(4, Math.min(LIGNES - 1, Math.floor(px.y / CELL))) });

// ------------------------------------------------------------------- personnages (calques + palette, cachés)
// Chaque image est un petit canvas pré-rendu une fois par apparence. On réutilise ensuite.
function spriteCanvas(l, h) {
  const c = document.createElement("canvas"); c.width = l; c.height = h;
  return c;
}
const L_PERSO = 14, H_PERSO = 20;

function dessinerPerso(ctx, perso, pose, pas) {
  // pose : "bas"|"haut"|"cote" (marche/assis), pas : 0|1 pour l'alternance des jambes/bras
  const peau = perso?.peau || "#E8B98A", cheveux = perso?.cheveux || "#5A3A22";
  const vet = perso?.vetements || "#5AA9E6", acc = perso?.accessoire || "aucun";
  const px = (x, y, w, h, col) => { ctx.fillStyle = col; ctx.fillRect(x, y, w, h); };
  const cx = 4;
  // jambes (alternées à la marche)
  px(cx + 1, 16, 2, 4, "#2b2f3b"); px(cx + 5, 16, 2, 4, "#2b2f3b");
  if (pose === "cote") { px(cx + 3, 16 + (pas ? 1 : 0), 2, 4 - (pas ? 1 : 0), "#2b2f3b"); }
  // torse (vêtements)
  px(cx, 10, 8, 7, vet);
  // bras (pour « tape » : main qui monte/descend)
  if (pose === "assis") { px(cx - 1, 11, 2, 3 + (pas ? 1 : 0), vet); px(cx + 7, 11, 2, 3 + (pas ? 0 : 1), vet); }
  else { px(cx - 1, 11, 2, 4, vet); px(cx + 7, 11, 2, 4, vet); }
  // tête
  px(cx + 1, 3, 6, 7, peau);
  // cheveux
  px(cx + 1, 2, 6, 3, cheveux); if (pose !== "haut") px(cx + 1, 3, 1, 3, cheveux), px(cx + 6, 3, 1, 3, cheveux);
  // yeux (face / profil), seulement si on voit le visage
  if (pose === "bas" || pose === "assis") { px(cx + 2, 6, 1, 1, "#20222e"); px(cx + 5, 6, 1, 1, "#20222e"); }
  else if (pose === "cote") px(cx + 5, 6, 1, 1, "#20222e");
  // accessoires
  if (acc === "lunettes" && (pose === "bas" || pose === "assis")) { px(cx + 1, 6, 2, 1, "#101018"); px(cx + 4, 6, 2, 1, "#101018"); px(cx + 3, 6, 1, 1, "#101018"); }
  if (acc === "casquette") { px(cx, 2, 8, 2, vet); px(cx + 5, 3, 4, 1, vet); }
  if (acc === "casque") { px(cx, 4, 1, 3, "#20222e"); px(cx + 7, 4, 1, 3, "#20222e"); px(cx + 1, 1, 6, 1, "#20222e"); }
}

const POSES = ["bas", "haut", "cote", "cote1", "assis", "assis1"];
function construireSprites(perso) {
  const out = {};
  for (const p of POSES) {
    const c = spriteCanvas(L_PERSO, H_PERSO), x = c.getContext("2d");
    const base = p.replace("1", "");
    dessinerPerso(x, perso, base, p.endsWith("1") ? 1 : 0);
    out[p] = c;
  }
  return out;
}

// Aperçu en direct d'un seul personnage (éditeur de personnalisation dans l'app).
export function apercuPerso(canvas) {
  const ctx = canvas.getContext("2d"); ctx.imageSmoothingEnabled = false;
  const L = 32, H = 40; canvas.width = L; canvas.height = H;
  let sprites = null, sig = "", t = 0, timer = null;
  function img(ts) {
    if (!timer) return;
    t++;
    ctx.clearRect(0, 0, L, H);
    ctx.fillStyle = "#2a2d3a"; ctx.fillRect(0, 0, L, H);
    ctx.fillStyle = "#1d6b4f"; for (let k = 0; k < 4; k++) ctx.fillRect(6, 26 + ((Math.floor(t / 6) + k) % 7) - 2, 3 + (k * 2) % 9, 1);
    if (sprites) { const pose = Math.floor(t / 8) % 2 ? "assis1" : "assis"; ctx.drawImage(sprites[pose], (L - L_PERSO) / 2, H - H_PERSO - 2); }
    timer = requestAnimationFrame(img);
  }
  return {
    maj(perso) { const s = JSON.stringify(perso); if (s !== sig) { sprites = construireSprites(perso); sig = s; } },
    demarrer() { if (!timer) timer = requestAnimationFrame(img); },
    arreter() { if (timer) { cancelAnimationFrame(timer); timer = null; } },
  };
}

// ------------------------------------------------------------------- un agent animé
class Pion {
  constructor(id, directeur = false) {
    this.id = id; this.directeur = directeur;
    this.pos = { x: directeur ? 318 : 150, y: directeur ? 50 : 70 };
    this.chemin = null; this.etape = 0; this.pose = "bas"; this.pas = 0;
    this.action = "idle";            // idle | versMur | versPoste | tape | pause | aide | retour
    this.bulle = null; this.carte = false; this.minuteur = 0; this.sprites = null;
    this.apparence = ""; this.ecran = 0;
  }
  majApparence(perso) {
    const sig = JSON.stringify(perso || {}) + (this.directeur ? "D" : "");
    if (sig !== this.apparence) { this.sprites = construireSprites(this.directeur ? { peau: "#F0D0B0", cheveux: "#3a3a44", vetements: "#2b2f3b", accessoire: "lunettes" } : perso); this.apparence = sig; }
  }
  versPixel(cible, bloque) { this.chemin = astar(bloque, enCase(this.pos), enCase(cible)); this.etape = 1; }
  avancer(v = 2.2) {
    if (!this.chemin || this.etape >= this.chemin.length) { this.chemin = null; return true; }
    const c = this.chemin[this.etape];
    const dx = c.x - this.pos.x, dy = c.y - this.pos.y, dist = Math.hypot(dx, dy);
    if (dist <= v) { this.pos.x = c.x; this.pos.y = c.y; this.etape++; }
    else { this.pos.x += (dx / dist) * v; this.pos.y += (dy / dist) * v; }
    this.pose = Math.abs(dx) > Math.abs(dy) ? "cote" : dy < 0 ? "haut" : "bas";
    return this.etape >= this.chemin.length;
  }
}

// ------------------------------------------------------------------- la scène
export function creerScene(canvas) {
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  const fond = spriteCanvas(LARGEUR, HAUTEUR);
  const fctx = fond.getContext("2d");
  let disp = disposition(3), bloque = grilleObstacles(disp), thm = null, fondSig = "";
  const pions = new Map();          // id -> Pion
  let directeur = new Pion("__dir__", true);
  let flocons = [];
  let derniereData = { equipe: [], distants: [], reglages: {} };

  // --- décor fixe (recalculé seulement si le thème ou la disposition changent) ---
  function dessinerFond() {
    const t = thm;
    fctx.fillStyle = t.sol; fctx.fillRect(0, 0, LARGEUR, HAUTEUR);
    // damier du sol
    for (let y = 40; y < HAUTEUR; y += 12) for (let x = 0; x < LARGEUR; x += 12) {
      if (((x + y) / 12) % 2 === 0) { fctx.fillStyle = t.lumiere; fctx.globalAlpha = 0.25; fctx.fillRect(x, y, 12, 12); fctx.globalAlpha = 1; }
    }
    // mur du fond + fenêtres
    fctx.fillStyle = t.mur; fctx.fillRect(0, 0, LARGEUR, 40);
    for (let x = 20; x < 280; x += 60) { fctx.fillStyle = t.nuit ? "#2b3a5a" : "#7fb6e8"; fctx.fillRect(x, 8, 34, 22); fctx.fillStyle = t.mur; fctx.fillRect(x + 16, 8, 2, 22); fctx.fillRect(x, 18, 34, 2); }
    if (t.nuit) for (let x = 24; x < 280; x += 17) { fctx.fillStyle = "#cfe0ff"; fctx.globalAlpha = Math.random() * 0.6 + 0.2; fctx.fillRect(x, 10 + (x % 14), 1, 1); fctx.globalAlpha = 1; }
    // tableau des tâches + colonne Fait
    const cadre = (r, titre, col) => {
      fctx.fillStyle = "#4a3a2a"; fctx.fillRect(r.x - 2, r.y - 2, r.l + 4, r.h + 4);
      fctx.fillStyle = col; fctx.fillRect(r.x, r.y, r.l, r.h);
    };
    cadre(disp.mur.tache, "Tâches", "#caa36b");
    cadre(disp.mur.fait, "Fait", "#8fae7a");
    // bureaux
    for (const p of disp.postes) {
      fctx.fillStyle = "#6b4f34"; fctx.fillRect(p.x - 10, p.y - 2, 28, 10);
      fctx.fillStyle = "#3a2d1e"; fctx.fillRect(p.x - 10, p.y + 7, 28, 2);
      fctx.fillStyle = "#15171f"; fctx.fillRect(p.x + 1, p.y - 12, 14, 10);  // écran
      fctx.fillStyle = "#2a2d3a"; fctx.fillRect(p.x, p.y - 2, 2, 2);          // pied d'écran
    }
    // salle serveur
    const s = disp.serveur;
    fctx.fillStyle = "#14161e"; fctx.fillRect(s.x, s.y, s.l, s.h);
    fctx.fillStyle = "#0c0d13";
    for (let y = s.y + 4; y < s.y + s.h - 4; y += 14) { fctx.fillRect(s.x + 4, y, s.l - 8, 11); }
    // machine à café
    const cf = disp.cafe;
    fctx.fillStyle = "#2a2d3a"; fctx.fillRect(cf.x, cf.y, 14, 18);
    fctx.fillStyle = "#15171f"; fctx.fillRect(cf.x + 2, cf.y + 4, 10, 6);
    fctx.fillStyle = "#6b4f34"; fctx.fillRect(cf.x + 4, cf.y + 12, 6, 3);
    // bureau en verre du directeur (= Claude)
    const db = disp.directeur.bureau;
    fctx.fillStyle = t.nuit ? "#1c2740" : "#aee0e8"; fctx.globalAlpha = 0.35; fctx.fillRect(db.x, db.y, db.l, db.h); fctx.globalAlpha = 1;
    fctx.strokeStyle = "#8fd3dd"; fctx.lineWidth = 1; fctx.strokeRect(db.x + 0.5, db.y + 0.5, db.l - 1, db.h - 1);
    fctx.fillStyle = "#6b4f34"; fctx.fillRect(disp.directeur.poste.x - 10, disp.directeur.poste.y - 2, 24, 8);
    fctx.fillStyle = "#15171f"; fctx.fillRect(disp.directeur.poste.x - 2, disp.directeur.poste.y - 12, 12, 9);
    // plantes
    for (const pl of disp.plantes) {
      fctx.fillStyle = "#7a5a3a"; fctx.fillRect(pl.x, pl.y + 8, 7, 5);
      fctx.fillStyle = t.noel ? "#3a8f4a" : "#4f9e5a"; fctx.fillRect(pl.x - 1, pl.y, 9, 9);
      if (t.noel) { fctx.fillStyle = "#e5484d"; fctx.fillRect(pl.x + 1, pl.y + 2, 1, 1); fctx.fillStyle = "#e8c33d"; fctx.fillRect(pl.x + 5, pl.y + 4, 1, 1); }
    }
  }

  function majFond() {
    const sig = `${thm.sol}|${thm.saison}|${disp.postes.length}|${thm.nuit}`;
    if (sig !== fondSig) { dessinerFond(); fondSig = sig; }
  }

  // --- état des agents -> comportement des pions ---
  function majAgents(equipe, distants, reglages) {
    derniereData = { equipe, distants, reglages };
    const vus = new Set();
    equipe.forEach((a, i) => {
      vus.add(a.id);
      let p = pions.get(a.id);
      if (!p) { p = new Pion(a.id); p.poste = disp.postes[i % disp.postes.length]; p.pos = { ...(p.poste || { x: 150, y: 70 }) }; pions.set(a.id, p); }
      p.poste = disp.postes[i % disp.postes.length] || p.poste;
      p.majApparence(a.perso);
      p.couleur = a.couleur; p.nom = a.nom;
      reagir(p, a.run || {});
    });
    // agents distants (« depuis l'appli Claude ») : des pions marqués d'un badge
    distants.forEach((a, i) => {
      const id = "d:" + a.nom;
      vus.add(id);
      let p = pions.get(id);
      if (!p) { p = new Pion(id); p.distant = true; p.poste = disp.postes[(equipe.length + i) % disp.postes.length]; p.pos = { x: 300, y: 56 }; pions.set(id, p); }
      p.majApparence({ peau: "#F0D0B0", cheveux: "#43384f", vetements: a.couleur || "#B57BE0", accessoire: "casque" });
      p.couleur = a.couleur; p.nom = a.nom; p.distant = true;
      reagirDistant(p, a);
    });
    for (const id of [...pions.keys()]) if (!vus.has(id)) pions.delete(id);
  }

  function reagir(p, run) {
    const e = run.etat || "en_attente";
    if (e !== p.dernierEtat) {
      p.dernierEtat = e;
      if (e === "travaille") { p.action = "versMur"; p.versPixel({ x: disp.mur.tache.x + 36, y: disp.mur.tache.y + disp.mur.tache.h - 8 }, bloque); p.bulle = "…"; }
      else if (e === "termine") { p.action = "versFait"; p.carte = true; p.versPixel({ x: disp.mur.fait.x + 36, y: disp.mur.fait.y + 10 }, bloque); p.bulle = "✓"; }
      else if (e === "besoin_aide") { p.bulle = "?"; p.action = "tape"; }
      else if (e === "erreur") { p.bulle = "!"; p.action = "tape"; }
      else { p.bulle = null; p.action = "idle"; }
    }
    p.progression = run.progression || 0;
  }
  function reagirDistant(p, a) {
    if (!p.poste) return;
    if (a.termine) { p.bulle = "✓"; p.action = "idle"; }
    else { p.bulle = "…"; if (p.action === "idle") { p.action = "versPoste"; p.versPixel(p.poste, bloque); } }
  }

  // --- une image ---
  let tic = 0;
  function pas() {
    tic++;
    const bougeOk = !sansMouvement();
    // directeur intervient quand un agent est bloqué (? ou !)
    const bloqueAgent = [...pions.values()].find((p) => !p.distant && (p.bulle === "?" || p.bulle === "!"));
    gererDirecteur(bloqueAgent, bougeOk);
    for (const p of pions.values()) {
      if (!bougeOk) { p.pas = 0; continue; }
      if (p.chemin) {
        const fini = p.avancer();
        if (fini) terminerDeplacement(p);
      } else if (p.action === "tape" || (p.poste && Math.hypot(p.pos.x - p.poste.x, p.pos.y - p.poste.y) < 3)) {
        p.pose = "assis"; if (tic % 4 === 0) { p.pas ^= 1; p.ecran = (p.ecran + 1) % 6; }
      } else if (p.action === "pause") {
        p.pose = "cote";
      }
      if (p.chemin && tic % 3 === 0) p.pas ^= 1;
    }
    // café : un agent oisif va parfois à la pause
    if (bougeOk && tic % 80 === 0) {
      const oisif = [...pions.values()].find((p) => !p.distant && p.action === "idle" && (!p.poste || Math.hypot(p.pos.x - p.poste.x, p.pos.y - p.poste.y) < 4) && Math.random() < 0.5);
      if (oisif) { oisif.action = "pause"; oisif.bulle = "☕"; oisif.versPixel({ x: disp.cafe.x + 7, y: disp.cafe.y + 22 }, bloque); }
    }
    if (thm.noel && bougeOk) majFlocons();
    dessiner();
  }

  function terminerDeplacement(p) {
    if (p.action === "versMur") { p.carte = true; p.action = "versPoste"; p.versPixel(p.poste, bloque); }
    else if (p.action === "versPoste") { p.action = "tape"; }
    else if (p.action === "versFait") { p.carte = false; p.bulle = "✓"; p.action = "versPoste"; p.versPixel(p.poste, bloque); }
    else if (p.action === "pause") { setTimeout(() => { if (p.action === "pause") { p.bulle = null; p.action = "idle"; p.versPixel(p.poste, bloque); } }, 2600); p.action = "pauseAttend"; }
    else if (p.action === "pauseAttend") { /* reste */ }
    else p.action = "tape";
  }

  function gererDirecteur(cible, bougeOk) {
    const d = directeur;
    if (!bougeOk) return;
    if (cible && d.etat !== "versAgent" && d.etat !== "parle") {
      d.etat = "versAgent"; d.cible = cible;
      d.versPixel({ x: cible.pos.x - 16, y: cible.pos.y }, bloque);
    }
    if (d.chemin) { if (d.avancer()) { if (d.etat === "versAgent") { d.etat = "parle"; d.minuteur = 40; } else if (d.etat === "retour") { d.etat = "idle"; d.pose = "bas"; } } if (tic % 3 === 0) d.pas ^= 1; return; }
    if (d.etat === "parle") { d.pose = "cote"; if (--d.minuteur <= 0 || !cible) { d.etat = "retour"; d.versPixel(disp.directeur.poste, bloque); } }
    else if (d.etat === "idle" || !d.etat) { d.pose = "bas"; }
  }

  function majFlocons() {
    if (flocons.length < 40 && tic % 2 === 0) flocons.push({ x: Math.random() * LARGEUR, y: -2, v: 0.3 + Math.random() * 0.5 });
    for (const f of flocons) { f.y += f.v; f.x += Math.sin(f.y / 18) * 0.3; }
    flocons = flocons.filter((f) => f.y < HAUTEUR);
  }

  // --- dessin d'une image ---
  function dessiner() {
    ctx.drawImage(fond, 0, 0);
    // cartes posées sur le tableau (tâches en cours) et dans « Fait »
    const enCours = derniereData.equipe.filter((a) => a.run && a.run.etat && a.run.etat !== "en_attente" && a.run.etat !== "termine");
    const faits = derniereData.equipe.filter((a) => a.run && a.run.etat === "termine");
    enCours.forEach((a, i) => carteMur(disp.mur.tache, i, a.couleur));
    faits.forEach((a, i) => carteMur(disp.mur.fait, i, a.couleur));
    // LEDs du serveur (clignotent)
    const s = disp.serveur;
    for (let y = s.y + 4, k = 0; y < s.y + s.h - 4; y += 14, k++) {
      ctx.fillStyle = (tic + k * 3) % 7 < 4 ? "#7FBF8E" : "#2a4a34"; ctx.fillRect(s.x + s.l - 8, y + 3, 2, 2);
      ctx.fillStyle = (tic + k) % 11 < 3 ? "#E8A33D" : "#3a3020"; ctx.fillRect(s.x + s.l - 12, y + 3, 2, 2);
    }
    // pions (triés par y : profondeur)
    const tous = [...pions.values(), directeur].sort((a, b) => a.pos.y - b.pos.y);
    for (const p of tous) dessinerPion(p);
    // flocons de Noël
    if (thm.noel) { ctx.fillStyle = "#eef3ff"; for (const f of flocons) ctx.fillRect(f.x | 0, f.y | 0, 1, 1); }
    // teinte de nuit
    if (thm.nuit) { ctx.fillStyle = "#101830"; ctx.globalAlpha = 0.18; ctx.fillRect(0, 0, LARGEUR, HAUTEUR); ctx.globalAlpha = 1; }
  }

  function carteMur(r, i, couleur) {
    const cols = 4, cw = 15, ch = 11;
    const x = r.x + 4 + (i % cols) * (cw + 2), y = r.y + 6 + Math.floor(i / cols) * (ch + 3);
    if (y > r.y + r.h - ch) return;
    ctx.fillStyle = "#00000030"; ctx.fillRect(x + 1, y + 1, cw, ch);
    ctx.fillStyle = couleur || "#5AA9E6"; ctx.fillRect(x, y, cw, ch);
    ctx.fillStyle = "#ffffff55"; ctx.fillRect(x + 2, y + 2, cw - 4, 1); ctx.fillRect(x + 2, y + 5, cw - 6, 1);
  }

  function dessinerPion(p) {
    if (!p.sprites) return;
    const x = Math.round(p.pos.x - L_PERSO / 2), y = Math.round(p.pos.y - H_PERSO + 4);
    // ombre
    ctx.fillStyle = "#00000040"; ctx.fillRect(x + 3, y + H_PERSO - 2, 8, 2);
    // écran qui défile quand il tape
    if (p.pose === "assis" && p.poste) {
      const sx = p.poste.x + 2, sy = p.poste.y - 11;
      ctx.fillStyle = "#1d6b4f"; for (let k = 0; k < 4; k++) ctx.fillRect(sx, sy + ((p.ecran + k) % 7), 3 + (k * 2) % 9, 1);
    }
    let pose = p.pose;
    if (pose === "cote" && p.pas) pose = "cote1";
    if (pose === "assis" && p.pas) pose = "assis1";
    ctx.drawImage(p.sprites[pose] || p.sprites.bas, x, y);
    // carte portée
    if (p.carte) { ctx.fillStyle = p.couleur || "#5AA9E6"; ctx.fillRect(x + (pose.startsWith("cote") ? -2 : 10), y + 10, 5, 4); }
    // badge « depuis l'appli Claude »
    if (p.distant) { ctx.fillStyle = "#B57BE0"; ctx.fillRect(x + 10, y + 1, 3, 3); ctx.fillStyle = "#fff"; ctx.fillRect(x + 11, y + 2, 1, 1); }
    // bulle d'état
    if (p.bulle) bulle(x + 6, y - 2, p.bulle);
  }

  function bulle(x, y, txt) {
    const col = { "…": "#cfd6e6", "?": "#E8A33D", "!": "#E5484D", "✓": "#7FBF8E", "☕": "#caa36b" }[txt] || "#cfd6e6";
    ctx.fillStyle = "#1a1d27"; ctx.fillRect(x - 1, y - 9, 12, 10); ctx.fillRect(x + 3, y + 1, 2, 2);
    ctx.fillStyle = col;
    if (txt === "…") { ctx.fillRect(x + 1, y - 4, 1, 1); ctx.fillRect(x + 4, y - 4, 1, 1); ctx.fillRect(x + 7, y - 4, 1, 1); }
    else if (txt === "?") { ctx.fillRect(x + 3, y - 7, 3, 1); ctx.fillRect(x + 6, y - 6, 1, 2); ctx.fillRect(x + 4, y - 4, 2, 1); ctx.fillRect(x + 4, y - 2, 1, 1); }
    else if (txt === "!") { ctx.fillRect(x + 4, y - 7, 2, 4); ctx.fillRect(x + 4, y - 2, 2, 1); }
    else if (txt === "✓") { ctx.fillRect(x + 3, y - 4, 1, 2); ctx.fillRect(x + 4, y - 3, 1, 2); ctx.fillRect(x + 5, y - 5, 1, 2); ctx.fillRect(x + 6, y - 7, 1, 2); }
    else { ctx.fillRect(x + 2, y - 6, 5, 4); ctx.fillRect(x + 7, y - 5, 1, 2); }
  }

  // --- boucle ---
  let timer = null, dernier = 0;
  function image(ts) {
    if (!timer) return;
    if (ts - dernier >= 1000 / IPS) { dernier = ts; try { pas(); } catch { /* une image ratée n'arrête pas tout */ } }
    timer = requestAnimationFrame(image);
  }
  return {
    maj(equipe, distants, reglages) {
      thm = theme(reglages); disp = disposition(equipe.length || 1); bloque = grilleObstacles(disp);
      majFond(); majAgents(equipe || [], distants || [], reglages || {});
      if (!timer) dessiner();                 // au moins une image fixe (reduced-motion)
    },
    demarrer() { if (!timer && !sansMouvement()) { timer = requestAnimationFrame(image); } else { dessiner(); } },
    arreter() { if (timer) { cancelAnimationFrame(timer); timer = null; } },
    get actif() { return Boolean(timer); },
  };
}
