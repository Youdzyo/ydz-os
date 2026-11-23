// YDZ Shell — écran de verrouillage (1.0.15).
// Sur un téléphone ou un ordinateur (jamais l'écran de la tour), rien n'est affiché ni chargé tant que le code
// n'est pas saisi : le serveur refuse /api/etat avant déverrouillage (ce n'est pas qu'un flou CSS). L'écran se
// reverrouille après un temps d'inactivité (réglable, 15 min par défaut) et quand l'app reste cachée trop longtemps.
import { esc, ic } from "./outils.js";
import { S, rendre, pave, actionsPave } from "./noyau.js";
import { session, connexion, rafraichirAuth } from "./api.js";

const V = {
  actif: false,          // l'écran de verrouillage est-il montré ?
  pin: "",
  erreur: "",
  secoue: 0,
  attente: 0,
  ouverture: false,      // animation d'ouverture (flou -> net) en cours
  surSucces: null,       // appelé une fois déverrouillé (recharge l'état)
};

let rythme = null;
let dernierSignal = Date.now();
let cacheDepuis = null;

export const estVerrouille = () => V.actif;

// Réglages du verrou (minutes). Repris de /api/auth tant que l'état complet n'est pas chargé.
function minutesInactivite() { return (session.auth?.verrou?.inactivite_min ?? S.etat?.auth?.verrou?.inactivite_min ?? 15); }
function minutesFond() { return (session.auth?.verrou?.fond_min ?? S.etat?.auth?.verrou?.fond_min ?? 5); }

export function verrouiller(raison) {
  if (V.actif || session.auth?.local || session.demo && !window.YDZ_VERROU_DEMO) return;
  V.actif = true;
  V.pin = "";
  V.erreur = "";
  V.ouverture = false;
  S.etat = null;                       // aucune donnée privée ne reste à l'écran
  document.documentElement.classList.add("verrouille");
  if (rythme) clearInterval(rythme);
  rythme = setInterval(() => { if (V.attente > 0) { V.attente--; rendre(); } }, 1000);
  rafraichirAuth().then(() => { V.attente = session.auth.attente_s || 0; rendre(); }).catch(() => {});
  rendre();
}

export function definirSurDeverrouillage(fn) { V.surSucces = fn; }

function deverrouille() {
  V.ouverture = true;                  // flou -> net
  document.documentElement.classList.remove("verrouille");
  rendre();
  setTimeout(() => {
    V.actif = false;
    V.ouverture = false;
    if (rythme) { clearInterval(rythme); rythme = null; }
    if (V.surSucces) V.surSucces();
    rendre();
  }, 420);
}

// Rendu plein écran : fond flouté + pavé PIN flottant au centre (mobile d'abord).
export function rendu() {
  const pinDefini = session.auth?.pin_defini !== false;
  const titre = pinDefini ? "Tour verrouillée" : "Aucun code PIN";
  const sous = pinDefini
    ? "Entre le code pour voir et piloter la tour."
    : "Choisis d'abord un code sur l'écran de la tour (Réglages, Code PIN).";
  return `<div class="verrou-ecran${V.ouverture ? " ouvre" : ""}" data-cle="verrou">
    <div class="verrou-fond" aria-hidden="true"></div>
    <section class="verrou-carte" role="dialog" aria-modal="true" aria-label="Déverrouiller la tour">
      <div class="verrou-logo">${ic("lock")}</div>
      <h1>${titre}</h1>
      <p class="verrou-sous">${esc(sous)}</p>
      ${pinDefini ? pave(V.pin, {
        erreur: V.attente ? `Trop d'essais. Réessaie dans ${V.attente} s.` : V.erreur,
        secoue: V.secoue, attente: V.attente, minOk: 4,
      }) : `<p class="verrou-aide">${ic("info")}L'interface s'ouvrira dès qu'un code existera.</p>`}
    </section>
  </div>`;
}

async function valider(pin) {
  try {
    await connexion(pin);
    deverrouille();
  } catch (e) {
    V.pin = "";
    V.secoue = Date.now();
    if (navigator.vibrate) { try { navigator.vibrate(60); } catch { /* sans vibreur */ } }
    if (e.donnees?.attente_s) V.attente = Math.ceil(e.donnees.attente_s);
    V.erreur = e.code === "pin_incorrect" ? "Code incorrect." : e.code === "pin_non_defini" ? "Aucun code n'est défini." : e.message;
    rendre();
  }
}

export const actions = {
  ...actionsPave,
  "pin-chiffre"({ c }) { if (V.pin.length < 8) { V.pin += c; V.erreur = ""; V.secoue = 0; } },
  "pin-effacer"() { V.pin = V.pin.slice(0, -1); },
  "pin-ok"() { return valider(V.pin); },
};

// Cet objet est fourni au noyau comme « source » d'actions quand l'écran est actif : donnees = V (pour le pavé).
export const source = { donnees: V, actions, valider };

// Touches physiques (clavier d'ordinateur) pendant l'écran de verrouillage.
export function surTouche(ev) {
  if (/^\d$/.test(ev.key) && V.pin.length < 8) { V.pin += ev.key; V.erreur = ""; V.secoue = 0; rendre(); }
  else if (ev.key === "Backspace") { V.pin = V.pin.slice(0, -1); rendre(); }
  else if (ev.key === "Enter" && V.pin.length >= 4) valider(V.pin);
}

// --- reverrouillage automatique --------------------------------------------------------------------------
// Toute action de l'utilisateur repousse l'inactivité ; sans action pendant N minutes, on reverrouille.
export function signalerActivite() { dernierSignal = Date.now(); }

export function demarrerSurveillance() {
  setInterval(() => {
    if (V.actif || session.auth?.local || (session.demo && !window.YDZ_VERROU_DEMO)) return;
    if (!session.auth?.connecte) return;
    if (Date.now() - dernierSignal > minutesInactivite() * 60000) verrouiller("inactivite");
  }, 15000);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { cacheDepuis = Date.now(); return; }
    if (cacheDepuis && Date.now() - cacheDepuis > minutesFond() * 60000
        && !session.auth?.local && !(session.demo && !window.YDZ_VERROU_DEMO)) {
      verrouiller("fond");
    }
    cacheDepuis = null;
  });
}
