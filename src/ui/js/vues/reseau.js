// Réseau : Wi-Fi du Pi, débit Internet, Pi-hole, appareils de la maison, nœuds, Wake-on-LAN, accès à distance.
import { esc, ic, debit, ilYA, trace } from "../outils.js";
import { S, NOM, btn, champ, pastille, jauge, ouvrir, enteteDialogue, confirmer, choisir, interrupteur, toast, rendre } from "../noyau.js";
import { entete } from "../cadre.js";
import { lire, agir } from "../api.js";

function ligneNoeud(n) {
  if (!n.connu) {
    return `<li class="ligne-noeud libre" data-cle="r-${n.nom}"><span class="voyant off"></span><div><b>${NOM(n.nom)}</b><small>emplacement libre</small></div></li>`;
  }
  const r = n.reseau || {};
  return `<li class="ligne-noeud${n.en_ligne ? "" : " hors-ligne"}" data-cle="r-${n.nom}">
    <span class="voyant ${n.en_ligne ? "ok" : "off"}"></span>
    <div class="ln-nom"><b>${NOM(n.nom)}</b><small>${esc(n.nom)}.local</small></div>
    <div class="ln-adresse"><b>${esc(n.ip || n.adresse || "–")}</b><small>${esc((n.mac || "").toUpperCase() || "MAC inconnue")}</small></div>
    <div class="ln-lien">${n.en_ligne
      ? `<b>${r.lien_mbps ? (r.lien_mbps >= 1000 ? `${r.lien_mbps / 1000} Gbit/s` : `${r.lien_mbps} Mbit/s`) : "Lien inconnu"}</b><small>${ic("arrow-down")}${debit(r.rx_bps)} ${ic("arrow-up")}${debit(r.tx_bps)}</small>`
      : `<b>Hors ligne</b><small>${n.cle_invalide ? "refuse les ordres" : "ne répond plus"}</small>`}</div>
  </li>`;
}

function carteCible(c) {
  const etat = c.en_ligne === true ? pastille("ok", "En ligne") : c.en_ligne === false ? pastille("off", "Éteint") : pastille("off", "État inconnu");
  return `<article class="cible" data-cle="w-${c.id}">
    <span class="disque-icone${c.en_ligne ? " ok" : ""}">${ic("monitor")}</span>
    <div class="disque-textes"><h3><b>${esc(c.nom)}</b></h3><p>${esc(c.mac)}${c.ip ? ` · ${esc(c.ip)}` : ""}</p>${etat}</div>
    <div class="actions">
      ${c.fixe ? "" : btn("supprimer", "", { icone: "trash-2", classe: "rond discret danger-texte", titre: `Retirer ${c.nom}`, d: { id: c.id } })}
      ${c.ip ? btn("eteindre-pc", "Éteindre", { icone: "power", classe: "discret danger-texte grand", d: { id: c.id } }) : ""}
      ${btn("reveiller", "Réveiller", { icone: "zap", classe: "plein grand", d: { id: c.id } })}
    </div>
    ${c.ip ? `<button type="button" class="lien-discret" data-action="config-extinction" data-id="${c.id}">${ic("settings")}<span>Configurer l'extinction à distance</span></button>` : ""}
  </article>`;
}

function ouvrirCible() {
  const d = ouvrir({
    classe: "large",
    donnees: { nom: "", mac: "", ip: "", erreur: "" },
    rendu: () => {
      const x = d.donnees;
      return `${enteteDialogue("Ajouter une machine", { icone: "zap", sous: "Elle doit être reliée par câble, avec le Wake-on-LAN activé dans son BIOS." })}
      <div class="dlg-corps formulaire">
        <label class="reglage"><span class="reglage-nom">Nom</span>${champ("nom", x.nom, { placeholder: "PC du salon", max: 32, label: "Nom", rendre: true })}</label>
        <label class="reglage${x.erreur ? " erreur" : ""}"><span class="reglage-nom">Adresse MAC</span>${champ("mac", x.mac, { placeholder: "20:0B:74:6A:0B:6C", max: 17, label: "Adresse MAC", rendre: true })}
          <small>${x.erreur ? esc(x.erreur) : "Six groupes de deux caractères. Elle se lit dans les réglages réseau de la machine ou dans ta box."}</small></label>
        <label class="reglage"><span class="reglage-nom">Adresse IP <em>facultatif</em></span>${champ("ip", x.ip, { placeholder: "192.168.1.40", max: 15, label: "Adresse IP" })}
          <small>Avec l'adresse IP, YDZ peut dire si la machine est allumée.</small></label>
      </div>
      <footer class="dlg-pied">${btn("fermer", "Annuler", { classe: "discret" })}${btn("ajouter", "Ajouter", { icone: "plus", classe: "plein", off: !x.nom.trim() || !x.mac.trim() })}</footer>`;
    },
    actions: {
      async ajouter() {
        const x = d.donnees;
        const diffusion = S.etat.noeuds[0]?.ip ? S.etat.noeuds[0].ip.replace(/\.\d+$/, ".255") : "192.168.1.255";
        const corps = { nom: x.nom.trim(), mac: x.mac.trim().toUpperCase().replace(/-/g, ":"), diffusion };
        if (x.ip.trim()) corps.ip = x.ip.trim();
        try { await agir("/api/wol", corps); } catch (e) {
          if (e.code === "mac") { x.erreur = e.message; return; }
          throw e;
        }
        d.fermer();
        toast(`${corps.nom} ajouté.`, "ok");
        vue.charger().then(rendre);
      },
    },
  });
}

// Commande PowerShell (administrateur) à coller une fois sur le PC Windows : serveur OpenSSH,
// clé de forge limitée à la seule commande d'extinction, pare-feu limité au réseau privé.
function commandeWindows(cle) {
  const ligne = `command="shutdown /s /f /t 5",no-port-forwarding,no-pty,no-agent-forwarding ${cle}`;
  return [
    "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null",
    "Set-Service sshd -StartupType Automatic; Start-Service sshd",
    "if (!(Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue)) { New-NetFirewallRule -Name OpenSSH-Server-In-TCP -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Profile Private | Out-Null }",
    `$l = '${ligne.replace(/'/g, "''")}'`,
    "$f = \"$env:ProgramData\\ssh\\administrators_authorized_keys\"; Add-Content -Path $f -Value $l; icacls $f /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null",
    "New-Item -ItemType Directory -Force \"$env:USERPROFILE\\.ssh\" | Out-Null; Add-Content -Path \"$env:USERPROFILE\\.ssh\\authorized_keys\" -Value $l",
    "Write-Host 'YDZ : extinction a distance prete.' -ForegroundColor Green",
  ].join("\n");
}

async function ouvrirExtinction(cible) {
  const r = await lire("/api/wol/cle");
  const cmd = commandeWindows(r.cle);
  const d = ouvrir({
    classe: "large",
    donnees: {},
    rendu: () => `${enteteDialogue("Extinction à distance", { icone: "power", sous: `${esc(cible?.nom || "PC")} · une seule fois, sur le PC` })}
      <div class="dlg-corps formulaire">
        <p class="texte">1. Sur le PC, clic droit sur le menu Démarrer, puis <b>Terminal (administrateur)</b> ou <b>Windows PowerShell (administrateur)</b>.</p>
        <p class="texte">2. Copie le bloc ci-dessous, colle-le dans la fenêtre (clic droit) et appuie sur Entrée. Attends le message vert.</p>
        <pre class="commande-pc">${esc(cmd)}</pre>
        <p class="note-fine">${ic("info")}La clé de ${esc(NOM(S.etat.hub))} ne peut lancer qu'une chose sur le PC : <code>shutdown</code>. Le compte utilisé est <b>${esc(r.utilisateur)}</b> (clé wol_utilisateur de /etc/ydz/ydz.conf). Pour rallumer, le bouton Réveiller utilise le Wake-on-LAN.</p>
      </div>
      <footer class="dlg-pied">${btn("copier", "Copier", { icone: "copy", classe: "discret" })}${btn("fermer", "Terminé", { classe: "plein" })}</footer>`,
    actions: {
      async copier() {
        let ok = false;
        try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(cmd); ok = true; } } catch { /* http simple */ }
        if (!ok) {
          // http simple : zone de texte temporaire hors écran + execCommand (marche aussi sur iPhone)
          const t = document.createElement("textarea");
          t.value = cmd; t.setAttribute("readonly", "");
          t.style.cssText = "position:fixed;top:0;left:-9999px;opacity:0";
          document.body.appendChild(t);
          t.select(); t.setSelectionRange(0, cmd.length);
          try { ok = document.execCommand("copy"); } catch { /* navigateur ancien */ }
          t.remove();
        }
        toast(ok ? "Commande copiée." : "Sélectionne le texte et copie-le à la main.", ok ? "ok" : undefined);
      },
    },
  });
}


// ============================================================================ réseau de la maison (1.0.10)
const nf = (v, d = 0) => (v == null ? "–" : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }));
export const mbit = (v) => nf(v, v != null && v < 10 ? 1 : 0);

// Icône d'un appareil d'après son nom (celui choisi par Justin ou celui donné par la box).
const ICONES = [
  [/livebox|freebox|bbox|\bbox\b|routeur|router|r[ée]p[ée]teur|wifi ?extender/, "router"],
  [/macbook|laptop|portable|thinkpad|notebook|chromebook/, "laptop"],
  [/ipad|tablette|tablet|galaxy tab/, "tablet"],
  [/iphone|galaxy|pixel|android|smartphone|t[ée]l[ée]phone|redmi|xiaomi|oneplus|huawei|oppo/, "smartphone"],
  [/\btv\b|t[ée]l[ée]|chromecast|fire ?stick|apple tv|roku|player/, "tv"],
  [/\bps[345]\b|playstation|xbox|switch|nintendo|steam ?deck/, "gamepad-2"],
  [/imprimante|printer|deskjet|laserjet|officejet|epson|canon|brother/, "printer"],
  [/nest|echo|alexa|sonos|homepod|enceinte|speaker/, "speaker"],
  [/watch|montre/, "watch"],
  [/hue|ampoule|lampe|tradfri|prise|shelly|tapo|meross/, "lightbulb"],
  [/cam[ée]ra|\bcam\b|ring|arlo/, "camera"],
  [/\bpc\b|desktop|ordinateur|imac|tour/, "monitor"],
  [/forge|vault|pulse|raspberry|^pi-/, "server"],
];
export function iconeAppareil(a) {
  if (a.moi) return "server";
  const n = (a.nom || "").toLowerCase();
  for (const [re, icone] of ICONES) if (re.test(n)) return icone;
  return n ? "wifi" : "circle-dashed";
}
export const nomAppareil = (a) => a.nom || "Appareil inconnu";

// Petit graphique des nuits passées : barres = réception, ligne = envoi (même échelle).
export function grapheDebits(h, l = 300, haut = 96) {
  if (!h || !h.length) return `<svg class="graphe-debits" viewBox="0 0 ${l} ${haut}" preserveAspectRatio="none" aria-hidden="true"></svg>`;
  const max = Math.max(1, ...h.map((x) => Math.max(x.down_mbps || 0, x.up_mbps || 0))) * 1.08;
  const pas = l / h.length, larg = Math.max(2, pas * 0.62);
  const barres = h.map((x, i) => {
    const v = ((x.down_mbps || 0) / max) * haut;
    return `<rect class="${i === h.length - 1 ? "dernier" : ""}" x="${(i * pas + (pas - larg) / 2).toFixed(1)}" y="${(haut - v).toFixed(1)}" width="${larg.toFixed(1)}" height="${v.toFixed(1)}" rx="${Math.min(3, larg / 2).toFixed(1)}"/>`;
  }).join("");
  const ups = h.map((x) => x.up_mbps || 0);
  const ligne = ups.length > 1 ? `<path class="envoi" d="${trace(ups, l - pas, haut, 0, max)}" transform="translate(${(pas / 2).toFixed(1)} 0)"/>` : "";
  return `<svg class="graphe-debits" viewBox="0 0 ${l} ${haut}" preserveAspectRatio="none" aria-hidden="true">${barres}${ligne}</svg>`;
}

export function quandDebit(t) {
  if (!t) return "";
  const d = new Date(t * 1000), auj = new Date();
  const hier = new Date(); hier.setDate(auj.getDate() - 1);
  const hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const nuit = d.getHours() < 6;
  if (d.toDateString() === auj.toDateString()) return nuit ? `cette nuit à ${hm}` : `aujourd'hui à ${hm}`;
  if (d.toDateString() === hier.toDateString()) return `hier à ${hm}`;
  return `${d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" })} à ${hm}`;
}

export const NIVEAU_WIFI = { excellent: ["Excellent", "ok", "wifi"], bon: ["Bon", "ok", "wifi"], moyen: ["Moyen", "warn", "wifi"], faible: ["Faible", "crit", "wifi"] };
export const bande = (f) => (!f ? "–" : f >= 5900 ? "6 GHz" : f >= 4900 ? "5 GHz" : "2,4 GHz");
export const CONSEIL_WIFI = "Signal faible : rapproche le Pi de la box, ou relie-le par un câble ou un boîtier CPL.";
export const CONSEIL_WIFI_COURT = "Rapproche le Pi de la box ou utilise un câble / CPL.";

function carteWifi(w) {
  let corps;
  if (!w) {
    corps = `<div class="tuile-corps colonne"><b class="chiffre petit">Mesure en attente</b><p>La qualité du Wi-Fi arrive dans une minute.</p></div>`;
  } else if (w.filaire) {
    corps = `<div class="wifi-tete"><span class="wifi-icone ok">${ic("cable")}</span><div><b class="chiffre moyen">Câble</b><p>Le Pi est relié à la box par un câble : rien à surveiller.</p></div></div>`;
  } else if (w.signal_dbm == null) {
    corps = `<div class="wifi-tete"><span class="wifi-icone crit">${ic("wifi-off")}</span><div><b class="chiffre moyen">Déconnecté</b><p>Le Wi-Fi du Pi n'est relié à aucun réseau.</p></div></div>`;
  } else {
    const [nom, ton, icone] = NIVEAU_WIFI[w.niveau] || NIVEAU_WIFI.moyen;
    corps = `<div class="wifi-tete"><span class="wifi-icone ${ton}">${ic(icone)}</span>
        <div><b class="chiffre moyen">${nom}</b><p>${w.signal_dbm} dBm · ${w.qualite_pct} %</p></div></div>
      ${jauge(w.qualite_pct, ton === "ok" ? "ok" : ton)}
      <dl class="fiche compacte">
        <div><dt>Réseau</dt><dd>${esc(w.ssid || "–")}</dd></div>
        <div><dt>Bande</dt><dd>${bande(w.frequence_mhz)}</dd></div>
        <div><dt>Vitesse du lien</dt><dd>${w.debit_mbps ? `${w.debit_mbps} Mbit/s` : "–"}</dd></div>
      </dl>
      ${w.niveau === "faible" ? `<p class="bandeau warn">${ic("triangle-alert")}${CONSEIL_WIFI}</p>` : ""}`;
  }
  return `<section class="carte bloc reseau-wifi" data-cle="r-wifi"><h2 class="bloc-titre">${ic("wifi")}Wi-Fi du Pi</h2>${corps}</section>`;
}

function carteDebit(d) {
  const der = d?.dernier;
  const enCours = d?.en_cours;
  return `<section class="carte bloc reseau-debit" data-cle="r-debit">
    <h2 class="bloc-titre">${ic("gauge")}Débit Internet</h2>
    ${der ? `<div class="debit-chiffres">
        <div><span>${ic("arrow-down")}Réception</span><b class="chiffre moyen">${mbit(der.down_mbps)}<small>Mbit/s</small></b></div>
        <div class="tx"><span>${ic("arrow-up")}Envoi</span><b class="chiffre moyen">${mbit(der.up_mbps)}<small>Mbit/s</small></b></div>
        <div><span>${ic("timer")}Latence</span><b class="chiffre moyen">${nf(der.ping_ms)}<small>ms</small></b></div>
      </div>
      <p class="debit-quand">${enCours ? `${ic("loader-circle", "tourne")}Test en cours…` : `Mesuré ${quandDebit(der.t)}`}</p>
      <div class="debit-graphe">${grapheDebits(d.historique)}
        <p class="legende"><span class="rx">Réception</span><span class="tx">Envoi</span><em>${d.historique.length} dernière${d.historique.length > 1 ? "s" : ""} mesure${d.historique.length > 1 ? "s" : ""}</em></p></div>`
    : `<div class="tuile-corps colonne"><b class="chiffre petit">${enCours ? "Test en cours…" : "Pas encore de mesure"}</b><p>${enCours ? "Moins d'une minute." : `Le premier test se fera cette nuit à ${esc(d?.heure_nuit || "03:30")}, ou maintenant avec le bouton.`}</p></div>`}
    ${d?.erreur ? `<p class="bandeau warn">${ic("triangle-alert")}${esc(d.erreur)}</p>` : ""}
    <p class="note-fine">${ic("info")}Mesuré depuis le Pi, qui est en <b>Wi-Fi</b> : c'est le débit qui arrive jusqu'au Pi, pas celui de la box. Test automatique chaque nuit à ${esc(d?.heure_nuit || "03:30")}.</p>
    ${btn("lancer-debit", enCours ? "Test en cours…" : "Lancer un test", { icone: "play", classe: "seul", off: enCours })}
  </section>`;
}

// --- Contrôle réseau toutes les heures (1.0.15) ----------------------------------------------------------
const FREQ = { 0: "Jamais", 30: "Toutes les 30 min", 60: "Toutes les heures", 180: "Toutes les 3 h" };

export function grapheControle(h, fenetre, l = 300, haut = 90) {
  const pts = fenetre === 24 ? h.slice(-24) : h;
  if (!pts.length) return `<svg class="graphe-controle" viewBox="0 0 ${l} ${haut}" preserveAspectRatio="none" aria-hidden="true"></svg>`;
  const vals = pts.map((x) => x.down_mbps);
  const max = Math.max(1, ...vals.filter((v) => v != null)) * 1.1;
  const pas = l / Math.max(1, pts.length - 1);
  // coupures : petits traits rouges en bas
  const coupes = pts.map((x, i) => x.internet === false ? `<rect class="coupe" x="${(i * pas - 1).toFixed(1)}" y="${haut - 6}" width="2.5" height="6"/>` : "").join("");
  // segments reliant les points connus
  let d = "", ouvert = false;
  pts.forEach((x, i) => {
    if (x.down_mbps == null) { ouvert = false; return; }
    const px = (i * pas).toFixed(1), py = (haut - 8 - (x.down_mbps / max) * (haut - 14)).toFixed(1);
    d += `${ouvert ? "L" : "M"}${px} ${py}`; ouvert = true;
  });
  return `<svg class="graphe-controle" viewBox="0 0 ${l} ${haut}" preserveAspectRatio="none" aria-hidden="true">
    <path class="courbe" d="${d}"/>${coupes}</svg>`;
}

function carteControle(c) {
  if (!c) return "";
  const der = c.dernier;
  const vide = !c.historique?.length;
  const fenetre = S.controleFenetre || 24;
  const pts = fenetre === 24 ? (c.historique || []).slice(-24) : (c.historique || []);
  const moy = (() => { const v = pts.map((x) => x.down_mbps).filter((x) => x != null); return v.length ? Math.round(v.reduce((a, b) => a + b) / v.length) : null; })();
  const coupures = pts.filter((x) => x.internet === false).length;
  return `<section class="carte bloc reseau-controle" data-cle="r-controle">
    <h2 class="bloc-titre">${ic("activity")}Contrôle toutes les heures${c.internet_coupe ? `<span class="pastille crit"><i></i>Internet coupé</span>` : ""}</h2>
    ${der ? `<div class="debit-chiffres">
        <div><span>${ic("arrow-down")}Débit</span><b class="chiffre moyen">${mbit(der.down_mbps)}<small>Mbit/s</small></b></div>
        <div><span>${ic("timer")}Latence</span><b class="chiffre moyen">${nf(der.ping_ms)}<small>ms</small></b></div>
        <div><span>${ic("wifi")}Gigue</span><b class="chiffre moyen">${nf(der.gigue_ms)}<small>ms</small></b></div>
        <div><span>${ic("circle-slash")}Perte</span><b class="chiffre moyen">${nf(der.perte_pct)}<small>%</small></b></div>
      </div>
      <div class="controle-entete">
        <div class="segments" role="tablist">
          <button type="button" class="seg${fenetre === 24 ? " actif" : ""}" data-action="controle-fenetre" data-f="24">24 h</button>
          <button type="button" class="seg${fenetre === 168 ? " actif" : ""}" data-action="controle-fenetre" data-f="168">7 j</button>
        </div>
        <span class="controle-resume">${moy != null ? `moyenne ${moy} Mbit/s` : ""}${coupures ? ` · ${coupures} coupure${coupures > 1 ? "s" : ""}` : ""}</span>
      </div>
      <div class="controle-graphe">${grapheControle(c.historique, fenetre)}</div>`
    : `<div class="tuile-corps colonne"><b class="chiffre petit">${c.actif ? "Premier contrôle en route" : "Contrôle désactivé"}</b><p>${c.actif ? "Le premier point arrive dans quelques minutes." : "Active-le ci-dessous pour suivre ton Internet d'heure en heure."}</p></div>`}
    <div class="controle-reglages">
      <button type="button" class="reglage-ligne" data-action="controle-freq">
        <span>${ic("timer-reset")}Fréquence</span><b>${FREQ[c.intervalle_min] || "Toutes les heures"}${ic("chevron-right")}</b></button>
      <button type="button" class="reglage-ligne" data-action="controle-seuil">
        <span>${ic("gauge")}Alerte si le débit passe sous</span><b>${c.seuil_mbps} Mbit/s${ic("chevron-right")}</b></button>
    </div>
    <p class="note-fine">${ic("info")}Test léger (~10 Mo) : environ <b>${esc(String(c.conso_jour_mo))} Mo par jour</b>. Le test complet, lui, reste la nuit. Une coupure ou un débit faible 3 fois d'affilée te prévient.</p>
    ${btn("lancer-controle", c.en_cours ? "Contrôle en cours…" : "Contrôler maintenant", { icone: "play", classe: "seul discret", off: c.en_cours })}
  </section>`;
}


function cartePihole(p) {
  const app = S.apps?.catalogue.find((a) => a.id === "pihole");
  const url = app?.installations?.[0]?.url;
  return `<section class="carte bloc reseau-pihole" data-cle="r-pihole">
    <h2 class="bloc-titre">${ic("shield-ban")}Pubs bloquées</h2>
    ${p ? `<div class="pihole-chiffre"><b class="chiffre">${nf(p.bloquees)}</b><span>pubs et traqueurs bloqués ces dernières 24 h</span></div>
      ${jauge(p.pourcentage, "accent")}
      <p class="texte">${nf(p.pourcentage, 1)} % des ${nf(p.requetes)} requêtes DNS de la maison · ${nf(p.domaines_liste)} domaines dans la liste de blocage.</p>
      ${url ? `<a class="btn seul" href="${esc(url)}" target="_blank" rel="noopener">${ic("external-link")}<span>Ouvrir Pi-hole</span></a>` : ""}`
    : `<div class="tuile-corps colonne"><b class="chiffre petit">Pi-hole ne répond pas</b><p>${app?.installations?.length ? "Il est installé mais YDZ n'arrive pas à lire ses chiffres. Ils reviendront tout seuls dans quelques minutes." : "Installe l'app Pi-hole pour bloquer les pubs sur tout le réseau et voir ici combien sont arrêtées."}</p></div>
      <a class="btn seul" href="#/apps/pihole">${ic("shield")}<span>Pi-hole dans les apps</span></a>`}
  </section>`;
}

function ligneAppareil(a, nouveaux) {
  const nouveau = nouveaux.has(a.mac);
  return `<li class="appareil${a.en_ligne ? "" : " absent"}" data-cle="ap-${a.mac}">
    <span class="appareil-icone">${ic(iconeAppareil(a))}</span>
    <div class="appareil-textes">
      <b>${esc(nomAppareil(a))}${a.moi ? `<em class="etiquette">ce Pi</em>` : ""}${nouveau ? `<em class="etiquette nouveau">nouveau</em>` : ""}</b>
      <small>${esc(a.ip || "–")}<span class="mac">${esc(a.mac.toUpperCase())}</span></small>
    </div>
    <span class="appareil-etat">${a.en_ligne ? `<i class="voyant ok"></i>en ligne` : `vu ${ilYA(a.derniere_vue)}`}</span>
    ${btn("renommer", "", { icone: "pencil", classe: "rond discret", titre: `Renommer ${nomAppareil(a)}`, d: { mac: a.mac } })}
  </li>`;
}

function carteAppareils(liste, r) {
  const nouveaux = new Set((r?.nouveaux || []).filter((x) => Date.now() / 1000 - x.t < 86400).map((x) => x.mac));
  const enLigne = liste ? liste.filter((a) => a.en_ligne).length : 0;
  return `<section class="carte bloc reseau-appareils" data-cle="r-appareils">
    <h2 class="bloc-titre">${ic("house")}Appareils de la maison${liste ? `<span class="bloc-compte">${enLigne} en ligne sur ${liste.length}</span>` : ""}</h2>
    ${!liste ? `<div class="attente">${ic("loader-circle", "tourne")}</div>`
      : liste.length ? `<ul class="appareils-liste">${liste.map((a) => ligneAppareil(a, nouveaux)).join("")}</ul>`
      : `<p class="texte">Le premier tour du réseau se fait quelques minutes après le démarrage du Pi.</p>`}
    <p class="note-fine">${ic("info")}Le Pi fait le tour du réseau toutes les 5 minutes. Un appareil jamais vu déclenche une alerte. Les noms viennent de la box : touche le crayon pour en choisir un plus parlant.</p>
  </section>`;
}

function ouvrirAppareil(a) {
  const d = ouvrir({
    donnees: { nom: a.nom_perso || "" },
    rendu: () => {
      const x = d.donnees;
      const date = (t) => (t ? new Date(t * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }) : "–");
      return `${enteteDialogue("Renommer l'appareil", { icone: iconeAppareil(a), sous: `${esc(a.ip || "")} · ${esc(a.mac.toUpperCase())}` })}
      <div class="dlg-corps formulaire">
        <label class="reglage"><span class="reglage-nom">Nom</span>${champ("nom", x.nom, { placeholder: a.nom_auto || "iPhone de Justin", max: 40, label: "Nom de l'appareil", entree: "enregistrer" })}
          <small>${a.nom_auto ? `Nom donné par la box : <b>${esc(a.nom_auto)}</b>. Laisse vide pour le garder.` : "La box ne donne pas de nom à cet appareil. 40 caractères au plus."}</small></label>
        <dl class="fiche compacte">
          <div><dt>État</dt><dd>${a.en_ligne ? "En ligne" : `Vu ${ilYA(a.derniere_vue)}`}</dd></div>
          <div><dt>Vu pour la première fois</dt><dd>${date(a.premiere_vue)}</dd></div>
        </dl>
      </div>
      <footer class="dlg-pied">
        ${a.moi ? "" : btn("oublier", "Oublier", { icone: "trash-2", classe: "discret danger-texte" })}
        ${btn("fermer", "Annuler", { classe: "discret" })}${btn("enregistrer", "Enregistrer", { icone: "check", classe: "plein" })}
      </footer>`;
    },
    actions: {
      async enregistrer() {
        await agir(`/api/appareils/${a.mac}`, { nom: d.donnees.nom.trim() });
        d.fermer();
        toast("Nom enregistré.", "ok");
        await vue.chargerAppareils();
      },
      async oublier() {
        const oui = await confirmer({ titre: `Oublier ${nomAppareil(a)} ?`, texte: "Il disparaît de la liste. S'il revient sur le réseau, il sera signalé comme un nouvel appareil.", libelle: "Oublier", danger: true, icone: "trash-2" });
        if (!oui) return;
        await agir(`/api/appareils/${a.mac}`, { oublier: true });
        d.fermer();
        toast("Appareil oublié.");
        await vue.chargerAppareils();
      },
    },
  });
}

const vue = {
  cibles: null,
  appareils: null,
  async charger() {
    const [r] = await Promise.all([lire("/api/wol").catch(() => null), this.chargerAppareils()]);
    if (r) this.cibles = r.cibles;
  },
  async chargerAppareils() {
    const r = await lire("/api/appareils").catch(() => null);
    if (r) { this.appareils = r.appareils; rendre(); }
  },
  rendu() {
    const e = S.etat;
    const enLigne = e.noeuds.filter((n) => n.en_ligne).length;
    const wg = S.apps?.catalogue.find((a) => a.id === "wg-easy");
    const wgIns = wg?.installations[0];
    const rm = e.reseau_maison || {};
    return `${entete("Réseau", { sous: `${enLigne} nœud${enLigne > 1 ? "s" : ""} en ligne · interface sur http://${esc(e.hub)}.local` })}
    <div class="reseau-maison">${carteWifi(rm.wifi)}${carteDebit(rm.debit)}${cartePihole(e.pihole)}</div>
    ${carteControle(rm.controle)}
    <div class="deux-colonnes">
      ${carteAppareils(this.appareils, rm)}
      <div class="colonne">
        <section class="carte bloc">
          <h2 class="bloc-titre">${ic("network")}Nœuds</h2>
          <ul class="lignes-noeuds">${e.noeuds.map(ligneNoeud).join("")}</ul>
          <p class="note-fine">${ic("info")}Les nœuds se trouvent par leur nom en <b>.local</b>. Si un nom ne répond pas, utilise l'adresse IP affichée sur le badge.</p>
        </section>
        <section class="carte bloc">
          <h2 class="bloc-titre">${ic("zap")}Réveil à distance</h2>
          ${this.cibles ? this.cibles.map(carteCible).join("") : `<div class="attente">${ic("loader-circle", "tourne")}</div>`}
          <button type="button" class="btn discret seul" data-action="ajouter">${ic("plus")}<span>Ajouter une machine</span></button>
        </section>
        <section class="carte bloc">
          <h2 class="bloc-titre">${ic("shield-check")}Accès depuis l'extérieur</h2>
          <p class="texte">Ta tour est joignable de partout grâce à <b>Tailscale</b> : ton téléphone rejoint ton réseau comme s'il était à la maison, sans rien ouvrir sur ta box.</p>
          <ol class="etapes">
            <li>Active l'app <b>Tailscale</b> sur ton téléphone ou ton PC (même compte que le Pi).</li>
            <li>Ouvre <b>http://${esc(S.etat?.hub || "pi-one")}/</b> : le site, le terminal et le réveil du PC marchent comme à la maison.</li>
            <li>Ne redirige aucun port de ta box vers la tour : Tailscale suffit.</li>
          </ol>
          ${S.etat.acces?.https_actif && S.etat.acces?.nom_dns ? `<p class="texte">Adresse HTTPS : <b class="mono">https://${esc(S.etat.acces.nom_dns)}/</b></p>` : ""}
          <div class="actions"><a class="btn" href="#/reglages/acces">${ic("globe-lock")}<span>Adresses HTTPS</span></a>
          <a class="btn" href="https://login.tailscale.com/admin/machines" target="_blank" rel="noopener">${ic("external-link")}<span>Console Tailscale</span></a></div>
        </section>
      </div>
    </div>`;
  },
  actions: {
    ajouter: ouvrirCible,
    renommer({ mac }) {
      const a = this.appareils?.find((x) => x.mac === mac);
      if (a) ouvrirAppareil(a);
    },
    async "lancer-debit"() {
      await agir("/api/reseau/debit");
      toast("Test de débit lancé : le résultat arrive dans moins d'une minute.");
    },
    "controle-fenetre"({ f }) { S.controleFenetre = Number(f); rendre(); },
    async "lancer-controle"() {
      await agir("/api/reseau/controle", { action: "lancer" });
      toast("Contrôle réseau lancé.");
      setTimeout(() => this.charger?.().then?.(rendre), 3000);
    },
    async "controle-freq"() {
      const v = await choisir({ titre: "Fréquence du contrôle", valeur: S.etat.reseau_maison?.controle?.intervalle_min,
        options: [{ nom: "Toutes les 30 minutes", valeur: 30 }, { nom: "Toutes les heures", valeur: 60 },
                  { nom: "Toutes les 3 heures", valeur: 180 }, { nom: "Jamais", valeur: 0, aide: "désactive le contrôle horaire" }] });
      if (v !== undefined) { await agir("/api/reseau/controle", { intervalle_min: v }); toast("Fréquence enregistrée.", "ok"); }
    },
    async "controle-seuil"() {
      const v = await choisir({ titre: "Alerte débit faible sous…", valeur: S.etat.reseau_maison?.controle?.seuil_mbps,
        options: [10, 25, 50, 100, 200, 500].map((x) => ({ nom: `${x} Mbit/s`, valeur: x })) });
      if (v !== undefined) { await agir("/api/reseau/controle", { seuil_mbps: v }); toast("Seuil enregistré.", "ok"); }
    },
    async "eteindre-pc"({ id }) {
      const c = this.cibles?.find((x) => x.id === id);
      if (!(await confirmer({ titre: `Éteindre ${c?.nom || "le PC"} ?`, texte: "Le PC s'éteint dans 5 secondes, sans demander. Enregistre ton travail avant.", libelle: "Éteindre", danger: true, icone: "power" }))) return;
      await agir(`/api/wol/${id}/eteindre`);
      toast("Ordre d'extinction envoyé.", "ok");
      setTimeout(() => this.charger().then(rendre), 15000);
    },
    async "config-extinction"({ id }) {
      await ouvrirExtinction(this.cibles?.find((x) => x.id === id));
    },
    async reveiller({ id }) {
      const r = await agir(`/api/wol/${id}/reveiller`);
      toast(r.message || "Signal de réveil envoyé.", "ok");
    },
    async supprimer({ id }) {
      const c = this.cibles.find((x) => x.id === id);
      if (!(await confirmer({ titre: `Retirer ${c?.nom || "cette machine"} ?`, texte: "Elle ne sera plus proposée au réveil. Tu pourras la rajouter.", libelle: "Retirer", danger: true }))) return;
      await agir(`/api/wol/${id}/supprimer`);
      await this.charger();
    },
  },
};
export default vue;
