// Réglages : ventilateur, écran, mises à jour (et historique des paquets), sauvegarde vers le PC, notifications,
// accès à distance (HTTPS Tailscale, 1.0.12), alimentation, code PIN, badges, terminal, diagnostic,
// journal d'activité (1.0.12), à propos.
import { esc, ic, ilYA, borne } from "../outils.js";
import {
  S, NOM, btn, champ, interrupteur, pastille, vide, ouvrir, enteteDialogue, confirmer, choisir, toast, pave, actionsPave, toucheClavierPave, demanderPin, rendre,
} from "../noyau.js";
import { entete } from "../cadre.js";
import { lire, lireProtege, agir, session, changerPin, deconnexion, rafraichirAuth, envoyerPaquet } from "../api.js";
import { alimentation } from "./accueil.js";
import { ouvrirAssistant } from "./assistant.js";
import { panneauGeneral, chargerGeneral, actionsGeneral, offset } from "./general.js";
import { panneauIphone, chargerIphone, actionsIphone } from "./iphone.js";

const SECTIONS = [
  ["ventilateur", "Ventilateur", "fan"],
  ["general", "Général", "sliders-horizontal"],   // 1.0.13 : remplace « Écran » (#/reglages/ecran redirige ici)
  ["perso", "Perso", "calendar"],                 // 1.0.18 : agenda, rappels, Wi-Fi invités
  ["iphone", "iPhone", "smartphone"],             // 1.0.20 : notifications iOS + musique (BLE, affichage seul)

  ["mises-a-jour", "Mises à jour", "download"],
  ["sauvegarde", "Sauvegarde", "hard-drive-upload"],
  ["notifications", "Notifications", "bell"],
  ["agents", "Agents", "bot"],                   // 1.0.19 : cerveau des agents + Canal Claude
  ["acces", "Accès à distance", "globe-lock"],
  ["visiteurs", "Espace visiteurs", "users"],
  ["alimentation", "Redémarrer, éteindre", "power"],
  ["code-pin", "Code PIN", "lock"],
  ["badges", "Badges", "badge-check"],
  ["terminal", "Terminal", "terminal"],
  ["diagnostic", "Diagnostic", "heart-pulse"],
  ["journal", "Journal d'activité", "scroll-text"],
  ["a-propos", "À propos", "info"],
];
const ICONE_MODE = { silence: "volume-x", equilibre: "scale", performance: "rocket" };
const AIDE_MODE = { silence: "Discret, laisse monter la température", equilibre: "Le bon compromis au quotidien", performance: "Refroidit tôt, pour les longues sessions" };

// ---------------------------------------------------------------------------------- ventilateur
function courbe(v) {
  const L = 520, H = 210, g = 44, b = 30, d = 14, h = 12;
  const x = (t) => g + ((borne(t, 30, 85) - 30) / 55) * (L - g - d);
  const y = (p) => h + (1 - p / 100) * (H - h - b);
  const chemin = (pts) => pts.map(([t, p], i) => `${i ? "L" : "M"}${x(t).toFixed(1)} ${y(p).toFixed(1)}`).join(" ");
  const active = v.courbe || [];
  const px = v.temp != null ? x(v.temp) : null, py = y(v.vitesse ?? 0);
  return `<svg class="graphe-ventilo" viewBox="0 0 ${L} ${H}" role="img" aria-label="Courbe du ventilateur : vitesse selon la température">
    ${[0, 25, 50, 75, 100].map((p) => `<line class="grille" x1="${g}" x2="${L - d}" y1="${y(p)}" y2="${y(p)}"/><text class="axe" x="${g - 8}" y="${y(p) + 4}" text-anchor="end">${p}</text>`).join("")}
    ${[30, 40, 50, 60, 70, 80].map((t) => `<text class="axe" x="${x(t)}" y="${H - 8}" text-anchor="middle">${t}°</text>`).join("")}
    ${(v.modes || []).filter((m) => m.id !== v.mode).map((m) => `<path class="autre" d="${chemin(m.courbe)}"/>`).join("")}
    <path class="aire" d="${chemin(active)} L${x(85)} ${y(0)} L${x(30)} ${y(0)} Z"/>
    <path class="active" d="${chemin(active)}"/>
    ${px != null ? `<line class="repere" x1="${px}" x2="${px}" y1="${y(0)}" y2="${py}"/><circle class="halo" cx="${px}" cy="${py}" r="11"/><circle class="point" cx="${px}" cy="${py}" r="5.5"/>` : ""}
  </svg>`;
}
function ventilateur() {
  const v = S.etat.ventilo;
  return `
    ${v.pwm === false ? `<p class="bandeau warn">${ic("triangle-alert")}${esc(v.erreur || "Le ventilateur n'est pas piloté : il tourne à fond par sécurité.")} Voir Diagnostic.</p>` : ""}
    <div class="modes">${(v.modes || []).map((m) => `<button type="button" class="mode${m.id === v.mode ? " actif" : ""}" data-action="mode" data-id="${m.id}">
      ${ic(ICONE_MODE[m.id] || "fan")}<b>${esc(m.nom)}</b><small>${AIDE_MODE[m.id] || ""}</small></button>`).join("")}</div>
    <div class="carte bloc ventilo-detail">
      <div class="ventilo-chiffres">
        <div><small>Vitesse</small><b class="chiffre">${v.vitesse}<small>%</small></b></div>
        <div><small>Nœud le plus chaud</small><b class="chiffre moyen">${v.noeud ? `${NOM(v.noeud)}` : "–"}</b><span>${v.temp != null ? `${v.temp.toFixed(1)} °C` : "température inconnue"}</span></div>
        ${btn("test", v.test ? "Test en cours…" : "Tester 5 s", { icone: "wind", off: v.test })}
      </div>
      ${courbe(v)}
    </div>
    <p class="note-fine">${ic("info")}Le ventilateur suit le nœud le plus chaud. Le point montre où il se trouve sur la courbe du mode choisi.</p>`;
}

// ---------------------------------------------------------------------------------------- écran
const VEILLES = [[0, "Jamais"], [1, "1 min"], [5, "5 min"], [10, "10 min"], [30, "30 min"], [60, "1 h"]];
function glissiere(champNom, valeur, min, max, fin, icone) {
  return `<div class="glissiere">${ic(icone, "pale")}
    <input type="range" min="${min}" max="${max}" step="5" value="${valeur}" data-champ="${champNom}" data-rendre data-fin="${fin}" style="--p:${((valeur - min) / (max - min)) * 100}%" aria-label="Luminosité">
    <b>${valeur} %</b></div>`;
}
function ecran(vue) {
  const e = S.etat.ecran;
  if (!e) return "";
  const lum = vue.donnees.lum ?? e.luminosite;
  return `
    ${e.present ? "" : `<p class="bandeau warn">${ic("triangle-alert")}Aucun écran tactile détecté sur ${esc(NOM(S.etat.hub))}. Ces réglages s'appliqueront quand il sera branché.</p>`}
    <div class="carte bloc"><h2 class="bloc-titre">${ic("sun")}Luminosité</h2>${glissiere("lum", lum, 5, 100, "luminosite", "sun")}</div>
    <div class="carte bloc"><h2 class="bloc-titre">${ic("moon")}Mise en veille</h2>
      <div class="segments">${VEILLES.map(([m, nom]) => `<button type="button" class="${e.veille_min === m ? "actif" : ""}" data-action="veille" data-min="${m}">${nom}</button>`).join("")}</div>
      <p class="note-fine">${e.veille_min ? `L'écran s'éteint après ${e.veille_min} min sans appui. Touche-le pour le rallumer.` : "L'écran reste toujours allumé."}</p></div>
    <div class="carte bloc"><h2 class="bloc-titre">${ic("rotate-cw")}Rotation</h2>
      <div class="segments">${(e.rotations || [0, 90, 180, 270]).map((r) => `<button type="button" class="${e.rotation === r ? "actif" : ""}" data-action="rotation" data-angle="${r}">${r}°</button>`).join("")}</div>
      <p class="note-fine">Si l'image est à l'envers, passe de 90 à 270. L'écran se recharge en quelques secondes.</p></div>
    ${ecranPi(vue)}`;
}

// Décalage par rapport au soleil (1.0.13) : -2 h à +4 h par quarts d'heure.
function selectDecalage(cle, v) {
  const vals = [];
  for (let m = -120; m <= 240; m += 15) vals.push(m);
  return `<select class="champ court" data-fin="nuit-decalage" data-cle-dec="${cle}" aria-label="Décalage">${vals.map((m) => `<option value="${m}"${m === v ? " selected" : ""}>${offset(m)}</option>`).join("")}</select>`;
}
// Écran du Pi en mode affichage (1.0.9) : nuit, pages, météo, prix de l'électricité.
const ROTATIONS = [[0, "Jamais"], [15, "15 s"], [30, "30 s"], [60, "1 min"], [120, "2 min"]];
function ecranPi(vue) {
  const a = S.etat.affichage;
  if (!a) return "";
  const n = a.nuit;
  const mode = n.actif ? n.mode : "non";
  const prix = a.conso?.prix_kwh ?? 0.194;
  return `
    <h3 class="intertitre">Écran d'affichage (HDMI)</h3>
    <div class="carte bloc"><h2 class="bloc-titre">${ic("moon")}La nuit</h2>
      <div class="segments">${[["non", "Rien"], ["eteint", "Écran éteint"], ["horloge", "Horloge douce"]].map(([id, nom]) =>
        `<button type="button" class="${mode === id ? "actif" : ""}" data-action="nuit-mode" data-mode="${id}">${nom}</button>`).join("")}</div>
      ${n.actif ? `<div class="segments">${[["heures", "Heures fixes", "clock"], ["soleil", "Lever et coucher du soleil", "sunset"]].map(([id, nom, icone]) =>
        `<button type="button" class="${(n.plage || "heures") === id ? "actif" : ""}" data-action="nuit-plage" data-plage="${id}">${nom}</button>`).join("")}</div>` : ""}
      ${n.actif && n.plage === "soleil" ? `<div class="plage-soleil">
          <label>${ic("sunset", "pale")}<span>Coucher${n.coucher ? ` (${n.coucher})` : ""}</span>${selectDecalage("nuit_decalage_coucher", n.decalage_coucher)}</label>
          <label>${ic("sunrise", "pale")}<span>Lever${n.lever ? ` (${n.lever})` : ""}</span>${selectDecalage("nuit_decalage_lever", n.decalage_lever)}</label></div>`
      : n.actif ? `<div class="plage-nuit">De <input type="time" class="champ" value="${n.debut}" data-fin="nuit-heure" data-cle-heure="nuit_debut" aria-label="Début de la nuit">
        à <input type="time" class="champ" value="${n.fin}" data-fin="nuit-heure" data-cle-heure="nuit_fin" aria-label="Fin de la nuit"></div>` : ""}
      <p class="note-fine">${!n.actif ? "L'écran reste allumé toute la nuit."
        : n.plage === "soleil" ? `Aujourd'hui : ${n.mode === "eteint" ? "écran en veille" : "horloge douce"} de ${esc(n.debut_effectif || "–")} à ${esc(n.fin_effective || "–")} (soleil à ${esc(a.meteo_ville || "Toulouse")}, calculé sur le Pi).`
        : n.mode === "eteint" ? `L'écran se met en veille de ${n.debut} à ${n.fin}.` : `De ${n.debut} à ${n.fin}, seule une grande horloge très sombre reste affichée.`}${n.actif && n.en_cours ? " C'est la nuit en ce moment." : ""}</p>
      <div class="actions">${btn("test-veille", "Tester la mise en veille (10 s)", { icone: "monitor-off" })}</div>
      <p class="note-fine">En « Écran éteint », le Pi coupe complètement la sortie HDMI : l'écran affiche « pas de signal » puis se met en veille tout seul. Le test le fait pendant 10 secondes, puis l'image revient.</p></div>
    <div class="carte bloc"><h2 class="bloc-titre">${ic("rotate-cw")}Pages qui tournent</h2>
      <div class="segments">${ROTATIONS.map(([v, nom]) => `<button type="button" class="${a.rotation_s === v ? "actif" : ""}" data-action="rotation-pages" data-s="${v}">${nom}</button>`).join("")}</div>
      <p class="note-fine">L'écran alterne entre le labo, la page « maison » (météo, services, PC, consommation) et la page « réseau ».</p></div>
    <div class="carte bloc"><h2 class="bloc-titre">${ic("cloud-sun")}Météo</h2>
      <div class="ligne-champ"><input class="champ" type="text" value="${esc(vue.donnees.ville ?? a.meteo_ville)}" data-champ="ville" data-entree="ville" aria-label="Ville" maxlength="60">
        ${btn("ville", "Changer", { icone: "map-pin" })}</div></div>
    <div class="carte bloc"><h2 class="bloc-titre">${ic("zap")}Prix de l'électricité</h2>
      <div class="ligne-champ"><input class="champ" type="number" step="0.001" min="0.01" max="1.99" value="${vue.donnees.prix ?? prix}" data-champ="prix" data-entree="prix" aria-label="Prix du kWh en euros">
        ${btn("prix", "Enregistrer", { icone: "check" })}</div>
      <p class="note-fine">En euros par kWh, comme sur ta facture (tarif bleu EDF : environ 0,194 €). Sert à estimer le coût de la tour.</p></div>`;
}

// --------------------------------------------------------------------------------- mises à jour
// 1.0.14 : notes de version (markdown léger) rendues SANS jamais injecter de HTML : chaque ligne est échappée
// d'abord, puis seuls **gras**, `code`, titres « # » et listes « - » deviennent des balises connues.
function notesMd(texte, max = 14) {
  const lignes = String(texte || "").split("\n");
  const enLigne = (t) => esc(t).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "", liste = false, n = 0;
  for (const brute of lignes) {
    const l = brute.trim();
    if (!l) { if (liste) { html += "</ul>"; liste = false; } continue; }
    if (++n > max) { html += `${liste ? "</ul>" : ""}<p class="notes-suite">…</p>`; liste = false; break; }
    const puce = l.match(/^(?:[-*+]|\d+[.)])\s+(.*)$/);
    if (puce) { if (!liste) { html += "<ul>"; liste = true; } html += `<li>${enLigne(puce[1])}</li>`; continue; }
    if (liste) { html += "</ul>"; liste = false; }
    const titre = l.match(/^#{1,6}\s+(.*)$/);
    html += titre ? `<h4>${enLigne(titre[1])}</h4>` : `<p>${enLigne(l)}</p>`;
  }
  return html + (liste ? "</ul>" : "");
}
const moTxt = (o) => (o == null ? "" : o >= 1048576 ? `${(o / 1048576).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} Mo` : `${Math.max(1, Math.round(o / 1024))} Ko`);

function majYdz(vue) {
  const m = S.etat.maj_ydz;
  if (!m) return "";
  const p = m.progression;
  const dispo = m.disponibles || [];
  const pretes = dispo.filter((d) => d.prete);
  const recente = p && p.fin && Date.now() / 1000 - p.fin < 86400;
  let tete;
  if (m.en_cours && p) tete = [ic("loader-circle", "tourne"), "cours", p.texte || "Installation…", "YDZ redémarre entre deux versions : l'interface revient toute seule."];
  else if (dispo.length) tete = [ic("package"), "accent", dispo.length > 1 ? `${dispo.length} mises à jour disponibles` : "Mise à jour disponible", pretes.length === dispo.length ? (dispo.length > 1 ? "Téléchargées et vérifiées : prêtes à installer, dans l'ordre." : "Téléchargée et vérifiée : prête à installer.") : m.verification ? "Téléchargement en cours…" : "Téléchargement à la prochaine vérification."];
  else tete = [ic("circle-check"), "ok", `Tu es à jour (${esc(m.version_installee)})`, "Les nouvelles versions arrivent toutes seules depuis GitHub."];
  const etapes = m.en_cours && p ? `<div class="maj-progression"><span class="jauge"><i style="width:${Math.round(((p.index + (p.etape === "redemarrage" ? 0.8 : p.etape === "apres" ? 0.5 : 0.2)) / Math.max(1, p.total)) * 100)}%"></i></span>
      <small>${p.installees?.length ? `Déjà installée${p.installees.length > 1 ? "s" : ""} : ${p.installees.map(esc).join(", ")}` : "Première version en cours"}</small></div>` : "";
  const liste = dispo.length && !m.en_cours ? `<ul class="maj-versions">${dispo.map((d) => `<li data-cle="mv-${esc(d.version)}">
      <div class="mv-tete"><b>${esc(d.nom || `YDZ OS ${d.version}`)}</b>
        ${d.prete ? pastille("ok", `Prête · ${moTxt(d.taille)}`, "circle-check") : d.telechargement ? `<span class="pastille cours">${ic("loader-circle", "tourne")}Téléchargement</span>` : pastille("off", "À télécharger", "cloud-download")}</div>
      <div class="notes-version">${notesMd(d.notes) || `<p>Pas de notes pour cette version.</p>`}</div></li>`).join("")}</ul>` : "";
  const bilan = !m.en_cours && recente && p.etat === "fini" && p.installees?.length ? `<p class="bandeau ok">${ic("circle-check")}Installé${p.installees.length > 1 ? "es" : "e"} ${p.origine === "nuit" ? "cette nuit" : ilYA(p.fin)} : ${p.installees.map(esc).join(", ")}. Un souci ? « Annuler ce paquet » dans l'historique, plus bas.</p>`
    : !m.en_cours && recente && (p.etat === "echec" || p.etat === "interrompue") ? `<p class="bandeau crit">${ic("triangle-alert")}${esc(p.erreur || "L'installation s'est arrêtée.")}</p>` : "";
  return `<div class="carte bloc maj-ydz" data-cle="maj-ydz">
      <div class="sv-tete"><span class="grand-icone ${tete[1]}">${tete[0]}</span>
        <div><h2>${tete[2]}</h2><p class="texte">${tete[3]}</p></div></div>
      ${etapes}${bilan}
      ${m.erreur && !m.en_cours ? `<p class="bandeau warn">${ic("triangle-alert")}${esc(m.erreur)}</p>` : ""}
      ${liste}
      <div class="actions">
        ${dispo.length && !m.en_cours ? btn("ydz-installer", pretes.length > 1 ? `Installer les ${pretes.length} versions` : "Installer", { icone: "download", classe: "plein", off: !pretes.length || m.verification }) : ""}
        ${btn("ydz-verifier", m.verification ? "Recherche…" : "Rechercher des mises à jour", { icone: "refresh-cw", off: m.en_cours || m.verification })}</div>
      <p class="note-fine">${ic("clock")}Dernière vérification ${m.derniere_verification ? ilYA(m.derniere_verification) : "jamais"} · toutes les 6 heures, depuis github.com/${esc(m.depot || "Youdzyo/ydz-os")}</p>
      <label class="reglage en-ligne cadre"><span class="reglage-nom">Installer automatiquement la nuit</span>${interrupteur("ydz-auto-nuit", m.auto_nuit, {}, "Installer automatiquement la nuit")}
        <small>${m.auto_nuit ? `Les versions prêtes s'installent à ${esc(m.heure_nuit || "04:00")}. Une notification te dit ce qui a changé, et « Annuler » reste possible.` : "Coupé : rien ne s'installe sans ton accord. Tu es prévenu dès qu'une version est prête."}</small></label>
    </div>`;
}

function misesAJour(vue) {
  const m = vue.maj;
  if (!m) return `${majYdz(vue)}<div class="attente">${ic("loader-circle", "tourne")}</div>`;
  const total = m.noeuds.reduce((s, n) => s + (n.paquets || 0), 0);
  const enCours = m.noeuds.some((n) => n.en_cours);
  return `
    ${majYdz(vue)}
    <h3 class="intertitre">Paquets du système (Debian)</h3>
    <div class="carte bloc maj-resume">
      <div><b class="chiffre moyen">${enCours ? "Mise à jour en cours" : total ? `${total} mise${total > 1 ? "s" : ""} à jour` : "Tout est à jour"}</b>
        <p>YDZ OS ${esc(S.etat.version)} · les correctifs de sécurité s'installent seuls chaque nuit.</p></div>
      <div class="actions">${btn("verifier", "Vérifier", { icone: "refresh-cw", off: enCours })}${btn("appliquer", "Tout installer", { icone: "download", classe: "plein", off: enCours || !total })}</div>
    </div>
    <div class="carte bloc paquet-bloc">
      <h2 class="bloc-titre">${ic("download")}Installer un fichier de mise à jour (secours)</h2>
      <p class="texte">Normalement inutile : les versions arrivent toutes seules. Si GitHub est injoignable, choisis le fichier <b>.zip</b> (depuis Téléchargements sur le PC, ou Fichiers sur le téléphone) : YDZ l'installe et se recharge tout seul. L'ancienne version est gardée en sauvegarde.</p>
      <div class="actions"><label class="btn">${ic("download")}<span>Choisir un fichier .zip</span><input type="file" accept=".zip,application/zip,application/x-zip-compressed" data-fichier="paquet" hidden></label></div>
    </div>
    ${historiquePaquets(vue)}
    <div class="pile">${m.noeuds.map((n) => n.erreur
      ? `<div class="carte ligne-reglage" data-cle="mj-${n.noeud}"><span class="voyant off"></span><div><h3>${NOM(n.noeud)}</h3><p>Injoignable : ${esc(n.erreur)}</p></div></div>`
      : `<div class="carte ligne-reglage colonne" data-cle="mj-${n.noeud}">
          <div class="ligne"><span class="voyant ${n.paquets ? "warn" : "ok"}"></span>
            <div><h3>${NOM(n.noeud)}</h3><p>${n.paquets == null ? "Jamais vérifié" : n.paquets ? `${n.paquets} paquet${n.paquets > 1 ? "s" : ""} à mettre à jour` : "À jour"} · vérifié ${ilYA(n.derniere_verif)}</p></div>
            ${n.en_cours ? `<span class="pastille cours">${ic("loader-circle", "tourne")}Installation</span>` : n.redemarrage_requis ? pastille("warn", "Redémarrage requis", "rotate-cw") : ""}</div>
          ${n.en_cours && n.journal?.length ? `<pre class="journal mini">${n.journal.slice(-4).map(esc).join("\n")}</pre>` : ""}
          ${!n.en_cours && n.liste?.length ? `<p class="paquets">${n.liste.slice(0, 6).map((p) => esc(p.paquet)).join(", ")}${n.liste.length > 6 ? `, et ${n.liste.length - 6} autres` : ""}</p>` : ""}
        </div>`).join("")}</div>`;
}


// Historique des paquets (1.0.11) : on peut annuler le dernier, un à la fois.
const dateCourte = (t) => (t ? new Date(t * 1000).toLocaleString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "date inconnue");
function historiquePaquets(vue) {
  const h = vue.histo;
  if (!h) return "";
  return `<div class="carte bloc historique-paquets" data-cle="histo">
    <h2 class="bloc-titre">${ic("history")}Historique des paquets</h2>
    ${h.length ? `<ul class="liste-paquets">${h.slice(0, 10).map((x) => `<li class="${x.annulee ? "annule" : ""}" data-cle="hp-${esc(x.id)}">
        <span class="paquet-icone">${ic(x.annulee ? "undo-2" : "download")}</span>
        <div class="paquet-textes"><b>${esc(x.nom || "Paquet sans nom")}</b>
          <small>${dateCourte(x.date)} · ${x.version_avant ? `${esc(x.version_avant)} → ` : ""}${esc(x.version || "?")} · ${x.fichiers} fichier${x.fichiers > 1 ? "s" : ""}${x.complet ? "" : " · sauvegarde ancienne"}</small></div>
        ${x.annulee ? pastille("off", "Annulé", "undo-2") : x.annulable ? btn("annuler-paquet", "Annuler ce paquet", { icone: "undo-2", classe: "discret danger-texte", d: { id: x.id } }) : ""}
      </li>`).join("")}</ul>
      <p class="note-fine">${ic("info")}Seul le dernier paquet installé peut être annulé, puis le précédent, et ainsi de suite. Les 10 plus récents sont gardés.</p>`
    : `<p class="texte">Aucun paquet installé depuis cette interface pour l'instant.</p>`}
  </div>`;
}

// Copie dans le presse-papiers, y compris en http simple (iPhone compris).
async function copierTexte(texte, message = "Commande copiée.") {
  let ok = false;
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(texte); ok = true; } } catch { /* http simple */ }
  if (!ok) {
    const t = document.createElement("textarea");
    t.value = texte; t.setAttribute("readonly", "");
    t.style.cssText = "position:fixed;top:0;left:-9999px;opacity:0";
    document.body.appendChild(t);
    t.select(); t.setSelectionRange(0, texte.length);
    try { ok = document.execCommand("copy"); } catch { /* navigateur ancien */ }
    t.remove();
  }
  toast(ok ? message : "Sélectionne le texte et copie-le à la main.", ok ? "ok" : undefined);
}

// ----------------------------------------------------------------------------- sauvegarde (1.0.11)
const octetsFr = (n) => (n == null ? "–" : n >= 1e9 ? `${(n / 1e9).toFixed(1).replace(".", ",")} Go` : n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(".", ",")} Mo` : `${Math.max(1, Math.round(n / 1e3))} Ko`);
const quand = (t) => {
  if (!t) return "";
  const d = new Date(t * 1000), auj = new Date(), dem = new Date(); dem.setDate(auj.getDate() + 1);
  const hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === auj.toDateString()) return `aujourd'hui à ${hm}`;
  if (d.toDateString() === dem.toDateString()) return `demain à ${hm}`;
  return `${d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" })} à ${hm}`;
};
const GARDES = [3, 7, 14, 30];
// 1.0.12 : « dès que le PC s'allume » (par défaut) ou « à heure fixe »
const INTERVALLES = [[12, "2 fois par jour"], [20, "1 fois par jour"], [44, "Tous les 2 jours"]];
const libelleIntervalle = (h) => (INTERVALLES.find((x) => x[0] === h)?.[1] || `toutes les ${h} h`).toLowerCase();
const hm = (t) => new Date(t * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
function prochaineSauvegarde(s) {
  if (!s.actif) return "–";
  if (s.mode === "heure") return s.prochaine ? quand(s.prochaine) : "–";
  if (s.fait_aujourdhui && s.dernier_succes) return `Fait aujourd'hui à ${hm(s.dernier_succes)}`;
  if (s.prochaine && s.prochaine > Date.now() / 1000 + 120) return `dès que le PC s'allume, après ${quand(s.prochaine)}`;
  return "dès que le PC s'allume";
}
function blocSauvegarde(vue) {
  const s = S.etat.sauvegarde;
  if (!s) return `<div class="attente">${ic("loader-circle", "tourne")}</div>`;
  const d = s.derniere;
  const pcAllume = s.mode !== "heure";
  let tete;
  if (s.en_cours) tete = [`${ic("loader-circle", "tourne")}`, "cours", "Sauvegarde en cours", esc(s.etape || "…")];
  else if (!d) tete = [ic("hard-drive-upload"), "", "Aucune sauvegarde pour l'instant", s.actif ? (pcAllume ? "La première se fera dès que le PC s'allume." : `La première se fera ${quand(s.prochaine)}.`) : "Prépare le PC, puis active la sauvegarde."];
  else if (d.ok) tete = [ic("circle-check"), "ok", `Sauvegardé ${ilYA(d.t)}`, `${octetsFr(d.taille)} envoyés sur le PC · ${esc(d.fichier || "")}`];
  else tete = [ic("triangle-alert"), "crit", "La dernière sauvegarde a échoué", `${esc(d.erreur || "Raison inconnue")} · ${ilYA(d.t)}`];
  const heure = vue.donnees.svHeure ?? s.heure;
  return `
    ${s.smbclient === false ? `<p class="bandeau warn">${ic("triangle-alert")}L'outil smbclient manque sur ${esc(NOM(S.etat.hub))} : réinstalle le paquet 1.0.11, ou lance <code>sudo apt install smbclient</code>.</p>` : ""}
    <div class="carte bloc sauvegarde-etat" data-cle="sv-etat">
      <div class="sv-tete"><span class="grand-icone ${tete[1]}">${tete[0]}</span>
        <div><h2>${tete[2]}</h2><p class="texte">${tete[3]}</p></div>
        ${interrupteur("sv-actif", s.actif, {}, "Sauvegarde automatique")}</div>
      <dl class="fiche">
        <div><dt>Automatique</dt><dd>${!s.actif ? "désactivée" : pcAllume ? `dès que le PC s'allume${s.intervalle_h ? `, ${libelleIntervalle(s.intervalle_h)}` : ""}` : `chaque jour à ${esc(s.heure)}`}</dd></div>
        <div><dt>Prochaine</dt><dd>${prochaineSauvegarde(s)}</dd></div>
        <div><dt>Dernière réussie</dt><dd>${s.dernier_succes ? ilYA(s.dernier_succes) : "jamais"}</dd></div>
        <div><dt>Vers</dt><dd>\\\\${esc(s.hote || "?")}\\${esc(s.partage)}</dd></div>
      </dl>
      <div class="actions">${btn("sv-maintenant", s.en_cours ? "En cours…" : "Sauvegarder maintenant", { icone: "hard-drive-upload", classe: "plein", off: s.en_cours })}${btn("sv-preparer", "Préparer le PC", { icone: "monitor" })}</div>
    </div>
    <div class="carte bloc" data-cle="sv-reglages"><h2 class="bloc-titre">${ic("clock")}Quand et combien</h2>
      <div class="segments">${[["pc_allume", "Dès que le PC s'allume"], ["heure", "À heure fixe"]].map(([m, nom]) => `<button type="button" class="${(pcAllume ? "pc_allume" : "heure") === m ? "actif" : ""}" data-action="sv-mode" data-mode="${m}">${nom}</button>`).join("")}</div>
      ${pcAllume ? `<div class="segments">${INTERVALLES.map(([h, nom]) => `<button type="button" class="${s.intervalle_h === h ? "actif" : ""}" data-action="sv-intervalle" data-h="${h}">${nom}</button>`).join("")}</div>
      <p class="note-fine">Quand le PC répond depuis 2 minutes et que la dernière sauvegarde réussie a plus de ${s.intervalle_h || 20} h, ${esc(NOM(S.etat.hub))} l'envoie tout seul. PC éteint : rien ne t'est signalé, sauf après 3 jours sans sauvegarde.</p>`
      : `<div class="ligne-champ"><span class="libelle-champ">Heure</span><input type="time" class="champ court" value="${esc(heure)}" data-fin="sv-heure" aria-label="Heure de la sauvegarde"></div>
      <p class="note-fine">Si le PC est éteint à cette heure, ${esc(NOM(S.etat.hub))} réessaie toutes les heures jusqu'à 09:00, puis te prévient.</p>`}
      <h3 class="intertitre">Garder sur le PC</h3>
      <div class="segments">${GARDES.map((g) => `<button type="button" class="${s.garder === g ? "actif" : ""}" data-action="sv-garder" data-n="${g}">${g} dernières</button>`).join("")}</div>
      <label class="reglage en-ligne cadre"><span class="reglage-nom">Inclure les données des apps</span>${interrupteur("sv-donnees", s.inclure_donnees_apps, {}, "Inclure les données des apps")}
        <small>${s.inclure_donnees_apps ? "Mondes Minecraft, bases des apps… L'archive peut devenir très grosse." : "Par défaut : configuration de la tour, réglages et mots de passe des apps (quelques Mo)."}</small></label>
    </div>
    <p class="note-fine">${ic("info")}Chaque sauvegarde est un fichier <b>ydz-${esc(S.etat.hub)}-AAAAMMJJ-HHMM.tar.gz</b> dans <b>C:\\YDZ-Sauvegardes</b> : /etc/ydz et /var/lib/ydz (réglages, apps, clés). Il contient des mots de passe : garde ce dossier pour toi.</p>`;
}

function ouvrirPreparerPC(r0) {
  let r = r0;
  const d = ouvrir({
    classe: "large",
    rendu: () => `${enteteDialogue("Préparer le PC", { icone: "monitor", sous: `${esc(r.hote || "PC")} · une seule fois, à refaire seulement si tu changes le mot de passe` })}
      <div class="dlg-corps formulaire">
        <p class="texte">1. Sur le PC, clic droit sur le menu Démarrer, puis <b>Terminal (administrateur)</b> ou <b>Windows PowerShell (administrateur)</b>.</p>
        <p class="texte">2. Copie le bloc ci-dessous, colle-le dans la fenêtre (clic droit) et appuie sur Entrée. Attends le message vert.</p>
        <pre class="commande-pc">${esc(r.script)}</pre>
        <p class="texte">3. Reviens ici et touche <b>Sauvegarder maintenant</b> pour vérifier.</p>
        <p class="note-fine">${ic("info")}Le script crée le compte <b>${esc(r.utilisateur)}</b> (caché de l'écran de connexion), le dossier <b>${esc(r.dossier)}</b>, le partage <b>${esc(r.partage)}</b> réservé à ce compte, et ouvre le port 445 pour ${esc(NOM(S.etat.hub))} (${esc(r.ip_pi)}) seulement, même en réseau « Public ». On peut le relancer sans risque. Le mot de passe ne s'affiche qu'ici.</p>
      </div>
      <footer class="dlg-pied">${btn("nouveau-mdp", "Nouveau mot de passe", { icone: "key-round", classe: "discret" })}${btn("copier", "Copier", { icone: "copy", classe: "discret" })}${btn("fermer", "Terminé", { classe: "plein" })}</footer>`,
    actions: {
      copier() { return copierTexte(r.script); },
      async "nouveau-mdp"() {
        if (!(await confirmer({ titre: "Changer le mot de passe ?", texte: "Forge tire un nouveau mot de passe. Il faudra recoller le script sur le PC, sinon les sauvegardes échoueront.", libelle: "Changer", icone: "key-round" }))) return;
        r = await agir("/api/sauvegarde/preparer-pc", { nouveau_mot_de_passe: true });
        toast("Nouveau mot de passe : recopie le script sur le PC.", "ok");
        rendre();
      },
    },
  });
  return d;
}

// --------------------------------------------------------------------------- notifications (1.0.11)
function blocNotifications(vue) {
  const n = vue.notifs;
  if (!n) return `<div class="attente">${ic("loader-circle", "tourne")}</div>`;
  const dc = n.discord, nt = n.ntfy;
  const ntfySh = /^https:\/\/ntfy\.sh\/?$/.test(nt.serveur);
  return `
    <div class="notifs-grille">
    <div class="carte bloc" data-cle="nt-ntfy">
      <div class="nt-tete"><h2 class="bloc-titre">${ic("smartphone")}iPhone (ntfy)</h2>${interrupteur("nt-ntfy", nt.actif, {}, "Activer ntfy")}</div>
      <div class="sujet-ntfy"><small>Sujet</small><code>${esc(nt.sujet)}</code>${btn("nt-copier-sujet", "", { icone: "copy", classe: "rond discret", titre: "Copier le sujet" })}</div>
      ${nt.erreur ? `<p class="bandeau warn">${ic("triangle-alert")}${esc(nt.erreur)}</p>` : ""}
      <ol class="etapes">
        <li>Installe l'app <b>ntfy</b> depuis l'App Store.</li>
        <li>Touche <b>+</b>, colle le sujet <b>${esc(nt.sujet)}</b>${ntfySh ? "" : `, coche « Use another server » et mets <b>${esc(nt.serveur)}</b>`}, puis <b>Subscribe</b>.</li>
        <li>Autorise les notifications, active le bouton ci-dessus et touche <b>Envoyer un test</b>.</li>
      </ol>
      <div class="ligne-champ"><span class="libelle-champ">Serveur</span><input class="champ" type="text" value="${esc(vue.donnees.ntServeur ?? nt.serveur)}" data-champ="ntServeur" data-entree="nt-serveur" aria-label="Serveur ntfy" maxlength="80">${btn("nt-serveur", "", { icone: "check", classe: "rond", titre: "Enregistrer le serveur" })}</div>
      <p class="note-fine">${ic("lock")}Le sujet sert de mot de passe : qui le connaît peut lire tes alertes. Ne le partage pas.</p>
      <div class="actions">${btn("nt-test", "Envoyer un test", { icone: "send", classe: "plein", d: { canal: "ntfy" }, off: !nt.actif })}${btn("nt-sujet", "Nouveau sujet", { icone: "refresh-cw", classe: "discret" })}</div>
    </div>
    <div class="carte bloc" data-cle="nt-discord">
      <div class="nt-tete"><h2 class="bloc-titre">${ic("bell")}Discord</h2>${interrupteur("nt-discord", dc.actif, {}, "Activer Discord")}</div>
      ${dc.defini ? `<p class="webhook">${ic("circle-check")}<span>${esc(dc.apercu)}</span></p>` : ""}
      ${dc.erreur ? `<p class="bandeau warn">${ic("triangle-alert")}${esc(dc.erreur)}</p>` : ""}
      <ol class="etapes">
        <li>Dans Discord : <b>Paramètres du serveur</b> → <b>Intégrations</b> → <b>Webhooks</b>.</li>
        <li><b>Nouveau webhook</b>, choisis le salon, puis <b>Copier l'URL du webhook</b>.</li>
        <li>Colle-la ici et touche <b>Enregistrer</b>.</li>
      </ol>
      <div class="ligne-champ">${champ("discord", vue.donnees.discord ?? "", { type: "secret", placeholder: dc.defini ? "Coller une nouvelle adresse" : "https://discord.com/api/webhooks/…", label: "Adresse du webhook Discord", entree: "nt-discord-url", max: 220 })}${btn("nt-discord-url", "Enregistrer", { icone: "check" })}</div>
      <div class="actions">${btn("nt-test", "Envoyer un test", { icone: "send", classe: "plein", d: { canal: "discord" }, off: !dc.actif })}${dc.defini ? btn("nt-discord-oublier", "Retirer", { icone: "trash-2", classe: "discret danger-texte" }) : ""}</div>
    </div>
    </div>
    <div class="carte bloc" data-cle="nt-types"><h2 class="bloc-titre">${ic("bell-ring")}Quoi m'envoyer</h2>
      <ul class="types-alertes">${n.types.map((t) => `<li data-cle="ty-${t.id}"><div><b>${esc(t.nom)}</b><small>${esc(t.description)}</small></div>${interrupteur("nt-type", t.actif, { id: t.id }, t.nom)}</li>`).join("")}</ul>
      <p class="note-fine">${ic("info")}Une même alerte part au plus une fois par heure, et jamais plus de 20 par heure en tout.</p>
    </div>
    ${n.recentes?.length ? `<div class="carte bloc" data-cle="nt-recentes"><h2 class="bloc-titre">${ic("history")}Dernières alertes</h2>
      <ul class="liste-alertes">${n.recentes.slice(0, 8).map((a) => `<li class="${esc(a.niveau)}"><i class="voyant ${a.niveau === "info" ? "" : esc(a.niveau)}"></i><span>${esc(a.texte)}</span><small>${ilYA(a.t)}</small></li>`).join("")}</ul></div>` : ""}`;
}

// ------------------------------------------------------------------------ accès à distance (1.0.12)
// Adresses HTTPS dans le tailnet (tailscale serve). Jamais Funnel : rien n'est publié sur Internet.
function blocAcces(vue) {
  const a = vue.acces || S.etat.acces;
  if (!a) return `<div class="attente">${ic("loader-circle", "tourne")}</div>`;
  const pret = a.disponible;
  const tete = a.https_actif && pret ? ["ok", "shield-check", "Adresses HTTPS actives", `Depuis tes appareils Tailscale : <b class="mono">https://${esc(a.nom_dns)}/</b>`]
    : a.https_actif ? ["warn", "triangle-alert", "Adresses HTTPS en pause", esc(a.raison_si_indispo || "Tailscale ne répond pas.")]
    : ["", "globe-lock", "Adresses HTTPS", pret ? `Prêt : YDZ et chaque app auront une adresse <b class="mono">https://${esc(a.nom_dns)}</b>.` : "Trois réglages à faire une fois dans la console Tailscale, puis un bouton ici."];
  return `
    <div class="carte bloc acces-etat" data-cle="ac-etat">
      <div class="sv-tete"><span class="grand-icone ${tete[0]}">${ic(tete[1])}</span>
        <div><h2>${tete[2]}</h2><p class="texte">${tete[3]}</p></div>
        ${interrupteur("acces-https", a.https_actif, {}, "Adresses HTTPS")}</div>
      ${!pret && a.raison_si_indispo && !a.https_actif ? `<p class="bandeau warn">${ic("triangle-alert")}${esc(a.raison_si_indispo)}</p>` : ""}
      ${a.erreur ? `<p class="bandeau warn">${ic("triangle-alert")}${esc(a.erreur)}</p>` : ""}
      <p class="note-fine">${ic("lock")}Seuls tes appareils connectés à Tailscale (ton compte) voient ces adresses. Rien n'est ouvert sur Internet, rien à faire sur la box.</p>
    </div>
    ${a.https_actif && a.liens?.length ? `<div class="carte bloc" data-cle="ac-liens"><h2 class="bloc-titre">${ic("link")}Tes adresses</h2>
      <ul class="liens-https">${a.liens.map((l) => `<li data-cle="al-${esc(l.app)}-${esc(l.noeud)}">
        <span class="app-icone mini">${ic(l.app === "ydz" ? "house" : l.icone || "box")}</span>
        <div class="lien-textes"><b>${esc(l.nom)}${l.app !== "ydz" ? ` <small>${NOM(l.noeud)}</small>` : ""}</b><code>${esc(l.url.replace(/^https:\/\//, ""))}</code></div>
        ${l.actif === false ? `<em class="etiquette">en attente</em>` : ""}
        ${btn("ac-copier", "", { icone: "copy", classe: "rond discret", titre: `Copier l'adresse de ${l.nom}`, d: { url: l.url } })}
        <a class="btn rond" href="${esc(l.url)}" target="_blank" rel="noopener" title="Ouvrir ${esc(l.nom)}" aria-label="Ouvrir ${esc(l.nom)}">${ic("external-link")}</a>
      </li>`).join("")}</ul>
      <p class="note-fine">${ic("info")}Une app qui n'est pas sur ${esc(NOM(S.etat.hub))} passe par lui (relais local). Home Assistant et Nextcloud demandent parfois d'autoriser cette adresse dans leurs propres réglages (proxy de confiance, domaines autorisés).</p></div>` : ""}
    <div class="carte bloc" data-cle="ac-etapes"><h2 class="bloc-titre">${ic("settings")}Dans la console Tailscale (une seule fois)</h2>
      <ol class="etapes">
        <li>Ouvre la console Tailscale, page <b>DNS</b> : <a href="https://login.tailscale.com/admin/dns" target="_blank" rel="noopener">login.tailscale.com/admin/dns</a> (connecte-toi avec ton compte Apple, celui du Pi).</li>
        <li>Section <b>MagicDNS</b> : si le bouton dit <b>Enable MagicDNS</b>, touche-le.${a.magicdns ? " <em class=\"etiquette ok\">fait</em>" : ""}</li>
        <li>Tout en bas, section <b>HTTPS Certificates</b> : touche <b>Enable HTTPS</b>, puis accepte. Reviens ici et active <b>Adresses HTTPS</b>.${a.certificats ? " <em class=\"etiquette ok\">fait</em>" : ""}</li>
      </ol>
      <div class="actions"><a class="btn" href="https://login.tailscale.com/admin/dns" target="_blank" rel="noopener">${ic("external-link")}<span>Console Tailscale (DNS)</span></a>${btn("ac-verifier", "Vérifier", { icone: "refresh-cw", classe: "discret" })}</div>
      <p class="note-fine">${ic("info")}Les certificats HTTPS sont publics : le nom « ${esc(S.etat.hub)} » et celui de ton tailnet (tailXXXX.ts.net) apparaissent dans un registre de certificats. Ton nom et tes données, non.</p>
    </div>`;
}

// ------------------------------------------------------------------------- espace visiteurs (1.0.16)
// Un coin public, en lecture seule, pour les amis. Coupé par défaut ; un lien secret que Justin partage.
function blocVisiteurs(vue) {
  const v = vue.visiteur || S.etat.visiteur;
  if (!v) return `<div class="attente">${ic("loader-circle", "tourne")}</div>`;
  const pret = v.disponible;
  const actif = v.actif;
  const lien = v.lien_public;
  let tete;
  if (actif && v.funnel_ok && lien) tete = ["warn", "globe", "Espace visiteurs ouvert", "Toute personne qui a le lien peut voir tes serveurs de jeu. Rien d'autre."];
  else if (actif) tete = ["crit", "triangle-alert", "Espace visiteurs activé, lien en panne", esc(v.erreur || v.raison_si_indispo || "Le lien public n'a pas pu être créé. Vérifie Tailscale.")];
  else tete = ["", "users", "Espace visiteurs", pret ? "Coupé. Active-le pour obtenir un lien secret à partager avec tes amis." : "Coupé. Active d'abord les adresses HTTPS (Accès à distance), puis reviens ici."];
  const jeux = S.etat.minecraft !== undefined && S.etat.minecraft !== null;
  return `
    <div class="carte bloc visiteur-etat" data-cle="vi-etat">
      <div class="sv-tete"><span class="grand-icone ${tete[0]}">${ic(tete[1])}</span>
        <div><h2>${tete[2]}</h2><p class="texte">${tete[3]}</p></div>
        ${interrupteur("vi-actif", actif, {}, "Espace visiteurs")}</div>
      ${!pret && !actif ? `<p class="bandeau warn">${ic("triangle-alert")}${esc(v.raison_si_indispo || "Tailscale n'est pas prêt.")} <a href="#/reglages/acces">Ouvrir Accès à distance</a></p>` : ""}
      ${actif && v.erreur ? `<p class="bandeau crit">${ic("triangle-alert")}${esc(v.erreur)}</p>` : ""}
      <p class="note-fine">${ic("shield-check")}Les visiteurs voient seulement : tes serveurs de jeu (adresse à rejoindre, en ligne ou non, nombre de joueurs, message d'accueil) et l'état « tout va bien » de la tour. Jamais ton interface, tes autres apps, tes noms d'appareils, tes adresses internes ni tes réglages.</p>
    </div>
    ${actif && lien ? `<div class="carte bloc" data-cle="vi-lien"><h2 class="bloc-titre">${ic("link")}Le lien à partager</h2>
      <div class="lien-visiteur"><code>${esc(lien)}</code></div>
      <p class="bandeau warn">${ic("triangle-alert")}Qui a ce lien peut voir cette page, sans mot de passe. Partage-le seulement à tes amis. Un doute ? Touche « Nouveau lien » : l'ancien ne marchera plus.</p>
      <div class="actions">${btn("vi-copier", "Copier le lien", { icone: "copy", classe: "plein", d: { url: lien } })}${btn("vi-nouveau", "Nouveau lien", { icone: "refresh-cw", classe: "discret" })}</div>
    </div>` : ""}
    ${jeux ? `<div class="carte bloc" data-cle="vi-adresse"><h2 class="bloc-titre">${ic("pickaxe")}Adresse Minecraft</h2>
      <p class="texte">L'adresse que tes amis tapent dans Minecraft pour rejoindre le serveur (par exemple <b>mc.ton-domaine.fr</b> ou l'adresse de ton hébergeur). Elle s'affiche telle quelle sur la page des visiteurs ; laisse vide si tu préfères la donner toi-même.</p>
      <div class="ligne-champ"><input class="champ" type="text" value="${esc(vue.donnees.viAdresse ?? (v.adresses?.minecraft || ""))}" data-champ="viAdresse" data-entree="vi-adresse" placeholder="mc.exemple.fr" aria-label="Adresse publique Minecraft" maxlength="80">${btn("vi-adresse", "Enregistrer", { icone: "check" })}</div>
    </div>` : ""}
    <div class="carte bloc" data-cle="vi-stop"><h2 class="bloc-titre">${ic("power")}Arrêt d'urgence</h2>
      <p class="texte">Coupe tout de suite l'espace visiteurs : le lien ne répond plus, le petit serveur s'arrête et le funnel Tailscale est retiré.</p>
      <div class="actions">${btn("vi-couper", "Tout couper", { icone: "octagon-x", classe: "danger", off: !actif && !v.funnel_ok })}</div>
    </div>
    <p class="note-fine">${ic("info")}L'espace visiteurs tourne dans un petit programme séparé, sans aucun privilège, qui ne sait que lire une liste de données publiques. Il ne peut pas atteindre la tour. ${v.visites_recentes_n != null ? `Visites dans la dernière heure : ${v.visites_recentes_n}.` : ""}</p>`;
}

// ------------------------------------------------------------------------------- perso (1.0.18)
// Agenda du jour (iCloud, lecture seule), rappels du jour, Wi-Fi invités (QR code).
const ALERTES_AGENDA = [[0, "Jamais"], [5, "5 min avant"], [10, "10 min"], [15, "15 min"], [30, "30 min"], [60, "1 h"]];
const quandMaj = (t) => (t ? `à jour ${ilYA(t)}` : "pas encore lu");

function blocAgenda(vue) {
  const ag = vue.perso || S.etat.agenda || {};
  const cals = ag.calendriers || [];
  const alerte = ag.alerte_min ?? 0;
  return `<div class="carte bloc" data-cle="pe-agenda">
    <h2 class="bloc-titre">${ic("calendar")}Agenda du jour (iCloud)</h2>
    <p class="texte">Colle le lien d'un <b>calendrier public</b> iCloud (en lecture seule). Sur l'iPhone : l'app Calendrier, touche un calendrier, <b>Calendrier public</b>, puis <b>Partager le lien</b> et copie-le (il commence par <b>webcal://</b>). YDZ affiche tes rendez-vous du jour sur l'écran. Aucun mot de passe, aucune connexion à ton compte.</p>
    ${cals.length ? `<ul class="agenda-cals">${cals.map((c) => `<li data-cle="ac-${esc(c.id)}">
        <span class="app-icone mini">${ic("calendar")}</span>
        <div class="agenda-cal-textes"><b>${esc(c.nom || c.hote || "Calendrier")}</b>
          <small>${esc(c.hote || "")} · ${c.erreur ? esc(c.erreur) : `${c.evenements_n} événement${c.evenements_n > 1 ? "s" : ""} · ${quandMaj(c.maj)}`}</small></div>
        ${btn("ag-retirer", "", { icone: "trash-2", classe: "rond discret danger-texte", titre: "Retirer ce calendrier", d: { id: c.id } })}
      </li>`).join("")}</ul>` : ""}
    <div class="ligne-champ"><input class="champ" type="text" value="${esc(vue.donnees.agUrl ?? "")}" data-champ="agUrl" data-entree="ag-ajouter" placeholder="webcal://p01.calendar.icloud.com/…" aria-label="Lien du calendrier public" maxlength="400">${btn("ag-ajouter", "Ajouter", { icone: "plus" })}</div>
    <label class="reglage en-ligne cadre"><span class="reglage-nom">Me prévenir avant un rendez-vous</span>
      <select class="champ court" data-fin="ag-alerte" aria-label="Prévenir avant un rendez-vous">${ALERTES_AGENDA.map(([m, nom]) => `<option value="${m}"${m === alerte ? " selected" : ""}>${nom}</option>`).join("")}</select>
      <small>${alerte ? `Une notification ${alerte} min avant chaque rendez-vous à heure fixe.` : "Aucune notification avant les rendez-vous."}</small></label>
    <p class="note-fine">${ic("shield-check")}YDZ ne lit le calendrier que depuis l'adresse que tu colles (HTTPS), toutes les 30 minutes, sans jamais suivre de redirection ailleurs. Rien n'est envoyé, rien n'est modifié dans ton calendrier.</p>
  </div>`;
}

function blocRappels(vue) {
  const rp = (S.etat.rappels?.rappels || []).slice().sort((a, b) => (a.fait === b.fait ? 0 : a.fait ? 1 : -1));
  const faits = rp.filter((r) => r.fait).length;
  return `<div class="carte bloc" data-cle="pe-rappels">
    <h2 class="bloc-titre">${ic("list-todo")}Rappels du jour</h2>
    <div class="ligne-champ"><input class="champ" type="text" value="${esc(vue.donnees.rpTexte ?? "")}" data-champ="rpTexte" data-entree="rp-ajouter" placeholder="Sortir les poubelles…" aria-label="Nouveau rappel" maxlength="120">${btn("rp-ajouter", "Ajouter", { icone: "plus" })}</div>
    ${rp.length ? `<ul class="rappels-reglage">${rp.map((r) => `<li class="${r.fait ? "fait" : ""}" data-cle="rr-${esc(r.id)}">
        <button type="button" class="rappel-case" data-action="rp-basculer" data-id="${esc(r.id)}" aria-label="${r.fait ? "Décocher" : "Cocher"}">${ic(r.fait ? "circle-check" : "square")}</button>
        <span>${esc(r.texte)}</span>
        ${btn("rp-retirer", "", { icone: "trash-2", classe: "rond discret danger-texte", titre: "Retirer", d: { id: r.id } })}
      </li>`).join("")}</ul>
      ${faits ? `<div class="actions">${btn("rp-vider", `Retirer les ${faits} faits`, { icone: "trash-2", classe: "discret" })}</div>` : ""}`
    : `<p class="jour-vide">Aucun rappel pour l'instant. Ils s'affichent aussi sur l'écran de la tour.</p>`}
  </div>`;
}

function blocWifiInvite(vue) {
  const w = S.etat.wifi_invite || {};
  const ouvert = w.securite === "nopass";
  return `<div class="carte bloc" data-cle="pe-wifi">
    <h2 class="bloc-titre">${ic("qr-code")}Wi-Fi invités</h2>
    <p class="texte">Un QR code que tes amis scannent pour rejoindre le Wi-Fi sans taper le mot de passe. Entre le réseau que tu veux partager (par exemple ton Wi-Fi « invités » de la box).</p>
    <div class="ligne-champ"><span class="libelle-champ">Réseau</span><input class="champ" type="text" value="${esc(vue.donnees.wiSsid ?? (w.ssid || ""))}" data-champ="wiSsid" placeholder="Nom du réseau (SSID)" aria-label="Nom du réseau Wi-Fi" maxlength="32"></div>
    <div class="segments">${[["WPA", "Avec mot de passe"], ["nopass", "Réseau ouvert"]].map(([id, nom]) => `<button type="button" class="${(w.securite === id ? id : (w.securite || "WPA")) === id ? "actif" : ""}" data-action="wi-securite" data-s="${id}">${nom}</button>`).join("")}</div>
    ${ouvert ? "" : `<div class="ligne-champ">${champ("wiMdp", vue.donnees.wiMdp ?? "", { type: "secret", placeholder: w.mdp_defini ? "Mot de passe enregistré — laisse vide pour le garder" : "Mot de passe du Wi-Fi", label: "Mot de passe du Wi-Fi", max: 63 })}</div>`}
    <div class="actions">${btn("wi-enregistrer", "Enregistrer", { icone: "check", classe: "plein" })}${w.defini ? btn("wi-qr", "Afficher le QR code", { icone: "qr-code" }) : ""}${w.defini ? btn("wi-retirer", "Retirer", { icone: "trash-2", classe: "discret danger-texte" }) : ""}</div>
    <p class="note-fine">${ic("lock")}Le mot de passe est gardé sur la tour et n'est jamais réaffiché : il ne sert qu'à fabriquer le QR code.</p>
    ${w.defini ? `<label class="reglage en-ligne cadre"><span class="reglage-nom">Montrer le QR sur l'écran de la tour</span>${interrupteur("wi-ecran", w.ecran, {}, "Afficher le QR sur l'écran")}
      <small>${w.ecran ? "Le QR code apparaît sur la page « maison » de l'écran." : "Coupé par défaut : le QR affiché à l'écran expose le mot de passe du Wi-Fi à qui le regarde."}</small></label>
    ${w.ecran ? `<p class="bandeau warn">${ic("triangle-alert")}Le QR est visible par toute personne devant l'écran de la tour : elle peut rejoindre ton Wi-Fi invités en le scannant.</p>` : ""}` : ""}
  </div>`;
}

function blocPerso(vue) {
  return `${blocAgenda(vue)}${blocRappels(vue)}${blocWifiInvite(vue)}`;
}

function ouvrirQrWifi(d) {
  ouvrir({
    classe: "qr-dialogue",
    rendu: () => `${enteteDialogue("Wi-Fi invités", { icone: "qr-code", sous: `${esc(d.ssid || "")} · fais scanner ce code` })}
      <div class="dlg-corps qr-corps">
        <div class="qr-grand">${d.svg}</div>
        <p class="texte">Tes amis ouvrent l'appareil photo de leur téléphone et visent ce code : ils rejoignent le Wi-Fi sans taper le mot de passe.</p>
      </div>
      <footer class="dlg-pied">${btn("fermer", "Fermer", { classe: "plein" })}</footer>`,
    actions: {},
  });
}

// ------------------------------------------------------------------------ journal d'activité (1.0.12)
const FILTRES_JOURNAL = [["tout", "Tout"], ["actions", "Actions"], ["auto", "Automatique"], ["erreurs", "Erreurs"]];
const ICONE_ORIGINE = { ecran: "monitor", reseau: "house", tailscale: "shield-check", auto: "clock" };
function origineTexte(e) {
  if (e.origine === "ecran") return "Écran du Pi";
  if (e.origine === "tailscale") return `Tailscale · ${e.qui || "?"}`;
  if (e.origine === "auto") return "Automatique";
  return `Réseau local · ${e.qui || "?"}`;
}
function jourJournal(t) {
  const d = new Date(t * 1000), auj = new Date(), hier = new Date(); hier.setDate(auj.getDate() - 1);
  if (d.toDateString() === auj.toDateString()) return "Aujourd'hui";
  if (d.toDateString() === hier.toDateString()) return "Hier";
  const s = d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  return s.charAt(0).toUpperCase() + s.slice(1);
}
function blocJournal(vue) {
  const f = vue.donnees.filtreJournal || "tout";
  const filtres = `<div class="puces">${FILTRES_JOURNAL.map(([id, nom]) => `<button type="button" class="puce${f === id ? " actif" : ""}" data-action="jr-filtre" data-f="${id}">${nom}</button>`).join("")}</div>`;
  if (!vue.journal) {
    const permis = session.auth.local || session.auth.connecte || session.demo;
    return `${filtres}${permis ? `<div class="attente">${ic("loader-circle", "tourne")}</div>`
      : vide("lock", "Journal protégé", "Il montre les adresses des appareils qui ont agi : saisis le code PIN pour le lire.", btn("jr-ouvrir", "Saisir le code", { icone: "lock-open", classe: "plein" }))}`;
  }
  const l = vue.journal.entrees || [];
  const jours = [];
  for (const e of l) {
    const j = jourJournal(e.t);
    if (!jours.length || jours[jours.length - 1][0] !== j) jours.push([j, []]);
    jours[jours.length - 1][1].push(e);
  }
  return `${filtres}
    ${l.length ? jours.map(([j, es]) => `<section class="jr-jour" data-cle="jr-${esc(j)}"><h3 class="intertitre">${esc(j)}</h3>
      <ul class="journal-activite">${es.map((e) => `<li class="${e.ok ? "" : "erreur"}${e.type === "auto" ? " auto" : ""}">
        <time>${hm(e.t)}</time><span class="jr-icone">${ic(e.ok ? ICONE_ORIGINE[e.origine] || "info" : "circle-x")}</span>
        <div><b>${esc(e.texte)}</b><small>${esc(origineTexte(e))}</small>${e.erreur ? `<p>${esc(e.erreur)}</p>` : ""}</div></li>`).join("")}</ul></section>`).join("")
      : vide("scroll-text", "Rien pour l'instant", f === "erreurs" ? "Aucune erreur : tout s'est bien passé." : "Les actions faites sur la tour s'afficheront ici.")}
    <p class="note-fine">${ic("info")}Les 2000 derniers événements sont gardés sur ${esc(NOM(S.etat.hub))} (/var/log/ydz/activite.jsonl). Jamais de code PIN, de mot de passe ni d'adresse de webhook.</p>`;
}

// --------------------------------------------------------------------------------- alimentation
function blocAlimentation() {
  const e = S.etat;
  return `
    <div class="pile">${e.noeuds.filter((n) => n.connu).map((n) => `<div class="carte ligne-reglage" data-cle="al-${n.nom}">
      <span class="voyant ${n.en_ligne ? "ok" : "off"}"></span>
      <div><h3>${NOM(n.nom)}</h3><p>${n.en_ligne ? `${esc(n.role_nom || n.role)}${n.redemarrage_requis ? " · redémarrage requis" : ""}` : "Hors ligne"}</p></div>
      <div class="actions">${btn("n-redemarrer", "Redémarrer", { icone: "rotate-cw", d: { nom: n.nom }, off: !n.en_ligne })}${btn("n-eteindre", "Éteindre", { icone: "power", classe: "danger-texte", d: { nom: n.nom }, off: !n.en_ligne })}</div>
    </div>`).join("")}</div>
    <div class="carte bloc tour-entiere">
      <h2 class="bloc-titre">${ic("server")}Toute la tour</h2>
      <p class="texte">Les nœuds s'arrêtent dans l'ordre, ${esc(NOM(S.etat.hub))} en dernier. Attends que les badges affichent « Arrêt en cours » puis s'éteignent avant de couper le courant.</p>
      <div class="actions">${btn("tour-redemarrer", "Redémarrer la tour", { icone: "rotate-cw", classe: "grand" })}${btn("tour-eteindre", "Éteindre la tour", { icone: "power", classe: "danger grand" })}</div>
    </div>`;
}

// ------------------------------------------------------------------------------------- code PIN
// Choix ou changement du code. Renvoie true si le code est enregistré.
export function ouvrirChangementPin() {
  return new Promise((resoudre) => {
    const local = session.auth.local;
    const defini = S.etat.auth?.pin_defini || session.auth.pin_defini;
    const etapes = [...(defini && !local ? ["ancien"] : []), "nouveau", "encore"];
    const titres = { ancien: "Code actuel", nouveau: defini ? "Nouveau code" : "Choisis un code", encore: "Encore une fois" };
    const min6 = S.etat.auth?.pin_min || 6;
    const sous = { ancien: "Saisis le code PIN actuel.", nouveau: `${min6} à 8 chiffres. Évite les suites (123456) et les chiffres identiques.`, encore: "Retape le même code pour vérifier." };
    const d = ouvrir({
      classe: "pin",
      donnees: { pin: "", erreur: "", secoue: 0, i: 0, saisies: {} },
      surFermer: (v) => resoudre(v === true),
      surTouche(ev) { toucheClavierPave(d, ev); },
      rendu: () => {
        const e = etapes[d.donnees.i];
        return `${enteteDialogue(titres[e], { icone: "lock", sous: sous[e] })}
          <div class="dlg-corps">${pave(d.donnees.pin, { erreur: d.donnees.erreur, secoue: d.donnees.secoue, minOk: e === "ancien" ? 4 : (S.etat.auth?.pin_min || 6) })}</div>`;
      },
      actions: actionsPave,
      async valider(pin) {
        const x = d.donnees;
        const e = etapes[x.i];
        x.saisies[e] = pin;
        x.pin = "";
        if (e !== "encore") { x.i++; return; }
        if (x.saisies.encore !== x.saisies.nouveau) {
          Object.assign(x, { i: etapes.indexOf("nouveau"), erreur: "Les deux codes sont différents. Recommence.", secoue: Date.now() });
          return;
        }
        try {
          await changerPin(x.saisies.nouveau, x.saisies.ancien);
          d.fermer(true);
          toast(local ? "Code PIN enregistré." : "Code PIN changé. Saisis-le à la prochaine action.", "ok");
        } catch (err) {
          Object.assign(x, { i: 0, saisies: {}, erreur: err.code === "pin_incorrect" ? "Le code actuel est incorrect." : err.message, secoue: Date.now() });
        }
      },
    });
  });
}
function codePin() {
  const a = session.auth;
  const defini = S.etat.auth?.pin_defini ?? a.pin_defini;
  let action;
  if (a.local) action = btn("pin-changer", defini ? "Changer le code" : "Choisir un code", { icone: "lock", classe: defini ? "" : "plein grand" });
  else if (!defini) action = "";
  else if (a.connecte) action = `${btn("pin-changer", "Changer le code", { icone: "lock" })}${btn("pin-verrouiller", "Verrouiller cet appareil", { icone: "lock" })}`;
  else action = btn("pin-saisir", "Saisir le code", { icone: "lock-open", classe: "plein grand" });
  return `
    <div class="carte bloc pin-etat">
      <span class="grand-icone ${defini ? "ok" : ""}">${ic(defini ? "shield-check" : "lock-open")}</span>
      <div><h2>${defini ? "Un code PIN protège la tour" : "Aucun code PIN"}</h2>
        <p class="texte">${a.local
          ? "Sur cet écran, tout est permis sans code. Depuis un téléphone ou un ordinateur, on peut tout voir, mais il faut le code pour agir."
          : defini ? (a.connecte ? "Cet appareil est déverrouillé : tu peux agir sur la tour." : "Depuis cet appareil, tu peux tout voir. Le code est demandé à la première action.")
          : "Depuis cet appareil, tu peux tout voir mais pas agir. Le premier code se choisit sur l'écran de la tour : Réglages, Code PIN."}</p>
        <div class="actions">${action}</div></div>
    </div>
    <p class="note-fine">${ic("info")}Code oublié ? Sur ${esc(NOM(S.etat.hub))} : <code>sudo ydz pin</code>. Après 5 erreurs, la saisie se bloque 30 s, puis de plus en plus longtemps ; 20 erreurs d'affilée bloquent la connexion 15 minutes.</p>
    ${defini ? blocVerrou(a) : ""}`;
}

// 1.0.15 : réglage du verrouillage automatique (inactivité + retour d'arrière-plan) et déconnexion partout.
function blocVerrou(a) {
  const v = S.etat.auth?.verrou || { inactivite_min: 15, fond_min: 5 };
  const mins = (n) => (n >= 60 ? `${n / 60} h` : `${n} min`);
  return `
    <div class="carte bloc"><h2 class="bloc-titre">${ic("timer-reset")}Verrouillage automatique</h2>
      <p class="texte">Sur un téléphone ou un ordinateur, la tour se reverrouille toute seule après un moment sans rien faire, et quand l'app reste cachée. L'écran de la tour, lui, n'est jamais verrouillé.</p>
      <button type="button" class="reglage-ligne" data-action="verrou-inactivite"><span>${ic("timer")}Après une inactivité de</span><b>${mins(v.inactivite_min)}${ic("chevron-right")}</b></button>
      <button type="button" class="reglage-ligne" data-action="verrou-fond"><span>${ic("eye-off")}Quand l'app est cachée plus de</span><b>${mins(v.fond_min)}${ic("chevron-right")}</b></button>
      ${a.connecte && !a.local ? `<div class="actions">${btn("deconnexion-partout", "Déconnecter tous les appareils", { icone: "lock", classe: "discret" })}</div>` : ""}
    </div>`;
}

// --------------------------------------------------------------------------------------- badges
// L'API ne renvoie pas le sens du badge : l'interface se souvient du dernier choix fait ici.
function retournes() {
  try { return JSON.parse(localStorage.getItem("ydz_badges_retournes") || "{}"); } catch { return {}; }
}
const ACCENTS = [["D9713F", "Terracotta"], ["E8A33D", "Ambre"], ["7FBF8E", "Sauge"], ["5AA9E6", "Azur"], ["B48EE0", "Lilas"], ["F4EFE9", "Ivoire"]];
function badges(vue) {
  const noeuds = S.etat.noeuds.filter((n) => n.connu);
  const ref = noeuds.find((n) => n.badge?.present)?.badge || noeuds[0]?.badge || {};
  const lum = vue.donnees.blum ?? ref.luminosite ?? 80;
  const accent = (ref.accent || "D9713F").toUpperCase();
  return `
    <div class="carte bloc"><h2 class="bloc-titre">${ic("sun")}Luminosité des badges</h2>
      <div class="glissiere">${ic("sun", "pale")}<input type="range" min="0" max="100" step="5" value="${lum}" data-champ="blum" data-rendre data-fin="badge-luminosite" style="--p:${lum}%" aria-label="Luminosité des badges"><b>${lum} %</b></div></div>
    <div class="carte bloc"><h2 class="bloc-titre">${ic("sparkles")}Couleur d'accent</h2>
      <div class="pastilles-couleur">${ACCENTS.map(([hex, nom]) => `<button type="button" class="${accent === hex ? "actif" : ""}" style="--c:#${hex}" data-action="badge-accent" data-hex="${hex}" aria-label="${nom}"><i></i><span>${nom}</span></button>`).join("")}</div></div>
    <div class="pile">${noeuds.map((n) => {
      const b = n.badge || {};
      return `<div class="carte ligne-reglage" data-cle="bd-${n.nom}">
        <span class="mini-badge" style="--c:#${esc(b.accent || "D9713F")}"><b>${NOM(n.nom)}</b><i>${n.temp != null ? Math.round(n.temp) : "–"}°</i></span>
        <div><h3>${NOM(n.nom)}</h3><p>${!n.en_ligne ? "Nœud hors ligne" : b.present ? `Branché · firmware ${esc(b.fw || "?")}` : b.actif === false ? "Badge désactivé dans ydz.conf" : esc(b.erreur || "Badge non détecté : vérifie le câble USB")}</p></div>
        <label class="retourne"><span>Retourné</span>${interrupteur("badge-retourne", !!retournes()[n.nom], { nom: n.nom }, `Retourner le badge de ${n.nom}`)}</label>
      </div>`;
    }).join("")}</div>
    <p class="note-fine">${ic("info")}Un badge neuf reçoit le programme YDZ tout seul au branchement. S'il reste noir : <code>sudo ydz badge flasher</code> sur son Pi.</p>`;
}

// ----------------------------------------------------------------------------------- diagnostic
const ICONE_CONTROLE = { ok: ["circle-check", "ok"], warn: ["triangle-alert", "warn"], err: ["circle-x", "crit"], info: ["info", ""] };
function diagnostic(vue) {
  const noeuds = S.etat.noeuds.filter((n) => n.connu);
  const choisi = vue.donnees.diagNoeud || noeuds[0]?.nom;
  const r = vue.diag && vue.diag.noeud === choisi ? vue.diag : null;
  const soucis = r ? r.controles.filter((c) => c.etat === "warn" || c.etat === "err").length : 0;
  return `
    <div class="carte bloc diag-tete">
      <div class="segments">${noeuds.map((n) => `<button type="button" class="${n.nom === choisi ? "actif" : ""}" data-action="diag-noeud" data-nom="${n.nom}"${n.en_ligne ? "" : " disabled"}>${NOM(n.nom)}</button>`).join("")}</div>
      <div class="actions"><a class="btn" href="#/historique/${esc(choisi)}">${ic("chart-line")}<span>Historique 7 jours</span></a>
      ${btn("diag-lancer", r ? "Relancer" : "Lancer le diagnostic", { icone: "heart-pulse", classe: "plein", d: { nom: choisi } })}</div>
    </div>
    ${r ? `<p class="bandeau ${soucis ? "warn" : "ok"}">${ic(soucis ? "triangle-alert" : "circle-check")}${soucis ? `${soucis} point${soucis > 1 ? "s" : ""} à regarder sur ${NOM(choisi)}.` : `Tout est en ordre sur ${NOM(choisi)}.`}</p>
      <ul class="controles">${r.controles.map((c) => {
        const [icone, ton] = ICONE_CONTROLE[c.etat] || ICONE_CONTROLE.info;
        return `<li class="${ton}" data-cle="c-${esc(c.id)}">${ic(icone)}<div><b>${esc(c.nom)}</b><small>${esc(c.detail)}</small>${c.conseil ? `<p>${esc(c.conseil)}</p>` : ""}</div></li>`;
      }).join("")}</ul>`
      : vide("heart-pulse", "Vérifier le matériel", "Alimentation, ventilateur, écran, disques, badge, Docker : chaque point est contrôlé, avec un conseil si quelque chose cloche.")}`;
}

// -------------------------------------------------------------------------------------- terminal
// Terminal web (ttyd, port 7681) du Pi qui sert l'interface. Connexion avec le compte Linux.
const PORT_TERMINAL = 7681;
function terminal() {
  const url = `${location.protocol}//${location.hostname}:${PORT_TERMINAL}/`;
  return `
    <div class="carte bloc terminal-bloc">
      <h2 class="bloc-titre">${ic("terminal")}Terminal de ${esc(location.hostname)}</h2>
      <p class="texte">Le même terminal qu'en SSH. Connecte-toi avec ton nom d'utilisateur et ton mot de passe Linux.</p>
      <iframe class="terminal-cadre" src="${esc(url)}" title="Terminal" allow="clipboard-read; clipboard-write"></iframe>
      <div class="actions"><a class="btn" href="${esc(url)}" target="_blank" rel="noopener">${ic("external-link")}<span>Ouvrir en plein écran</span></a></div>
      <p class="note-fine">${ic("info")}Écran noir ou « connexion refusée » : le service ydz-terminal n'est pas lancé (sudo systemctl restart ydz-terminal). Réservé au réseau local : n'ouvre jamais le port ${PORT_TERMINAL} sur Internet.</p>
    </div>`;
}

// ------------------------------------------------------------------------------------- à propos
// ------------------------------------------------------------------ agents : cerveau + Canal Claude (1.0.19)
function blocAgents(vue) {
  const d = vue.agentsReg;
  if (!d) return `<div class="attente">${ic("loader-circle", "tourne")}</div>`;
  const c = d.cerveau || {}, ca = d.canal || {};
  const enLigne = c.mode === "online";
  return `
    <div class="carte bloc" data-cle="ag-cerveau">
      <h2 class="bloc-titre">${ic("brain")}Cerveau des agents</h2>
      <p class="texte">Les agents réfléchissent grâce à un modèle de langage. Au choix : <b>en local</b> sur ta tour (Ollama, rien ne sort), ou une <b>API en ligne</b> (plus rapide, mais tes questions partent chez le fournisseur). Dans tous les cas, un agent ne peut jamais lancer de commande ni rien modifier : il ne lit que l'état de la tour.</p>
      <div class="segments">${[["local", "Local (Ollama)"], ["online", "API en ligne"]].map(([v, n]) => `<button type="button" class="${c.mode === v ? "actif" : ""}" data-action="ce-mode" data-v="${v}">${n}</button>`).join("")}</div>
      ${!enLigne ? `<div class="ag-local">
        ${c.ollama_installe ? `<p class="bandeau ok">${ic("circle-check")}L'app Ollama est installée.</p>` : `<p class="bandeau warn">${ic("triangle-alert")}L'app Ollama n'est pas installée. <a href="#/apps/ollama">Installe-la depuis le catalogue</a>, puis télécharge un petit modèle.</p>`}
        <div class="ligne-champ"><span class="libelle-champ">Modèle</span>${champ("modele", vue.donnees.modele ?? c.modele ?? "", { placeholder: "llama3.2:3b", max: 60, entree: "ce-modele", label: "Modèle local" })}${btn("ce-modele", "", { icone: "check", classe: "rond", titre: "Enregistrer le modèle" })}</div>
        <p class="note-fine">${ic("info")}Télécharge un modèle sur la tour : <code>docker exec ydz-ollama-ollama-1 ollama pull llama3.2:3b</code>. Les petits modèles (1 à 4 milliards de paramètres) tournent sur le Pi 5, à quelques mots par seconde.</p>`
      : `<div class="ag-online">
        <div class="ligne-champ"><span class="libelle-champ">Adresse de l'API</span>${champ("online_base", vue.donnees.online_base ?? c.online_base ?? "", { placeholder: "https://api.exemple.com/v1", max: 160, entree: "ce-online", label: "Adresse API" })}</div>
        <div class="ligne-champ">${champ("online_cle", vue.donnees.online_cle ?? "", { type: "secret", placeholder: c.online_cle_definie ? "•••••• (déjà enregistrée)" : "Clé d'API", max: 200, label: "Clé d'API", entree: "ce-online" })}${btn("ce-online", "Enregistrer", { icone: "check" })}</div>
        <div class="ligne-champ"><span class="libelle-champ">Modèle</span>${champ("modele", vue.donnees.modele ?? c.modele ?? "", { placeholder: "gpt-4o-mini", max: 60, entree: "ce-modele", label: "Modèle API" })}${btn("ce-modele", "", { icone: "check", classe: "rond", titre: "Enregistrer le modèle" })}</div>
        <div class="reglage-ligne-sw"><span>${ic("power")}Utiliser l'API en ligne</span>${interrupteur("ce-online-actif", c.online_actif, {}, "Activer l'API en ligne")}</div>
        <p class="note-fine">${ic("lock")}La clé est rangée sur la tour, jamais réaffichée en entier. ${ic("triangle-alert")}En ligne, le contenu de tes tâches sort vers le fournisseur.</p>`}</div>
    </div>
    <div class="carte bloc" data-cle="ag-canal">
      <div class="nt-tete"><h2 class="bloc-titre">${ic("sparkles")}Canal Claude</h2>${interrupteur("ca-actif", ca.actif, {}, "Activer le Canal Claude")}</div>
      <p class="texte">Quand Claude (dans l'appli) fait tourner des agents pour toi, ils peuvent apparaître dans les Bureaux, avec un badge « depuis l'appli Claude ». Transmis par un sujet ntfy secret et <b>signés</b> : seul ce qui porte la bonne signature s'affiche, et <b>rien n'est jamais exécuté</b> sur la tour.</p>
      ${ca.erreur ? `<p class="bandeau warn">${ic("triangle-alert")}${esc(ca.erreur)}</p>` : ""}
      <div class="ag-canal-cle">
        <div><small>Sujet ntfy</small><code>${esc(ca.sujet || "—")}</code>${btn("ca-copier-sujet", "", { icone: "copy", classe: "rond discret", titre: "Copier le sujet" })}</div>
        <div><small>Clé de signature</small><code class="ag-cle">${ca.cle ? esc(ca.cle) : "••••••"}</code>${btn("ca-copier-cle", "", { icone: "copy", classe: "rond discret", titre: "Copier la clé" })}</div>
      </div>
      <p class="note-fine">${ic("info")}Donne-moi une fois ce sujet et cette clé (dans l'appli Claude) pour que je puisse animer mes agents chez toi. Garde-les secrets : qui les a peut faire bouger des personnages à l'écran (rien de plus).</p>
      <div class="actions">${btn("ca-rotation", "Changer la clé", { icone: "refresh-cw", classe: "discret" })}</div>
    </div>`;
}

function aPropos(vue) {
  const s = vue.systeme;
  const e = S.etat;
  return `
    <div class="carte bloc apropos">
      <span class="logo geant">YDZ</span>
      <div><h2>YDZ OS ${esc(e.version)}</h2><p class="texte">Le système de ton labo ${esc(S.etat.labo?.nom || "Home Pi Lab")}. ${e.noeuds.filter((n) => n.connu).length} nœuds, une seule interface.</p>
        ${session.demo ? `<p class="note-fine">${ic("sparkles")}Mode démonstration : les données sont simulées, rien n'est modifié.</p>` : s?.simulation ? `<p class="note-fine">${ic("sparkles")}Le démon tourne en simulation (--sim).</p>` : ""}</div>
    </div>
    ${s ? `<div class="carte bloc"><h2 class="bloc-titre">${ic("server")}${NOM(s.nom)}</h2>
      <dl class="fiche"><div><dt>Modèle</dt><dd>${esc(s.modele)}</dd></div>
      ${Object.entries(s.config || {}).map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      ${[...(s.erreurs_config || []), ...(s.erreurs_catalogue || [])].map((x) => `<p class="bandeau warn">${ic("triangle-alert")}${esc(x)}</p>`).join("")}</div>` : ""}
    <div class="carte bloc"><h2 class="bloc-titre">${ic("sparkles")}Premier démarrage</h2>
      <p class="texte">L'assistant reprend les étapes du début : code PIN, vérification des nœuds et des badges, test du ventilateur, premières applications.</p>
      <div class="actions">${btn("assistant", "Relancer l'assistant", { icone: "rocket" })}</div></div>
    <p class="note-fine">${ic("info")}Polices Unbounded et Instrument Sans (licence SIL OFL 1.1), icônes Lucide (licence ISC). Aucune ressource externe : la tour fonctionne hors ligne.</p>`;
}

// ------------------------------------------------------------------------------------------ vue
const vue = {
  donnees: {},
  maj: null, general: null, wifi: null, bt: null, systeme: null, diag: null, histo: null, notifs: null, acces: null, visiteur: null, journal: null, perso: null, agentsReg: null, iphone: null,
  entrer() { this.donnees.lum = undefined; this.donnees.blum = undefined; },
  async charger(args) {
    const section = args?.[0];
    if (section === "mises-a-jour") {
      const [m, h] = await Promise.all([lire("/api/maj").catch(() => this.maj), lire("/api/maj/sauvegardes").catch(() => null)]);
      this.maj = m;
      if (h) this.histo = h.sauvegardes;
    }
    if (section === "notifications") this.notifs = await lire("/api/notifications").catch(() => this.notifs);
    if (section === "agents") this.agentsReg = await lire("/api/agents").catch(() => this.agentsReg);
    if (section === "acces") this.acces = await lire("/api/acces").catch(() => this.acces);
    if (section === "visiteurs" && (session.auth.local || session.auth.connecte || session.demo)) this.visiteur = await lire("/api/visiteur").catch(() => this.visiteur);
    if (section === "journal" && (session.auth.local || session.auth.connecte || session.demo)) await this.lireJournal(false);
    if (section === "general") await chargerGeneral(this);
    if (section === "iphone") await chargerIphone(this);
    if (section === "perso") this.perso = await lire("/api/agenda").catch(() => this.perso);
    if (section === "a-propos" && !this.systeme) this.systeme = await lire("/api/systeme").catch(() => null);
  },
  async lireJournal(protege) {
    const f = this.donnees.filtreJournal || "tout";
    const chemin = `/api/activite?limite=300&filtre=${f}`;
    try { this.journal = await (protege ? lireProtege(chemin) : lire(chemin)); } catch (e) { if (protege) throw e; }
  },
  rendu(args) {
    const section = SECTIONS.find((s) => s[0] === args[0]);
    const courante = section || SECTIONS[0];
    const panneaux = { ventilateur, general: (v) => panneauGeneral(v, { ecran }), perso: blocPerso, iphone: panneauIphone, "mises-a-jour": misesAJour, sauvegarde: blocSauvegarde, notifications: blocNotifications, agents: blocAgents, acces: blocAcces, visiteurs: blocVisiteurs, journal: blocJournal, alimentation: blocAlimentation, "code-pin": codePin, badges, terminal, diagnostic, "a-propos": aPropos };
    return `${entete(section ? `<span class="large-seul">Réglages</span><span class="etroit-seul">${courante[1]}</span>` : "Réglages", { retour: section ? "#/reglages" : null, retourEtroit: true })}
    <div class="reglages ${section ? "avec-section" : "sans-section"}">
      <nav class="sections" aria-label="Sections des réglages">${SECTIONS.map(([id, nom, icone]) => `<a class="section-lien${courante[0] === id ? " actif" : ""}" href="#/reglages/${id}">${ic(icone)}<span>${nom}</span>${ic("chevron-right", "pale")}</a>`).join("")}</nav>
      <section class="panneau" data-cle="pan-${courante[0]}"><h2 class="panneau-titre">${courante[1]}</h2>${panneaux[courante[0]](this)}</section>
    </div>`;
  },
  actions: {
    ...actionsGeneral,
    ...actionsIphone,
    async mode({ id }) {
      const r = await agir("/api/ventilo", { mode: id });
      S.etat.ventilo = r.ventilo;
      toast(`Ventilateur en mode ${r.ventilo.mode_nom}.`, "ok");
    },
    async test() {
      await agir("/api/ventilo/test", { secondes: 5 });
      S.etat.ventilo.test = true;
      toast("Test : le ventilateur tourne à fond pendant 5 secondes.");
    },
    async luminosite({ valeur }) {
      const r = await agir("/api/ecran", { luminosite: Number(valeur) });
      S.etat.ecran = r.ecran;
      this.donnees.lum = undefined;
    },
    async "nuit-mode"({ mode }) {
      const corps = mode === "non" ? { nuit_actif: false } : { nuit_actif: true, nuit_mode: mode };
      const r = await agir("/api/affichage", corps);
      Object.assign(S.etat.affichage, r);
    },
    async "nuit-heure"({ valeur, cleHeure }) {
      if (!/^\d\d:\d\d$/.test(valeur || "")) return;
      const r = await agir("/api/affichage", { [cleHeure]: valeur });
      Object.assign(S.etat.affichage, r);
      toast("Horaires de nuit enregistrés.", "ok");
    },
    async "test-veille"() {
      await agir("/api/affichage/test-veille");
      toast("L'écran HDMI se coupe pendant 10 secondes : il doit afficher « pas de signal » puis se mettre en veille.", "ok", 9000);
    },
    async "rotation-pages"({ s }) {
      const r = await agir("/api/affichage", { rotation_s: Number(s) });
      Object.assign(S.etat.affichage, r);
    },
    async ville() {
      const v = (this.donnees.ville ?? "").trim();
      if (!v) return;
      const r = await agir("/api/affichage", { meteo_ville: v });
      Object.assign(S.etat.affichage, r);
      this.donnees.ville = undefined;
      toast(`Météo : ${r.meteo_ville}.`, "ok");
    },
    async prix() {
      const v = Number(String(this.donnees.prix ?? "").replace(",", "."));
      if (!(v > 0 && v < 2)) { toast("Prix invalide (par exemple 0.194).", "erreur"); return; }
      const r = await agir("/api/affichage", { prix_kwh: v });
      Object.assign(S.etat.affichage, r);
      this.donnees.prix = undefined;
      toast("Prix du kWh enregistré.", "ok");
    },
    async veille({ min }) {
      const r = await agir("/api/ecran", { veille_min: Number(min) });
      S.etat.ecran = r.ecran;
    },
    async rotation({ angle }) {
      if (Number(angle) === S.etat.ecran.rotation) return;
      if (!(await confirmer({ titre: `Tourner l'écran à ${angle}° ?`, texte: "L'écran de la tour se recharge pendant quelques secondes. Si l'image est à l'envers ensuite, reviens ici et choisis l'angle opposé.", libelle: "Tourner", icone: "rotate-cw" }))) return;
      const r = await agir("/api/ecran", { rotation: Number(angle) });
      S.etat.ecran = r.ecran;
      toast(r.redemarrage_kiosque ? "Rotation enregistrée. L'écran de la tour se recharge." : "Rotation enregistrée.", "ok");
    },
    async paquet({ fichier }) {
      if (!fichier) return;
      if (!/\.zip$/i.test(fichier.name)) { toast("Choisis un fichier .zip (paquet YDZ).", "erreur"); return; }
      if (!(await confirmer({ titre: "Installer ce paquet ?", texte: `${fichier.name} (${Math.max(1, Math.round(fichier.size / 1024))} Ko). YDZ copie les fichiers, garde une sauvegarde de l'ancienne version, puis se recharge.`, libelle: "Installer", icone: "download" }))) return;
      toast("Installation du paquet…");
      const r = await envoyerPaquet(fichier);
      toast(`Paquet « ${r.nom} » installé : ${r.fichiers} fichier${r.fichiers > 1 ? "s" : ""}${r.script ? ", script lancé" : ""}.`, "ok");
      // 1.0.13 : le paquet peut renommer le Pi (forge -> pi-one) : si l'adresse .local d'ici ne répond plus,
      // on essaie la nouvelle avant de recharger.
      const ancien = location.hostname === "forge.local" && /1\.0\.1[3-9]/.test(r.version || r.nom || "");
      const suivre = async (essai = 0) => {
        try { const x = await fetch("/api/ping", { cache: "no-store" }); if (x.ok) { location.reload(); return; } } catch { /* pas encore */ }
        if (ancien && essai > 2) {
          try { await fetch("http://pi-one.local/api/ping", { mode: "no-cors", cache: "no-store" }); location.href = "http://pi-one.local/#/reglages/mises-a-jour"; return; } catch { /* pas encore */ }
        }
        if (essai < 40) setTimeout(() => suivre(essai + 1), 3000);
      };
      setTimeout(suivre, r.redemarrage_demon ? 7000 : 1500);
    },
    async "annuler-paquet"({ id }) {
      const x = (this.histo || []).find((h) => h.id === id);
      if (!x) return;
      const avant = x.version_avant ? `YDZ OS ${x.version_avant}` : "la version d'avant";
      const texte = `Retour à <b>${esc(avant)}</b> : les fichiers remplacés par « ${esc(x.nom || "ce paquet")} » sont remis${x.complet ? ", ceux qu'il a ajoutés sont effacés" : ""}. YDZ redémarre et l'écran se recharge tout seul dans quelques secondes.`;
      const detail = x.complet ? "" : `<p class="bandeau warn">${ic("triangle-alert")}Sauvegarde faite avant 1.0.11 : seuls les fichiers remplacés sont remis. Les fichiers que ce paquet a ajoutés restent en place (ils ne gênent pas).</p>`;
      if (!(await confirmer({ titre: "Annuler ce paquet ?", texte, detail, libelle: "Annuler le paquet", danger: true, annuler: "Garder" }))) return;
      const r = await agir("/api/maj/annuler", { sauvegarde: id });
      toast(`Paquet annulé : retour à ${r.version_avant || "la version d'avant"}. Rechargement…`, "ok");
      setTimeout(() => location.reload(), 9000);
    },
    async "sv-actif"() {
      const r = await agir("/api/sauvegarde", { actif: !S.etat.sauvegarde.actif });
      S.etat.sauvegarde = r;
      toast(r.actif ? (r.mode === "heure" ? `Sauvegarde automatique chaque jour à ${r.heure}.` : "Sauvegarde automatique dès que le PC s'allume.") : "Sauvegarde automatique coupée.", r.actif ? "ok" : undefined);
    },
    async "sv-heure"({ valeur }) {
      if (!/^\d\d:\d\d$/.test(valeur || "")) return;
      S.etat.sauvegarde = await agir("/api/sauvegarde", { heure: valeur });
      this.donnees.svHeure = undefined;
      toast(`Sauvegarde à ${valeur}.`, "ok");
    },
    async "acces-https"() {
      const a = this.acces || S.etat.acces;
      const voulu = !a.https_actif;
      this.acces = await agir("/api/acces", { https: voulu });
      S.etat.acces = this.acces;
      toast(voulu ? "Adresses HTTPS activées : elles marchent depuis tes appareils Tailscale." : "Adresses HTTPS coupées.", voulu ? "ok" : undefined);
    },
    async "ac-verifier"() {
      this.acces = await agir("/api/acces", {});
      S.etat.acces = this.acces;
      toast(this.acces.disponible ? "Tailscale est prêt pour les adresses HTTPS." : this.acces.raison_si_indispo, this.acces.disponible ? "ok" : "erreur");
    },
    "ac-copier"({ url }) { return copierTexte(url, "Adresse copiée."); },
    async "vi-actif"() {
      const v = this.visiteur || S.etat.visiteur;
      const voulu = !v.actif;
      if (voulu && !(await confirmer({ titre: "Ouvrir l'espace visiteurs ?", texte: "Un lien secret est créé. Tout le monde avec ce lien pourra voir tes serveurs de jeu (adresse, joueurs, message d'accueil) — jamais ton interface. Tu peux le couper quand tu veux.", libelle: "Ouvrir", icone: "globe" }))) return;
      this.visiteur = await agir("/api/visiteur", { actif: voulu });
      S.etat.visiteur = this.visiteur;
      toast(voulu ? "Espace visiteurs ouvert. Copie le lien pour tes amis." : "Espace visiteurs fermé.", voulu ? "ok" : undefined);
    },
    "vi-copier"({ url }) { return copierTexte(url, "Lien copié : partage-le à tes amis."); },
    async "vi-nouveau"() {
      if (!(await confirmer({ titre: "Créer un nouveau lien ?", texte: "L'ancien lien cessera aussitôt de marcher : les personnes à qui tu l'avais donné devront recevoir le nouveau.", libelle: "Nouveau lien", icone: "refresh-cw" }))) return;
      this.visiteur = await agir("/api/visiteur/lien");
      S.etat.visiteur = this.visiteur;
      toast("Nouveau lien créé. L'ancien ne marche plus.", "ok");
    },
    async "vi-adresse"() {
      const a = (this.donnees.viAdresse ?? "").trim();
      this.visiteur = await agir("/api/visiteur", { app: "minecraft", adresse: a });
      S.etat.visiteur = this.visiteur;
      this.donnees.viAdresse = undefined;
      toast(a ? "Adresse Minecraft enregistrée." : "Adresse Minecraft retirée.", "ok");
    },
    async "vi-couper"() {
      if (!(await confirmer({ titre: "Tout couper ?", texte: "L'espace visiteurs s'arrête immédiatement et le lien ne répond plus. Tu pourras le rouvrir plus tard.", libelle: "Tout couper", danger: true, icone: "octagon-x" }))) return;
      this.visiteur = await agir("/api/visiteur/couper");
      S.etat.visiteur = this.visiteur;
      toast("Espace visiteurs coupé.", "ok");
    },
    async "jr-filtre"({ f }) { this.donnees.filtreJournal = f; if (this.journal) await this.lireJournal(false); },
    async "jr-ouvrir"() { await this.lireJournal(true); },
    async "sv-mode"({ mode }) { S.etat.sauvegarde = await agir("/api/sauvegarde", { mode }); },
    async "sv-intervalle"({ h }) { S.etat.sauvegarde = await agir("/api/sauvegarde", { intervalle_h: Number(h) }); },
    async curseur() {
      const voulu = !S.etat.affichage.curseur;
      const r = await agir("/api/affichage", { curseur: voulu });
      Object.assign(S.etat.affichage, r);
      toast(voulu ? "Curseur affiché : l'écran HDMI se recharge." : "Curseur caché : l'écran HDMI se recharge.", "ok");
    },
    async "sv-garder"({ n }) { S.etat.sauvegarde = await agir("/api/sauvegarde", { garder: Number(n) }); },
    async "sv-donnees"() { S.etat.sauvegarde = await agir("/api/sauvegarde", { inclure_donnees_apps: !S.etat.sauvegarde.inclure_donnees_apps }); },
    async "sv-maintenant"() {
      S.etat.sauvegarde = await agir("/api/sauvegarde/lancer");
      toast("Sauvegarde lancée : le résultat arrive dans une minute ou deux.");
    },
    async "sv-preparer"() { ouvrirPreparerPC(await agir("/api/sauvegarde/preparer-pc")); },
    async "nt-ntfy"() { this.notifs = await agir("/api/notifications", { ntfy_actif: !this.notifs.ntfy.actif }); },
    async "nt-discord"() {
      if (!this.notifs.discord.defini) { toast("Colle d'abord l'adresse du webhook Discord.", "erreur"); return; }
      this.notifs = await agir("/api/notifications", { discord_actif: !this.notifs.discord.actif });
    },
    async "nt-discord-url"() {
      const v = (this.donnees.discord || "").trim();
      if (!v) return;
      this.notifs = await agir("/api/notifications", { discord: v });
      this.donnees.discord = "";
      toast("Webhook Discord enregistré.", "ok");
    },
    async "nt-discord-oublier"() {
      if (!(await confirmer({ titre: "Retirer Discord ?", texte: "Plus aucune alerte ne partira vers ce salon. Tu pourras recoller une adresse.", libelle: "Retirer", danger: true, icone: "trash-2" }))) return;
      this.notifs = await agir("/api/notifications", { discord: "" });
    },
    async "nt-serveur"() {
      const v = (this.donnees.ntServeur ?? "").trim();
      if (!v) return;
      this.notifs = await agir("/api/notifications", { ntfy_serveur: v });
      this.donnees.ntServeur = undefined;
      toast("Serveur ntfy enregistré.", "ok");
    },
    async "nt-sujet"() {
      if (!(await confirmer({ titre: "Nouveau sujet ntfy ?", texte: "L'ancien sujet ne recevra plus rien : il faudra t'abonner au nouveau dans l'app ntfy.", libelle: "Changer", icone: "refresh-cw" }))) return;
      this.notifs = await agir("/api/notifications", { ntfy_sujet: "" });
    },
    "nt-copier-sujet"() { return copierTexte(this.notifs.ntfy.sujet, "Sujet copié."); },
    async "nt-type"({ id }) {
      const t = this.notifs.types.find((x) => x.id === id);
      this.notifs = await agir("/api/notifications", { types: { [id]: !t.actif } });
    },
    async "nt-test"({ canal }) {
      const r = await agir("/api/notifications/test", { canal });
      const res = r.resultats?.[canal];
      if (res && !res.ok) toast(res.erreur || "L'envoi a échoué.", "erreur");
      else toast(canal === "discord" ? "Test envoyé sur Discord." : "Test envoyé : regarde ton téléphone.", "ok");
      this.notifs = await lire("/api/notifications").catch(() => this.notifs);
    },
    // --- agents : cerveau + Canal Claude (1.0.19) ---
    async "ce-mode"({ v }) { this.agentsReg = await agir("/api/agents/cerveau", { mode: v }); this.agentsReg = await lire("/api/agents").catch(() => this.agentsReg); },
    async "ce-modele"() {
      const m = (this.donnees.modele ?? "").trim();
      if (!m) return;
      await agir("/api/agents/cerveau", { modele: m });
      this.donnees.modele = undefined;
      this.agentsReg = await lire("/api/agents").catch(() => this.agentsReg);
      toast("Modèle enregistré.", "ok");
    },
    async "ce-online"() {
      const corps = {};
      if (this.donnees.online_base !== undefined) corps.online_base = this.donnees.online_base.trim();
      if (this.donnees.online_cle) corps.online_cle = this.donnees.online_cle;
      if (!Object.keys(corps).length) return;
      await agir("/api/agents/cerveau", corps);
      this.donnees.online_cle = undefined; this.donnees.online_base = undefined;
      this.agentsReg = await lire("/api/agents").catch(() => this.agentsReg);
      toast("API en ligne enregistrée.", "ok");
    },
    async "ce-online-actif"() {
      const a = !this.agentsReg.cerveau.online_actif;
      try { await agir("/api/agents/cerveau", { online_actif: a }); }
      catch (e) { toast(e.message, "erreur"); }
      this.agentsReg = await lire("/api/agents").catch(() => this.agentsReg);
    },
    async "ca-actif"() {
      this.agentsReg = await agir("/api/agents", { action: "canal", actif: !this.agentsReg.canal.actif });
      this.agentsReg = await lire("/api/agents").catch(() => this.agentsReg);
    },
    async "ca-rotation"() {
      if (!(await confirmer({ titre: "Changer la clé du Canal Claude ?", texte: "L'ancienne clé et l'ancien sujet cessent de marcher : il faudra me redonner les nouveaux dans l'appli Claude.", libelle: "Changer", icone: "refresh-cw" }))) return;
      this.agentsReg = await agir("/api/agents", { action: "canal", rotation: true });
      this.agentsReg = await lire("/api/agents").catch(() => this.agentsReg);
      toast("Nouvelle clé générée.", "ok");
    },
    "ca-copier-sujet"() { copierTexte(this.agentsReg?.canal?.sujet || "", "Sujet copié."); },
    "ca-copier-cle"() { copierTexte(this.agentsReg?.canal?.cle || "", "Clé copiée."); },
    async "ydz-verifier"() {
      const r = await agir("/api/maj/ydz/verifier");
      if (r.maj_ydz) S.etat.maj_ydz = r.maj_ydz;
      const t = r.tache;
      if (t && t.etat === "erreur") toast(t.message, "erreur");
      else if (t && t.etat === "ok") toast(t.message, "ok");
      else toast("Recherche de mises à jour sur GitHub…");
    },
    async "ydz-installer"() {
      const m = S.etat.maj_ydz;
      const pretes = (m?.disponibles || []).filter((d) => d.prete).map((d) => d.version);
      if (!pretes.length) return;
      const texte = pretes.length > 1
        ? `YDZ OS ${pretes.map(esc).join(", puis ")} s'installent l'une après l'autre. YDZ redémarre entre chaque version et l'écran se recharge ; compte une à deux minutes par version. Chacune garde sa sauvegarde : « Annuler » reste possible, une version à la fois.`
        : `YDZ OS ${esc(pretes[0])} s'installe. YDZ redémarre et l'écran se recharge ; l'ancienne version est gardée en sauvegarde (« Annuler » reste possible).`;
      if (!(await confirmer({ titre: pretes.length > 1 ? `Installer ${pretes.length} versions ?` : `Installer YDZ OS ${pretes[0]} ?`, texte, libelle: "Installer", icone: "download" }))) return;
      const r = await agir("/api/maj/ydz/installer");
      if (r.maj_ydz) S.etat.maj_ydz = r.maj_ydz;
      toast("Installation lancée. L'interface revient toute seule entre deux versions.", "ok");
    },
    async "ydz-auto-nuit"() {
      const r = await agir("/api/maj/reglages", { auto_nuit: !S.etat.maj_ydz?.auto_nuit });
      S.etat.maj_ydz = { ...S.etat.maj_ydz, ...r };
      toast(r.auto_nuit ? `Installation automatique la nuit, à ${r.heure_nuit || "04:00"}.` : "Installation automatique coupée.", r.auto_nuit ? "ok" : undefined);
    },
    async verifier() {
      await agir("/api/maj/verifier");
      toast("Recherche de mises à jour sur tous les nœuds.");
    },
    async appliquer() {
      if (!(await confirmer({ titre: "Installer les mises à jour ?", texte: "Tous les nœuds en ligne se mettent à jour, le Pi principal en dernier. Les applications continuent de tourner ; un redémarrage peut être demandé à la fin.", libelle: "Installer", icone: "download" }))) return;
      const r = await agir("/api/maj/appliquer");
      const rates = (r.noeuds || []).filter((n) => !n.ok);
      if (rates.length) toast(`Échec sur ${rates.map((n) => NOM(n.noeud)).join(", ")} : ${rates[0].erreur || "nœud injoignable"}.`, "erreur");
      else toast("Mise à jour lancée.");
      this.maj = await lire("/api/maj").catch(() => this.maj);
    },
    "n-redemarrer"({ nom }) { return alimentation(nom, "redemarrer"); },
    "n-eteindre"({ nom }) { return alimentation(nom, "eteindre"); },
    async "tour-redemarrer"() {
      if (!(await confirmer({ titre: "Redémarrer toute la tour ?", texte: "Tous les nœuds redémarrent, le Pi principal en dernier. Les applications et cette interface seront coupées une à deux minutes.", libelle: "Redémarrer la tour", icone: "rotate-cw" }))) return;
      await agir("/api/tour/redemarrer");
      toast("La tour redémarre. L'interface revient toute seule.");
    },
    async "tour-eteindre"() {
      if (!(await confirmer({ titre: "Éteindre toute la tour ?", danger: true, libelle: "Éteindre la tour", texte: "Tous les nœuds s'arrêtent proprement, le Pi principal en dernier. Attends que les badges s'éteignent avant de couper le courant. Pour rallumer, il faudra remettre l'alimentation." }))) return;
      await agir("/api/tour/eteindre");
      toast("La tour s'éteint. Attends l'extinction des badges avant de couper le courant.");
    },
    async "pin-changer"() { await ouvrirChangementPin(); },
    "pin-saisir"() { return demanderPin(); },
    async "pin-verrouiller"() { await deconnexion(); toast("Appareil verrouillé."); },
    async "verrou-inactivite"() {
      const v = await choisir({ titre: "Verrouiller après une inactivité de", valeur: S.etat.auth?.verrou?.inactivite_min,
        options: [{ nom: "5 minutes", valeur: 5 }, { nom: "15 minutes", valeur: 15 }, { nom: "30 minutes", valeur: 30 }, { nom: "1 heure", valeur: 60 }] });
      if (v !== undefined) { await agir("/api/auth/verrou", { inactivite_min: v }); await rafraichirAuth(); toast("Enregistré.", "ok"); }
    },
    async "verrou-fond"() {
      const v = await choisir({ titre: "Reverrouiller quand l'app est cachée plus de", valeur: S.etat.auth?.verrou?.fond_min,
        options: [{ nom: "1 minute", valeur: 1 }, { nom: "5 minutes", valeur: 5 }, { nom: "15 minutes", valeur: 15 }, { nom: "30 minutes", valeur: 30 }] });
      if (v !== undefined) { await agir("/api/auth/verrou", { fond_min: v }); await rafraichirAuth(); toast("Enregistré.", "ok"); }
    },
    async "deconnexion-partout"() {
      if (!(await confirmer({ titre: "Déconnecter tous les appareils ?", texte: "Toutes les sessions ouvertes (téléphone, ordinateur) devront ressaisir le code.", libelle: "Déconnecter", icone: "lock" }))) return;
      await agir("/api/auth/deconnexion-partout"); await deconnexion(); toast("Tous les appareils sont déconnectés.", "ok");
    },
    async "badge-luminosite"({ valeur }) {
      await agir("/api/badges", { luminosite: Number(valeur) });
      S.etat.noeuds.forEach((n) => { if (n.badge) n.badge.luminosite = Number(valeur); });
      this.donnees.blum = undefined;
    },
    async "badge-accent"({ hex }) {
      await agir("/api/badges", { accent: hex });
      S.etat.noeuds.forEach((n) => { if (n.badge) n.badge.accent = hex; });
    },
    async "badge-retourne"({ nom }) {
      const etat = retournes();
      const voulu = !etat[nom];
      await agir("/api/badges", { noeud: nom, retourne: voulu });
      etat[nom] = voulu;
      try { localStorage.setItem("ydz_badges_retournes", JSON.stringify(etat)); } catch { /* stockage indisponible */ }
    },
    "diag-noeud"({ nom }) { this.donnees.diagNoeud = nom; },
    async "diag-lancer"({ nom }) {
      this.diag = await lireProtege(`/api/diagnostic?noeud=${encodeURIComponent(nom)}`);
    },
    // --- perso (1.0.18) : agenda, rappels, Wi-Fi invités
    async "ag-ajouter"() {
      const url = (this.donnees.agUrl ?? "").trim();
      if (!url) return;
      this.perso = await agir("/api/agenda", { action: "ajouter", url });
      S.etat.agenda = this.perso;
      this.donnees.agUrl = "";
      toast("Calendrier ajouté : tes rendez-vous apparaissent dans un instant.", "ok");
    },
    async "ag-retirer"({ id }) {
      this.perso = await agir("/api/agenda", { action: "retirer", id });
      S.etat.agenda = this.perso;
      toast("Calendrier retiré.");
    },
    async "ag-alerte"({ valeur }) {
      this.perso = await agir("/api/agenda", { action: "alerte", minutes: Number(valeur) });
      S.etat.agenda = this.perso;
      toast(Number(valeur) ? `Alerte ${valeur} min avant chaque rendez-vous.` : "Alertes de rendez-vous coupées.", "ok");
    },
    async "rp-ajouter"() {
      const texte = (this.donnees.rpTexte ?? "").trim();
      if (!texte) return;
      S.etat.rappels = await agir("/api/rappels", { action: "ajouter", texte });
      this.donnees.rpTexte = "";
    },
    async "rp-basculer"({ id }) { S.etat.rappels = await agir("/api/rappels", { action: "basculer", id }); },
    async "rp-retirer"({ id }) { S.etat.rappels = await agir("/api/rappels", { action: "retirer", id }); },
    async "rp-vider"() { S.etat.rappels = await agir("/api/rappels", { action: "vider" }); },
    async "wi-securite"({ s }) {
      S.etat.wifi_invite = await agir("/api/wifi-invite", { securite: s });
    },
    async "wi-enregistrer"() {
      const ssid = (this.donnees.wiSsid ?? "").trim();
      const w = S.etat.wifi_invite || {};
      if (!ssid && !w.ssid) { toast("Entre d'abord le nom du réseau.", "erreur"); return; }
      const corps = {};
      if (ssid) corps.ssid = ssid;
      if (this.donnees.wiMdp) corps.mdp = this.donnees.wiMdp;
      S.etat.wifi_invite = await agir("/api/wifi-invite", corps);
      this.donnees.wiSsid = undefined;
      this.donnees.wiMdp = "";
      toast("Wi-Fi invités enregistré.", "ok");
    },
    async "wi-qr"() {
      try {
        const d = await lire("/api/wifi-invite/qr");
        ouvrirQrWifi(d);
      } catch (e) {
        toast(e.code === "non_defini" ? "Renseigne d'abord le réseau et le mot de passe." : (e.message || "Impossible d'afficher le QR."), "erreur");
      }
    },
    async "wi-ecran"() {
      const w = S.etat.wifi_invite || {};
      const voulu = !w.ecran;
      if (voulu && !(await confirmer({ titre: "Montrer le QR sur l'écran ?", texte: "Le QR code apparaîtra sur l'écran de la tour. Toute personne devant l'écran pourra le scanner et rejoindre ton Wi-Fi invités.", libelle: "Afficher", icone: "qr-code", danger: true }))) return;
      S.etat.wifi_invite = await agir("/api/wifi-invite", { ecran: voulu });
      toast(voulu ? "QR affiché sur l'écran de la tour." : "QR retiré de l'écran.", voulu ? "ok" : undefined);
    },
    async "wi-retirer"() {
      if (!(await confirmer({ titre: "Retirer le Wi-Fi invités ?", texte: "Le réseau et son mot de passe sont effacés. Le QR ne sera plus disponible.", libelle: "Retirer", danger: true, icone: "trash-2" }))) return;
      S.etat.wifi_invite = await agir("/api/wifi-invite", { ssid: "", ecran: false });
      toast("Wi-Fi invités retiré.");
    },
    assistant() { ouvrirAssistant(); },
  },
};
export default vue;
