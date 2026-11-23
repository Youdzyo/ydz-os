// YDZ Shell : en-tête commun des écrans, panneau « Activité » (tâches longues), suivi des tâches.
import { esc, ic, ilYA } from "./outils.js";
import { S, NOM, ouvrir, enteteDialogue, rendre, toast, tenter } from "./noyau.js";
import { session, lire } from "./api.js";

export const heure = () => new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
export const jour = () => new Date().toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });

export const tachesEnCours = () => (S.etat?.taches || []).filter((t) => t.etat === "en_cours");
export const tachePour = (cible, nomNoeud) => tachesEnCours().find((t) => t.cible === cible && (!nomNoeud || t.noeud === nomNoeud));

export function etatCommun() {
  const enCours = tachesEnCours();
  const t = enCours[0];
  return `<div class="etat-commun">
    ${session.demo ? `<span class="pastille demo" title="Données simulées">${ic("sparkles")}Démonstration</span>` : ""}
    ${S.lien ? "" : `<span class="pastille crit">${ic("wifi-off")}Connexion perdue</span>`}
    <button type="button" class="btn activite${t ? " vivant" : ""}" data-action="activite" aria-label="Activité">
      ${t ? `${ic("loader-circle", "tourne")}<span class="activite-titre">${esc(t.titre)}${enCours.length > 1 ? ` <em>+${enCours.length - 1}</em>` : ""}</span>` : ic("activity")}
    </button>
  </div>`;
}

export function entete(titre, o = {}) {
  return `<header class="entete">
    ${o.retour ? `<a class="btn rond discret${o.retourEtroit ? " etroit-seul" : ""}" href="${o.retour}" aria-label="Retour">${ic("chevron-left")}</a>` : ""}
    <div class="entete-titres"><h1>${titre}</h1>${o.sous ? `<p>${o.sous}</p>` : ""}</div>
    ${o.droite || ""}
    ${etatCommun()}
    <time class="horloge-mini">${heure()}</time>
  </header>`;
}

const ICONE_TACHE = { app: "layout-grid", stockage: "hard-drive", maj: "download" };
function ligneTache(t, ouvert, journal) {
  const ton = t.etat === "en_cours" ? "cours" : t.etat === "ok" ? "ok" : "crit";
  const lignes = ouvert ? journal || t.journal : t.etat === "en_cours" ? t.journal.slice(-1) : [];
  return `<li class="tache ${ton}" data-cle="t${t.id}">
    <button type="button" class="tache-tete" data-action="detail" data-id="${t.id}" data-noeud="${t.noeud}">
      <span class="tache-icone">${t.etat === "en_cours" ? ic("loader-circle", "tourne") : ic(t.etat === "ok" ? "circle-check" : "circle-x")}</span>
      <span class="tache-textes"><b>${esc(t.titre)}</b>
        <small>${NOM(t.noeud)} · ${t.etat === "en_cours" ? "en cours" : esc(t.message || (t.etat === "ok" ? "terminé" : "échec"))}${t.fin ? ` · ${ilYA(t.fin)}` : ""}</small></span>
      ${ic(ICONE_TACHE[t.type] || "activity", "pale")}
    </button>
    ${lignes.length ? `<pre class="journal mini">${lignes.map(esc).join("\n")}</pre>` : ""}
  </li>`;
}

export function ouvrirActivite() {
  const d = ouvrir({
    classe: "large",
    donnees: { ouvert: null, journal: null },
    rendu: () => {
      const taches = S.etat?.taches || [];
      return `${enteteDialogue("Activité", { icone: "activity", sous: "Les opérations longues de la tour, la plus récente d'abord." })}
        <div class="dlg-corps">${taches.length
          ? `<ul class="taches">${taches.map((t) => ligneTache(t, d.donnees.ouvert === t.id, d.donnees.journal)).join("")}</ul>`
          : `<div class="vide">${ic("circle-check")}<h3>Rien en cours</h3><p>Les installations, mises à jour et préparations de disques apparaîtront ici.</p></div>`}
        </div>`;
    },
    actions: {
      async detail({ id, noeud: n }) {
        if (d.donnees.ouvert === id) { d.donnees.ouvert = null; return; }
        d.donnees.ouvert = id;
        d.donnees.journal = null;
        const r = await tenter(() => lire(`/api/taches/${id}?noeud=${encodeURIComponent(n)}`));
        if (r && d.donnees.ouvert === id) d.donnees.journal = r.tache.journal;
      },
    },
  });
}

// Suivi : annonce la fin de chaque tâche vue en cours, et prévient les vues pour qu'elles se rechargent.
const vues = new Map(); // id -> état
let surFin = null;
export function definirFinTache(fn) { surFin = fn; }
export function suivreTaches(taches, premier) {
  let fini = false;
  for (const t of taches || []) {
    const avant = vues.get(t.id);
    if (!premier && t.etat !== "en_cours" && avant !== t.etat && (avant === "en_cours" || (avant === undefined && t.fin && Date.now() / 1000 - t.fin < 6))) {
      toast(t.message || t.titre, t.etat === "ok" ? "ok" : "erreur");
      fini = true;
    }
    vues.set(t.id, t.etat);
  }
  if (fini && surFin) surFin();
}
export function tacheRecue(t) {
  // Événement « tache » du hub : retour immédiat, sans attendre le prochain état.
  if (!S.etat) return;
  const i = S.etat.taches.findIndex((x) => x.id === t.id);
  if (i >= 0) S.etat.taches[i] = t; else S.etat.taches.unshift(t);
  suivreTaches(S.etat.taches, false);
  rendre();
}
