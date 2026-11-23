// Accueil : la tour d'un coup d'œil.
import { esc, ic, octets, memoire, debit, debitCourt, duree, pct, trace, ilYA } from "../outils.js";
import { S, NOM, noeud, appCatalogue, btn, jauge, pastille, ouvrir, enteteDialogue, confirmer, toast } from "../noyau.js";
import { etatCommun, heure, jour } from "../cadre.js";
import { agir } from "../api.js";
import { iconeAppareil, grapheDebits, grapheControle, quandDebit, mbit, NIVEAU_WIFI, bande, CONSEIL_WIFI_COURT } from "./reseau.js";
import { creerScene } from "../bureaux.js";
import { vueIphoneEcran } from "./iphone.js";

const ROLE_ICONE = { forge: "gamepad-2", vault: "database", pulse: "radio-tower" };
const tonEtat = { ok: "ok", warn: "warn", crit: "crit", halt: "off" };

function phrase(e) {
  if (e.tour.arret_en_cours) return ["La tour s'arrête", "Attends que les badges s'éteignent avant de couper le courant.", "warn"];
  const horsLigne = e.noeuds.filter((n) => n.connu && !n.en_ligne);
  if (e.tour.etat === "crit") return ["Attention requise", e.tour.alertes[0]?.texte || "", "crit"];
  if (e.tour.alertes.length) return ["À surveiller", e.tour.alertes.map((a) => a.texte).join(" · "), "warn"];
  if (horsLigne.length) return ["À surveiller", `${horsLigne.map((n) => NOM(n.nom)).join(", ")} hors ligne`, "warn"];
  const actifs = e.noeuds.filter((n) => n.en_ligne).length;
  return ["Tout va bien", `${actifs} nœud${actifs > 1 ? "s" : ""} en ligne · ${jour()}`, "ok"];
}

function mesure(icone, nom, valeur, p) {
  return `<div class="mesure"><span class="mesure-nom">${ic(icone)}${nom}</span><b>${valeur}</b>${jauge(p)}</div>`;
}

// Température des dernières minutes : échelle d'au moins 12 °C pour ne pas grossir le bruit du capteur.
function courbeTemp(nom, h) {
  if (!h || h.temp.length < 2) return '<svg class="courbe" viewBox="0 0 200 80" preserveAspectRatio="none" aria-hidden="true"></svg>';
  const lo = Math.min(...h.temp), hi = Math.max(...h.temp);
  const marge = Math.max(0, 12 - (hi - lo)) / 2 + 1;
  const d = trace(h.temp, 200, 72, lo - marge, hi + marge);
  return `<svg class="courbe" viewBox="0 0 200 80" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="deg-${nom}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity="0.28"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
    <path class="aire" d="${d} L200 76 L0 76 Z" transform="translate(0 4)" fill="url(#deg-${nom})"/><path d="${d}" transform="translate(0 4)"/></svg>`;
}

// Appareils vus sur le réseau de la maison (balayage du hub toutes les 5 min) et via Tailscale.
function appareils(r) {
  if (!r || !r.appareils) return "";
  const ts = r.tailscale ? `<span class="appareils-ts">${ic("shield-check")}${r.tailscale} à distance</span>` : "";
  return `<div class="appareils" title="Appareils présents sur le réseau de la maison">
    ${ic("wifi")}<b>${r.appareils}</b><span>appareil${r.appareils > 1 ? "s" : ""} sur le réseau</span>${ts}
  </div>`;
}

function carteNoeud(n) {
  if (!n.connu) {
    return `<article class="carte noeud libre" data-cle="n-${n.nom}">
      <header><div><h2>${NOM(n.nom)}</h2><p>emplacement libre</p></div>${ic(ROLE_ICONE[n.role] || "server", "pale")}</header>
      <p class="libre-texte">Ce Pi n'a pas encore rejoint le labo. Il apparaîtra ici tout seul après son installation.</p>
    </article>`;
  }
  const h = S.hist[n.nom];
  const ton = n.en_ligne ? tonEtat[n.etat] || "ok" : "off";
  const actives = (n.apps || []).filter((a) => a.etat === "actif").length;
  const chaud = n.temp >= 80 ? "crit" : n.temp >= 70 ? "warn" : "";
  return `<article class="carte noeud ${ton}${n.en_ligne ? "" : " hors-ligne"}" data-cle="n-${n.nom}" data-action="noeud" data-nom="${n.nom}" role="button" tabindex="0">
    <header>
      <div><h2>${NOM(n.nom)}</h2><p>${esc(n.role_nom || n.role)}</p></div>
      ${n.en_ligne ? `<span class="voyant ${ton}" aria-label="${ton === "ok" ? "en ligne" : "alerte"}"></span>` : pastille("off", n.etat === "halt" ? "Arrêté" : "Hors ligne", "wifi-off")}
    </header>
    <div class="noeud-temp ${chaud}">
      <span class="temp-grande">${n.temp == null ? "–" : Math.round(n.temp)}<sup>°C</sup></span>
      ${courbeTemp(n.nom, h)}
    </div>
    <div class="mesures">
      ${mesure("cpu", "Processeur", pct(n.cpu), n.cpu)}
      ${mesure("memory-stick", "Mémoire", pct(n.ram?.pct), n.ram?.pct)}
      ${mesure("hard-drive", "Disque", pct(n.disk), n.disk)}
    </div>
    <footer>
      <span>${esc(n.ip || n.adresse || "–")}</span>
      <span>${n.en_ligne ? `${ic("layout-grid")}${actives ? `${actives} app${actives > 1 ? "s" : ""}` : "aucune app"}` : `vu ${ilYA(Date.now() / 1000 - (n.vu_il_y_a || 0))}`}</span>
      ${n.alertes?.length ? `<span class="alerte">${ic("triangle-alert")}${esc(n.alertes[0])}</span>` : ""}
    </footer>
  </article>`;
}

function tuileVentilo(v) {
  const vitesse = v.vitesse ?? 0;
  const tour = vitesse > 0 ? (2.4 - (vitesse / 100) * 1.9).toFixed(2) : 0;
  return `<a class="carte tuile ventilo" href="#/reglages/ventilateur" data-cle="t-ventilo">
    <span class="tuile-nom">${ic("fan")}Ventilateur</span>
    <div class="tuile-corps">
      <span class="helice${v.test ? " test" : ""}" style="--tour:${tour}s">${ic("fan")}</span>
      <div><b class="chiffre">${vitesse}<small>%</small></b>
      <p>${v.pwm === false ? "Non piloté : à fond" : v.test ? "Test en cours" : esc(v.mode_nom)}</p></div>
    </div>
    <span class="tuile-pied">${v.noeud ? `${NOM(v.noeud)} à ${Math.round(v.temp)} °C` : "Température inconnue"}</span>
  </a>`;
}

function tuileStockage(e) {
  const disques = e.noeuds.filter((n) => n.en_ligne).flatMap((n) => (n.disques || []).filter((d) => d.point !== "/").map((d) => ({ ...d, noeud: n.nom })));
  const total = disques.reduce((s, d) => s + d.total, 0), utilise = disques.reduce((s, d) => s + d.utilise, 0);
  const p = total ? (utilise / total) * 100 : 0;
  return `<a class="carte tuile" href="#/stockage" data-cle="t-stockage">
    <span class="tuile-nom">${ic("hard-drive")}Stockage</span>
    ${disques.length ? `<div class="tuile-corps colonne">
        <b class="chiffre">${octets(total - utilise).replace(" ", "<small>")}</small></b>
        <p>libres sur ${octets(total)}</p>
      </div>
      ${jauge(p)}
      <span class="tuile-pied">${disques.length} disque${disques.length > 1 ? "s" : ""} de données · ${Math.round(p)} % utilisés</span>`
    : `<div class="tuile-corps colonne"><b class="chiffre petit">Aucun disque</b><p>de données pour l'instant</p></div>
       <span class="tuile-pied">Ajoute un disque</span>`}
  </a>`;
}

function tuileDebit(e) {
  const enLigne = e.noeuds.filter((n) => n.en_ligne && n.reseau);
  const rx = enLigne.reduce((s, n) => s + (n.reseau.rx_bps || 0), 0), tx = enLigne.reduce((s, n) => s + (n.reseau.tx_bps || 0), 0);
  const longueur = Math.max(0, ...Object.values(S.hist).map((h) => h.rx.length));
  const somme = (cle) => Array.from({ length: longueur }, (_, i) => Object.values(S.hist).reduce((s, h) => s + (h[cle][h[cle].length - longueur + i] || 0), 0));
  const sr = somme("rx"), st = somme("tx");
  const max = Math.max(1, ...sr, ...st);
  const r = debitCourt(rx), t = debitCourt(tx);
  return `<a class="carte tuile debit" href="#/reseau" data-cle="t-debit">
    <span class="tuile-nom">${ic("activity")}Débit</span>
    <div class="debits">
      <div>${ic("arrow-down")}<b class="chiffre moyen">${r.v}</b><small>${r.u}</small></div>
      <div class="tx">${ic("arrow-up")}<b class="chiffre moyen">${t.v}</b><small>${t.u}</small></div>
    </div>
    <svg class="courbe double" viewBox="0 0 240 46" preserveAspectRatio="none" aria-hidden="true">
      ${longueur > 1 ? `<path class="tx" d="${trace(st, 240, 42, 0, max)}" transform="translate(0 2)"/><path d="${trace(sr, 240, 42, 0, max)}" transform="translate(0 2)"/>` : ""}
    </svg>
  </a>`;
}

function tuileApps(e) {
  const actives = e.noeuds.filter((n) => n.en_ligne).flatMap((n) => (n.apps || []).filter((a) => a.etat === "actif").map((a) => ({ ...a, noeud: n.nom })));
  const arretees = e.noeuds.flatMap((n) => (n.apps || []).filter((a) => a.etat !== "actif")).length;
  return `<a class="carte tuile" href="#/apps" data-cle="t-apps">
    <span class="tuile-nom">${ic("layout-grid")}Apps actives</span>
    <div class="tuile-corps"><b class="chiffre">${actives.length}</b>
      <div class="pile-apps">${actives.slice(0, 5).map((a) => `<span class="app-icone mini" title="${esc(appCatalogue(a.id)?.nom || a.id)}">${ic(appCatalogue(a.id)?.icone || "container")}</span>`).join("")}
      ${actives.length > 5 ? `<span class="app-icone mini plus">+${actives.length - 5}</span>` : ""}</div>
    </div>
    ${e.minecraft ? piedMinecraft(e.minecraft)
      : e.maj_apps?.length ? piedMajApps(e.maj_apps)
      : `<span class="tuile-pied">${actives.length ? esc(actives.slice(0, 3).map((a) => appCatalogue(a.id)?.nom || a.id).join(" · ")) : "Installe ta première app"}${arretees ? ` · ${arretees} à l'arrêt` : ""}</span>`}
  </a>`;
}

// Nouvelles versions d'apps (1.0.12) : pied de la tuile Apps quand il n'y a pas de ligne Minecraft.
export function texteMajApps(l) {
  if (!l?.length) return "";
  return l.length === 1 ? `Mise à jour disponible : ${l[0].nom}` : `${l.length} mises à jour : ${l.map((x) => x.nom).join(", ")}`;
}
function piedMajApps(l) {
  return `<span class="tuile-pied maj-apps-pied">${ic("download")}<span>${esc(texteMajApps(l))}</span></span>`;
}

// Joueurs Minecraft (1.0.11) : remplace le pied de la tuile Apps quand le serveur est installé.
export function texteMinecraft(m) {
  if (!m) return "";
  if (!m.en_ligne) return "Minecraft : serveur hors ligne";
  return `Minecraft : ${m.joueurs}/${m.max} joueur${m.joueurs > 1 ? "s" : ""}${m.noms?.length ? ` · ${m.noms.join(", ")}` : ""}`;
}
function piedMinecraft(m) {
  return `<span class="tuile-pied minecraft${m.en_ligne && m.joueurs ? " joueurs" : ""}">${ic("pickaxe")}<span>${esc(texteMinecraft(m))}</span></span>`;
}

// ============================================================================ écran du Pi (mode affichage)
const PAGES = ["tour", "maison", "reseau", "agents"];
// 1.0.20 : la page « iphone » n'entre dans la rotation que si la fonction est active (sinon pas de page vide).
export function pagesAffichees(e) {
  return e?.iphone?.actif ? [...PAGES, "iphone"] : PAGES;
}
// Page voulue à cet instant (rotation réglée dans Réglages > Général > Écran).
export function pageCible() {
  const a = S.etat?.affichage;
  const r = a?.rotation_s || 0;
  const liste = pagesAffichees(S.etat);
  if (!S.affichage || !r || a?.nuit?.en_cours) return 0;
  const q = new URLSearchParams(location.search);
  const force = q.get("page");   // aperçu : ?page=1
  if (force !== null) return Math.min(liste.length - 1, Math.max(0, Number(force) || 0));
  const pas = Number(q.get("pas")) || r;   // aperçu des transitions : ?pas=4 (secondes)
  return Math.floor(Date.now() / 1000 / pas) % liste.length;
}
// Page affichée : celle que main.js a posée à la fin de la transition (1.0.13), sinon la page voulue.
export function pageAffichage() {
  return S.pageAff ?? pageCible();
}
// Rang de chaque carte, pour le décalage de l'entrée en cascade (constant d'un rendu à l'autre : pas de saut).
function rangs(html) {
  let i = 0;
  return html.replace(/<(article|div|a) class="carte\b/g, (m, t) => `<${t} style="--i:${i++}" class="carte`);
}

function meteoMini(m) {
  if (!m) return "";
  return `<div class="meteo-mini" title="${esc(m.ville)} · ${esc(m.texte)}">${ic(m.icone)}<b>${m.temp}°</b></div>`;
}

// 1.0.18 : prochain rendez-vous de l'agenda, discret, sous l'horloge de la page « tour ».
function heureCourte(epoch) {
  return new Date(epoch * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
function agendaProchain(ag) {
  const p = ag?.prochain;
  if (!p) return "";
  const quand = p.journee_entiere ? "aujourd'hui"
    : p.bientot_min != null && p.bientot_min < 90 ? (p.bientot_min < 1 ? "maintenant" : `dans ${p.bientot_min} min`)
    : heureCourte(p.debut);
  return `<div class="agenda-prochain" title="Prochain rendez-vous">${ic("calendar")}<b>${esc(p.titre)}</b><span>${esc(quand)}</span></div>`;
}

function depuis(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 90 ? "à l'instant" : `depuis ${duree(s)}`;
}

function tuilePC(p) {
  const etat = !p ? null : p.en_ligne;
  const ton = etat ? "ok" : etat === false ? "off" : "";
  return `<div class="carte tuile pc ${ton}" data-cle="t-pc">
    <span class="tuile-nom">${ic("monitor")}${esc(p?.nom || "PC")}</span>
    <div class="tuile-corps">
      <span class="pc-ecran ${ton}">${ic(etat ? "monitor" : "monitor-off")}</span>
      <div><b class="chiffre moyen">${etat ? "Allumé" : etat === false ? "Éteint" : "Inconnu"}</b>
      <p>${p && etat !== null ? depuis(p.depuis) : "adresse IP à régler"}</p></div>
    </div>
    <span class="tuile-pied">${etat ? "Il répond sur le réseau" : etat === false ? "Réveil possible depuis l'appli" : "—"}</span>
  </div>`;
}

const TON_SERVICE = { ok: "En ligne", warn: "Ne répond pas", crit: "En panne", off: "À l'arrêt" };
function carteServices(liste, maj = []) {
  const l = liste || [];
  const aMaj = (x) => maj.some((m) => m.id === x.id);
  const ok = l.filter((x) => x.etat === "ok").length;
  return `<article class="carte services" data-cle="m-services">
    <span class="tuile-nom">${ic("activity")}Services</span>
    ${l.length ? `<p class="services-resume"><b class="chiffre">${ok}</b><span>sur ${l.length} en ligne</span></p>
      <ul>${l.slice(0, 7).map((x) => `<li class="${x.etat}"><span class="app-icone mini">${ic(appCatalogue(x.id)?.icone || x.icone || "box")}</span>
        <span class="services-nom">${esc(x.nom)}<small>${NOM(x.noeud)}</small></span>${aMaj(x) ? `<em class="etiquette maj">${ic("download")}mise à jour</em>` : ""}<i class="voyant ${x.etat === "off" ? "off" : x.etat}"></i><em>${TON_SERVICE[x.etat] || ""}</em></li>`).join("")}</ul>`
    : `<div class="tuile-corps colonne"><b class="chiffre petit">${l ? "Aucune app" : "Vérification…"}</b><p>${l ? "Installe une app pour la suivre ici." : "Premier contrôle dans 2 minutes."}</p></div>`}
  </article>`;
}

function carteMeteo(m) {
  if (!m) return `<article class="carte meteo" data-cle="m-meteo"><span class="tuile-nom">${ic("cloud")}Météo</span>
    <div class="tuile-corps colonne"><b class="chiffre petit">Chargement…</b><p>La météo arrive dans une minute.</p></div></article>`;
  const jourNom = (d) => new Date(d + "T12:00").toLocaleDateString("fr-FR", { weekday: "short" }).replace(".", "");
  return `<article class="carte meteo" data-cle="m-meteo">
    <span class="tuile-nom">${ic("map-pin")}${esc(m.ville)}</span>
    <div class="meteo-maint">
      <span class="meteo-icone">${ic(m.icone)}</span>
      <div><b class="meteo-temp">${m.temp}<sup>°C</sup></b><p>${esc(m.texte)} · ressenti ${m.ressenti}°</p>
        <p class="meteo-plus">${ic("wind")}${m.vent} km/h${m.humidite != null ? ` · ${ic("droplets")}${m.humidite} %` : ""}</p></div>
      <ul class="meteo-jours">${(m.jours || []).map((j, i) => `<li><span>${i ? jourNom(j.date) : "Auj."}</span>${ic(j.icone)}<b>${j.max}°</b><small>${j.min}°</small></li>`).join("")}</ul>
    </div>
    <ul class="meteo-heures">${(m.heures || []).map((h) => `<li><span>${h.h}</span>${ic(h.icone)}<b>${h.temp}°</b><small>${h.pluie ? `${ic("umbrella")}${h.pluie} %` : ""}</small></li>`).join("")}</ul>
  </article>`;
}

function carteConso(c) {
  if (!c) return "";
  return `<article class="carte conso" data-cle="m-conso">
    <span class="tuile-nom">${ic("zap")}Consommation</span>
    <div class="tuile-corps colonne"><b class="chiffre">${String(c.watts).replace(".", ",")}<small>W</small></b>
      <p>${c.mesure ? `mesurée sur ${esc(NOM(S.etat?.hub))}` : "estimée"}</p></div>
    <dl class="conso-chiffres">
      <div><dt>Par mois</dt><dd>${String(c.kwh_mois).replace(".", ",")} kWh</dd></div>
      <div><dt>Coût</dt><dd>≈ ${c.euros_mois.toFixed(2).replace(".", ",")} €</dd></div>
    </dl>
    <span class="tuile-pied">au tarif de ${String(c.prix_kwh).replace(".", ",")} €/kWh</span>
  </article>`;
}

// 1.0.18 : carte « Aujourd'hui » de la page « maison » — agenda du jour (iCloud) + rappels.
function carteJour(e) {
  const ag = e.agenda || {};
  const auj = ag.aujourdhui || [];
  const rp = (e.rappels?.rappels || []).slice().sort((a, b) => (a.fait === b.fait ? 0 : a.fait ? 1 : -1));
  return `<article class="carte jour" data-cle="m-jour">
    <span class="tuile-nom">${ic("calendar")}Aujourd'hui</span>
    ${auj.length ? `<ul class="agenda-liste">${auj.slice(0, 5).map((ev) => ev.journee_entiere
      ? `<li class="jour"><span class="agenda-t"><span class="agenda-h">journée</span>${esc(ev.titre)}${ev.lieu ? `<small>${ic("map-pin")}${esc(ev.lieu)}</small>` : ""}</span></li>`
      : `<li><span class="agenda-h">${esc(ev.heure)}</span>
        <span class="agenda-t">${esc(ev.titre)}${ev.lieu ? `<small>${ic("map-pin")}${esc(ev.lieu)}</small>` : ""}</span></li>`).join("")}</ul>
      ${ag.jours_suivants_n ? `<p class="agenda-suite">${ic("calendar")}${ag.jours_suivants_n} rendez-vous à venir</p>` : ""}`
    : `<p class="jour-vide">${ag.actif ? "Rien de prévu aujourd'hui." : "Relie un calendrier dans Réglages › Perso."}</p>`}
    ${rp.length ? `<h3 class="jour-sous">${ic("list-todo")}Rappels</h3>
      <ul class="rappels-liste">${rp.slice(0, 5).map((r) => `<li class="${r.fait ? "fait" : ""}">${ic(r.fait ? "circle-check" : "square")}<span>${esc(r.texte)}</span></li>`).join("")}
      ${rp.length > 5 ? `<li class="rappels-plus">+ ${rp.length - 5}</li>` : ""}</ul>` : ""}
  </article>`;
}

// 1.0.18 : QR code Wi-Fi invités — seulement si Justin l'a explicitement mis sur l'écran (le QR expose le mot de passe).
function carteWifiQr(w) {
  if (!w?.qr_svg) return "";
  return `<article class="carte wifi-qr" data-cle="m-wifiqr">
    <span class="tuile-nom">${ic("qr-code")}Wi-Fi invités</span>
    <div class="wifi-qr-img">${w.qr_svg}</div>
    <span class="tuile-pied">${esc(w.ssid || "")} · scanne pour te connecter</span>
  </article>`;
}

// --- page « réseau » de l'écran (1.0.10) : appareils, débit des nuits, Wi-Fi du Pi, Pi-hole
const nf = (v, d = 0) => (v == null ? "–" : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }));

function carteAppareilsEcran(r) {
  const recents = r?.recents || [];
  const maintenant = Date.now() / 1000;
  return `<article class="carte ecran-appareils" data-cle="r-appareils">
    <span class="tuile-nom">${ic("house")}Appareils de la maison</span>
    <p class="services-resume"><b class="chiffre">${r?.appareils ?? "–"}</b><span>en ligne${r?.connus ? ` sur ${r.connus} connus` : ""}${r?.tailscale ? ` · ${r.tailscale} à distance` : ""}</span></p>
    ${recents.length ? `<h3 class="ecran-sous">Derniers arrivés</h3>
      <ul>${recents.map((a) => {
        const neuf = maintenant - (a.premiere_vue || 0) < 86400;
        return `<li class="${a.en_ligne ? "" : "absent"}"><span class="app-icone mini">${ic(iconeAppareil(a))}</span>
          <span class="services-nom">${esc(a.nom || "Appareil inconnu")}<small>${esc(a.ip || "")}</small></span>
          ${neuf ? `<em class="etiquette nouveau">nouveau</em>` : ""}<em>${ilYA(a.premiere_vue).replace("il y a ", "")}</em>
          <i class="voyant ${a.en_ligne ? "ok" : "off"}"></i></li>`;
      }).join("")}</ul>`
    : `<div class="tuile-corps colonne"><b class="chiffre petit">Premier tour du réseau…</b><p>La liste arrive dans quelques minutes.</p></div>`}
  </article>`;
}

function carteDebitEcran(d, controle) {
  const der = d?.dernier;
  if (!der) {
    return `<article class="carte ecran-debit" data-cle="r-debit"><span class="tuile-nom">${ic("gauge")}Débit Internet</span>
      <div class="tuile-corps colonne"><b class="chiffre petit">${d?.en_cours ? "Test en cours…" : "Pas encore de mesure"}</b><p>Test automatique chaque nuit à ${esc(d?.heure_nuit || "03:30")}.</p></div></article>`;
  }
  return `<article class="carte ecran-debit" data-cle="r-debit">
    <span class="tuile-nom">${ic("gauge")}Débit Internet<em>${d.en_cours ? "test en cours…" : `mesuré ${quandDebit(der.t)}`}</em></span>
    <div class="debit-chiffres grand">
      <div><span>${ic("arrow-down")}Réception</span><b class="chiffre">${mbit(der.down_mbps)}<small>Mbit/s</small></b></div>
      <div class="tx"><span>${ic("arrow-up")}Envoi</span><b class="chiffre">${mbit(der.up_mbps)}<small>Mbit/s</small></b></div>
      <div><span>${ic("timer")}Latence</span><b class="chiffre">${nf(der.ping_ms)}<small>ms</small></b></div>
    </div>
    <div class="debit-graphe">${grapheDebits(d.historique, 300, 120)}
      <p class="legende"><span class="rx">Réception</span><span class="tx">Envoi</span><em>${d.historique.length} dernières nuits · mesuré en Wi-Fi depuis le Pi</em></p></div>
    ${controle?.historique?.length ? `<div class="ecran-controle"><span class="tuile-nom petit">${ic("activity")}Contrôle horaire · 24 h</span>${grapheControle(controle.historique, 24, 300, 70)}</div>` : ""}
  </article>`;
}

function tuileWifi(w) {
  const corps = (icone, ton, titre, sous) => `<div class="tuile-corps"><span class="wifi-icone ${ton}">${ic(icone)}</span>
    <div><b class="chiffre moyen">${titre}</b><p>${sous}</p></div></div>`;
  let html, pied = "";
  if (!w) html = corps("wifi", "", "Mesure…", "dans une minute");
  else if (w.filaire) html = corps("cable", "ok", "Câble", "le Pi est relié à la box");
  else if (w.signal_dbm == null) html = corps("wifi-off", "crit", "Déconnecté", "aucun réseau Wi-Fi");
  else {
    const [nom, ton, icone] = NIVEAU_WIFI[w.niveau] || NIVEAU_WIFI.moyen;
    html = `${corps(icone, ton, nom, `${w.signal_dbm} dBm · ${w.qualite_pct} %`)}${jauge(w.qualite_pct, ton === "ok" ? "ok" : ton)}`;
    pied = w.niveau === "faible" ? `<span class="tuile-pied alerte">${ic("triangle-alert")}${CONSEIL_WIFI_COURT}</span>`
      : `<span class="tuile-pied">${esc(w.ssid || "")} · ${bande(w.frequence_mhz)}${w.debit_mbps ? ` · lien ${w.debit_mbps} Mbit/s` : ""}</span>`;
  }
  return `<div class="carte tuile ecran-wifi" data-cle="r-wifi"><span class="tuile-nom">${ic("wifi")}Wi-Fi du Pi</span>${html}${pied}</div>`;
}

function tuilePihole(p) {
  if (!p) {
    return `<div class="carte tuile ecran-pihole" data-cle="r-pihole"><span class="tuile-nom">${ic("shield-ban")}Pubs bloquées</span>
      <div class="tuile-corps colonne"><b class="chiffre petit">Pi-hole absent</b><p>ou injoignable pour l'instant</p></div></div>`;
  }
  return `<div class="carte tuile ecran-pihole" data-cle="r-pihole"><span class="tuile-nom">${ic("shield-ban")}Pubs bloquées · 24 h</span>
    <div class="tuile-corps colonne"><b class="chiffre">${nf(p.bloquees)}</b><p>${nf(p.pourcentage, 1)} % des ${nf(p.requetes)} requêtes</p></div>
    ${jauge(p.pourcentage, "accent")}
    <span class="tuile-pied">${nf(p.domaines_liste)} domaines dans la liste</span></div>`;
}

// 1.0.14 : une ligne discrète quand une mise à jour est prête (ou en cours d'installation).
function ligneMaj(e) {
  const m = e.maj_ydz;
  if (!m) return "";
  if (m.en_cours) return `<i></i><span class="sig-maj">installation ${esc(m.progression?.version || "")}…</span>`;
  const p = (m.disponibles || []).filter((d) => d.prete);
  return p.length ? `<i></i><span class="sig-maj">mise à jour ${esc(p[p.length - 1].version)} prête</span>` : "";
}

// Signature (1.0.13) : « Home Pi Lab · YDZ OS 1.0.x ». La pilule active glisse d'un point à l'autre (transform).
function signature(e, page, nb) {
  return `<footer class="signature"><img src="img/icone-32.png" alt=""><span>${esc(e.labo?.nom || "Home Pi Lab")}</span><i></i><span>YDZ OS</span><b>${esc(e.version || "–")}</b>${ligneMaj(e)}
    ${nb > 1 ? `<span class="pages">${pagesAffichees(e).map((_, i) => `<em class="${i === page ? "actif" : ""}"></em>`).join("")}<i class="pages-pilule" style="transform:translateX(${page * 14}px)"></i></span>` : ""}</footer>`;
}

// 1.0.14 : horloge douce avec les secondes. Un minuteur calé sur chaque seconde ne touche que les deux
// nœuds de texte de l'horloge (jamais un rendu complet) ; il s'arrête tout seul dès que l'horloge disparaît.
const deux = (n) => String(n).padStart(2, "0");
const partiesHeure = (d = new Date()) => [`${deux(d.getHours())}:${deux(d.getMinutes())}`, `:${deux(d.getSeconds())}`];
let minuteurNuit = null;
function tictac() {
  minuteurNuit = null;
  const hm = document.querySelector(".nuit-heure .nh-hm"), s = document.querySelector(".nuit-heure .nh-s");
  if (!hm || !s) return;                                   // plus de vue de nuit : le minuteur s'arrête
  const [a, b] = partiesHeure();
  if (hm.textContent !== a) hm.textContent = a;
  if (s.textContent !== b) s.textContent = b;
  minuteurNuit = setTimeout(tictac, 1000 - (Date.now() % 1000) + 5);
}
function lancerSecondes() {
  if (minuteurNuit === null) minuteurNuit = setTimeout(tictac, 0);
}

function vueNuit(e, a) {
  if (a.nuit.mode === "eteint") return `<div class="nuit noire" data-cle="nuit"></div>`;
  const m = a.meteo;
  const [hm, s] = partiesHeure();
  lancerSecondes();
  return `<div class="nuit" data-cle="nuit">
    <time class="nuit-heure" aria-label="${hm}"><span class="nh-hm">${hm}</span><span class="nh-s">${s}</span></time>
    <p class="nuit-jour">${jour().replace(/^./, (c) => c.toUpperCase())}${m ? ` · ${ic(m.icone)} ${m.temp}°` : ""}</p>
    ${e.tour.etat !== "ok" ? `<p class="nuit-alerte">${ic("triangle-alert")}${esc(phrase(e)[1])}</p>` : ""}
  </div>`;
}

function alerteGrande(e) {
  return `<div class="alerte-ecran" data-cle="alerte-ecran">
    <span class="alerte-icone">${ic("octagon-alert")}</span>
    <h2>Attention requise</h2>
    <ul>${e.tour.alertes.filter((x) => x.niveau === "crit").concat(e.tour.alertes.filter((x) => x.niveau !== "crit")).slice(0, 4)
      .map((x) => `<li>${esc(x.texte)}</li>`).join("")}</ul>
    <p>Ouvre l'appli YDZ sur ton téléphone pour voir le détail.</p>
  </div>`;
}

// ============================================================================ « Bureaux de Claude » (1.0.19)
const ETAT_AGENT = { en_attente: "en attente", travaille: "travaille", besoin_aide: "besoin d'aide", erreur: "erreur", termine: "terminé" };
const TON_AGENT = { en_attente: "", travaille: "cours", besoin_aide: "warn", erreur: "crit", termine: "ok" };

function panneauEquipe(ag) {
  if (ag.bureaux?.panneau === false) return "";
  const equipe = ag.equipe || [], distants = ag.distants || [];
  const ligne = (nom, couleur, etat, prog, distant) => `<li data-cle="bag-${esc(nom)}">
    <span class="bag-pastille" style="--c:${esc(couleur || "#5AA9E6")}"></span>
    <span class="bag-txt"><b>${esc(nom)}</b><small class="${TON_AGENT[etat] || ""}">${distant ? "appli Claude" : esc(ETAT_AGENT[etat] || "en attente")}</small></span>
    <span class="bag-barre"><i style="width:${Math.max(0, Math.min(100, prog || 0))}%;background:${esc(couleur || "#5AA9E6")}"></i></span></li>`;
  return `<aside class="bureaux-equipe" data-cle="bur-equipe">
    <h2>${ic("users")}Équipe d'agents</h2>
    <ul>${equipe.map((a) => ligne(a.nom, a.couleur, a.run?.etat, a.run?.progression)).join("")}
    ${distants.map((d) => ligne(d.nom, d.couleur, d.termine ? "termine" : "travaille", d.progression, true)).join("")}</ul>
  </aside>`;
}

function boiteDialogue(ag) {
  if (ag.bureaux?.boite === false) return "";
  const t = ag.bureaux?.dialogue_taille || 20;
  return `<div class="bureaux-dialogue" data-cle="bur-dia" style="--dia-taille:${t}px" data-fige>
    <span class="bur-dia-nom"></span><span class="bur-dia-texte"></span></div>`;
}

export function dialoguePhrase(e) {
  const ag = e.agents || {};
  const eq = ag.equipe || [];
  const bloque = eq.find((a) => a.run?.etat === "erreur") || eq.find((a) => a.run?.etat === "besoin_aide");
  if (bloque) return ["Docteur", `${bloque.nom} a besoin d'un coup de main… j'arrive !`];
  const fini = eq.find((a) => a.run?.etat === "termine" && a.run?.resultat);
  if (fini) return [fini.nom, fini.run.resultat];
  const bosse = eq.find((a) => a.run?.etat === "travaille");
  if (bosse) return [bosse.nom, bosse.run?.etapes?.length ? bosse.run.etapes[bosse.run.etapes.length - 1] + "…" : "je m'y mets tout de suite…"];
  const etat = e.tour?.etat;
  if (etat === "crit") return ["Docteur", "Attention, la tour a besoin de toi !"];
  if (etat === "warn") return ["Docteur", "Un petit truc à surveiller sur la tour."];
  return ["Docteur", "La tour est en pleine forme ! Tout tourne."];
}

function vueBureaux(e) {
  const ag = e.agents || { equipe: [], distants: [] };
  return `<section class="bureaux" data-cle="p-bureaux">
    <canvas class="bureaux-canvas" width="384" height="216" data-cle="bureaux-canvas" data-fige aria-label="Les bureaux de Claude"></canvas>
    ${panneauEquipe(ag)}
    ${boiteDialogue(ag)}
  </section>`;
}

// --- cycle de vie de la scène : créée quand le canvas apparaît, animée seulement quand la page est affichée.
let scene = null, sceneCanvas = null, diaTexte = "", diaPos = 0, diaMinuteur = null;
function majBureaux() {
  const canvas = document.querySelector('canvas[data-cle="bureaux-canvas"]');
  if (!canvas || !S.etat?.agents) { if (scene) { scene.arreter(); } return; }
  if (canvas !== sceneCanvas) { if (scene) scene.arreter(); scene = creerScene(canvas); sceneCanvas = canvas; }
  const ag = S.etat.agents;
  scene.maj(ag.equipe || [], ag.distants || [], ag.bureaux || {});
  if (!document.hidden && !S.veille) scene.demarrer(); else scene.arreter();
  taperDialogue(ag);
}

// Boîte RPG : le texte s'écrit lettre par lettre (un minuteur qui ne touche qu'un nœud de texte, jamais le DOM entier).
function taperDialogue(ag) {
  const nomEl = document.querySelector(".bur-dia-nom"), txtEl = document.querySelector(".bur-dia-texte");
  if (!nomEl || !txtEl) { clearTimeout(diaMinuteur); diaMinuteur = null; return; }
  const [nom, phrase] = dialoguePhrase(S.etat);
  const complet = `${phrase}`;
  if (nomEl.textContent !== nom + " : ") nomEl.textContent = nom + " : ";
  const sansMvt = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (complet !== diaTexte) { diaTexte = complet; diaPos = sansMvt ? complet.length : 0; }
  if (sansMvt) { if (txtEl.textContent !== complet) txtEl.textContent = complet; return; }
  const vitesse = ag.bureaux?.dialogue_vitesse || 45;
  const avancer = () => {
    const el = document.querySelector(".bur-dia-texte");
    if (!el) { diaMinuteur = null; return; }
    if (diaPos < diaTexte.length) { diaPos += 1; el.textContent = diaTexte.slice(0, diaPos); diaMinuteur = setTimeout(avancer, 1000 / vitesse); }
    else { diaMinuteur = null; }
  };
  if (!diaMinuteur && diaPos < diaTexte.length) avancer();
  else if (diaPos >= diaTexte.length && txtEl.textContent !== diaTexte) txtEl.textContent = diaTexte;
}
if (typeof window !== "undefined") {
  setInterval(majBureaux, 400);
  document.addEventListener("visibilitychange", () => { if (document.hidden && scene) scene.arreter(); });
}

function rendreAffichage(e, tete) {
  const a = e.affichage || {};
  if (a.nuit?.en_cours) return vueNuit(e, a);
  const liste = pagesAffichees(e);
  const nb = a.rotation_s ? liste.length : 1;
  const page = Math.min(pageAffichage(), liste.length - 1);
  const qr = e.wifi_invite?.qr_svg;
  const aPerso = e.agenda?.actif || e.rappels?.rappels?.length || qr;
  const corps = rangs(liste[page] === "iphone"
    ? vueIphoneEcran(e)
    : liste[page] === "maison"
    ? (aPerso
      ? `<section class="maison ${qr ? "maison5" : "maison4"}" data-cle="p-maison">${carteMeteo(a.meteo)}${carteServices(a.services, e.maj_apps)}${carteJour(e)}<div class="maison-col">${tuilePC(a.pc)}${carteConso(a.conso)}</div>${qr ? carteWifiQr(e.wifi_invite) : ""}</section>`
      : `<section class="maison" data-cle="p-maison">${carteMeteo(a.meteo)}${carteServices(a.services, e.maj_apps)}<div class="maison-col">${tuilePC(a.pc)}${carteConso(a.conso)}</div></section>`)
    : liste[page] === "reseau"
    ? `<section class="maison ecran-reseau" data-cle="p-reseau">${carteAppareilsEcran(e.reseau_maison)}${carteDebitEcran(e.reseau_maison?.debit, e.reseau_maison?.controle)}<div class="maison-col">${tuileWifi(e.reseau_maison?.wifi)}${tuilePihole(e.pihole)}</div></section>`
    : liste[page] === "agents"
    ? vueBureaux(e)
    : `<section class="noeuds" data-cle="p-tour-noeuds">${e.noeuds.map(carteNoeud).join("")}</section>
       <section class="tuiles" data-cle="p-tour-tuiles">${tuileVentilo(e.ventilo)}${tuileStockage(e)}${tuileDebit(e)}${tuileApps(e)}${tuilePC(a.pc)}</section>`);
  const ton = e.tour.etat === "crit" ? "crit" : e.tour.etat === "warn" ? "warn" : "";
  return `${ton ? `<div class="cadre-alerte ${ton}" aria-hidden="true"></div>` : ""}
    ${e.tour.etat === "crit" ? alerteGrande(e) : ""}
    ${tete}${corps}${signature(e, page, nb)}`;
}

// Fiche d'un nœud : détails et alimentation.
function ouvrirNoeud(nom) {
  ouvrir({
    classe: "large",
    rendu: () => {
      const n = noeud(nom);
      if (!n) return "";
      const ligne = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
      const th = n.throttle;
      return `${enteteDialogue(NOM(n.nom), { icone: ROLE_ICONE[n.role] || "server", sous: `${esc(n.role_nom || n.role)} · ${esc(n.modele || "")}` })}
      <div class="dlg-corps">
        ${n.cle_invalide ? `<p class="bandeau crit">${ic("triangle-alert")}Ce nœud refuse les ordres de ${esc(NOM(S.etat?.hub))}. Lance <code>sudo ydz appairer</code> sur ${NOM(n.nom)}.</p>` : ""}
        ${(n.alertes || []).map((a) => `<p class="bandeau warn">${ic("triangle-alert")}${esc(a)}</p>`).join("")}
        ${n.redemarrage_requis ? `<p class="bandeau">${ic("rotate-cw")}Un redémarrage est nécessaire pour finir une mise à jour.</p>` : ""}
        <dl class="fiche">
          ${ligne("État", n.en_ligne ? "En ligne" : `Hors ligne (vu ${ilYA(Date.now() / 1000 - (n.vu_il_y_a || 0))})`)}
          ${ligne("Allumé depuis", duree(n.uptime))}
          ${ligne("Température", n.temp == null ? "–" : `${n.temp.toFixed(1)} °C`)}
          ${ligne("Processeur", `${pct(n.cpu)} · charge ${(n.charge || []).map((c) => c.toFixed(2)).join(" ")}`)}
          ${ligne("Mémoire", `${memoire(n.ram?.utilise)} sur ${memoire(n.ram?.total)}`)}
          ${ligne("Adresse", esc(n.ip || "–"))}
          ${ligne("Adresse MAC", esc(n.mac || "–"))}
          ${ligne("Réseau", n.reseau ? `${n.reseau.lien_mbps ? `${n.reseau.lien_mbps} Mbit/s · ` : ""}↓ ${debit(n.reseau.rx_bps)} · ↑ ${debit(n.reseau.tx_bps)}` : "–")}
          ${ligne("Alimentation", th ? (th.sous_tension ? "Sous-tension en ce moment" : th.deja_sous_tension ? "Sous-tension détectée depuis l'allumage" : "Correcte") : "–")}
          ${ligne("Badge", n.badge?.present ? `Branché · firmware ${esc(n.badge.fw || "?")}` : n.badge?.actif ? "Non détecté" : "Désactivé")}
          ${ligne("YDZ OS", esc(n.version || "–"))}
        </dl>
        <h3 class="intertitre">Disques</h3>
        <ul class="liste-fine">${(n.disques || []).map((d) => `<li><span>${esc(d.nom)}<small>${esc(d.point)}</small></span><span>${octets(d.utilise)} / ${octets(d.total)}</span>${jauge(d.pct)}</li>`).join("")}</ul>
        ${n.docker?.conteneurs?.length ? `<h3 class="intertitre">Conteneurs</h3>
        <ul class="liste-fine">${n.docker.conteneurs.map((c) => `<li><span>${esc(c.nom)}<small>${esc(c.image)}</small></span><span>${esc(c.statut)}</span></li>`).join("")}</ul>` : ""}
      </div>
      <footer class="dlg-pied">
        <a class="btn discret" href="#/historique/${esc(n.nom)}">${ic("chart-line")}<span>Historique 7 jours</span></a>
        ${btn("n-redemarrer", "Redémarrer", { icone: "rotate-cw", classe: "discret", off: !n.en_ligne })}
        ${btn("n-eteindre", "Éteindre", { icone: "power", classe: "discret danger-texte", off: !n.en_ligne })}
      </footer>`;
    },
    actions: {
      async "n-redemarrer"() { if (await alimentation(nom, "redemarrer")) this.fermer(); },
      async "n-eteindre"() { if (await alimentation(nom, "eteindre")) this.fermer(); },
    },
  });
}

export async function alimentation(nom, action) {
  const eteindre = action === "eteindre";
  const hub = noeud(nom)?.hub;
  const oui = await confirmer({
    titre: `${eteindre ? "Éteindre" : "Redémarrer"} ${NOM(nom)} ?`,
    texte: `${eteindre ? `${NOM(nom)} s'arrête proprement. Pour le rallumer, il faudra couper puis remettre son alimentation.` : `${NOM(nom)} sera indisponible une minute environ.`}${hub ? " C'est le nœud qui affiche cette interface : elle sera coupée pendant ce temps." : ""} Ses applications s'arrêtent avec lui.`,
    libelle: eteindre ? "Éteindre" : "Redémarrer", danger: eteindre, icone: "rotate-cw",
  });
  if (!oui) return false;
  await agir(`/api/noeuds/${nom}/${action}`);
  toast(`${NOM(nom)} : ${eteindre ? "extinction" : "redémarrage"} en cours.`);
  return true;
}

export default {
  rendu() {
    const e = S.etat;
    const [titre, sous, ton] = phrase(e);
    const tete = `
    <header class="accueil-tete">
      <div class="salut ${ton}">
        <span class="voyant grand ${ton}"></span>
        <div><h1>${titre}</h1><p>${esc(sous)}</p></div>
      </div>
      ${appareils(e.reseau_maison)}
      ${S.affichage ? agendaProchain(e.agenda) : ""}
      ${S.affichage ? meteoMini(e.affichage?.meteo) : ""}
      ${etatCommun()}
      <time class="horloge" aria-label="Heure">${heure()}</time>
    </header>`;
    if (S.affichage) return rendreAffichage(e, tete);
    return `${tete}
    <section class="noeuds">${e.noeuds.map(carteNoeud).join("")}</section>
    <section class="tuiles">${tuileVentilo(e.ventilo)}${tuileStockage(e)}${tuileDebit(e)}${tuileApps(e)}</section>`;
  },
  actions: {
    noeud({ nom }) { ouvrirNoeud(nom); },
  },
};
