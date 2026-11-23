// Réglages > Général (1.0.13) : nom du labo et des Pi, Wi-Fi, Bluetooth, souris, écran, heure, son.
// Remplace l'ancienne section « Écran » (#/reglages/ecran redirige ici).
import { esc, ic } from "../outils.js";
import { S, NOM, LABO, btn, champ, interrupteur, pastille, ouvrir, enteteDialogue, confirmer, informer, toast, rendre } from "../noyau.js";
import { lire, agir, session } from "../api.js";

const PUCES = [["g-nom", "Nom", "tag"], ["g-wifi", "Wi-Fi", "wifi"], ["g-bluetooth", "Bluetooth", "bluetooth"], ["g-souris", "Souris", "mouse"],
  ["g-ecran", "Écran", "monitor"], ["g-heure", "Heure", "clock"], ["g-son", "Son", "volume-2"]];

// « Pi One » -> « pi-one » (même règle que le démon : a-z, 0-9, tirets)
export const slug = (t) => String(t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 32).replace(/-+$/, "");
const hoteValide = (h) => /^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$/.test(h || "");

// ------------------------------------------------------------------------------------------- nom
function blocNom(vue) {
  const g = vue.general;
  const l = S.etat.labo || {};
  const hub = S.etat.hub;
  const d = vue.donnees;
  const nomPi = d.gNom ?? NOM(hub);
  const hote = d.gHote ?? hub;
  const propose = slug(nomPi);
  const autres = S.etat.noeuds.filter((n) => n.nom !== hub && n.connu && n.numero);
  const demandes = S.etat.appairage || [];
  return `
    <h2 class="rubrique" id="g-nom">Nom</h2>
    ${demandes.map((x) => `<p class="bandeau">${ic("network")}<span>Un Pi « ${esc(x.nom)} » (${esc(x.ip)}) demande à rejoindre le labo.</span>${btn("g-demande", "Répondre", { classe: "discret", d: { nom: x.nom } })}</p>`).join("")}
    <div class="carte bloc">
      <h2 class="bloc-titre">${ic("tag")}Nom du labo</h2>
      <div class="ligne-champ g-ligne">${champ("gLabo", d.gLabo ?? (l.nom || "Home Pi Lab"), { max: 40, entree: "g-labo", label: "Nom du labo" })}${btn("g-labo", "Enregistrer", { icone: "check", titre: "Enregistrer le nom du labo" })}</div>
      <p class="note-fine">C'est le nom de l'appli sur l'écran d'accueil du téléphone et en bas de l'écran HDMI. Le logiciel, lui, reste YDZ OS.</p>
    </div>
    <div class="carte bloc">
      <h2 class="bloc-titre">${ic("server")}Ce Pi</h2>
      <div class="reglage"><span class="reglage-nom">Nom affiché</span>
        <div class="ligne-champ g-ligne">${champ("gNom", nomPi, { max: 32, entree: "g-nom", label: "Nom affiché de ce Pi", rendre: true })}${btn("g-nom", "Enregistrer", { icone: "check", titre: "Enregistrer le nom affiché" })}</div></div>
      <div class="reglage"><span class="reglage-nom">Nom réseau</span>
        <div class="ligne-champ g-ligne">${champ("gHote", hote, { max: 32, entree: "g-hote", label: "Nom réseau (nom d'hôte)", rendre: true, erreur: !hoteValide(hote) })}${btn("g-hote", "Changer", { icone: "pencil", titre: "Changer le nom réseau", off: hote === hub || !hoteValide(hote) })}</div>
        ${propose && propose !== hub && propose !== hote ? `<button type="button" class="lien-discret" data-action="g-proposer" data-h="${esc(propose)}">Utiliser « ${esc(propose)} »</button>` : ""}
        <small>${hoteValide(hote) ? "" : "Lettres minuscules sans accent, chiffres et tirets seulement. "}Adresse actuelle : <b>${esc(g?.adresse || `http://${hub}.local/`)}</b>${g?.ip ? ` · IP <b>${esc(g.ip)}</b>` : ""}. Si tu le changes, l'adresse en .local change aussi ; l'adresse IP, elle, reste la même.</small></div>
    </div>
    ${autres.length ? `<div class="carte bloc"><h2 class="bloc-titre">${ic("layers")}Les autres Pi du labo</h2>
      <ul class="liste-fine g-pis">${autres.map((n) => `<li><span><b>${esc(NOM(n.nom))}</b><small>${esc(n.nom)}.local${n.en_ligne ? "" : " · hors ligne"}</small></span>${btn("g-renommer", "Renommer", { icone: "pencil", classe: "discret", d: { nom: n.nom } })}</li>`).join("")}</ul>
      <p class="note-fine">${ic("info")}Un nouveau Pi s'appelle tout seul « Pi Two », « Pi Three »… Pour remplacer un Pi, éteins l'ancien et choisis « Remplace … » quand le nouveau demande à rejoindre le labo.</p></div>` : ""}`;
}

// ------------------------------------------------------------------------------------------- Wi-Fi
const barres = (s) => (s >= 67 ? "wifi" : s >= 40 ? "wifi-high" : s >= 20 ? "wifi-low" : "wifi-zero");
function bandeauBascule(b) {
  if (!b) return "";
  const age = Date.now() / 1000 - (b.t || 0);
  if (b.etat === "en_cours") return `<p class="bandeau">${ic("loader-circle", "tourne")}<span>${esc(b.message)} ${b.etape === "verification" ? "Vérification de la box et d'Internet…" : ""} Cette page peut rester injoignable jusqu'à une minute.</span></p>`;
  if (age > 3600) return "";
  if (b.etat === "ok") return `<p class="bandeau ok">${ic("circle-check")}<span>${esc(b.message)}.</span></p>`;
  return `<p class="bandeau warn">${ic("triangle-alert")}<span>${esc(b.message)}</span></p>`;
}
function blocWifi(vue) {
  const w = vue.wifi;
  if (!w) return `<h2 class="rubrique" id="g-wifi">Wi-Fi</h2><div class="carte bloc"><div class="attente">${ic("loader-circle", "tourne")}</div></div>`;
  if (!w.present) return `<h2 class="rubrique" id="g-wifi">Wi-Fi</h2><div class="carte bloc"><p class="texte">Pas de Wi-Fi géré par NetworkManager sur ce Pi.</p></div>`;
  const occupe = w.bascule_en_cours;
  const connu = new Set((w.enregistres || []).map((x) => x.ssid));
  const reseaux = (w.reseaux || []).slice(0, 14);
  return `
    <h2 class="rubrique" id="g-wifi">Wi-Fi</h2>
    ${bandeauBascule(w.bascule)}
    <div class="carte bloc">
      <label class="reglage en-ligne"><span class="reglage-nom">${ic("wifi")} Wi-Fi</span>${w.coupure_possible || !w.actif ? interrupteur("g-wifi-radio", w.actif, {}, "Wi-Fi") : `<button type="button" class="inter actif" role="switch" aria-checked="true" disabled aria-label="Wi-Fi"><i></i></button>`}
        <small>${w.actif ? (w.connecte ? `Connecté à <b>${esc(w.connecte)}</b>.` : "Pas connecté.") : "Wi-Fi coupé."} ${w.coupure_possible ? "" : esc(w.coupure_raison || "")}</small></label>
    </div>
    <div class="carte bloc">
      <div class="bloc-tete"><h2 class="bloc-titre">${ic("signal")}Réseaux à portée</h2>${btn("g-wifi-scan", "Rechercher", { icone: "refresh-cw", classe: "discret", off: occupe })}</div>
      ${reseaux.length ? `<ul class="g-reseaux">${reseaux.map((r) => `<li class="${r.en_cours ? "actif" : ""}" data-cle="w-${esc(r.ssid)}">
          <button type="button" class="g-reseau" data-action="g-wifi-choisir" data-ssid="${esc(r.ssid)}"${r.en_cours || occupe || !r.pris_en_charge ? " disabled" : ""}>
            ${ic(barres(r.signal), r.signal < 30 ? "pale" : "")}<span class="g-reseau-nom"><b>${esc(r.ssid)}</b><small>${esc(r.bande)}${r.securite !== "Ouvert" ? ` · ${esc(r.securite)}` : " · ouvert"}${connu.has(r.ssid) ? " · enregistré" : ""}</small></span>
            ${r.en_cours ? pastille("ok", "Connecté") : r.securite !== "Ouvert" ? ic("lock", "pale") : ""}</button></li>`).join("")}</ul>`
      : `<p class="texte">Aucun réseau trouvé pour l'instant. Touche « Rechercher ».</p>`}
    </div>
    ${(w.enregistres || []).length ? `<div class="carte bloc"><h2 class="bloc-titre">${ic("archive")}Réseaux enregistrés</h2>
      <ul class="liste-fine g-pis">${w.enregistres.map((c) => `<li data-cle="we-${esc(c.uuid)}"><span><b>${esc(c.ssid)}</b><small>${c.actif ? "utilisé en ce moment" : c.nom !== c.ssid ? esc(c.nom) : "enregistré"}</small></span>
        <span class="actions">${c.actif ? pastille("ok", "Connecté") : `${btn("g-wifi-saved", "Se connecter", { classe: "discret", d: { uuid: c.uuid }, off: occupe })}${btn("g-wifi-oublier", "", { icone: "trash-2", classe: "discret rond danger-texte", d: { uuid: c.uuid }, titre: `Oublier ${c.ssid}` })}`}</span></li>`).join("")}</ul></div>` : ""}
    <p class="note-fine">${ic("info")}Changer de réseau se fait en sécurité : si le Pi n'arrive pas à joindre la box et Internet dans la minute, il revient tout seul sur le réseau d'avant. Le profil Ethernet du PC (ydz-pc) n'est jamais touché.</p>`;
}

function dialogueWifi(vue, { ssid, uuid }) {
  const w = vue.wifi;
  const r = (w.reseaux || []).find((x) => x.ssid === ssid);
  const enr = (w.enregistres || []).find((x) => (uuid ? x.uuid === uuid : x.ssid === ssid));
  const nom = ssid || enr?.ssid;
  const besoinMdp = !uuid && !enr && r && r.securite !== "Ouvert";
  const ancien = w.connecte;
  ouvrir({
    classe: "etroit",
    donnees: { mdp: "" },
    rendu: (d) => `${enteteDialogue(`Se connecter à ${esc(nom)}`, { icone: "wifi" })}
      <div class="dlg-corps">
        ${besoinMdp ? `<label class="reglage"><span class="reglage-nom">Mot de passe du Wi-Fi</span>${champ("mdp", d.donnees.mdp, { type: "secret", max: 64, entree: "valider", rendre: true, label: "Mot de passe du Wi-Fi" })}</label>` : ""}
        ${!besoinMdp && enr && !uuid ? `<p class="note-fine">${ic("info")}Réseau déjà enregistré : le mot de passe connu sera utilisé.</p>` : ""}
        <p class="bandeau warn">${ic("triangle-alert")}<span>Pendant le changement, cette page peut rester injoignable jusqu'à une minute. Sur le nouveau réseau, le Pi peut recevoir une autre adresse IP.</span></p>
        ${ancien ? `<p class="bandeau">${ic("undo-2")}<span>Le Pi reviendra tout seul sur <b>${esc(ancien)}</b> en cas d'échec.</span></p>` : ""}
      </div>
      <footer class="dlg-pied">${btn("fermer", "Annuler", { classe: "discret" })}${btn("valider", "Changer de réseau", { icone: "wifi", classe: "plein", off: besoinMdp && (d.donnees.mdp || "").length < 8 })}</footer>`,
    actions: {
      async valider() {
        const corps = uuid ? { uuid } : { ssid: nom, ...(besoinMdp ? { mot_de_passe: this.donnees.mdp } : {}) };
        this.donnees.mdp = "";
        const rep = await agir("/api/wifi/connecter", corps);
        this.fermer();
        vue.wifi = { ...vue.wifi, bascule_en_cours: true, bascule: { etat: "en_cours", ssid: rep.ssid, ancien: rep.ancien, t: Date.now() / 1000, message: `Connexion à ${rep.ssid} en cours…` } };
        toast(`Changement de réseau vers ${rep.ssid} : réponse dans une minute au plus.`);
        suivreWifi(vue);
      },
    },
  });
}

// Pendant une bascule : relecture toutes les 3 s (les échecs de connexion sont attendus, la page revient seule)
function suivreWifi(vue) {
  clearTimeout(vue._suiviWifi);
  vue._suiviWifi = setTimeout(async () => {
    try { vue.wifi = await lire("/api/wifi"); rendre(); } catch { /* le Pi change de réseau */ }
    const b = vue.wifi?.bascule;
    if (!b || b.etat === "en_cours" || vue.wifi.bascule_en_cours) suivreWifi(vue);
    else toast(b.message, b.etat === "ok" ? "ok" : "erreur", 9000);
  }, 3000);
}

// --------------------------------------------------------------------------------------- Bluetooth
function bandeauAppairage(a) {
  if (!a) return "";
  if (a.etat === "confirmer") {
    // 1.0.15 : Justin confirme explicitement que l'appareil affiche bien ce code (sinon « no » à bluetoothctl).
    return `<div class="bandeau g-code">${ic("bluetooth")}<div><span>${esc(a.nom)} affiche-t-il ce code ?</span><b class="g-code-chiffres">${esc(a.code)}</b></div>
      <div class="g-code-actions">${btn("g-bt-refuser", "Non", { classe: "discret", d: { mac: a.mac } })}${btn("g-bt-confirmer", "Oui, c'est le bon", { classe: "plein", icone: "check", d: { mac: a.mac } })}</div></div>`;
  }
  if (a.etat === "taper") {
    return `<div class="bandeau g-code">${ic("bluetooth")}<div><span>${esc(a.nom)} : tape ce code sur l'appareil, puis Entrée.</span><b class="g-code-chiffres">${esc(a.code)}</b></div></div>`;
  }
  if (a.etat === "pin") return `<div class="bandeau">${ic("key-round")}<span>${esc(a.message)}</span></div>
    <div class="ligne-champ">${champ("btPin", "", { chiffres: true, max: 16, entree: "g-bt-pin", label: "Code de l'appareil" })}${btn("g-bt-pin", "Envoyer", { icone: "send", classe: "plein", d: { mac: a.mac } })}</div>`;
  if (a.etat === "en_cours") return `<p class="bandeau">${ic("loader-circle", "tourne")}<span>${esc(a.message)}</span></p>`;
  return `<p class="bandeau ${a.etat === "ok" ? "ok" : "warn"}">${ic(a.etat === "ok" ? "circle-check" : "triangle-alert")}<span>${esc(a.message)}</span></p>`;
}
function blocBluetooth(vue) {
  const b = vue.bt;
  const titre = `<h2 class="rubrique" id="g-bluetooth">Bluetooth</h2>`;
  if (!b) return `${titre}<div class="carte bloc"><div class="attente">${ic("loader-circle", "tourne")}</div></div>`;
  if (!b.present) return `${titre}<div class="carte bloc"><p class="texte">${esc(b.raison || "Pas de Bluetooth sur ce Pi.")}</p></div>`;
  const app = b.appareils || [];
  const appaires = app.filter((x) => x.appaire), autres = app.filter((x) => !x.appaire);
  const ligne = (x) => `<li data-cle="bt-${x.mac}" class="${x.connecte ? "actif" : ""}">
    <span class="app-icone mini">${ic(x.icone)}</span>
    <span class="g-bt-nom"><b>${esc(x.nom)}</b><small>${esc(x.type_nom)} · ${esc(x.mac)}${x.batterie != null ? ` · ${x.batterie} %` : ""}</small></span>
    <span class="actions">${x.appaire
      ? `${x.connecte ? btn("g-bt", "Déconnecter", { classe: "discret", d: { mac: x.mac, a: "deconnecter" } }) : btn("g-bt", "Connecter", { classe: "discret", d: { mac: x.mac, a: "connecter" } })}${btn("g-bt", "", { icone: "trash-2", classe: "discret rond danger-texte", d: { mac: x.mac, a: "oublier" }, titre: `Oublier ${x.nom}` })}`
      : btn("g-bt", "Appairer", { icone: "link", classe: "discret", d: { mac: x.mac, a: "appairer" } })}</span></li>`;
  return `${titre}
    <div class="carte bloc">
      <label class="reglage en-ligne"><span class="reglage-nom">${ic("bluetooth")} Bluetooth</span>${interrupteur("g-bt-actif", b.actif, {}, "Bluetooth")}
        <small>${b.actif ? "Allumé : manettes, claviers, souris, casques et enceintes peuvent se connecter au Pi." : "Coupé."}</small></label>
    </div>
    ${bandeauAppairage(b.appairage)}
    ${b.actif ? `<div class="carte bloc">
      <div class="bloc-tete"><h2 class="bloc-titre">${ic("bluetooth-connected")}Mes appareils</h2>${btn("g-bt-scan", b.recherche ? `Recherche… ${b.recherche_reste_s || ""} s` : "Rechercher", { icone: b.recherche ? "" : "bluetooth-searching", classe: "discret", off: b.recherche })}</div>
      ${appaires.length ? `<ul class="g-bt">${appaires.map(ligne).join("")}</ul>` : `<p class="texte">Aucun appareil appairé.</p>`}
      ${autres.length ? `<h3 class="intertitre">À proximité</h3><ul class="g-bt">${autres.map(ligne).join("")}</ul>` : ""}
      <p class="note-fine">${ic("info")}Mets l'appareil en mode appairage (souvent un appui long sur le bouton Bluetooth), touche « Rechercher », puis « Appairer ».</p>
    </div>` : ""}`;
}

// ------------------------------------------------------------------------------------- heure, son
function blocHeure(vue) {
  const h = vue.general?.heure;
  return `<h2 class="rubrique" id="g-heure">Heure</h2>
    <div class="carte bloc"><div class="g-heure">
      <div><b class="chiffre moyen">${esc(h?.heure || new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }))}</b>
        <p>${esc((h?.fuseau || "Europe/Paris").replace(/_/g, " "))}${h?.decalage ? ` · UTC${esc(h.decalage.slice(0, 3))}:${esc(h.decalage.slice(3))}` : ""}</p></div>
      ${btn("g-fuseau", "Changer le fuseau", { icone: "globe" })}</div>
      <p class="note-fine">L'heure se règle toute seule par Internet. Le fuseau décide de l'heure affichée, des nuits de l'écran et de l'heure des sauvegardes.</p></div>`;
}
function ouvrirFuseaux(vue) {
  const actuel = vue.general?.heure?.fuseau || "Europe/Paris";
  const d = ouvrir({
    classe: "etroit",
    donnees: { q: "", liste: null },
    rendu: () => {
      const q = (d.donnees.q || "").toLowerCase().replace(/ /g, "_");
      const l = (d.donnees.liste || []).filter((f) => !q || f.toLowerCase().includes(q)).slice(0, 80);
      return `${enteteDialogue("Fuseau horaire", { icone: "globe", sous: `Actuel : ${esc(actuel.replace(/_/g, " "))}` })}
        <div class="dlg-corps sans-marge"><div class="g-recherche">${champ("q", d.donnees.q, { placeholder: "Chercher : Paris, Montreal, Tokyo…", rendre: true, label: "Chercher un fuseau" })}</div>
        <div class="liste-choix">${d.donnees.liste ? (l.length ? l.map((f) => `<button type="button" class="choix${f === actuel ? " actif" : ""}" data-action="prendre" data-f="${esc(f)}"><span><b>${esc(f.split("/").pop().replace(/_/g, " "))}</b><small>${esc(f.replace(/_/g, " "))}</small></span>${f === actuel ? ic("check") : ""}</button>`).join("") : `<p class="texte g-vide">Aucun fuseau ne correspond.</p>`) : `<div class="attente">${ic("loader-circle", "tourne")}</div>`}</div></div>`;
    },
    actions: {
      async prendre({ f }) {
        if (f === actuel) { this.fermer(); return; }
        const r = await agir("/api/general/heure", { fuseau: f });
        vue.general = { ...vue.general, heure: r };
        this.fermer();
        toast(`Fuseau horaire : ${f.replace(/_/g, " ")}.`, "ok");
      },
    },
  });
  lire("/api/general/fuseaux").then((r) => { d.donnees.liste = r.fuseaux?.length ? r.fuseaux : ["Europe/Paris", "UTC"]; rendre(); }).catch(() => { d.donnees.liste = ["Europe/Paris", "UTC"]; rendre(); });
}
function blocSon(vue) {
  const s = vue.general?.son;
  if (!s || !s.present) return "";   // pas de sortie son : pas de carte
  const vol = vue.donnees.gVol ?? s.volume ?? 0;
  return `<h2 class="rubrique" id="g-son">Son</h2>
    <div class="carte bloc">
      ${s.sorties.length > 1 ? `<div class="reglage"><span class="reglage-nom">Sortie</span><div class="segments">${s.sorties.map((x) => `<button type="button" class="${x.id === s.sortie ? "actif" : ""}" data-action="g-sortie" data-id="${esc(x.id)}">${esc(x.nom)}</button>`).join("")}</div></div>`
        : `<p class="texte">Sortie : <b>${esc(s.sorties[0]?.nom || "–")}</b></p>`}
      ${s.volume_reglable ? `<div class="glissiere">${ic("volume-2", "pale")}<input type="range" min="0" max="100" step="5" value="${vol}" data-champ="gVol" data-rendre data-fin="g-volume" style="--p:${vol}%" aria-label="Volume"><b>${vol} %</b></div>`
        : `<p class="note-fine">${ic("info")}Le volume de cette sortie se règle sur l'écran ou l'enceinte elle-même.</p>`}
    </div>`;
}

// ---------------------------------------------------------------------------------- panneau entier
export function panneauGeneral(vue, { ecran }) {
  const sonVisible = vue.general?.son?.present;
  return `
    <nav class="g-puces" aria-label="Parties de Général">${PUCES.filter(([id]) => id !== "g-son" || sonVisible).map(([id, nom, icone]) => `<button type="button" class="puce" data-action="g-aller" data-id="${id}">${ic(icone)}<span>${nom}</span></button>`).join("")}</nav>
    ${blocNom(vue)}
    ${blocWifi(vue)}
    ${blocBluetooth(vue)}
    <h2 class="rubrique" id="g-souris">Souris</h2>
    <div class="carte bloc"><label class="reglage en-ligne"><span class="reglage-nom">${ic("mouse-pointer-2")} Afficher le curseur de la souris</span>${interrupteur("curseur", Boolean(S.etat.affichage?.curseur), {}, "Afficher le curseur de la souris")}
      <small>${S.etat.affichage?.curseur ? "Le curseur reste visible sur l'écran HDMI (pratique avec une souris branchée sur le Pi)." : "Caché : l'écran HDMI n'affiche que le tableau de bord, sans flèche au milieu."} L'écran se recharge quelques secondes quand tu changes.</small></label></div>
    <h2 class="rubrique" id="g-ecran">Écran</h2>
    ${ecran(vue)}
    ${blocHeure(vue)}
    ${blocSon(vue)}`;
}

export async function chargerGeneral(vue) {
  const [g, w, b] = await Promise.all([lire("/api/general").catch(() => vue.general), lire("/api/wifi").catch(() => vue.wifi), lire("/api/bluetooth").catch(() => vue.bt)]);
  vue.general = g; vue.wifi = w; vue.bt = b;
  const a = b?.appairage;
  if (b?.recherche || (a && ["en_cours", "confirmer", "taper", "pin"].includes(a.etat))) suivreBt(vue);
}
function suivreBt(vue) {
  clearTimeout(vue._suiviBt);
  vue._suiviBt = setTimeout(async () => {
    if (S.route.args[0] !== "general") return;
    try { vue.bt = await lire("/api/bluetooth"); rendre(); } catch { /* réessai au prochain tour */ }
    const a = vue.bt?.appairage;
    if (vue.bt?.recherche || (a && ["en_cours", "confirmer", "taper", "pin"].includes(a.etat))) suivreBt(vue);
  }, 1200);
}

// ------------------------------------------------------------------- appairage : nouveau Pi ou remplaçant
export function ouvrirAppairage(dem) {
  const hub = S.etat?.hub;
  const pis = (S.etat?.noeuds || []).filter((n) => n.nom !== hub && n.numero && n.nom !== dem.nom);
  const prochain = S.etat?.labo?.noms?.[dem.nom] && !S.etat?.labo?.numeros?.[dem.nom] ? S.etat.labo.noms[dem.nom] : "un nouveau nom";
  ouvrir({
    classe: "etroit",
    donnees: { remplace: "" },
    rendu: (d) => `${enteteDialogue(`Un Pi veut rejoindre ${esc(LABO())}`, { icone: "network", sansCroix: true })}
      <div class="dlg-corps"><p class="texte">Le Pi « ${esc(dem.nom)} » (${esc(dem.ip)}) demande à rejoindre le labo. N'accepte que si c'est bien ton Raspberry Pi : il pourra ensuite recevoir des ordres de ${esc(NOM(hub))}.</p>
        <div class="liste-choix g-choix">
          <button type="button" class="choix${!d.donnees.remplace ? " actif" : ""}" data-action="choisir" data-r=""><span><b>Nouveau Pi</b><small>Il s'appellera ${esc(prochain)}</small></span>${!d.donnees.remplace ? ic("check") : ""}</button>
          ${pis.map((n) => `<button type="button" class="choix${d.donnees.remplace === n.nom ? " actif" : ""}" data-action="choisir" data-r="${esc(n.nom)}"${n.en_ligne ? " disabled" : ""}><span><b>Remplace ${esc(NOM(n.nom))}</b><small>${n.en_ligne ? "encore allumé : éteins-le d'abord" : `reprend son nom, son numéro et son nom réseau (${esc(n.nom)})`}</small></span>${d.donnees.remplace === n.nom ? ic("check") : ""}</button>`).join("")}
        </div></div>
      <footer class="dlg-pied">${btn("plus-tard", "Plus tard", { classe: "discret" })}${btn("refuser", "Refuser", { classe: "discret danger-texte" })}${btn("accepter", "Accepter", { classe: "plein" })}</footer>`,
    actions: {
      choisir({ r }) { this.donnees.remplace = r; },
      "plus-tard"() { this.fermer(); },
      async refuser() { this.fermer(); await agir(`/api/appairage/${dem.nom}/refuser`).then(() => toast("Demande refusée.")).catch((x) => toast(x.message, "erreur")); },
      async accepter() {
        const remplace = this.donnees.remplace || undefined;
        this.fermer();
        try {
          const r = await agir(`/api/appairage/${dem.nom}/accepter`, remplace ? { remplace } : {});
          toast(remplace ? `${r.nom_affiche || "Le nouveau Pi"} remplace l'ancien.` : `${r.nom_affiche || dem.nom} a rejoint le labo.`, "ok", 7000);
          if (r.a_faire) informer({ titre: "Encore une étape", icone: "terminal", texte: `<p>${esc(r.a_faire)}</p>` });
        } catch (x) { toast(x.message, "erreur"); }
      },
    },
  });
}

// ------------------------------------------------------------------------------------------ actions
const offset = (m) => (m === 0 ? "pile" : `${m > 0 ? "+" : "−"}${Math.abs(m) >= 60 ? `${Math.floor(Math.abs(m) / 60)} h${Math.abs(m) % 60 ? ` ${Math.abs(m) % 60}` : ""}` : `${Math.abs(m)} min`}`);
export { offset };

export const actionsGeneral = {
  "g-aller"({ id }) { document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }); },
  "g-demande"({ nom }) { const d = (S.etat.appairage || []).find((x) => x.nom === nom); if (d) ouvrirAppairage(d); },
  "g-proposer"({ h }) { this.donnees.gHote = h; },
  async "g-labo"() {
    const v = (this.donnees.gLabo ?? "").trim();
    if (!v) return;
    const r = await agir("/api/general/nom", { labo: v });
    S.etat.labo = r.etat_labo;
    this.donnees.gLabo = undefined;
    toast(`Le labo s'appelle maintenant « ${r.labo} ».`, "ok");
  },
  async "g-nom"() {
    const v = (this.donnees.gNom ?? "").trim();
    if (!v || v === NOM(S.etat.hub)) return;
    const r = await agir("/api/general/nom", { nom: v });
    S.etat.labo = r.etat_labo;
    this.donnees.gNom = undefined;
    toast(`Ce Pi s'appelle maintenant « ${r.nom} ».`, "ok");
  },
  async "g-hote"() {
    const h = (this.donnees.gHote ?? "").trim();
    const hub = S.etat.hub;
    if (!hoteValide(h) || h === hub) return;
    const ip = this.general?.ip;
    if (!(await confirmer({
      titre: `Nom réseau : ${h} ?`, libelle: "Changer le nom", icone: "pencil",
      texte: `L'adresse de l'interface devient <b>http://${esc(h)}.local/</b> (l'ancienne, http://${esc(hub)}.local/, ne marchera plus : refais tes raccourcis et favoris). L'adresse IP${ip ? ` <b>${esc(ip)}</b>` : ""} ne change pas. Le nom dans Tailscale suit aussi. YDZ redémarre : la page revient dans une dizaine de secondes.`,
    }))) return;
    const r = await agir("/api/general/nom", { hote: h });
    this.donnees.gHote = undefined;
    const ici = location.hostname;
    const url = ici.endsWith(".local") ? `${location.protocol}//${h}.local${location.port ? `:${location.port}` : ""}/#/reglages/general` : null;
    informer({ titre: "Nom réseau changé", icone: "circle-check", texte: `<p>Nouvelle adresse : <b>${esc(r.adresse)}</b>${r.ip ? ` (ou <b>http://${esc(r.ip)}/</b>)` : ""}.</p><p>Pense à refaire l'icône sur l'écran d'accueil du téléphone si elle utilisait l'ancienne adresse.</p>` })
      .then(() => { if (url) setTimeout(() => { location.href = url; }, 8000); });
  },
  async "g-renommer"({ nom }) {
    const d = ouvrir({
      classe: "etroit", donnees: { v: NOM(nom) },
      rendu: () => `${enteteDialogue(`Renommer ${esc(NOM(nom))}`, { icone: "pencil", sous: `${esc(nom)}.local` })}
        <div class="dlg-corps">${champ("v", d.donnees.v, { max: 32, entree: "ok", label: "Nouveau nom" })}</div>
        <footer class="dlg-pied">${btn("fermer", "Annuler", { classe: "discret" })}${btn("ok", "Renommer", { classe: "plein" })}</footer>`,
      actions: {
        async ok() {
          const r = await agir("/api/general/nom", { noeud: nom, nom: (this.donnees.v || "").trim() });
          S.etat.labo = r.etat_labo;
          this.fermer();
          toast(`Renommé en « ${r.nom} ».`, "ok");
        },
      },
    });
  },
  async "g-wifi-radio"() {
    const w = this.wifi;
    const voulu = !w.actif;
    if (!voulu && !(await confirmer({ titre: "Couper le Wi-Fi ?", texte: "Le Pi reste joignable par son câble Ethernet.", libelle: "Couper", icone: "wifi-off" }))) return;
    await agir("/api/wifi/radio", { actif: voulu });
    this.wifi = await lire("/api/wifi").catch(() => ({ ...w, actif: voulu }));
  },
  async "g-wifi-scan"() { this.wifi = await agir("/api/wifi/scan"); },
  "g-wifi-choisir"({ ssid }) { dialogueWifi(this, { ssid }); },
  "g-wifi-saved"({ uuid }) { dialogueWifi(this, { uuid }); },
  async "g-wifi-oublier"({ uuid }) {
    const c = (this.wifi.enregistres || []).find((x) => x.uuid === uuid);
    if (!(await confirmer({ titre: `Oublier ${c?.ssid || "ce réseau"} ?`, texte: "Le Pi ne s'y connectera plus tout seul. Il faudra redonner le mot de passe pour y revenir.", libelle: "Oublier", danger: true, icone: "trash-2" }))) return;
    await agir("/api/wifi/oublier", { uuid });
    this.wifi = await lire("/api/wifi").catch(() => this.wifi);
    toast("Réseau oublié.");
  },
  async "g-bt-actif"() { this.bt = await agir("/api/bluetooth", { actif: !this.bt.actif }); },
  async "g-bt-scan"() {
    await agir("/api/bluetooth/scan");
    this.bt = { ...this.bt, recherche: true, recherche_reste_s: 15 };
    suivreBt(this);
  },
  async "g-bt"({ mac, a }) {
    const x = (this.bt.appareils || []).find((d) => d.mac === mac);
    if (a === "oublier" && !(await confirmer({ titre: `Oublier ${x?.nom || mac} ?`, texte: "Il faudra l'appairer de nouveau pour s'en servir avec le Pi.", libelle: "Oublier", danger: true, icone: "trash-2" }))) return;
    this.bt = await agir("/api/bluetooth/appareil", { mac, action: a });
    if (a === "appairer") suivreBt(this);
    else toast({ connecter: "Connecté.", deconnecter: "Déconnecté.", oublier: "Appareil oublié." }[a], "ok");
  },
  async "g-bt-pin"({ mac }) {
    const pin = (this.donnees.btPin || "").trim();
    if (!/^\d{4,16}$/.test(pin)) { toast("Le code fait 4 à 16 chiffres.", "erreur"); return; }
    await agir("/api/bluetooth/pin", { mac: mac || this.bt.appairage?.mac, pin });
    this.donnees.btPin = "";
    suivreBt(this);
  },
  async "g-bt-confirmer"({ mac }) {
    this.bt = await agir("/api/bluetooth/confirmer", { mac: mac || this.bt.appairage?.mac, accepter: true });
    suivreBt(this);
  },
  async "g-bt-refuser"({ mac }) {
    this.bt = await agir("/api/bluetooth/confirmer", { mac: mac || this.bt.appairage?.mac, accepter: false });
    suivreBt(this);
  },
  "g-fuseau"() { ouvrirFuseaux(this); },
  async "g-sortie"({ id }) { this.general = { ...this.general, son: await agir("/api/general/son", { sortie: id }) }; },
  async "g-volume"({ valeur }) {
    this.general = { ...this.general, son: await agir("/api/general/son", { volume: Number(valeur) }) };
    this.donnees.gVol = undefined;
  },
  async "nuit-plage"({ plage }) {
    Object.assign(S.etat.affichage, await agir("/api/affichage", { nuit_plage: plage }));
  },
  async "nuit-decalage"({ valeur, cleDec }) {
    Object.assign(S.etat.affichage, await agir("/api/affichage", { [cleDec]: Number(valeur) }));
  },
};
export { session };
