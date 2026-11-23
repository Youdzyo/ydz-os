// Historique (1.0.12) : 24 h ou 7 jours de mesures d'un nœud (une moyenne toutes les 5 minutes, gardée par forge).
// Graphiques en SVG, sans bibliothèque. Ouvert depuis la fiche d'un nœud (Accueil) ou Réglages > Diagnostic.
import { esc, ic, debit } from "../outils.js";
import { S, NOM, vide } from "../noyau.js";
import { entete } from "../cadre.js";
import { lire } from "../api.js";

const L = 600, H = 170;
const nf = (v, d = 0) => Number(v).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
const COMMUNES = ["cpu", "temp", "ram", "disque", "rx", "tx"];
const HUB = ["watts", "ventilo", "wifi"];
// carte -> séries dessinées ensemble
const CARTES = [
  { id: "temp", titre: "Température", icone: "thermometer", series: ["temp"] },
  { id: "cpu", titre: "Processeur", icone: "cpu", series: ["cpu"] },
  { id: "ram", titre: "Mémoire", icone: "memory-stick", series: ["ram"] },
  { id: "reseau", titre: "Réseau", icone: "activity", series: ["rx", "tx"] },
  { id: "disque", titre: "Disque système", icone: "hard-drive", series: ["disque"] },
  { id: "watts", titre: "Consommation de la tour", icone: "zap", series: ["watts"], hub: true },
  { id: "ventilo", titre: "Ventilateur", icone: "fan", series: ["ventilo"], hub: true },
  { id: "wifi", titre: "Signal Wi-Fi du Pi", icone: "wifi", series: ["wifi"], hub: true },
];

function valeur(v, unite) {
  if (v == null) return "–";
  if (unite === "bit/s") return debit(v);
  if (unite === "°C") return `${nf(v, 1)} °C`;
  if (unite === "W") return `${nf(v, 1)} W`;
  if (unite === "dBm") return `${nf(v)} dBm`;
  return `${nf(v)} %`;
}

function axe(v, unite) {
  if (unite === "bit/s") return debit(v).replace(" bit/s", "").replace(" kbit/s", " k").replace(" Mbit/s", " M").replace(" Gbit/s", " G");
  return `${nf(v)} ${unite === "%" ? "%" : unite}`;
}

// Échelle verticale lisible selon l'unité
function echelle(vals, unite) {
  if (!vals.length) return [0, 1];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (unite === "%") return [0, Math.max(10, Math.min(100, Math.ceil((hi + 5) / 10) * 10))];
  if (unite === "bit/s" || unite === "W") return [0, hi > 0 ? hi * 1.15 : 1];
  const pas = unite === "°C" ? 10 : 10;
  if (hi - lo < pas) { const m = (lo + hi) / 2; lo = m - pas / 2; hi = m + pas / 2; }
  return [Math.floor(lo - 1), Math.ceil(hi + 1)];
}

function chemin(pts, x, y, trou) {
  let d = "", prec = null;
  for (const [t, v] of pts) {
    d += `${prec == null || t - prec > trou ? "M" : "L"}${x(t).toFixed(1)} ${y(v).toFixed(1)}`;
    prec = t;
  }
  return d;
}

function graphe(series, jours) {
  const fin = Date.now() / 1000, debut = fin - jours * 86400;
  const tous = series.flatMap((s) => s.points.map((p) => p[1]));
  const unite = series[0].unite;
  const [lo, hi] = echelle(tous, unite);
  const x = (t) => ((t - debut) / (fin - debut)) * L;
  const y = (v) => H - 6 - ((v - lo) / (hi - lo || 1)) * (H - 14);
  const trou = jours === 1 ? 900 : 3 * 2016;               // au-delà : forge était éteint, la ligne s'arrête
  const graduations = [lo, (lo + hi) / 2, hi];
  // repères horaires : toutes les 6 h (24 h) ou chaque minuit (7 jours)
  const reperes = [];
  const d0 = new Date(debut * 1000);
  if (jours === 1) {
    d0.setMinutes(0, 0, 0); d0.setHours(Math.ceil(d0.getHours() / 6) * 6);
    for (let t = d0.getTime() / 1000; t < fin; t += 6 * 3600) reperes.push([t, `${String(new Date(t * 1000).getHours()).padStart(2, "0")}h`]);
  } else {
    d0.setHours(24, 0, 0, 0);
    for (let t = d0.getTime() / 1000; t < fin; t += 86400) reperes.push([t, new Date(t * 1000).toLocaleDateString("fr-FR", { weekday: "short" }).replace(".", "")]);
  }
  const p0 = series[0].points;
  const aire = p0.length > 1 ? `${chemin(p0, x, y, trou)} L${x(p0[p0.length - 1][0]).toFixed(1)} ${H} L${x(p0[0][0]).toFixed(1)} ${H} Z` : "";
  return `<div class="hg">
    <div class="hg-axe">${graduations.slice().reverse().map((g) => `<span>${esc(axe(g, unite))}</span>`).join("")}</div>
    <div class="hg-zone">
      <svg viewBox="0 0 ${L} ${H}" preserveAspectRatio="none" aria-hidden="true">
        ${graduations.map((g) => `<line class="grille" x1="0" x2="${L}" y1="${y(g).toFixed(1)}" y2="${y(g).toFixed(1)}"/>`).join("")}
        ${reperes.map(([t]) => `<line class="repere" x1="${x(t).toFixed(1)}" x2="${x(t).toFixed(1)}" y1="0" y2="${H}"/>`).join("")}
        ${series.length === 1 && aire ? `<path class="aire" d="${aire}"/>` : ""}
        ${series.map((s, i) => `<path class="ligne s${i}" d="${chemin(s.points, x, y, trou)}"/>`).join("")}
      </svg>
      <div class="hg-temps">${reperes.map(([t, txt]) => `<span style="left:${((x(t) / L) * 100).toFixed(2)}%">${esc(txt)}</span>`).join("")}</div>
    </div>
  </div>`;
}

function carte(c, data, jours) {
  const series = c.series.map((id) => data[id]).filter(Boolean);
  const avec = series.filter((s) => s.points.length > 1);
  const s0 = series[0];
  let corps;
  if (!avec.length) corps = `<p class="hg-vide">${ic("clock")}Pas encore assez de mesures : une toutes les 5 minutes, depuis la mise à jour 1.0.12.</p>`;
  else corps = graphe(avec, jours);
  const chiffres = series.length > 1
    ? series.map((s, i) => `<div class="hg-leg s${i}"><i></i><span>${esc(s.nom)}</span><b>${valeur(s.derniere, s.unite)}</b><small>moy. ${valeur(s.moy, s.unite)} · max ${valeur(s.max, s.unite)}</small></div>`).join("")
    : s0 ? `<div class="hg-chiffres"><b class="chiffre moyen">${valeur(s0.derniere, s0.unite)}</b>
        <dl><div><dt>Min</dt><dd>${valeur(s0.min, s0.unite)}</dd></div><div><dt>Moy.</dt><dd>${valeur(s0.moy, s0.unite)}</dd></div><div><dt>Max</dt><dd>${valeur(s0.max, s0.unite)}</dd></div></dl></div>` : "";
  return `<article class="carte bloc hg-carte" data-cle="hg-${c.id}">
    <h2 class="bloc-titre">${ic(c.icone)}${esc(c.titre)}</h2>
    ${chiffres ? `<div class="hg-tete${series.length > 1 ? " multi" : ""}">${chiffres}</div>` : ""}
    ${corps}
  </article>`;
}

const vue = {
  donnees: {},
  data: null, cle: null, erreur: null,
  noeudCourant(args) {
    const n = args[0] || S.etat?.hub || "forge";
    return S.etat?.noeuds.some((x) => x.nom === n) ? n : S.etat?.hub || "forge";
  },
  async charger(args) {
    const noeud = this.noeudCourant(args);
    const jours = this.donnees.jours || 1;
    const hub = S.etat?.noeuds.find((x) => x.nom === noeud)?.hub;
    const series = [...COMMUNES, ...(hub ? HUB : [])];
    const cle = `${noeud}/${jours}`;
    // relu au plus toutes les 60 s : une nouvelle moyenne n'arrive que toutes les 5 minutes
    if (this.cle === cle && this.data && Date.now() - this.lu < 60000) return;
    try {
      const r = await lire(`/api/historique?serie=${series.join(",")}&noeud=${encodeURIComponent(noeud)}&jours=${jours}`);
      this.data = Object.fromEntries((r.series || [r]).map((s) => [s.serie, s]));
      this.cle = cle; this.lu = Date.now(); this.erreur = null;
    } catch (e) { this.erreur = e.message; }
  },
  rendu(args) {
    const noeud = this.noeudCourant(args);
    const jours = this.donnees.jours || 1;
    const noeuds = (S.etat?.noeuds || []).filter((n) => n.connu);
    const hub = noeuds.find((x) => x.nom === noeud)?.hub;
    const pret = this.data && this.cle === `${noeud}/${jours}`;
    return `${entete("Historique", { retour: "#/accueil", sous: `${NOM(noeud)} · ${jours === 1 ? "dernières 24 heures" : "7 derniers jours"}` })}
      <div class="hg-barre">
        <div class="segments">${noeuds.map((n) => `<a class="${n.nom === noeud ? "actif" : ""}" href="#/historique/${esc(n.nom)}">${NOM(n.nom)}</a>`).join("")}</div>
        <div class="segments">${[[1, "24 h"], [7, "7 jours"]].map(([j, nom]) => `<button type="button" class="${jours === j ? "actif" : ""}" data-action="jours" data-j="${j}">${nom}</button>`).join("")}</div>
      </div>
      ${this.erreur && !pret ? vide("circle-x", "Historique indisponible", esc(this.erreur)) : ""}
      ${pret ? `<div class="hg-grille">${CARTES.filter((c) => !c.hub || hub).map((c) => carte(c, this.data, jours)).join("")}</div>
        <p class="note-fine">${ic("info")}Une moyenne toutes les 5 minutes, gardée 7 jours par le Pi principal (écrite sur la carte SD au plus toutes les 30 minutes).</p>`
        : this.erreur ? "" : `<div class="attente">${ic("loader-circle", "tourne")}</div>`}`;
  },
  actions: {
    async jours({ j }) {
      this.donnees.jours = Number(j);
      await this.charger(S.route.args);
    },
  },
};
export default vue;
