// iPhone (1.0.20) : notifications iOS (ANCS) et musique (AMS) par Bluetooth LE — AFFICHAGE SEUL.
// Tout le texte vient de l'iPhone (hostile) : il est nettoyé/borné côté Pi, et toujours échappé ici (esc).
// Restriction Apple : on NE PEUT PAS répondre à une notification depuis le Pi. Seules les commandes musique
// (lecture/pause, suivant, précédent) sont possibles, et elles demandent le code PIN (accès « action »).
import { esc, ic, ilYA } from "../outils.js";
import { S, btn, interrupteur, vide, toast } from "../noyau.js";
import { entete } from "../cadre.js";
import { lire, agir } from "../api.js";

const LIEN = {
  connecte: ["ok", "smartphone", "iPhone connecté", "Tes notifications et ta musique arrivent en direct."],
  attente: ["warn", "smartphone", "En attente de l'iPhone", "Appaire-le et autorise les notifications."],
  indisponible: ["", "bluetooth", "Non connecté", "Active le Bluetooth et appaire ton iPhone."],
};

function blocLien(ip) {
  const [ton, icone, titre, sousDefaut] = LIEN[ip.lien] || LIEN.indisponible;
  const sous = ip.raison || sousDefaut;
  return `<div class="carte bloc iph-lien ${ton}" data-cle="iph-lien">
    <span class="grand-icone ${ton}">${ic(icone)}</span>
    <div><h2>${titre}</h2><p class="texte">${esc(sous)}</p></div>
  </div>`;
}

// Carte « musique en cours » : titre/artiste/album + télécommande simple (commandes AMS).
function carteMedia(ip, grand = false) {
  const m = ip.media || {};
  if (!m.present) return "";
  const connecte = ip.lien === "connecte";
  const cmd = (action, icone, titre) => btn("iph-commande", "", { icone, classe: "rond", titre, d: { cmd: action }, off: !connecte });
  return `<article class="carte iph-media${grand ? " grand" : ""}" data-cle="iph-media">
    <span class="iph-disc${m.en_lecture ? " tourne" : ""}">${ic("disc-3")}</span>
    <div class="iph-media-txt">
      <b>${esc(m.titre || "Musique")}</b>
      <span>${esc([m.artiste, m.album].filter(Boolean).join(" · ") || (m.app || ""))}</span>
      <em>${m.en_lecture ? "lecture en cours" : m.etat === "pause" ? "en pause" : ""}</em>
    </div>
    <div class="iph-media-cmd">
      ${cmd("precedent", "skip-back", "Précédent")}
      ${cmd("lecture_pause", m.en_lecture ? "pause" : "play", m.en_lecture ? "Pause" : "Lecture")}
      ${cmd("suivant", "skip-forward", "Suivant")}
    </div>
  </article>`;
}

function ligneNotif(n) {
  const titre = n.titre || n.app || n.categorie || "Notification";
  const sous = n.app && n.titre ? n.app : n.categorie;
  return `<li class="iph-notif${n.important ? " important" : ""}" data-cle="iph-n-${n.uid}">
    <span class="iph-cat">${ic(n.categorie_icone || "bell")}</span>
    <div class="iph-notif-txt">
      <b>${esc(titre)}</b>${sous ? `<small>${esc(sous)}</small>` : ""}
      ${n.message ? `<p>${esc(n.message)}</p>` : ""}
    </div>
    <time>${n.maj ? ilYA(n.maj) : ""}</time>
  </li>`;
}

function listeNotifs(ip, max = 0) {
  const l = ip.notifications || [];
  const vues = max ? l.slice(0, max) : l;
  if (!l.length) {
    return `<p class="jour-vide">${ip.lien === "connecte" ? "Aucune notification pour l'instant." : "Les notifications de ton iPhone s'afficheront ici."}</p>`;
  }
  return `<ul class="iph-notifs">${vues.map(ligneNotif).join("")}${max && l.length > max ? `<li class="iph-plus">+ ${l.length - max}</li>` : ""}</ul>`;
}

// --- rappel affichage seul (restriction Apple) : on le dit clairement, une fois.
const RAPPEL_AFFICHAGE = `<p class="note-fine">${ic("lock")}Affichage seul : on ne peut pas répondre à une notification depuis le Pi (restriction Apple). Les commandes musique demandent le code PIN.</p>`;
const BANDEAU_EXPE = `<p class="bandeau">${ic("info")}Expérimental : le lien Bluetooth avec l'iPhone (ANCS/AMS) n'a pas pu être vérifié sur du matériel. Si rien n'apparaît, c'est que l'iPhone n'a pas (encore) autorisé le partage de ses notifications.</p>`;

// =============================================================================== panneau Réglages > iPhone
export const chargerIphone = async (vue) => { vue.iphone = await lire("/api/iphone").catch(() => vue.iphone); };

export function panneauIphone(vue) {
  const ip = vue.iphone || S.etat.iphone || { actif: false, lien: "indisponible", notifications: [], media: {} };
  return `
    <div class="carte bloc" data-cle="iph-reglage">
      <div class="sv-tete"><span class="grand-icone ${ip.actif ? "ok" : ""}">${ic("smartphone")}</span>
        <div><h2>Notifications iPhone (Bluetooth)</h2>
          <p class="texte">Fais apparaître les notifications et la musique de ton iPhone sur l'écran de la tour et dans l'appli, par Bluetooth. En lecture seule : le Pi n'envoie rien à l'iPhone (sauf lecture/pause et suivant/précédent pour la musique).</p></div>
        ${interrupteur("iph-actif", ip.actif, {}, "Activer les notifications iPhone")}</div>
      ${BANDEAU_EXPE}
      ${ip.actif ? `${blocLien(ip)}
        <div class="carte bloc" data-cle="iph-etapes"><h2 class="bloc-titre">${ic("list-ordered")}Pour que ça marche</h2>
          <ol class="etapes">
            <li>Appaire ton iPhone dans <a href="#/reglages/general">Réglages › Général › Bluetooth</a> (accepte l'appairage sur l'iPhone).</li>
            <li>Sur l'iPhone : <b>Réglages › Bluetooth</b>, touche le <b>(i)</b> à côté de « Home Pi Lab », puis active <b>Afficher les notifications</b>.</li>
            <li>Ouvre la page <a href="#/iphone"><b>iPhone</b></a> (menu de gauche) : tes notifications et ta musique s'y affichent.</li>
          </ol>
          ${ip.iphone_appaire ? `<p class="bandeau ok">${ic("circle-check")}Un iPhone est appairé en Bluetooth.</p>` : `<p class="bandeau warn">${ic("triangle-alert")}Aucun iPhone appairé pour l'instant.</p>`}
        </div>` : ""}
      ${RAPPEL_AFFICHAGE}
    </div>`;
}

// Actions partagées par la vue d'app et le panneau Réglages.
export const actionsIphone = {
  async "iph-actif"() {
    const actif = !(S.etat.iphone?.actif ?? this.iphone?.actif);
    const r = await agir("/api/iphone", { actif });
    S.etat.iphone = r;
    if (this.iphone !== undefined) this.iphone = r;
    toast(actif ? "Notifications iPhone activées (expérimental)." : "Notifications iPhone coupées.", actif ? "ok" : undefined);
  },
  async "iph-commande"({ cmd }) {
    const r = await agir("/api/iphone", { commande: cmd });
    toast({ lecture_pause: "Lecture / pause.", suivant: "Morceau suivant.", precedent: "Morceau précédent." }[r.commande] || "Commande envoyée.", "ok");
  },
  async "iph-effacer"() {
    const r = await agir("/api/iphone", { effacer: true });
    S.etat.iphone = r;
    if (this.iphone !== undefined) this.iphone = r;
  },
};

// =============================================================================== vue d'app « iPhone »
export default {
  donnees: {},
  rendu() {
    const ip = S.etat.iphone || { actif: false, lien: "indisponible", notifications: [], media: {} };
    const droite = ip.actif && (ip.notifications || []).length
      ? btn("iph-effacer", "", { icone: "bell-off", classe: "rond discret", titre: "Effacer les notifications" }) : "";
    if (!ip.actif) {
      return `${entete("iPhone")}
        <div class="iphone-vue">${BANDEAU_EXPE}
          ${vide("smartphone", "Notifications iPhone coupées", "Active-les dans Réglages pour voir les notifications et la musique de ton iPhone sur la tour.",
            `<a class="btn plein" href="#/reglages/iphone">${ic("settings")}<span>Ouvrir les réglages</span></a>`)}
        </div>`;
    }
    return `${entete("iPhone", { droite })}
      <div class="iphone-vue">
        ${BANDEAU_EXPE}
        ${blocLien(ip)}
        ${carteMedia(ip)}
        <section class="carte bloc" data-cle="iph-liste">
          <h2 class="bloc-titre">${ic("bell")}Notifications${(ip.notifications || []).length ? ` <em>(${ip.notifications.length})</em>` : ""}</h2>
          ${listeNotifs(ip)}
        </section>
        ${RAPPEL_AFFICHAGE}
      </div>`;
  },
  actions: actionsIphone,
};

// =============================================================================== page d'écran HDMI
// Rendue par accueil.js (page « iphone » de la rotation, seulement quand la fonction est active).
export function vueIphoneEcran(e) {
  const ip = e.iphone || { notifications: [], media: {} };
  const notifs = ip.notifications || [];
  return `<section class="maison ecran-iphone" data-cle="p-iphone">
    <article class="carte iph-ecran-notifs" data-cle="ie-notifs">
      <span class="tuile-nom">${ic("smartphone")}Notifications iPhone${notifs.length ? ` · ${notifs.length}` : ""}</span>
      ${notifs.length ? `<ul class="iph-notifs grand">${notifs.slice(0, 7).map(ligneNotif).join("")}</ul>`
        : `<div class="tuile-corps colonne"><b class="chiffre petit">${ip.lien === "connecte" ? "Rien de neuf" : "En attente de l'iPhone"}</b><p>${esc(ip.raison || "Les notifications s'afficheront ici.")}</p></div>`}
    </article>
    <div class="maison-col">
      ${ip.media?.present ? carteMedia(ip, true) : `<article class="carte iph-media vide" data-cle="ie-media"><span class="tuile-nom">${ic("music")}Musique</span><div class="tuile-corps colonne"><b class="chiffre petit">Rien en lecture</b><p>Lance de la musique sur ton iPhone.</p></div></article>`}
      <article class="carte" data-cle="ie-note"><span class="tuile-nom">${ic("lock")}Affichage seul</span><p class="texte">On ne peut pas répondre depuis la tour (restriction Apple). Expérimental.</p></article>
    </div>
  </section>`;
}
