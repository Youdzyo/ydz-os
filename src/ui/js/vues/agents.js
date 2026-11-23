// Vue « Agents » : l'équipe de mini-Claude, les tâches, et l'éditeur de personnalisation des Bureaux
// (aperçu en direct). Le cerveau (Ollama local / API en ligne) et le Canal Claude se règlent dans Réglages.
import { esc, ic } from "../outils.js";
import { S, NOM, btn, champ, interrupteur, pastille, vide, ouvrir, enteteDialogue, confirmer, toast, rendre } from "../noyau.js";
import { entete } from "../cadre.js";
import { lire, agir, session } from "../api.js";
import { apercuPerso } from "../bureaux.js";

const ETAT = { en_attente: ["En attente", ""], travaille: ["Travaille", "cours"], besoin_aide: ["Besoin d'aide", "warn"], erreur: ["Erreur", "crit"], termine: ["Terminé", "ok"] };
const ACCESSOIRES = [["aucun", "Aucun"], ["lunettes", "Lunettes"], ["casquette", "Casquette"], ["casque", "Casque"]];
const PEAUX = ["#F2D2B6", "#E8B98A", "#C68642", "#8D5524", "#5C3A21"];
const CHEVEUX = ["#2b2f3b", "#5A3A22", "#9A6A3A", "#C9A24B", "#B0B0B8", "#E5484D"];
const COULEURS = ["#E5484D", "#E8A33D", "#7FBF8E", "#5AA9E6", "#B57BE0", "#4FC3C7", "#EC6FA0", "#8FBF3F"];

function barre(prog, couleur) {
  return `<span class="ag-barre"><i style="width:${Math.max(0, Math.min(100, prog || 0))}%;background:${esc(couleur || "#5AA9E6")}"></i></span>`;
}

function carteAgent(a) {
  const run = a.run || {};
  const [nom, ton] = ETAT[run.etat] || ETAT.en_attente;
  const occupe = run.etat === "travaille";
  return `<article class="carte ag-carte" data-cle="ag-${esc(a.id)}">
    <header class="ag-tete">
      <span class="ag-avatar" style="--peau:${esc(a.perso?.peau || "#E8B98A")};--chev:${esc(a.perso?.cheveux || "#5A3A22")};--vet:${esc(a.perso?.vetements || a.couleur)}"><i class="ag-chev"></i><i class="ag-tete-r"></i><i class="ag-corps"></i></span>
      <div class="ag-titres"><h2>${esc(a.nom)}</h2><p class="pastille ${ton}">${occupe ? ic("loader-circle", "tourne") : "<i></i>"}${nom}${run.progression ? ` · ${run.progression}%` : ""}</p></div>
      ${btn("ag-perso", "", { icone: "palette", classe: "rond discret", d: { id: a.id }, titre: "Personnaliser" })}
    </header>
    ${occupe ? barre(run.progression, a.couleur) : ""}
    ${run.tache ? `<p class="ag-tache">${ic("clipboard-list")}${esc(run.tache)}</p>` : ""}
    ${run.etapes?.length ? `<ul class="ag-etapes">${run.etapes.slice(-3).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}
    ${run.resultat ? `<div class="ag-resultat ${ton}">${ic(run.etat === "termine" ? "circle-check" : run.etat === "erreur" ? "circle-x" : "circle-help")}<p>${esc(run.resultat)}</p></div>` : ""}
    <div class="ligne-champ ag-tache-champ">
      ${champ("tache_" + a.id, "", { placeholder: "Donner une tâche à " + a.nom + "…", max: 600, entree: "ag-donner", label: "Tâche" })}
      ${btn("ag-donner", "", { icone: "send", classe: "rond", d: { id: a.id }, off: occupe, titre: "Lancer" })}
    </div>
    <footer class="ag-pied">
      ${run.etat && run.etat !== "en_attente" ? btn("ag-annuler", "Réinitialiser", { icone: "rotate-ccw", classe: "discret petit", d: { id: a.id } }) : ""}
      ${btn("ag-supprimer", "Retirer", { icone: "trash-2", classe: "discret petit danger-texte", d: { id: a.id } })}
    </footer>
  </article>`;
}

function banniereCerveau(d) {
  const c = d.cerveau || {};
  if (c.mode === "online" && c.online_actif) {
    return `<div class="carte bloc ag-cerveau en-ligne"><h2 class="bloc-titre">${ic("cloud")}Cerveau : API en ligne</h2>
      <p class="texte">Les agents réfléchissent via une API externe (modèle ${esc(c.modele || "?")}). Les données de tes questions sortent vers ce fournisseur.</p>
      <a class="btn discret" href="#/reglages/agents">${ic("settings")}<span>Changer dans Réglages</span></a></div>`;
  }
  const ok = c.ollama_installe;
  return `<div class="carte bloc ag-cerveau ${ok ? "" : "attention"}"><h2 class="bloc-titre">${ic("brain")}Cerveau : Ollama (local)</h2>
    <p class="texte">${ok ? `Modèle local <b>${esc(c.modele || "llama3.2:3b")}</b>. Tout reste sur ta tour, rien ne part chez un fournisseur.`
      : "Pour faire réfléchir les agents en local, installe l'app <b>Ollama</b> depuis le catalogue, puis télécharge un petit modèle (par ex. llama3.2:3b). Sinon, active une API en ligne dans Réglages."}</p>
    <div class="actions">${ok ? "" : `<a class="btn plein" href="#/apps/ollama">${ic("download")}<span>Installer Ollama</span></a>`}
      <a class="btn discret" href="#/reglages/agents">${ic("settings")}<span>Réglages du cerveau</span></a></div></div>`;
}

// --- éditeur de personnalisation (aperçu en direct) ---
function nuancier(action, cle, valeurs, actuel, id) {
  return `<div class="nuancier">${valeurs.map((v) => `<button type="button" class="nuance${v.toLowerCase() === String(actuel || "").toLowerCase() ? " actif" : ""}" style="--c:${v}" data-action="${action}" data-cle="${cle}" data-v="${v}" data-id="${id}" aria-label="${v}"></button>`).join("")}</div>`;
}

let apercu = null;
function ouvrirPerso(agent) {
  const brouillon = JSON.parse(JSON.stringify(agent));
  const d = ouvrir({
    classe: "large ag-editeur",
    donnees: { nomPerso: agent.nom, perso: brouillon.perso || {}, phrasesTexte: (agent.phrases || []).join("\n"), personnalite: agent.personnalite || "", couleur: agent.couleur },
    surFermer: () => { if (apercu) { apercu.arreter(); apercu = null; } },
    rendu: () => {
      const p = d.donnees.perso;
      return `${enteteDialogue("Personnaliser " + esc(agent.nom), { icone: "palette" })}
      <div class="dlg-corps ag-editeur-corps">
        <div class="ag-apercu"><canvas class="ag-apercu-canvas" data-cle="ag-apercu" data-fige></canvas><span>Aperçu en direct</span></div>
        <div class="ag-reglages-perso">
          <label class="ligne-champ"><span class="libelle-champ">Nom</span>${champ("nomPerso", d.donnees.nomPerso, { max: 24, label: "Nom" })}</label>
          <div class="reglage-groupe"><span class="libelle-champ">Couleur de l'agent</span>${nuancier("ag-couleur", "couleur", COULEURS, d.donnees.couleur, agent.id)}</div>
          <div class="reglage-groupe"><span class="libelle-champ">Peau</span>${nuancier("ag-teinte", "peau", PEAUX, p.peau, agent.id)}</div>
          <div class="reglage-groupe"><span class="libelle-champ">Cheveux</span>${nuancier("ag-teinte", "cheveux", CHEVEUX, p.cheveux, agent.id)}</div>
          <div class="reglage-groupe"><span class="libelle-champ">Vêtements</span>${nuancier("ag-teinte", "vetements", COULEURS, p.vetements, agent.id)}</div>
          <div class="reglage-groupe"><span class="libelle-champ">Accessoire</span>
            <div class="segments">${ACCESSOIRES.map(([v, n]) => `<button type="button" class="${(p.accessoire || "aucun") === v ? "actif" : ""}" data-action="ag-accessoire" data-v="${v}">${esc(n)}</button>`).join("")}</div></div>
          <label class="reglage-groupe"><span class="libelle-champ">Personnalité (quelques mots)</span>${champ("personnalite", d.donnees.personnalite, { max: 280, placeholder: "ex. curieux, taquin, va droit au but" })}</label>
          <label class="reglage-groupe"><span class="libelle-champ">Phrases fétiches (une par ligne)</span>
            <textarea class="champ zone" data-champ="phrasesTexte" rows="3" maxlength="400" placeholder="Au boulot !">${esc(d.donnees.phrasesTexte)}</textarea></label>
        </div>
      </div>
      <footer class="dlg-pied">${btn("fermer", "Annuler", { classe: "discret" })}${btn("ag-enregistrer", "Enregistrer", { classe: "plein", icone: "check" })}</footer>`;
    },
    actions: {
      "ag-teinte"({ cle, v }) { d.donnees.perso = { ...d.donnees.perso, [cle]: v }; majApercu(d); },
      "ag-couleur"({ v }) { d.donnees.couleur = v; },
      "ag-accessoire"({ v }) { d.donnees.perso = { ...d.donnees.perso, accessoire: v }; majApercu(d); },
      async "ag-enregistrer"() {
        const phrases = d.donnees.phrasesTexte.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 8);
        await agir("/api/agents", { action: "configurer", id: agent.id, nom: d.donnees.nomPerso, couleur: d.donnees.couleur, perso: d.donnees.perso, personnalite: d.donnees.personnalite, phrases });
        toast("Agent personnalisé.", "ok");
        d.fermer();
        vue.charger();
      },
    },
  });
  setTimeout(() => majApercu(d, true), 30);
}
function majApercu(d, demarrer) {
  const canvas = document.querySelector(".ag-apercu-canvas");
  if (!canvas) return;
  if (!apercu || demarrer) { if (apercu) apercu.arreter(); apercu = apercuPerso(canvas); apercu.demarrer(); }
  apercu.maj({ peau: d.donnees.perso.peau || "#E8B98A", cheveux: d.donnees.perso.cheveux || "#5A3A22", vetements: d.donnees.perso.vetements || d.donnees.couleur || "#5AA9E6", accessoire: d.donnees.perso.accessoire || "aucun" });
}

// --- réglages des bureaux (thème, dialogue, panneau) ---
function reglagesBureaux(d) {
  const b = d?.bureaux || S.etat?.agents?.bureaux || {};
  return `<div class="carte bloc ag-bureaux-reg"><h2 class="bloc-titre">${ic("sliders-horizontal")}Les bureaux à l'écran</h2>
    <div class="reglage-groupe"><span class="libelle-champ">Ambiance</span>
      <div class="segments">${[["auto", "Auto (jour/nuit)"], ["jour", "Jour"], ["nuit", "Nuit"]].map(([v, n]) => `<button type="button" class="${(b.theme || "auto") === v ? "actif" : ""}" data-action="bu-theme" data-v="${v}">${esc(n)}</button>`).join("")}</div></div>
    <div class="reglage-ligne-sw"><span>${ic("panel-right")}Panneau « Équipe d'agents »</span>${interrupteur("bu-panneau", b.panneau !== false, {}, "Panneau")}</div>
    <div class="reglage-ligne-sw"><span>${ic("message-square")}Boîte de dialogue</span>${interrupteur("bu-boite", b.boite !== false, {}, "Boîte de dialogue")}</div>
    <div class="reglage-groupe"><span class="libelle-champ">Vitesse du texte : ${b.dialogue_vitesse || 45} car/s</span>
      <input type="range" min="10" max="100" step="5" value="${b.dialogue_vitesse || 45}" data-fin="bu-vitesse" aria-label="Vitesse du texte"></div>
    <div class="reglage-groupe"><span class="libelle-champ">Taille du texte : ${b.dialogue_taille || 20} px</span>
      <input type="range" min="14" max="36" step="2" value="${b.dialogue_taille || 20}" data-fin="bu-taille" aria-label="Taille du texte"></div>
    <p class="note-fine">${ic("info")}Les Bureaux de Claude s'affichent sur l'écran de la tour (page qui tourne avec les autres).</p>
  </div>`;
}

const vue = {
  donnees: {},
  agents: null,
  async charger() {
    this.agents = await lire("/api/agents").catch(() => this.agents);
    rendre();
  },
  rendu() {
    const d = this.agents;
    const ag = S.etat.agents || { equipe: [] };
    const equipe = d?.equipe || ag.equipe || [];
    const distants = (d?.distants || ag.distants || []);
    return `${entete("Agents", { sous: "Une petite équipe de mini-Claude à qui confier une tâche.", droite: `<a class="btn discret etroit-cache" href="#/reglages/agents">${ic("settings")}<span>Réglages</span></a>` })}
    <div class="agents-vue">
      ${banniereCerveau(d || ag)}
      <div class="ag-grille">${equipe.map(carteAgent).join("")}
        ${equipe.length < 6 ? `<button type="button" class="carte ag-ajouter" data-action="ag-ajouter">${ic("plus")}<span>Ajouter un agent</span></button>` : ""}</div>
      ${distants.length ? `<div class="carte bloc ag-distants"><h2 class="bloc-titre">${ic("sparkles")}Depuis l'appli Claude</h2>
        <ul class="ag-distants-liste">${distants.map((x) => `<li><span class="bag-pastille" style="--c:${esc(x.couleur || "#B57BE0")}"></span><b>${esc(x.nom)}</b><small>${esc(x.etape || x.tache || "")}</small>${x.termine ? pastille("ok", "terminé", "circle-check") : pastille("cours", `${x.progression || 0}%`)}</li>`).join("")}</ul>
        <p class="note-fine">${ic("shield-check")}Ces agents sont animés à titre indicatif (événements signés) : rien n'est exécuté sur la tour.</p></div>` : ""}
      ${reglagesBureaux(d)}
    </div>`;
  },
  actions: {
    async "ag-donner"({ id }) {
      const tache = (this.donnees["tache_" + id] || "").trim();
      if (!tache) { toast("Écris une tâche pour l'agent.", "erreur"); return; }
      this.donnees["tache_" + id] = "";
      try { await agir("/api/agents", { action: "tache", id, tache }); }
      catch (e) { toast(e.message, "erreur"); }
      await this.charger();
    },
    async "ag-annuler"({ id }) { await agir("/api/agents", { action: "annuler", id }); await this.charger(); },
    async "ag-ajouter"() {
      const r = await agir("/api/agents", { action: "creer", nom: "Agent" });
      await this.charger();
      if (r.agent) ouvrirPerso(r.agent);
    },
    async "ag-supprimer"({ id }) {
      const a = (this.agents?.equipe || []).find((x) => x.id === id);
      if (!(await confirmer({ titre: `Retirer ${a?.nom || "cet agent"} ?`, texte: "Il disparaît de l'équipe et des bureaux.", libelle: "Retirer", danger: true, icone: "trash-2" }))) return;
      await agir("/api/agents", { action: "supprimer", id });
      await this.charger();
    },
    "ag-perso"({ id }) { const a = (this.agents?.equipe || []).find((x) => x.id === id) || (S.etat.agents?.equipe || []).find((x) => x.id === id); if (a) ouvrirPerso(a); },
    async "bu-theme"({ v }) { await this.bureaux({ theme: v }); },
    async "bu-panneau"() { await this.bureaux({ panneau: !(this.bur()?.panneau !== false) }); },
    async "bu-boite"() { await this.bureaux({ boite: !(this.bur()?.boite !== false) }); },
    async "bu-vitesse"({ valeur }) { await this.bureaux({ dialogue_vitesse: Number(valeur) }); },
    async "bu-taille"({ valeur }) { await this.bureaux({ dialogue_taille: Number(valeur) }); },
  },
  bur() { return this.agents?.bureaux || S.etat.agents?.bureaux || {}; },
  async bureaux(champs) {
    const r = await agir("/api/agents", { action: "bureaux", ...champs });
    if (this.agents) this.agents.bureaux = r.bureaux;
    if (S.etat.agents) S.etat.agents.bureaux = r.bureaux;
    rendre();
  },
};

export default vue;
