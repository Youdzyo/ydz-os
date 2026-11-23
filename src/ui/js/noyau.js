// YDZ Shell : le noyau de l'interface. État partagé, rendu, actions, dialogues, messages, pavé PIN.
import { esc, ic, morph } from "./outils.js";
import { session, ErreurApi, connexion } from "./api.js";

// État partagé par toutes les vues.
export const S = {
  etat: null, // dernier /api/etat reçu
  apps: null, // dernier /api/apps
  lien: true, // le hub répond
  hist: {}, // nom du nœud -> { temp: [], cpu: [], rx: [], tx: [] }
  route: { vue: "accueil", args: [] },
  clavier: false, // clavier virtuel actif (écran de la tour)
  veille: false,
  appui: null, // élément sous le doigt au début de l'appui en cours
};

export const noeud = (nom) => S.etat?.noeuds.find((n) => n.nom === nom);
// 1.0.13 : nom d'affichage du Pi (« Pi One »), à défaut le nom d'hôte en majuscules (comme avant).
// 1.0.15 (sécurité) : NOM et LABO viennent d'un nœud du réseau (nom d'affichage) : toujours échappés avant le HTML.
export const NOM = (nom) => esc(S.etat?.labo?.noms?.[nom] || String(nom || "").toUpperCase());
export const LABO = () => esc(S.etat?.labo?.nom || "Home Pi Lab");
export const appCatalogue = (id) => S.apps?.catalogue.find((a) => a.id === id);

// ------------------------------------------------------------------------------------- rendu
let fonctionRendu = null;
let prevu = false;
export function definirRendu(fn) { fonctionRendu = fn; }
export function rendre() {
  if (prevu) return;
  prevu = true;
  requestAnimationFrame(() => { prevu = false; fonctionRendu && fonctionRendu(); });
}

// ----------------------------------------------------------------------------------- boutons
const occupes = new Set();
const cleAction = (action, donnees) => `${action}|${Object.keys(donnees || {}).sort().map((k) => `${k}=${donnees[k]}`).join("&")}`;

// btn("installer", "Installer", { icone, classe, d: { id }, off })
export function btn(action, libelle, o = {}) {
  const d = o.d || {};
  const occupe = occupes.has(cleAction(action, d));
  const attrs = Object.entries(d).map(([k, v]) => ` data-${k}="${esc(v)}"`).join("");
  const icone = occupe ? ic("loader-circle", "tourne") : o.icone ? ic(o.icone) : "";
  return `<button type="button" class="btn ${o.classe || ""}${occupe ? " occupe" : ""}" data-action="${action}"${attrs}${o.off || occupe ? " disabled" : ""}${o.titre ? ` aria-label="${esc(o.titre)}"` : ""}>${icone}${libelle ? `<span>${libelle}</span>` : ""}</button>`;
}

// ---------------------------------------------------------------------------------- messages
export const messages = [];
let numeroMessage = 0;
export function toast(texte, niveau = "info", ms) {
  if (!texte) return;
  if (messages.some((m) => m.texte === texte)) return;
  const m = { id: ++numeroMessage, texte, niveau };
  messages.push(m);
  if (messages.length > 3) messages.shift();
  setTimeout(() => { const i = messages.indexOf(m); if (i >= 0) { messages.splice(i, 1); rendre(); } }, ms || (niveau === "erreur" ? 7000 : 4200));
  rendre();
}
export function erreur(e) {
  if (e instanceof ErreurApi) {
    if (e.code !== "annule") toast(e.message, "erreur");
  } else {
    console.error(e);
    toast("Une erreur inattendue s'est produite dans l'interface.", "erreur");
  }
}
// Lance une action ; en cas d'échec, affiche l'erreur et renvoie undefined.
export async function tenter(fn) {
  try { return await fn(); } catch (e) { erreur(e); return undefined; }
}

// --------------------------------------------------------------------------------- dialogues
export const dialogues = [];
let numeroDialogue = 0;
export function ouvrir(d) {
  d.id = ++numeroDialogue;
  d.donnees = d.donnees || {};
  d.actions = d.actions || {};
  d.fermer = (valeur) => {
    const i = dialogues.indexOf(d);
    if (i < 0) return;
    dialogues.splice(i, 1);
    d.surFermer && d.surFermer(valeur);
    rendre();
  };
  dialogues.push(d);
  document.activeElement && document.activeElement.blur && document.activeElement.blur();
  rendre();
  return d;
}
export const dialogueCourant = () => dialogues[dialogues.length - 1];

export function rendreDialogues() {
  return dialogues.map((d, i) => `
    <div class="calque${i === dialogues.length - 1 ? " dessus" : ""}" data-cle="dlg${d.id}" data-action="${d.fermable === false ? "" : "fermer-fond"}">
      <section class="dialogue ${d.classe || ""}" role="dialog" aria-modal="true">${d.rendu(d)}</section>
    </div>`).join("");
}

export function enteteDialogue(titre, o = {}) {
  return `<header class="dlg-tete">
    ${o.icone ? `<span class="dlg-icone ${o.ton || ""}">${ic(o.icone)}</span>` : ""}
    <div class="dlg-titres"><h2>${titre}</h2>${o.sous ? `<p>${o.sous}</p>` : ""}</div>
    ${o.sansCroix ? "" : `<button type="button" class="btn rond discret" data-action="fermer" aria-label="Fermer">${ic("x")}</button>`}
  </header>`;
}

// Confirmation simple. Renvoie true si l'utilisateur confirme.
export function confirmer({ titre, texte, libelle = "Confirmer", danger = false, icone = "circle-help", annuler = "Annuler", detail = "" }) {
  return new Promise((resoudre) => {
    ouvrir({
      classe: "etroit",
      surFermer: (v) => resoudre(v === true),
      rendu: () => `
        ${enteteDialogue(esc(titre), { icone: danger ? "triangle-alert" : icone, ton: danger ? "danger" : "", sansCroix: true })}
        <div class="dlg-corps"><p class="texte">${texte}</p>${detail}</div>
        <footer class="dlg-pied">
          ${btn("fermer", annuler, { classe: "discret" })}
          ${btn("valider", libelle, { classe: danger ? "danger" : "plein" })}
        </footer>`,
      actions: { valider() { this.fermer(true); } },
    });
  });
}

export function informer({ titre, texte, icone = "info", libelle = "Compris" }) {
  return new Promise((resoudre) => {
    ouvrir({
      classe: "etroit",
      surFermer: () => resoudre(),
      rendu: () => `${enteteDialogue(esc(titre), { icone, sansCroix: true })}
        <div class="dlg-corps"><div class="texte">${texte}</div></div>
        <footer class="dlg-pied">${btn("fermer", libelle, { classe: "plein" })}</footer>`,
    });
  });
}

// Liste de choix (remplace les listes déroulantes, inutilisables au doigt). Renvoie la valeur ou undefined.
export function choisir({ titre, options, valeur }) {
  return new Promise((resoudre) => {
    ouvrir({
      classe: "etroit",
      surFermer: (v) => resoudre(v),
      rendu: () => `${enteteDialogue(esc(titre))}
        <div class="dlg-corps sans-marge"><div class="liste-choix">
          ${options.map((o, i) => `<button type="button" class="choix${String(o.valeur) === String(valeur) ? " actif" : ""}" data-action="prendre" data-i="${i}">
            <span><b>${esc(o.nom)}</b>${o.aide ? `<small>${esc(o.aide)}</small>` : ""}</span>${String(o.valeur) === String(valeur) ? ic("check") : ""}</button>`).join("")}
        </div></div>`,
      actions: { prendre({ i }) { this.fermer(options[Number(i)].valeur); } },
    });
  });
}

// ----------------------------------------------------------------------------------- pavé PIN
export function pave(valeur, { erreur: err = "", max = 8, secoue = false, libelleOk = "Valider", minOk = 4, attente = 0 } = {}) {
  const points = Array.from({ length: Math.max(valeur.length, 4) }, (_, i) => `<i class="${i < valeur.length ? "plein" : ""}"></i>`).join("");
  const t = (c) => `<button type="button" class="touche" data-action="pin-chiffre" data-c="${c}"${valeur.length >= max || attente ? " disabled" : ""}>${c}</button>`;
  return `<div class="pave">
    <div class="pin-points${secoue ? " secoue" : ""}" data-cle="pts${secoue}" aria-label="${valeur.length} chiffres saisis">${points}</div>
    <p class="pin-message${err ? " erreur" : ""}" aria-live="polite">${esc(err) || "&nbsp;"}</p>
    <div class="pave-grille">
      ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map(t).join("")}
      <button type="button" class="touche discret" data-action="pin-effacer" aria-label="Effacer"${valeur ? "" : " disabled"}>${ic("delete")}</button>
      ${t(0)}
      <button type="button" class="touche ok" data-action="pin-ok" aria-label="${libelleOk}"${valeur.length >= minOk && !attente ? "" : " disabled"}>${ic("check")}</button>
    </div>
  </div>`;
}
// Actions communes d'un dialogue à pavé : d.donnees.pin, d.valider(pin).
export const actionsPave = {
  "pin-chiffre"({ c }) { if (this.donnees.pin.length < 8) { this.donnees.pin += c; this.donnees.erreur = ""; this.donnees.secoue = 0; } },
  "pin-effacer"() { this.donnees.pin = this.donnees.pin.slice(0, -1); },
  "pin-ok"() { return this.valider(this.donnees.pin); },
};
export function toucheClavierPave(d, ev) {
  if (/^\d$/.test(ev.key)) actionsPave["pin-chiffre"].call(d, { c: ev.key });
  else if (ev.key === "Backspace") actionsPave["pin-effacer"].call(d);
  else if (ev.key === "Enter" && d.donnees.pin.length >= 4) Promise.resolve(d.valider(d.donnees.pin)).then(rendre);
  else return;
  rendre();
}

// Demande le code PIN (action lancée depuis le réseau). Renvoie true une fois la session ouverte.
let demandeEnCours = null;
export function demanderPin() {
  if (demandeEnCours) return demandeEnCours;
  demandeEnCours = new Promise((resoudre) => {
    const d = ouvrir({
      classe: "pin",
      donnees: { pin: "", erreur: "", secoue: 0, attente: session.auth.attente_s || 0 },
      surFermer: (v) => { clearInterval(d.rythme); demandeEnCours = null; resoudre(v === true); },
      surTouche(ev) { toucheClavierPave(d, ev); },
      rendu: () => `
        ${enteteDialogue("Code PIN", { icone: "lock", sous: "Pour agir sur la tour depuis cet appareil." })}
        <div class="dlg-corps">${pave(d.donnees.pin, {
          erreur: d.donnees.attente ? `Trop d'essais. Réessaie dans ${d.donnees.attente} s.` : d.donnees.erreur,
          secoue: d.donnees.secoue, attente: d.donnees.attente,
        })}</div>`,
      actions: actionsPave,
      async valider(pin) {
        try {
          await connexion(pin);
          d.fermer(true);
          toast("Appareil déverrouillé.", "ok");
        } catch (e) {
          d.donnees.pin = "";
          d.donnees.secoue = Date.now();
          if (e.donnees?.attente_s) d.donnees.attente = Math.ceil(e.donnees.attente_s);
          if (e.code === "pin_non_defini") {
            d.fermer(false);
            toast("Aucun code PIN n'est défini. Choisis-le sur l'écran de la tour : Réglages, Code PIN.", "erreur");
          } else d.donnees.erreur = e.code === "pin_incorrect" ? "Code incorrect." : e.message;
        }
      },
    });
    d.rythme = setInterval(() => { if (d.donnees.attente > 0) { d.donnees.attente--; rendre(); } }, 1000);
  });
  return demandeEnCours;
}

// ------------------------------------------------------------------------------------ actions
// Ordre de recherche : dialogue du dessus, vue courante, actions globales.
let sources = () => [];
export function definirSources(fn) { sources = fn; }

export async function executer(action, donnees, el, ev) {
  const d = dialogueCourant();
  if (action === "fermer" && d) { d.fermer(); return; }
  // Fond du dialogue : ne ferme que si l'appui a commencé ET fini sur le fond (l'ouverture du clavier déplace le contenu sous le doigt).
  if (action === "fermer-fond") { if (d && ev.target === el && S.appui === el) d.fermer(); return; }
  for (const src of sources()) {
    const fn = src.actions && src.actions[action];
    if (!fn) continue;
    const cle = cleAction(action, donnees);
    if (occupes.has(cle)) return;
    let r;
    try {
      r = fn.call(src, donnees, el, ev);
      if (r && typeof r.then === "function") {
        occupes.add(cle);
        rendre();
        await r;
      }
    } catch (e) {
      erreur(e);
    } finally {
      occupes.delete(cle);
      rendre();
    }
    return;
  }
  if (action) console.warn("action sans gestionnaire :", action);
}

// Champs de saisie : data-champ="nom" écrit dans les données du dialogue du dessus, sinon de la vue.
export function porteeChamps() {
  const d = dialogueCourant();
  if (d) return d.donnees;
  const src = sources().find((s) => s.donnees);
  return src ? src.donnees : {};
}

// Champ texte prêt pour le clavier virtuel.
export function champ(nom, valeur, o = {}) {
  const type = o.type === "secret" ? "password" : "text";
  return `<input class="champ${o.erreur ? " erreur" : ""}" type="${type}" data-champ="${esc(nom)}" value="${esc(valeur ?? "")}"
    ${o.placeholder ? `placeholder="${esc(o.placeholder)}"` : ""} ${o.max ? `maxlength="${o.max}"` : ""}
    ${o.chiffres ? 'data-clavier="chiffres"' : ""} ${o.entree ? `data-entree="${o.entree}"` : ""} ${o.rendre ? "data-rendre" : ""}
    inputmode="${S.clavier ? "none" : o.chiffres ? "numeric" : "text"}" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
    ${o.label ? `aria-label="${esc(o.label)}"` : ""}>`;
}

export function interrupteur(action, actif, d = {}, label = "") {
  const attrs = Object.entries(d).map(([k, v]) => ` data-${k}="${esc(v)}"`).join("");
  return `<button type="button" class="inter${actif ? " actif" : ""}" role="switch" aria-checked="${actif}" data-action="${action}"${attrs} aria-label="${esc(label)}"><i></i></button>`;
}

// Petits composants d'affichage partagés.
export const pastille = (ton, texte, icone) => `<span class="pastille ${ton}">${icone ? ic(icone) : "<i></i>"}${esc(texte)}</span>`;
export function jauge(v, ton = "") {
  const p = v == null ? 0 : Math.max(0, Math.min(100, v));
  const t = ton || (p >= 92 ? "crit" : p >= 80 ? "warn" : "");
  return `<span class="jauge ${t}"><i style="width:${p.toFixed(1)}%"></i></span>`;
}
export const vide = (icone, titre, texte = "", action = "") => `<div class="vide">${ic(icone)}<h3>${titre}</h3>${texte ? `<p>${texte}</p>` : ""}${action}</div>`;

export { morph };
