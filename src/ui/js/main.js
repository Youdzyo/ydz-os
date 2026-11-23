// YDZ Shell : démarrage, coque (barre de navigation, calques), routage, événements en direct, veille.
import { esc, ic, morph, textesAnimes } from "./outils.js";
import * as api from "./api.js";
import { session } from "./api.js";
import {
  S, NOM, definirRendu, rendre, definirSources, executer, dialogues, dialogueCourant, rendreDialogues, messages,
  porteeChamps, toast, demanderPin, confirmer, informer, ouvrir, enteteDialogue, btn,
} from "./noyau.js";
import { installerClavier } from "./clavier.js";
import { suivreTaches, tacheRecue, definirFinTache, ouvrirActivite } from "./cadre.js";
import accueil, { pageCible } from "./vues/accueil.js";
import { ouvrirAppairage } from "./vues/general.js";
import apps from "./vues/apps.js";
import stockage from "./vues/stockage.js";
import reseau from "./vues/reseau.js";
import reglages from "./vues/reglages.js";
import terminal from "./vues/terminal.js";
import historique from "./vues/historique.js";
import agents from "./vues/agents.js";
import iphone from "./vues/iphone.js";
import { ouvrirAssistant } from "./vues/assistant.js";
import * as verrou from "./verrou.js";

const VUES = { accueil, apps, stockage, reseau, terminal, reglages, historique, agents, iphone }; // historique : hors menu (1.0.12)
const MENU = [
  ["accueil", "Accueil", "house"],
  ["apps", "Apps", "layout-grid"],
  ["stockage", "Stockage", "hard-drive"],
  ["reseau", "Réseau", "network"],
  ["agents", "Agents", "bot"],
  ["iphone", "iPhone", "smartphone"],   // 1.0.20 : notifications iOS + musique (BLE, affichage seul)
  ["terminal", "Terminal", "terminal"],
  ["reglages", "Réglages", "settings"],
];
const racine = document.getElementById("ydz");
const vueCourante = () => VUES[S.route.vue] || accueil;

// --------------------------------------------------------------------------------------- coque
function bandeauPin() {
  // 1.0.15 : code à 4 chiffres encore actif -> on invite Justin à passer à 6 chiffres ou plus.
  if (!S.etat?.auth?.pin_court || !(session.auth.local || session.auth.connecte)) return "";
  return `<div class="bandeau-pin" data-cle="bpin">${ic("shield-alert")}
    <p>Ton code PIN ne fait que 4 chiffres. Les codes font maintenant <b>6 chiffres ou plus</b> : change-le pour mieux protéger la tour.</p>
    <a class="btn plein" href="#/reglages/code-pin">${ic("lock")}<span>Changer</span></a></div>`;
}

function cadenas() {
  const a = session.auth;
  if (a.local) return { icone: "monitor", texte: "Écran local", ton: "" };
  if (a.connecte) return { icone: "lock-open", texte: "Déverrouillé", ton: "ok" };
  return { icone: "lock", texte: "Verrouillé", ton: "" };
}

function coque() {
  if (session.verrouille && verrou.estVerrouille()) {
    return `<div class="coque verrou">${verrou.rendu()}</div>`;
  }
  const v = vueCourante();
  const ver = cadenas();
  const alerte = S.etat?.tour.etat;
  if (S.affichage) {
    // Écran d'affichage (kiosque sans souris ni tactile) : le tableau de bord seul, sans menu.
    const anim = `${S.sortie ? " page-sortie" : ""}${S.entree ? " page-entree" : ""}`;
    return `<div class="coque"><main class="scene vue-accueil${anim}" data-cle="vue-affichage">${S.etat ? VUES.accueil.rendu([]) : ""}</main></div>`;
  }
  return `
  <div class="coque">
    <nav class="rail" aria-label="Navigation">
      <a class="logo" href="#/accueil" aria-label="YDZ OS">YDZ</a>
      <div class="rail-menu">
        ${MENU.map(([id, nom, icone]) => `<a class="rail-lien${S.route.vue === id ? " actif" : ""}" href="#/${id}"${S.route.vue === id ? ' aria-current="page"' : ""}>
          ${ic(icone)}<span>${nom}</span>${id === "accueil" && alerte && alerte !== "ok" ? `<i class="point ${alerte}"></i>` : ""}</a>`).join("")}
      </div>
      <button type="button" class="rail-verrou ${ver.ton}" data-action="verrou">${ic(ver.icone)}<span>${ver.texte}</span></button>
    </nav>
    <main class="scene vue-${S.route.vue}" data-cle="vue-${S.route.vue}-${esc(S.route.args.join("-"))}">${bandeauPin()}${S.etat ? v.rendu(S.route.args) : ""}</main>
  </div>
  <div class="calques">${rendreDialogues()}</div>
  <div class="messages" aria-live="polite">${messages.map((m) => `<div class="message ${m.niveau}" data-cle="m${m.id}" data-action="fermer-message" data-id="${m.id}">
    ${ic(m.niveau === "erreur" ? "circle-x" : m.niveau === "ok" ? "circle-check" : "info")}<span>${esc(m.texte)}</span></div>`).join("")}</div>
  ${S.veille ? '<div class="voile-veille" data-cle="veille"></div>' : ""}`;
}

definirRendu(() => {
  morph(racine, coque());
  document.documentElement.classList.toggle("dialogue-ouvert", dialogues.length > 0);
});

const globales = {
  actions: {
    activite: ouvrirActivite,
    "fermer-message"({ id }) { const i = messages.findIndex((m) => m.id === Number(id)); if (i >= 0) messages.splice(i, 1); },
    async verrou() {
      const a = session.auth;
      if (a.local) {
        return informer({ titre: "Écran de la tour", icone: "monitor", texte: "<p>Tu es sur l'écran de la tour : tout est permis sans code.</p><p>Depuis un téléphone ou un ordinateur, les actions demandent le code PIN (Réglages, Code PIN).</p>" });
      }
      if (a.connecte) {
        if (await confirmer({ titre: "Verrouiller cet appareil ?", texte: "Le code PIN sera redemandé à la prochaine action.", libelle: "Verrouiller", icone: "lock" })) {
          await api.deconnexion();
          toast("Appareil verrouillé.");
        }
        return undefined;
      }
      if (!a.pin_defini) {
        return informer({ titre: "Aucun code PIN", icone: "lock", texte: "<p>Depuis cet appareil, tu peux tout voir. Pour agir, choisis d'abord un code PIN sur l'écran de la tour : Réglages, Code PIN.</p>" });
      }
      return demanderPin();
    },
  },
};
definirSources(() => (session.verrouille && verrou.estVerrouille()
  ? [verrou.source]
  : [dialogueCourant(), vueCourante(), globales]).filter(Boolean));

// ------------------------------------------------------------------------------------- routage
function lireRoute() {
  let [vue = "accueil", ...args] = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (S.affichage) { vue = "accueil"; args = []; }
  // 1.0.13 : « Écran » est devenu « Général » (les anciens liens et raccourcis continuent de marcher)
  if (vue === "reglages" && args[0] === "ecran") { args[0] = "general"; history.replaceState(null, "", "#/reglages/general"); }
  const precedente = S.route;
  S.route = { vue: VUES[vue] ? vue : "accueil", args };
  if (precedente.vue !== S.route.vue) VUES[precedente.vue]?.sortir?.();
  vueCourante().entrer?.(args);
  charger();
  rendre();
}
window.addEventListener("hashchange", () => {
  while (dialogues.length) dialogueCourant().fermer();
  lireRoute();
  document.querySelector(".scene")?.scrollTo(0, 0);
});

// Données hors flux (catalogue, disques, cibles de réveil…) : relues régulièrement et après chaque tâche.
let chargement = false;
async function charger() {
  if (chargement || S.veille || document.hidden) return;
  chargement = true;
  try {
    const v = vueCourante();
    await Promise.all([
      api.lire("/api/apps").then((a) => { S.apps = a; }),
      v.charger ? v.charger(S.route.args) : null,
    ]);
  } catch { /* le bandeau « Connexion perdue » suffit */ }
  chargement = false;
  rendre();
}
definirFinTache(() => setTimeout(charger, 150));
setInterval(charger, 5000);
setInterval(() => { if (!S.veille) rendre(); }, 15000); // l'horloge avance même sans événement
// écran du Pi : change de page à l'heure dite (rotation réglée dans Réglages > Général > Écran).
// 1.0.13 : transition animée. Les cartes de l'ancienne page partent à gauche (250 ms), puis la nouvelle page
// arrive de la droite en cascade et ses chiffres montent jusqu'à leur valeur. Seul un changement de page
// anime : les rendus ordinaires (toutes les 2 s) gardent les mêmes nœuds et les mêmes classes.
const sansMouvement = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
let enTransition = false;
function changerPage(cible) {
  if (sansMouvement() || S.etat?.affichage?.nuit?.en_cours || S.etat?.tour?.etat === "crit" || S.pageAff == null) {
    S.pageAff = cible;
    rendre();
    return;
  }
  enTransition = true;
  S.sortie = true;
  rendre();
  setTimeout(() => {
    S.sortie = false;
    S.entree = true;
    S.pageAff = cible;
    rendre();
    requestAnimationFrame(() => requestAnimationFrame(compterChiffres));
    setTimeout(() => { S.entree = false; enTransition = false; rendre(); }, 1100);
  }, 260);
}
setInterval(() => {
  if (!S.affichage || !S.etat || enTransition) return;
  const p = pageCible();
  if (p !== S.pageAff) changerPage(p);
}, 250);

// Chiffres de la page qui arrive : de 0 à leur valeur en 600 ms (une seule fois, à l'entrée de la page).
const RE_NOMBRE = /^(\s*)(\d{1,3}(?:[\u202f\u00a0 ]\d{3})*|\d+)(?:,(\d+))?(\s*)$/;
function compterChiffres() {
  const scene = document.querySelector(".scene.page-entree");
  if (!scene) return;
  const cibles = [];
  scene.querySelectorAll(".chiffre, .temp-grande, .meteo-temp").forEach((el) => {
    const t = el.firstChild;
    if (!t || t.nodeType !== 3) return;
    const m = RE_NOMBRE.exec(t.nodeValue);
    if (!m) return;
    const decimales = m[3] ? m[3].length : 0;
    const valeur = Number(m[2].replace(/[\u202f\u00a0 ]/g, "") + (m[3] ? `.${m[3]}` : ""));
    if (!(valeur > 0)) return;
    textesAnimes.set(t, t.nodeValue);
    cibles.push({ t, valeur, decimales, avant: m[1], apres: m[4] });
  });
  if (!cibles.length) return;
  const debut = performance.now(), duree = 600;
  const fmt = (v, d) => v.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pas = (now) => {
    const x = Math.min(1, (now - debut) / duree);
    const k = 1 - (1 - x) ** 3;
    for (const c of cibles) {
      if (x >= 1) { c.t.nodeValue = textesAnimes.get(c.t); textesAnimes.delete(c.t); continue; }
      c.t.nodeValue = c.avant + fmt(c.valeur * k, c.decimales) + c.apres;
    }
    if (x < 1) requestAnimationFrame(pas);
  };
  requestAnimationFrame(pas);
}

// ---------------------------------------------------------------------------------- événements
function historiser(e) {
  for (const n of e.noeuds) {
    if (!n.en_ligne) continue;
    const h = (S.hist[n.nom] ||= { temp: [], cpu: [], rx: [], tx: [] });
    h.temp.push(n.temp ?? 0); h.cpu.push(n.cpu ?? 0); h.rx.push(n.reseau?.rx_bps ?? 0); h.tx.push(n.reseau?.tx_bps ?? 0);
    for (const k of Object.keys(h)) if (h[k].length > 45) h[k].shift();
  }
}
// Un nœud neuf demande à rejoindre la tour : sur l'écran de la tour, un dialogue Accepter / Refuser.
const demandesVues = new Map(); // "nom|ip" -> expire_s de la dernière demande montrée (une demande renouvelée repart plus haut)
function demandesAppairage(e) {
  if (!session.auth.local || session.demo) return;
  for (const d of e.appairage || []) {
    const cle = `${d.nom}|${d.ip}`;
    if (demandesVues.has(cle) && d.expire_s <= demandesVues.get(cle) + 5) { demandesVues.set(cle, Math.min(demandesVues.get(cle), d.expire_s)); continue; }
    demandesVues.set(cle, d.expire_s);
    // 1.0.15 (F-08) : le dialogue ne s'ouvre tout seul que pour un nom ATTENDU (listé dans « noeuds ») ; pour un
    // nom inconnu (un appareil qui s'annonce « pulse » par surprise), une simple bulle, pas de dialogue d'accord.
    if (d.attendu) ouvrirAppairage(d);
    else toast(`${NOM(d.nom)} (${esc(d.ip)}) demande à rejoindre la tour. Réglages → Général pour accepter si c'est bien ton Pi.`, "info", 8000);
  }
}
let premierEtat = true;
function etatRecu(e) {
  S.etat = e;
  if (e.labo?.nom && document.title !== e.labo.nom) document.title = e.labo.nom;   // 1.0.13 : « Home Pi Lab »
  // écran HDMI : curseur de la souris visible si Réglages > Écran le demande (1.0.12)
  document.documentElement.classList.toggle("affichage-curseur", Boolean(S.affichage && e.affichage?.curseur));
  demandesAppairage(e);
  historiser(e);
  suivreTaches(e.taches, premierEtat);
  premierEtat = false;
  if (session.auth.local && !session.demo && e.ecran?.en_veille && !S.veille) {
    // L'écran dort : on arrête les mises à jour, un voile attend le prochain appui.
    S.veille = true;
    canal.suspendre();
  }
  if (!session.auth.local && e.auth && session.auth.pin_defini !== e.auth.pin_defini) api.rafraichirAuth().then(rendre).catch(() => {});
  rendre();
}
const canal = api.flux({
  surEtat: etatRecu,
  surTache: tacheRecue,
  surNotification: (n) => toast(n.texte, n.niveau === "info" ? "info" : "erreur"),
  surLien: (ok) => { if (S.lien !== ok) { S.lien = ok; rendre(); if (ok) api.rafraichirAuth().then(rendre).catch(() => {}); } },
  surVerrou: () => { session.auth.connecte = false; canal.suspendre(); verrou.verrouiller("session"); },
});

// Déverrouillage réussi : on recharge l'état et on relance le flux en direct.
verrou.definirSurDeverrouillage(async () => {
  session.verrouille = false;
  try { S.etat = await api.lire("/api/etat"); premierEtat = true; historiser(S.etat); } catch { /* reparti */ }
  await api.rafraichirAuth().catch(() => {});
  canal.demarrer();
  charger();
  rendre();
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) canal.suspendre();
  else if (!S.veille) { canal.demarrer(); charger(); }
});

// Activité de l'écran local : réveille le rétroéclairage et repousse la veille (au plus un appel toutes les 10 s).
let dernierSignal = 0;
function signalerActivite(force) {
  if (!session.auth.local || session.demo) return Promise.resolve({});
  if (!force && Date.now() - dernierSignal < 10000) return Promise.resolve({});
  dernierSignal = Date.now();
  return api.agir("/api/ecran/activite").catch(() => ({}));
}
document.addEventListener("pointerdown", (ev) => {
  verrou.signalerActivite();
  S.appui = ev.target;
  if (S.veille) {
    // Premier appui pendant la veille : il réveille l'écran et ne va pas plus loin.
    ev.preventDefault();
    ev.stopPropagation();
    const debut = Date.now();
    signalerActivite(true).then(() => {
      setTimeout(() => { S.veille = false; canal.demarrer(); charger(); rendre(); }, Math.max(0, 450 - (Date.now() - debut)));
    });
    return;
  }
  signalerActivite(false);
}, true);

// ------------------------------------------------------------------------------------- saisie
document.addEventListener("click", (ev) => {
  if (S.veille) { ev.preventDefault(); ev.stopPropagation(); return; }
  const el = ev.target.closest("[data-action]");
  if (!el || el.disabled) return;
  const action = el.dataset.action;
  if (!action) return;
  const { action: _a, ...donnees } = el.dataset;
  executer(action, donnees, el, ev);
});
document.addEventListener("input", (ev) => {
  const el = ev.target;
  if (!el.dataset || el.dataset.champ === undefined) return;
  if (el.dataset.majuscules !== undefined && el.value !== el.value.toUpperCase()) {
    const p = el.selectionStart;
    el.value = el.value.toUpperCase();
    try { el.setSelectionRange(p, p); } catch { /* sans sélection */ }
  }
  const portee = porteeChamps();
  portee[el.dataset.champ] = el.type === "checkbox" ? el.checked : el.value;
  if (el.type === "range" && el.dataset.suivi) executer(el.dataset.suivi, { valeur: el.value }, el, ev);
  if (el.dataset.rendre !== undefined) rendre();
});
document.addEventListener("change", (ev) => {
  const el = ev.target;
  if ((el.type === "range" || el.type === "time" || el.tagName === "SELECT") && el.dataset.fin) executer(el.dataset.fin, { valeur: el.value, ...el.dataset }, el, ev);
  if (el.type === "file" && el.dataset.fichier && el.files?.length) {
    const fichier = el.files[0];
    el.value = "";
    executer(el.dataset.fichier, { fichier }, el, ev);
  }
});
function entree(ev) {
  const el = ev.target;
  if (el.dataset?.entree) { el.blur(); executer(el.dataset.entree, {}, el, ev); }
}
document.addEventListener("ydz-entree", entree);
document.addEventListener("keydown", (ev) => {
  verrou.signalerActivite();
  if (session.verrouille && verrou.estVerrouille()) { if (!ev.target.matches?.("input, textarea")) verrou.surTouche(ev); return; }
  const d = dialogueCourant();
  if (ev.key === "Enter" && ev.target.matches?.("input[data-entree]")) { ev.preventDefault(); entree(ev); return; }
  if (ev.key === "Escape" && d && d.fermable !== false) { d.fermer(); return; }
  if (d?.surTouche && !ev.target.matches?.("input, textarea")) d.surTouche(ev);
});

// ---------------------------------------------------------------------------------- démarrage
async function icones() {
  if (document.getElementById("ydz-icones")) return;
  try {
    const svg = await (await fetch("img/icones.svg")).text();
    const hote = document.createElement("div");
    hote.innerHTML = svg;
    hote.firstElementChild.id = "ydz-icones";
    hote.firstElementChild.setAttribute("aria-hidden", "true");
    document.body.prepend(hote.firstElementChild);
  } catch { /* sans icônes, l'interface reste utilisable */ }
}

async function lancer() {
  const params = new URLSearchParams(location.search);
  S.affichage = params.get("affichage") === "1";
  document.documentElement.classList.toggle("affichage", S.affichage);
  const [premier] = await Promise.all([api.demarrer(), icones()]);
  api.definirDemandePin(demanderPin);
  if (session.verrouille) {
    // Appareil du réseau non déverrouillé : on montre l'écran de verrouillage, rien d'autre n'est chargé.
    document.documentElement.classList.add("pret");
    verrou.verrouiller("demarrage");
    verrou.demarrerSurveillance();
    rendre();
    return;
  }
  S.etat = premier;
  document.documentElement.classList.toggle("affichage-curseur", Boolean(S.affichage && premier.affichage?.curseur));
  historiser(premier);
  const etroit = Math.min(window.innerWidth, window.innerHeight) < 600;
  S.clavier = !S.affichage && (params.has("clavier") ? params.get("clavier") === "1" : session.auth.local && !(session.demo && etroit));
  if (S.clavier) installerClavier();
  document.documentElement.classList.toggle("local", Boolean(session.auth.local));
  document.documentElement.classList.add("pret");
  lireRoute();
  canal.demarrer();
  if (!S.affichage) verrou.demarrerSurveillance();

  let fait = false;
  try { fait = localStorage.getItem("ydz_assistant") === "fait"; } catch { /* stockage indisponible */ }
  const voulu = params.get("assistant");
  if (!S.affichage && (voulu === "1" || (voulu !== "0" && session.auth.local && !session.demo && !premier.auth?.pin_defini && !fait))) ouvrirAssistant();

  if (session.demo && !session.demoForcee) {
    toast("La tour ne répond pas : l'interface montre des données de démonstration.", "info", 8000);
    setInterval(async () => { if (await api.tourRevenue()) location.reload(); }, 10000);
  }
}
lancer().catch((e) => {
  console.error(e);
  racine.innerHTML = `<div class="panne"><h1>YDZ</h1><p>L'interface n'a pas pu démarrer. Recharge la page ; si cela continue, lance <code>sudo ydz diagnostic</code> sur le Pi.</p></div>`;
});
