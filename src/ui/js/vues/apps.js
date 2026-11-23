// Apps : catalogue, fiche d'une application, installation, réglages, journal, désinstallation.
import { esc, ic, memoire, ilYA } from "../outils.js";
import {
  S, NOM, btn, champ, interrupteur, pastille, vide, ouvrir, enteteDialogue, choisir, informer, toast, tenter, rendre,
} from "../noyau.js";
import { entete, tachePour } from "../cadre.js";
import { lire, lireProtege, agir, session } from "../api.js";

const ETATS = { actif: ["ok", "Active"], arrete: ["off", "Arrêtée"], partiel: ["warn", "Partielle"], erreur: ["crit", "En erreur"] };
const MASQUE = "••••••";
// 1.0.12 : depuis le tailnet (adresse *.ts.net ou 100.x), on ouvre l'adresse HTTPS de l'app quand elle existe
export const surTailnet = () => /\.ts\.net$/i.test(location.hostname) || /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(location.hostname);
export const adresseApp = (i) => (surTailnet() && i.url_https) || i.url || i.url_https;
const majDispo = (i) => Boolean(i?.maj?.maj_disponible);

// ---------------------------------------------------------- recherche, filtres et drapeaux (1.0.17)
// Normalise pour la recherche : minuscules, sans accents (« Médias » trouvé en tapant « medias »).
const sansAccents = (s) => String(s ?? "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
const DIFFICULTES = { facile: ["Facile", "ok"], moyen: ["Moyen", "warn"], "avancé": ["Avancé", "crit"] };
// Bandes de mémoire pour le filtre : léger / moyen / costaud.
const RAM_BANDES = [
  ["leger", "Léger", "≤ 512 Mo", (mo) => mo <= 512],
  ["moyen", "Moyen", "≤ 1,5 Go", (mo) => mo > 512 && mo <= 1500],
  ["costaud", "Costaud", "> 1,5 Go", (mo) => mo > 1500],
];
// Avertissements affichés sur la fiche d'une app signalée : [titre, explication, icône].
const DRAPEAUX = {
  socket_docker: ["Accès au moteur Docker", "Cette app peut voir et piloter tous les conteneurs du nœud. Ne l'installe que si tu lui fais confiance.", "boxes"],
  reseau_hote: ["Réseau du nœud en direct", "Elle utilise directement le réseau de la machine, sans isolation. Pratique pour repérer les appareils de la maison, mais moins cloisonné.", "network"],
  usb: ["Clé USB nécessaire", "Elle a besoin d'une clé ou d'un dongle branché sur le nœud (Zigbee, Z-Wave…). Branche-le avant d'installer, puis indique-le dans les réglages.", "usb"],
  lourd: ["Application gourmande", "Elle demande beaucoup de mémoire et de processeur : installe-la sur un nœud costaud. Le téléchargement et le premier démarrage peuvent être longs.", "gauge"],
  privilegie: ["Droits étendus", "Elle demande des accès supplémentaires au système du nœud pour fonctionner.", "shield"],
};
const bandeRam = (mo) => (RAM_BANDES.find(([, , , test]) => test(mo || 0)) || [])[0];

function correspond(a, q) {
  if (!q) return true;
  const d = a.details || {};
  const foin = sansAccents([a.nom, a.description, a.categorie, d.alternative_a, d.a_quoi_ca_sert].join(" "));
  return q.split(/\s+/).every((mot) => foin.includes(mot));
}
function filtrer(cat, f) {
  const q = sansAccents(f.recherche || "").trim();
  return cat.filter((a) => {
    if (f.categorie === "installees" && !a.installations.length) return false;
    if (f.categorie && f.categorie !== "toutes" && f.categorie !== "installees" && a.categorie !== f.categorie) return false;
    if (f.difficulte && (a.details?.difficulte || "moyen") !== f.difficulte) return false;
    if (f.ram && bandeRam(a.details?.ram_mo ?? a.ram_mo) !== f.ram) return false;
    return correspond(a, q);
  });
}

// ------------------------------------------------------------------------- formulaire de réglages
const cleChamp = (cle) => `r:${cle}`;
function valeurChoix(r, v) {
  const o = (r.choix || []).map((c) => (typeof c === "object" ? c : { valeur: c, nom: c }));
  return { options: o, courant: o.find((c) => String(c.valeur) === String(v)) };
}
function champReglage(r, donnees, erreurCle, edition) {
  const v = donnees[cleChamp(r.cle)] ?? "";
  let saisie;
  if (r.type === "booleen") {
    saisie = interrupteur("r-bascule", String(v) === "true", { cle: r.cle }, r.nom);
  } else if (r.type === "choix") {
    const { options, courant } = valeurChoix(r, v);
    saisie = options.length <= 4 && options.every((o) => String(o.nom).length <= 12)
      ? `<div class="segments">${options.map((o) => `<button type="button" class="${courant === o ? "actif" : ""}" data-action="r-choix" data-cle="${esc(r.cle)}" data-valeur="${esc(o.valeur)}">${esc(o.nom)}</button>`).join("")}</div>`
      : `<button type="button" class="champ selecteur" data-action="r-liste" data-cle="${esc(r.cle)}"><span>${esc(courant ? courant.nom : v || "Choisir")}</span>${ic("chevron-down")}</button>`;
  } else {
    saisie = champ(cleChamp(r.cle), v === MASQUE ? "" : v, {
      type: r.type === "secret" ? "secret" : "texte", chiffres: r.type === "nombre", erreur: erreurCle === r.cle, label: r.nom, max: 256,
      placeholder: r.type === "secret" ? (edition ? "Inchangé" : r.genere ? "Vide : créé automatiquement" : "") : "",
    });
  }
  return `<label class="reglage${r.type === "booleen" ? " en-ligne" : ""}${erreurCle === r.cle ? " erreur" : ""}" data-cle="rg-${esc(r.cle)}">
    <span class="reglage-nom">${esc(r.nom)}${r.requis ? " <em>requis</em>" : ""}</span>${saisie}
    ${r.aide ? `<small>${esc(r.aide)}</small>` : ""}</label>`;
}
function valeursInitiales(a, existants) {
  const d = {};
  for (const r of a.reglages) d[cleChamp(r.cle)] = existants?.[r.cle] ?? (r.defaut !== undefined ? String(r.defaut) : "");
  return d;
}
function collecter(a, donnees, edition) {
  const sortie = {};
  for (const r of a.reglages) {
    const v = String(donnees[cleChamp(r.cle)] ?? "");
    if (edition && r.type === "secret" && (v === "" || v === MASQUE)) continue; // garde la valeur actuelle
    sortie[r.cle] = v;
  }
  return sortie;
}
const actionsFormulaire = (app) => ({
  "r-bascule"({ cle }) { this.donnees[cleChamp(cle)] = String(this.donnees[cleChamp(cle)]) === "true" ? "false" : "true"; },
  "r-choix"({ cle, valeur }) { this.donnees[cleChamp(cle)] = valeur; },
  async "r-liste"({ cle }) {
    const r = app().reglages.find((x) => x.cle === cle);
    const v = await choisir({ titre: r.nom, options: valeurChoix(r, "").options, valeur: this.donnees[cleChamp(cle)] });
    if (v !== undefined) this.donnees[cleChamp(cle)] = String(v);
  },
});

// ------------------------------------------------------------------------------------ catalogue
function carteApp(a) {
  const ins = a.installations || [];
  const t = tachePour(a.id);
  const principale = ins[0];
  const dif = a.details?.difficulte;
  let pied;
  if (t) pied = `<span class="pastille cours">${ic("loader-circle", "tourne")}${esc(t.titre.split(" ")[0])}…</span>`;
  else if (principale) {
    const [ton, nom] = ETATS[principale.etat] || ETATS.erreur;
    const mc = a.id === "minecraft" && S.etat?.minecraft?.en_ligne ? S.etat.minecraft : null;
    pied = `${ins.some(majDispo) ? `<span class="pastille maj">${ic("download")}Mise à jour</span>` : pastille(ton, nom)}<span class="sur">${mc ? `${ic("users")}${mc.joueurs}/${mc.max} · ` : ""}${ins.map((i) => NOM(i.noeud)).join(" · ")}</span>`;
  } else {
    pied = `<span class="sur">${esc(a.categorie)}</span>${dif ? `<span class="puce-diff ${DIFFICULTES[dif]?.[1] || ""}">${esc(DIFFICULTES[dif]?.[0] || dif)}</span>` : `<span class="installer">Installer${ic("chevron-right")}</span>`}`;
  }
  return `<a class="carte app${principale ? " installee" : ""}${principale?.etat === "actif" ? " active" : ""}" href="#/apps/${a.id}" data-cle="a-${a.id}">
    <span class="app-icone">${ic(a.icone)}</span>
    <div class="app-textes"><h2>${esc(a.nom)}${a.drapeaux?.length ? `<span class="app-drapeau" title="À lire avant d'installer">${ic("triangle-alert")}</span>` : ""}</h2><p>${esc(a.description)}</p></div>
    <div class="app-pied">${pied}</div>
  </a>`;
}

// Rangée de filtres : chips de catégorie, puis difficulté et mémoire (1.0.17).
function filtres(vue, cat) {
  const f = vue.donnees;
  const installees = cat.filter((a) => a.installations.length);
  const categories = [...new Set(cat.map((a) => a.categorie))].sort((x, y) => x.localeCompare(y, "fr"));
  const puce = (id, nom, n) => `<button type="button" class="puce${(f.categorie || "toutes") === id ? " actif" : ""}" data-action="filtre" data-f="${esc(id)}">${esc(nom)}${n != null ? `<em>${n}</em>` : ""}</button>`;
  const second = (champ, val, nom) => `<button type="button" class="puce mini${(f[champ] || "") === val ? " actif" : ""}" data-action="filtre-${champ}" data-v="${esc(val)}">${esc(nom)}</button>`;
  return `<div class="puces">${puce("toutes", "Toutes", cat.length)}${puce("installees", "Installées", installees.length)}${categories.map((c) => puce(c, c)).join("")}</div>
    <div class="puces secondaires">
      <span class="puces-titre">Difficulté</span>${second("difficulte", "", "Toutes")}${Object.entries(DIFFICULTES).map(([k, v]) => second("difficulte", k, v[0])).join("")}
      <span class="puces-titre">Mémoire</span>${second("ram", "", "Toutes")}${RAM_BANDES.map(([k, nom]) => second("ram", k, nom)).join("")}
    </div>`;
}

function catalogue(vue) {
  const cat = S.apps?.catalogue || [];
  const f = vue.donnees;
  const installees = cat.filter((a) => a.installations.length);
  const liste = S.apps ? filtrer(cat, f) : [];
  const filtreActif = f.recherche || (f.categorie && f.categorie !== "toutes") || f.difficulte || f.ram;
  return `${entete("Applications", { sous: S.apps ? `${installees.length} installée${installees.length > 1 ? "s" : ""} · ${cat.length} au catalogue` : "" })}
    <div class="recherche-apps" data-cle="rech">
      ${ic("search")}${champ("recherche", f.recherche || "", { placeholder: "Chercher une app, un usage, une alternative…", label: "Rechercher une application", rendre: true, max: 60 })}
      ${f.recherche ? `<button type="button" class="btn rond discret" data-action="vider-recherche" aria-label="Effacer">${ic("x")}</button>` : ""}
    </div>
    ${S.apps ? filtres(vue, cat) : `<div class="puces">${'<span class="puce fantome"></span>'.repeat(6)}</div>`}
    ${(S.apps?.erreurs || []).map((e) => `<p class="bandeau warn">${ic("triangle-alert")}${esc(e)}</p>`).join("")}
    ${bandeauMaj(installees)}
    ${!S.apps ? `<div class="grille-apps">${'<div class="carte app fantome"></div>'.repeat(6)}</div>`
      : liste.length ? `<p class="compte-apps">${liste.length} application${liste.length > 1 ? "s" : ""}</p><div class="grille-apps">${liste.map(carteApp).join("")}</div>`
      : vide("search", "Rien ne correspond", "Change ta recherche ou tes filtres pour voir d'autres applications.",
             filtreActif ? `<button type="button" class="btn plein" data-action="reinitialiser">Tout afficher</button>` : "")}`;
}

// 1.0.12 : nouvelles versions des apps (vérifiées chaque jour vers 05:00)
function bandeauMaj(installees) {
  if (!S.apps || !installees.length) return "";
  const maj = installees.flatMap((a) => a.installations.filter(majDispo).map((i) => ({ a, i })));
  const dates = installees.flatMap((a) => a.installations.map((i) => i.maj?.verifie_le).filter(Boolean));
  const verif = dates.length ? `vérifié ${ilYA(Math.min(...dates))}` : "jamais vérifié";
  const enCours = (S.etat?.taches || []).some((t) => t.etat === "en_cours" && (t.type === "maj_apps" || /^Mise à jour de \d+ app/.test(t.titre)));
  if (!maj.length) {
    return `<div class="maj-apps calme" data-cle="maj-apps">${ic("circle-check")}<span>Toutes les apps sont à jour <small>· ${verif}</small></span>
      ${btn("maj-verifier", enCours ? "Vérification…" : "Vérifier", { icone: "refresh-cw", classe: "discret", off: enCours })}</div>`;
  }
  const noms = [...new Set(maj.map((x) => x.a.nom))];
  return `<div class="maj-apps" data-cle="maj-apps">${ic("download")}
    <span><b>${maj.length} mise${maj.length > 1 ? "s" : ""} à jour disponible${maj.length > 1 ? "s" : ""}</b> : ${esc(noms.join(", "))}<small>${verif}. Réglages et données sont gardés.</small></span>
    <div class="actions">${btn("maj-verifier", "Vérifier", { icone: "refresh-cw", classe: "discret", off: enCours })}${btn("maj-tout", enCours ? "En cours…" : "Tout mettre à jour", { icone: "download", classe: "plein", off: enCours })}</div></div>`;
}

// ---------------------------------------------------------------------------------------- fiche
// Joueurs connectés au serveur Minecraft (1.0.11, « Server List Ping » toutes les minutes).
function blocJoueurs(m) {
  if (!m.en_ligne) return `<div class="joueurs-mc hors">${ic("users")}<div><b>Serveur hors ligne</b><small>Il démarre, ou il est arrêté. Les joueurs s'affichent ici dès qu'il répond.</small></div></div>`;
  return `<div class="joueurs-mc">${ic("users")}<div><b>${m.joueurs}/${m.max} joueur${m.joueurs > 1 ? "s" : ""} en ligne</b>
    <small>${esc(m.version || "")}${m.motd ? ` · « ${esc(m.motd)} »` : ""}</small></div>
    ${m.noms?.length ? `<ul class="noms-mc">${m.noms.map((n) => `<li><i class="voyant ok"></i>${esc(n)}</li>`).join("")}</ul>` : ""}</div>`;
}

function blocInstallation(a, i) {
  const [ton, nom] = ETATS[i.etat] || ETATS.erreur;
  const t = tachePour(a.id, i.noeud);
  const d = { noeud: i.noeud };
  const actif = i.etat === "actif" || i.etat === "partiel";
  return `<section class="carte installation" data-cle="i-${i.noeud}">
    <header>
      <div><h3>Sur ${NOM(i.noeud)}</h3><p>Installée ${ilYA(i.installe_le)}</p></div>
      ${t ? `<span class="pastille cours">${ic("loader-circle", "tourne")}${esc(t.titre)}</span>` : pastille(ton, nom)}
    </header>
    ${t && t.journal.length ? `<pre class="journal mini">${t.journal.map(esc).join("\n")}</pre>` : ""}
    ${a.id === "minecraft" && S.etat?.minecraft && S.etat.minecraft.noeud === i.noeud ? blocJoueurs(S.etat.minecraft) : ""}
    ${majDispo(i) && !t ? `<div class="maj-app">${ic("download")}<span><b>Nouvelle version disponible</b><small>Réglages et données sont gardés. Vérifié ${ilYA(i.maj.verifie_le)}.</small></span>${btn("mettre-a-jour", "Mettre à jour", { icone: "download", classe: "plein", d })}</div>` : ""}
    ${i.url_https ? `<button type="button" class="adresse https" data-action="ouvrir" data-url="${esc(i.url_https)}" title="Adresse HTTPS, depuis tes appareils Tailscale">${ic("lock")}<span>${esc(i.url_https.replace(/^https:\/\//, ""))}</span><em class="etiquette">Tailscale</em>${ic("external-link")}</button>` : ""}
    ${i.url ? `<button type="button" class="adresse" data-action="ouvrir" data-url="${esc(i.url)}">${ic("globe")}<span>${esc(i.url.replace(/^https?:\/\//, ""))}</span>${i.url_https ? `<em class="etiquette">Maison</em>` : ""}${ic("external-link")}</button>` : ""}
    <div class="actions">
      ${adresseApp(i) ? btn("ouvrir", "Ouvrir", { icone: "external-link", classe: "plein", d: { url: adresseApp(i) }, off: !actif }) : ""}
      ${actif ? btn("arreter", "Arrêter", { icone: "square", d, off: !!t }) : btn("demarrer", "Démarrer", { icone: "play", classe: i.url ? "" : "plein", d, off: !!t })}
      ${btn("redemarrer", "Redémarrer", { icone: "rotate-cw", d, off: !!t || !actif })}
      ${a.reglages.length ? btn("reglages", "Réglages", { icone: "settings", d, off: !!t }) : ""}
      ${btn("journal", "Journal", { icone: "scroll-text", d })}
      ${majDispo(i) ? "" : btn("mettre-a-jour", "Mettre à jour", { icone: "download", d, off: !!t })}
      ${btn("desinstaller", "Désinstaller", { icone: "trash-2", classe: "danger-texte", d, off: !!t })}
    </div>
  </section>`;
}

function blocInstaller(vue, a) {
  const noeuds = (vue.detail?.noeuds || S.apps?.noeuds || []).filter((n) => !a.installations.some((i) => i.noeud === n.nom));
  const d = vue.donnees;
  const choisi = noeuds.find((n) => n.nom === d.noeud) || noeuds.find((n) => n.nom === a.noeud_conseille && n.en_ligne) || noeuds.find((n) => n.en_ligne) || noeuds[0];
  if (!choisi) return "";
  const emplacements = choisi.emplacements || [];
  const manque = choisi.ram_libre != null && a.ram_mo * 2 ** 20 > choisi.ram_libre;
  const t = tachePour(a.id);
  return `<section class="carte installation nouvelle" data-cle="installer">
    <header><div><h3>Installer ${esc(a.nom)}</h3><p>Environ ${memoire(a.ram_mo * 2 ** 20)} de mémoire</p></div></header>
    <div class="reglage"><span class="reglage-nom">Sur quel nœud ?</span>
      <div class="segments">${noeuds.map((n) => `<button type="button" class="${n === choisi ? "actif" : ""}" data-action="noeud" data-nom="${n.nom}"${n.en_ligne && n.docker ? "" : " disabled"}>
        ${NOM(n.nom)}${n.nom === a.noeud_conseille ? "<em>conseillé</em>" : ""}${n.en_ligne ? "" : "<em>hors ligne</em>"}</button>`).join("")}</div>
      ${manque ? `<small class="alerte">${ic("triangle-alert")}Mémoire libre sur ${NOM(choisi.nom)} : ${memoire(choisi.ram_libre)}. C'est juste pour cette application.</small>` : ""}
    </div>
    ${emplacements.length ? `<div class="reglage"><span class="reglage-nom">Où ranger ses données ?</span>
      <div class="segments">
        <button type="button" class="${!d.emplacement ? "actif" : ""}" data-action="emplacement" data-chemin="">Carte système</button>
        ${emplacements.map((e) => `<button type="button" class="${d.emplacement === e ? "actif" : ""}" data-action="emplacement" data-chemin="${esc(e)}">Disque « ${esc(e.split("/").pop())} »</button>`).join("")}
      </div></div>` : ""}
    ${a.reglages.map((r) => champReglage(r, d, d.erreurCle, false)).join("")}
    <div class="actions">${t
      ? `<span class="pastille cours">${ic("loader-circle", "tourne")}${esc(t.titre)}…</span>`
      : btn("installer", `Installer sur ${NOM(choisi.nom)}`, { icone: "download", classe: "plein grand", d: { noeud: choisi.nom }, off: !choisi.en_ligne })}</div>
    ${t && t.journal.length ? `<pre class="journal mini">${t.journal.map(esc).join("\n")}</pre>` : ""}
  </section>`;
}

// Bloc d'avertissements d'une app signalée (socket Docker, réseau hôte, USB, gourmande, droits étendus).
function blocDrapeaux(a) {
  if (!a.drapeaux?.length) return "";
  return `<div class="drapeaux" data-cle="drapeaux">${a.drapeaux.map((code) => {
    const [titre, texte, icone] = DRAPEAUX[code] || ["À savoir", code, "triangle-alert"];
    return `<div class="drapeau"><span class="drapeau-icone">${ic(icone)}</span><div><b>${esc(titre)}</b><p>${esc(texte)}</p></div></div>`;
  }).join("")}</div>`;
}

// Fiche détaillée (details.json) : à quoi ça sert, pour qui, difficulté, mémoire, alternative, site.
function blocDetails(a) {
  const d = a.details;
  if (!d) return "";
  const dif = DIFFICULTES[d.difficulte] || ["Moyen", "warn"];
  const lignes = [
    d.a_quoi_ca_sert ? `<p class="description">${esc(d.a_quoi_ca_sert)}</p>` : "",
    d.pour_qui ? `<p class="pour-qui">${ic("users")}<span><b>Pour qui ?</b> ${esc(d.pour_qui)}</span></p>` : "",
  ].join("");
  return `<section class="carte details-app" data-cle="details">
    ${lignes}
    <dl class="fiche compacte">
      <div><dt>Difficulté</dt><dd><span class="puce-diff ${dif[1]}">${esc(dif[0])}</span></dd></div>
      <div><dt>Mémoire estimée</dt><dd>${memoire((d.ram_mo ?? a.ram_mo) * 2 ** 20)}</dd></div>
      ${d.alternative_a ? `<div><dt>Alternative à</dt><dd>${esc(d.alternative_a)}</dd></div>` : ""}
      <div><dt>Ports</dt><dd>${a.ports.map((p) => `${p.port}${p.proto === "udp" ? "/udp" : ""}`).join(", ") || "–"}</dd></div>
    </dl>
    ${d.site ? `<a class="lien-site" href="${esc(d.site)}" target="_blank" rel="noopener noreferrer">${ic("book-open")}<span>Site officiel et documentation</span>${ic("external-link")}</a>` : ""}
  </section>`;
}

function fiche(vue, id) {
  const a = S.apps?.catalogue.find((x) => x.id === id);
  if (!a) {
    return `${entete("Application", { retour: "#/apps" })}${S.apps ? vide("circle-help", "Application introuvable", "Elle n'est pas au catalogue de cette tour.") : ""}`;
  }
  const installations = (vue.detail?.id === id ? vue.detail.installations : a.installations) || [];
  const complet = { ...a, installations };
  return `${entete(esc(a.nom), { retour: "#/apps", sous: esc(a.categorie) })}
  <div class="fiche-app">
    <aside class="carte resume">
      <span class="app-icone geant">${ic(a.icone)}</span>
      <p class="description">${esc(a.description)}</p>
      <dl class="fiche compacte">
        <div><dt>Mémoire</dt><dd>${memoire(a.ram_mo * 2 ** 20)}</dd></div>
        <div><dt>Conseillé sur</dt><dd>${NOM(a.noeud_conseille)}</dd></div>
        <div><dt>Image</dt><dd class="fin">${esc(a.image)}</dd></div>
      </dl>
      ${a.notes?.length ? `<ul class="notes">${a.notes.map((n) => `<li>${ic("info")}<span>${esc(n)}</span></li>`).join("")}</ul>` : ""}
    </aside>
    <div class="colonne">
      ${blocDetails(complet)}
      ${blocDrapeaux(complet)}
      ${installations.map((i) => blocInstallation(complet, i)).join("")}
      ${!installations.length || vue.donnees.ailleurs ? blocInstaller(vue, complet)
        : (S.apps?.noeuds.length || 0) > installations.length ? `<button type="button" class="btn discret seul" data-action="ailleurs">${ic("plus")}<span>Installer aussi sur un autre nœud</span></button>` : ""}
    </div>
  </div>`;
}

// ------------------------------------------------------------------------------------ dialogues
function ouvrirReglages(a, installation) {
  const d = ouvrir({
    classe: "large",
    donnees: valeursInitiales(a, installation.reglages),
    rendu: () => `${enteteDialogue(`Réglages de ${esc(a.nom)}`, { icone: "settings", sous: `Sur ${NOM(installation.noeud)}. L'application redémarre pour les appliquer.` })}
      <div class="dlg-corps formulaire">${a.reglages.map((r) => champReglage(r, d.donnees, d.donnees.erreurCle, true)).join("")}</div>
      <footer class="dlg-pied">${btn("fermer", "Annuler", { classe: "discret" })}${btn("enregistrer", "Enregistrer", { icone: "check", classe: "plein" })}</footer>`,
    actions: {
      ...actionsFormulaire(() => a),
      async enregistrer() {
        try {
          await agir(`/api/apps/${a.id}/reglages`, { noeud: installation.noeud, reglages: collecter(a, d.donnees, true) });
          d.fermer();
        } catch (e) {
          d.donnees.erreurCle = e.donnees?.cle || null;
          throw e;
        }
      },
    },
  });
}

function ouvrirJournal(a, nomNoeud) {
  const d = ouvrir({
    classe: "large haut",
    donnees: { lignes: null, erreur: "" },
    rendu: () => `${enteteDialogue(`Journal de ${esc(a.nom)}`, { icone: "scroll-text", sous: `Sur ${NOM(nomNoeud)} · 200 dernières lignes` })}
      <div class="dlg-corps sans-marge">${d.donnees.lignes
        ? d.donnees.lignes.length ? `<pre class="journal" id="journal-app">${d.donnees.lignes.map(esc).join("\n")}</pre>` : vide("scroll-text", "Journal vide", "L'application n'a encore rien écrit.")
        : d.donnees.erreur ? vide("circle-x", "Journal indisponible", esc(d.donnees.erreur)) : `<div class="attente">${ic("loader-circle", "tourne")}</div>`}</div>
      <footer class="dlg-pied">${btn("actualiser", "Actualiser", { icone: "refresh-cw", classe: "discret" })}${btn("fermer", "Fermer", { classe: "plein" })}</footer>`,
    actions: { actualiser: () => relire() },
  });
  async function relire() {
    try {
      const r = await lireProtege(`/api/apps/${a.id}/journal?noeud=${encodeURIComponent(nomNoeud)}&lignes=200`);
      d.donnees.lignes = r.lignes;
      d.donnees.erreur = "";
    } catch (e) {
      d.donnees.erreur = e.message;
      if (e.code === "annule") d.fermer();
    }
    rendre();
    setTimeout(() => { const p = document.getElementById("journal-app"); if (p) p.scrollTop = p.scrollHeight; }, 40);
  }
  relire();
}

function ouvrirDesinstaller(a, nomNoeud) {
  const d = ouvrir({
    classe: "etroit",
    donnees: { supprimer: false },
    rendu: () => `${enteteDialogue(`Désinstaller ${esc(a.nom)} ?`, { icone: "triangle-alert", ton: "danger", sansCroix: true })}
      <div class="dlg-corps">
        <p class="texte">${esc(a.nom)} sera arrêtée et retirée de ${NOM(nomNoeud)}.</p>
        <label class="reglage en-ligne cadre${d.donnees.supprimer ? " danger" : ""}">
          <span class="reglage-nom">Supprimer aussi les données</span>${interrupteur("bascule", d.donnees.supprimer, {}, "Supprimer aussi les données")}
          <small>${d.donnees.supprimer ? "Mondes, fichiers et réglages de cette application seront effacés définitivement." : "Ses données sont gardées : en la réinstallant, tu retrouves tout."}</small>
        </label>
      </div>
      <footer class="dlg-pied">${btn("fermer", "Annuler", { classe: "discret" })}${btn("valider", d.donnees.supprimer ? "Désinstaller et effacer" : "Désinstaller", { icone: "trash-2", classe: "danger" })}</footer>`,
    actions: {
      bascule() { d.donnees.supprimer = !d.donnees.supprimer; },
      async valider() {
        await agir(`/api/apps/${a.id}/desinstaller`, { noeud: nomNoeud, supprimer_donnees: d.donnees.supprimer });
        d.fermer();
      },
    },
  });
}

// ------------------------------------------------------------------------------------------ vue
const FILTRES_CATALOGUE = ["recherche", "categorie", "difficulte", "ram"];
const vue = {
  donnees: { categorie: "toutes" },
  detail: null,
  entrer(args) {
    const id = args[0];
    if (id !== this.idCourant) {
      this.idCourant = id;
      this.detail = null;
      // On garde les filtres du catalogue en revenant d'une fiche, on repart propre sinon.
      const filtres = {};
      for (const k of FILTRES_CATALOGUE) if (this.donnees[k]) filtres[k] = this.donnees[k];
      this.donnees = filtres;
      this.formulairePret = false;
    }
  },
  async charger(args) {
    const id = args[0];
    if (!id) return;
    const r = await lire(`/api/apps/${id}`).catch(() => null);
    if (r && this.idCourant === id) this.detail = r;
  },
  app() { return S.apps?.catalogue.find((x) => x.id === this.idCourant); },
  rendu(args) {
    if (!args[0]) return catalogue(this);
    const a = this.app();
    if (a && !this.formulairePret) { Object.assign(this.donnees, valeursInitiales(a)); this.formulairePret = true; }
    return fiche(this, args[0]);
  },
  actions: {
    ...actionsFormulaire(() => vue.app()),
    filtre({ f }) { this.donnees.categorie = f; },
    "filtre-difficulte"({ v }) { this.donnees.difficulte = this.donnees.difficulte === v ? "" : v; },
    "filtre-ram"({ v }) { this.donnees.ram = this.donnees.ram === v ? "" : v; },
    "vider-recherche"() { this.donnees.recherche = ""; },
    reinitialiser() { for (const k of FILTRES_CATALOGUE) this.donnees[k] = k === "categorie" ? "toutes" : ""; },
    noeud({ nom }) { this.donnees.noeud = nom; this.donnees.emplacement = ""; },
    emplacement({ chemin }) { this.donnees.emplacement = chemin; },
    ailleurs() { this.donnees.ailleurs = true; },
    async installer({ noeud: n }) {
      const a = this.app();
      try {
        const corps = { noeud: n, reglages: collecter(a, this.donnees, false) };
        if (this.donnees.emplacement) corps.emplacement = this.donnees.emplacement;
        const r = await agir(`/api/apps/${a.id}/installer`, corps);
        this.donnees.erreurCle = null;
        this.donnees.ailleurs = false;
        if (r.tache && S.etat && !S.etat.taches.some((t) => t.id === r.tache.id)) S.etat.taches.unshift(r.tache);
        toast(`Installation de ${a.nom} lancée sur ${NOM(r.noeud || n)}.`);
      } catch (e) {
        this.donnees.erreurCle = e.donnees?.cle || null;
        throw e;
      }
    },
    async demarrer({ noeud: n }) { await this.simple("demarrer", n); },
    async arreter({ noeud: n }) { await this.simple("arreter", n); },
    async redemarrer({ noeud: n }) { await this.simple("redemarrer", n); },
    async "mettre-a-jour"({ noeud: n }) { await this.simple("mettre-a-jour", n); },
    async "maj-verifier"() {
      const r = await agir("/api/apps/maj/verifier");
      for (const x of r.noeuds || []) if (x.tache && S.etat && !S.etat.taches.some((t) => t.id === x.tache.id)) S.etat.taches.unshift(x.tache);
      toast("Recherche de nouvelles versions : le résultat arrive dans une minute.");
    },
    async "maj-tout"() {
      const r = await agir("/api/apps/maj/tout");
      for (const x of r.noeuds || []) if (x.tache && S.etat && !S.etat.taches.some((t) => t.id === x.tache.id)) S.etat.taches.unshift(x.tache);
      const rates = (r.noeuds || []).filter((x) => !x.ok);
      if (rates.length) toast(`Échec sur ${rates.map((x) => NOM(x.noeud)).join(", ")} : ${rates[0].erreur || "nœud injoignable"}.`, "erreur");
      else toast("Mise à jour des apps lancée : chacune redémarre à son tour.");
    },
    async reglages({ noeud: n }) {
      const a = this.app();
      // Relit la fiche : les réglages y sont en clair pour l'écran local ou une session PIN.
      const r = await tenter(() => lire(`/api/apps/${a.id}`));
      const i = (r || a).installations.find((x) => x.noeud === n);
      if (i) ouvrirReglages(a, i);
    },
    journal({ noeud: n }) { ouvrirJournal(this.app(), n); },
    desinstaller({ noeud: n }) { ouvrirDesinstaller(this.app(), n); },
    ouvrir({ url }) {
      if (session.demo) {
        return informer({ titre: "Démonstration", icone: "sparkles", texte: `<p>Sur la vraie tour, ce bouton ouvre l'application à l'adresse :</p><p class="adresse-grande">${esc(url)}</p>` });
      }
      if (session.auth.local) {
        return informer({ titre: "Ouvre-la sur ton appareil", icone: "smartphone", texte: `<p>L'écran de la tour reste sur YDZ OS. Depuis ton téléphone ou ton PC, va à l'adresse :</p><p class="adresse-grande">${esc(url)}</p>` });
      }
      window.open(url, "_blank", "noopener");
      return undefined;
    },
  },
  async simple(action, n) {
    const a = this.app();
    const r = await agir(`/api/apps/${a.id}/${action}`, { noeud: n });
    if (r.tache && S.etat && !S.etat.taches.some((t) => t.id === r.tache.id)) S.etat.taches.unshift(r.tache);
  },
};
export default vue;
