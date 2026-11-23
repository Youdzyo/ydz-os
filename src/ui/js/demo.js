// YDZ Shell : mode démonstration. Un faux hub, dans le navigateur, qui répond comme l'API de TECHNIQUE.md §3
// (mêmes routes, mêmes formes de JSON, mêmes codes d'erreur). Rien ne sort de la page, rien n'est gardé.
import DONNEES from "./demo-donnees.js";
import CATALOGUE_COMPLET from "./demo-catalogue.js";

const clone = (o) => JSON.parse(JSON.stringify(o));
const D = clone(DONNEES);
const maintenant = () => Math.floor(Date.now() / 1000);
const alea = (a, b) => a + Math.random() * (b - a);
const id8 = () => Math.random().toString(16).slice(2, 10).padEnd(8, "0");
const ok = (json = {}) => ({ statut: 200, json: { ok: true, ...json } });
const lu = (json) => ({ statut: 200, json });
const err = (statut, code, erreur, plus = {}) => ({ statut, json: { erreur, code, ...plus } });

const abonnes = new Set();
const diffuser = (type, donnee) => abonnes.forEach((a) => a[type] && a[type](clone(donnee)));
const notifier = (texte, niveau = "info") => diffuser("surNotification", { niveau, texte });

// --------------------------------------------------------------------------------- état de départ
const N = Object.fromEntries(D.etat.noeuds.map((n) => [n.nom, n]));
const ORDRE = D.etat.noeuds.map((n) => n.nom);
// 1.0.17 : les 135 apps du catalogue (généré) remplacent les 9 historiques des données de démo.
const CATALOGUE = clone(CATALOGUE_COMPLET);
D.apps.catalogue = CATALOGUE;
const app = (id) => CATALOGUE.find((a) => a.id === id);
const base = { forge: { temp: 52, cpu: 34, ram: 38 }, vault: { temp: 44, cpu: 9, ram: 21 }, pulse: { temp: 41, cpu: 6, ram: 33 } };
const debitBase = { forge: [9.5e6, 2.1e6], vault: [42e6, 6.4e6], pulse: [1.3e6, 0.6e6] };
const demarrage = maintenant();
const allumage = { forge: demarrage - 4 * 86400 - 7000, vault: demarrage - 4 * 86400 - 6900, pulse: demarrage - 86400 - 3000 };
const horsLigne = {}; // nom -> true

// Installations : la référence. Le catalogue et l'état des nœuds en sont déduits.
const inst = { forge: {}, vault: {}, pulse: {} };
function poser(noeud, id, etat, reglages = {}, jours = 3) {
  const a = app(id);
  const defauts = Object.fromEntries(a.reglages.filter((r) => r.defaut !== undefined).map((r) => [r.cle, String(r.defaut)]));
  inst[noeud][id] = {
    etat, reglages: { ...defauts, ...reglages }, installe_le: maintenant() - jours * 86400,
    donnees: noeud === "vault" ? `/srv/ydz/donnees/apps/${id}` : `/var/lib/ydz/donnees/${id}`,
  };
}
poser("forge", "minecraft", "actif", { TYPE: "NEOFORGE", VERSION: "1.21.1", MEMOIRE: "8G", MOTD: "Serveur YDZ" }, 12);
poser("forge", "portainer", "actif", {}, 12);
poser("pulse", "pihole", "actif", { MOT_DE_PASSE: "demo-pihole" }, 9);
poser("pulse", "wg-easy", "actif", {}, 9);
poser("vault", "jellyfin", "actif", { MEDIAS: "/srv/ydz/donnees/partages" }, 6);
// 1.0.17 : deux apps du nouveau catalogue, pour montrer le parcours « installée » parmi les 135.
poser("vault", "freshrss", "actif", {}, 4);
poser("forge", "dockge", "arrete", {}, 2);

// Un troisième disque sur vault, déjà préparé mais pas monté : pour montrer « Remonter ».
const SV = D.stockage.vault;
SV.disques.find((d) => d.id === "sda").libre = 431e9;
SV.disques.push({
  id: "sdc", chemin: "/dev/sdc", modele: "WD Red Plus 4TB", serie: "WD-WX42D81K7TLE", taille: 4000787030016,
  type: "hdd", transport: "sata", amovible: false, systeme: false,
  partitions: [{ chemin: "/dev/sdc1", type: "part", taille: 4000785981440, fs: "ext4", etiquette: "ydz-archives", uuid: "5d1c0de0-aaaa-bbbb-cccc-000000000003", montage: [] }],
  montage: null, nom: null, vide: false, miroir: false, a_remonter: "archives", formatable: true, raison: null,
  smart: { sain: true, temp: 34, heures: 18250, secteurs_realloues: 0, usure_pct: null, avis: null }, total: null, libre: null,
});
SV.partages.push({ nom: "photos", disque: "donnees", chemin: "/srv/ydz/donnees/partages/photos", acces: "prive", adresse: "\\\\vault\\photos", adresse_mac: "smb://vault.local/photos" });
SV.samba.mot_de_passe_defini = true;
D.wol.cibles[0].en_ligne = false;
D.maj.noeuds.forEach((m) => { m.derniere_verif = maintenant() - 86400 * 2; m.paquets = 0; m.derniere_maj = maintenant() - 86400 * 9; });
D.systeme.simulation = true;

const ventilo = D.etat.ventilo;
const ecran = D.etat.ecran;
let testVentiloFin = 0;
let arretTour = false;
let taches = [];
const jetons = {}; // jeton -> préparation de formatage

// ------------------------------------------------------------------------------------- mesures
function derive(v, cible, pas, min, max) {
  const n = v + (cible - v) * 0.15 + alea(-pas, pas);
  return Math.min(max, Math.max(min, n));
}
function tic() {
  for (const nom of ORDRE) {
    const n = N[nom];
    if (horsLigne[nom]) continue;
    const b = base[nom];
    const charge = Object.values(inst[nom]).filter((i) => i.etat === "actif").length;
    n.cpu = Math.round(derive(n.cpu, b.cpu + charge * 2, 4, 1, 97) * 10) / 10;
    n.temp = Math.round(derive(n.temp, b.temp + (n.cpu - b.cpu) * 0.12, 0.5, 30, 84) * 10) / 10;
    const ramPct = derive(n.ram.pct, b.ram + charge * 3, 0.6, 5, 95);
    n.ram.pct = Math.round(ramPct * 10) / 10;
    n.ram.utilise = Math.round((n.ram.total * ramPct) / 100);
    n.charge = [n.cpu / 25, n.cpu / 27, n.cpu / 30].map((x) => Math.round(x * 100) / 100);
    n.reseau.rx_bps = Math.round(derive(n.reseau.rx_bps, debitBase[nom][0], debitBase[nom][0] * 0.35, 2e4, 9e8));
    n.reseau.tx_bps = Math.round(derive(n.reseau.tx_bps, debitBase[nom][1], debitBase[nom][1] * 0.35, 1e4, 9e8));
    n.uptime = maintenant() - allumage[nom];
    n.heure = maintenant();
  }
  const enLigne = ORDRE.filter((x) => !horsLigne[x]);
  const chaud = enLigne.reduce((a, x) => (N[x].temp > (a ? N[a].temp : -1) ? x : a), null);
  ventilo.temp = chaud ? N[chaud].temp : null;
  ventilo.noeud = chaud;
  ventilo.test = maintenant() < testVentiloFin;
  const consigne = chaud ? interpoler(ventilo.courbe, ventilo.temp) : 100;
  ventilo.consigne = ventilo.test ? 100 : Math.round(consigne);
  ventilo.vitesse = ventilo.consigne;
  for (const m of SV.miroirs) {
    if (m.etat === "reconstruction") {
      m.progression = Math.min(100, Math.round((m.progression + alea(2, 5)) * 10) / 10);
      if (m.progression >= 100) { m.etat = "sain"; m.progression = null; }
    }
  }
}
function interpoler(courbe, t) {
  if (t <= courbe[0][0]) return courbe[0][1];
  for (let i = 1; i < courbe.length; i++) {
    const [t0, v0] = courbe[i - 1], [t1, v1] = courbe[i];
    if (t <= t1) return v0 + ((v1 - v0) * (t - t0)) / (t1 - t0 || 1);
  }
  return courbe[courbe.length - 1][1];
}

function conteneurs(noeud, id) {
  const i = inst[noeud][id];
  const marche = i.etat === "actif";
  return [{
    nom: `ydz-${id}-${id}-1`, image: app(id).image, etat: marche ? "running" : "exited",
    statut: marche ? "Up 3 days" : "Exited (0) 2 minutes ago", app: id,
  }];
}
function adresse(noeud, a) {
  return a.web ? `${a.web.schema}://${N[noeud].ip}:${a.web.port}${a.web.chemin}` : null;
}
function masquer(a, reglages) {
  const secrets = new Set(a.reglages.filter((r) => r.type === "secret").map((r) => r.cle));
  return Object.fromEntries(Object.entries(reglages).map(([k, v]) => [k, secrets.has(k) && v ? "••••••" : v]));
}
function installations(a, clair) {
  return ORDRE.filter((n) => inst[n][a.id]).map((n) => {
    const i = inst[n][a.id];
    return {
      noeud: n, etat: i.etat, url: adresse(n, a), url_https: adresseHttps(a), maj: majInfo(n, a.id), conteneurs: conteneurs(n, a.id),
      reglages: clair ? { ...i.reglages } : masquer(a, i.reglages), donnees: i.donnees, installe_le: i.installe_le,
    };
  });
}
function noeudsApps() {
  return ORDRE.map((n) => ({
    nom: n, role: N[n].role, en_ligne: !horsLigne[n], docker: true, ram_total: N[n].ram.total,
    ram_libre: N[n].ram.total - N[n].ram.utilise,
    emplacements: n === "vault" ? SV.disques.filter((d) => d.montage).map((d) => d.montage) : [],
  }));
}

function etat() {
  tic();
  const alertes = [];
  const noeuds = ORDRE.map((nom) => {
    const n = N[nom];
    n.en_ligne = !horsLigne[nom];
    n.connu = true;
    n.vu_il_y_a = n.en_ligne ? 0 : 20;
    n.etat = !n.en_ligne ? n.etat : n.temp >= 80 ? "crit" : n.temp >= 70 ? "warn" : "ok";
    n.apps = Object.keys(inst[nom]).map((id) => ({
      id, etat: inst[nom][id].etat, conteneurs: conteneurs(nom, id), reglages: {}, donnees: inst[nom][id].donnees,
      installe_le: inst[nom][id].installe_le, au_catalogue: true, maj: majInfo(nom, id),
    }));
    n.docker = { dispo: true, conteneurs: n.apps.flatMap((x) => x.conteneurs) };
    n.redemarrage_requis = Boolean(D.maj.noeuds.find((m) => m.noeud === nom)?.redemarrage_requis);
    if (nom === "vault") {
      n.disques = [n.disques[0], ...SV.disques.filter((d) => d.montage && d.total).map((d) => ({
        point: d.montage, nom: d.nom, peripherique: d.partitions[0]?.chemin || d.chemin, fs: "ext4",
        total: d.total, utilise: d.total - d.libre, pct: Math.round(((d.total - d.libre) / d.total) * 100),
      }))];
    }
    n.nom_affiche = LABO.noeuds[nom]?.nom || nom.toUpperCase();
    n.numero = LABO.noeuds[nom]?.numero || null;
    if (!n.en_ligne && !arretTour) alertes.push({ noeud: nom, niveau: "warn", texte: `${n.nom_affiche} ne répond plus` });
    return n;
  }).sort((a, b) => (a.numero || 99) - (b.numero || 99));   // 1.0.13 : Pi One, Pi Two, Pi Three
  taches = taches.slice(0, 12);
  return {
    heure: maintenant(), hub: "forge", version: D.etat.version,
    tour: DEMO_Q.get("alerte") === "crit" ? { etat: "crit", alertes: [{ noeud: "forge", niveau: "crit", texte: "Pi One surchauffe : 86 °C" }, { noeud: "forge", niveau: "warn", texte: "Disque système plein à 94 %" }], arret_en_cours: false }
      : DEMO_Q.get("alerte") === "warn" ? { etat: "warn", alertes: [{ noeud: "forge", niveau: "warn", texte: "Disque système plein à 91 %" }], arret_en_cours: false }
      : { etat: alertes.length ? "warn" : "ok", alertes, arret_en_cours: arretTour },
    noeuds, ventilo, ecran, taches,
    auth: { pin_defini: true, pin_court: DEMO_Q.get("pincourt") === "1", pin_min: 6,
            verrou: { inactivite_min: 15, fond_min: 5 } },
    reseau_maison: reseauDemo(),
    pihole: piholeDemo(),
    affichage: affichageDemo(noeuds),
    sauvegarde: sauvegardeDemo(),
    minecraft: minecraftDemo(),
    maj_apps: ORDRE.flatMap((n) => Object.keys(inst[n]).filter((id) => majInfo(n, id).maj_disponible).map((id) => ({ id, noeud: n, nom: app(id).nom }))),
    acces: accesDemo(),
    visiteur: visiteurDemo(false),
    agenda: agendaVue(),
    rappels: { rappels: RAPPELS },
    wifi_invite: wifiInviteSnapshot(),
    agents: agentsSnapshot(),
    iphone: iphoneDemo(),
    labo: laboDemo(),
    appairage: DEMANDES,
    maj_ydz: majYdzDemo(),
  };
}



// écran du Pi : nuit, météo, PC, services, conso (?nuit=horloge|eteint et ?alerte=crit|warn pour essayer)
const DEMO_Q = new URLSearchParams(location.search);
const DEMO_DEBUT = maintenant();
const DEMO_REG = { nuit_plage: "soleil", nuit_decalage_coucher: 60, nuit_decalage_lever: 0, curseur: false, nuit_actif: true, nuit_debut: "00:00", nuit_fin: "07:00", nuit_mode: DEMO_Q.get("nuit") || "eteint", rotation_s: Number(DEMO_Q.get("rotation") ?? 30), meteo_ville: "Toulouse", prix_kwh: 0.194 };
// Lever et coucher du soleil (Toulouse, valeurs du jour approchées pour la démonstration)
function nuitDemo(R, nuit) {
  const jourAn = Math.floor((Date.now() - new Date(new Date().getFullYear(), 0, 0)) / 864e5);
  const c = Math.cos(((jourAn + 10) / 365) * 2 * Math.PI);   // 1 au solstice d'hiver, -1 en été
  const ete = new Date().getTimezoneOffset() < new Date(new Date().getFullYear(), 0, 1).getTimezoneOffset() ? 60 : 0;
  const lever = Math.round(7 * 60 + 52 + 32 * c + ete), coucher = Math.round(18 * 60 + 30 - 70 * c + ete);
  const hm = (m) => `${String(Math.floor(((m % 1440) + 1440) % 1440 / 60)).padStart(2, "0")}:${String(((m % 60) + 60) % 60).padStart(2, "0")}`;
  const soleil = R.nuit_plage === "soleil";
  return { actif: R.nuit_actif, debut: R.nuit_debut, fin: R.nuit_fin, mode: R.nuit_mode, en_cours: Boolean(nuit) && R.nuit_actif,
    plage: R.nuit_plage, decalage_coucher: R.nuit_decalage_coucher, decalage_lever: R.nuit_decalage_lever, lever: hm(lever), coucher: hm(coucher),
    debut_effectif: soleil ? hm(coucher + R.nuit_decalage_coucher) : R.nuit_debut, fin_effective: soleil ? hm(lever + R.nuit_decalage_lever) : R.nuit_fin };
}
function affichageDemo(noeuds) {
  const nuit = DEMO_Q.get("nuit");
  const R = DEMO_REG;
  const watts = noeuds.filter((n) => n.en_ligne).reduce((s, n) => s + 3 + 5 * (n.cpu || 0) / 100, 0);
  const heures = Array.from({ length: 8 }, (_, i) => {
    const h = (new Date().getHours() + i) % 24;
    return { h: `${String(h).padStart(2, "0")}h`, temp: Math.round(12 + 4 * Math.sin((h - 9) / 24 * 6.28)), icone: h > 18 || h < 7 ? "moon" : i > 4 ? "cloud-rain" : "cloud-sun", pluie: i > 4 ? 60 : 0 };
  });
  const j = (k) => new Date(Date.now() + k * 864e5).toISOString().slice(0, 10);
  return {
    nuit: nuitDemo(R, nuit),
    rotation_s: R.rotation_s, meteo_ville: R.meteo_ville, curseur: R.curseur,
    meteo: { ville: R.meteo_ville, temp: 14, ressenti: 12, texte: "Partiellement nuageux", icone: "cloud-sun", vent: 18, humidite: 72,
      heures, jours: [{ date: j(0), min: 8, max: 16, icone: "cloud-sun" }, { date: j(1), min: 9, max: 14, icone: "cloud-rain" }, { date: j(2), min: 6, max: 13, icone: "sun" }], maj: maintenant() },
    pc: { nom: "PC de Justin", en_ligne: true, depuis: DEMO_DEBUT - 2 * 3600 - 840 },
    services: [
      { id: "homeassistant", nom: "Home Assistant", icone: "house-plug", noeud: "forge", etat: "ok" },
      { id: "minecraft", nom: "Minecraft", icone: "pickaxe", noeud: "forge", etat: "ok" },
      { id: "pihole", nom: "Pi-hole", icone: "shield", noeud: "forge", etat: "ok" },
      { id: "portainer", nom: "Portainer", icone: "container", noeud: "forge", etat: "ok" },
      { id: "uptime-kuma", nom: "Uptime Kuma", icone: "activity", noeud: "forge", etat: "warn" },
      { id: "wg-easy", nom: "WireGuard (wg-easy)", icone: "shield-check", noeud: "forge", etat: "off" },
    ],
    conso: { watts: Math.round(watts * 10) / 10, mesure: false, kwh_mois: Math.round(watts * 7.2 * 10) / 10, euros_mois: Math.round(watts * 7.2 * R.prix_kwh * 100) / 100, prix_kwh: R.prix_kwh },
  };
}

// réseau de la maison (1.0.10) : appareils, Wi-Fi du Pi, débit des nuits passées, Pi-hole
// (?wifi=faible|filaire et ?pihole=0 pour essayer les autres cas)
const H = 3600, J = 86400;
const APPAREILS = [
  ["dc:a6:32:5e:10:41", "192.168.1.41", "forge", "", 60 * J, 0, true, true],
  ["a4:3e:51:8c:02:01", "192.168.1.1", "livebox", "Livebox", 60 * J, 0, true],
  ["d8:3a:dd:12:7a:42", "192.168.1.42", "vault", "", 41 * J, 0, true],
  ["d8:3a:dd:12:7a:43", "192.168.1.43", "pulse", "", 41 * J, 0, true],
  ["f2:6b:91:3c:aa:35", "192.168.1.35", "iPhone de Justin", "", 60 * J, 0, true],
  ["2c:f0:5d:a1:b2:40", "192.168.1.40", "DESKTOP 7K2QH1", "PC de Justin", 60 * J, 0, true],
  ["8c:79:f5:20:11:52", "192.168.1.52", "Samsung", "TV Samsung", 60 * J, 0, true],
  ["78:c8:81:4e:90:60", "192.168.1.60", "PS5 712", "PS5", 52 * J, 0, true],
  ["a8:51:ab:77:31:36", "192.168.1.36", "iPad", "iPad du salon", 60 * J, 0, true],
  ["e4:f0:42:9b:00:21", "192.168.1.21", "Google Nest Mini", "", 33 * J, 0, true],
  ["ec:b5:fa:0d:44:22", "192.168.1.22", "Philips hue", "Pont Philips Hue", 60 * J, 0, true],
  ["02:7d:c4:19:e8:77", "192.168.1.77", "", "", 40 * 60, 0, true],
  ["3c:2a:f4:6e:c1:70", "192.168.1.70", "HP DeskJet 2700", "", 58 * J, 3 * H + 1200, false],
  ["5a:12:fe:33:70:38", "192.168.1.38", "Galaxy A54", "", 19 * J, 5 * H, false],
  ["f0:18:98:aa:12:45", "192.168.1.45", "MacBook Air", "", 25 * J, 2 * J + 4 * H, false],
].map(([mac, ip, nom_auto, nom_perso, depuis, vu, en_ligne, moi]) => ({
  mac, ip, nom_auto, nom_perso, premiere_vue: DEMO_DEBUT - depuis, derniere_vue: DEMO_DEBUT - vu, en_ligne, moi: Boolean(moi),
}));
const nomAppareil = (a) => a.nom_perso || a.nom_auto || "";
function listeAppareils() {
  return APPAREILS.map((a) => ({ ...a, nom: nomAppareil(a), derniere_vue: a.en_ligne ? maintenant() - 40 : a.derniere_vue }))
    .sort((a, b) => (b.moi - a.moi) || (b.en_ligne - a.en_ligne) || (b.derniere_vue - a.derniere_vue) || a.nom.localeCompare(b.nom));
}
const NOUVEAUX = [{ t: DEMO_DEBUT - 40 * 60, mac: "02:7d:c4:19:e8:77", ip: "192.168.1.77", nom: "" }];
// 30 nuits de mesures, à 03:30 (valeurs stables d'une visite à l'autre)
const DEBITS = Array.from({ length: 30 }, (_, i) => {
  const d = new Date(); d.setHours(3, 30, 0, 0);
  if (d.getTime() > Date.now()) d.setDate(d.getDate() - 1);
  d.setDate(d.getDate() - (29 - i));
  const s = Math.sin(i * 1.7) * 0.5 + Math.sin(i * 0.53) * 0.5;
  return { t: Math.floor(d.getTime() / 1000), down_mbps: Math.round((236 + 48 * s - (i === 11 ? 120 : 0)) * 10) / 10, up_mbps: Math.round((61 + 14 * Math.cos(i * 1.3)) * 10) / 10, ping_ms: Math.round((13 + 4 * Math.abs(Math.sin(i * 2.1))) * 10) / 10 };
});
const DEBIT = { en_cours: false, erreur: null };
function wifiDemo() {
  const w = DEMO_Q.get("wifi");
  if (w === "filaire") return { filaire: true };
  const dbm = w === "faible" ? -84 : -71;
  const pct = Math.round(Math.max(0, Math.min(100, (dbm + 90) * 2.5)));
  return { ssid: WIFI.connecte, signal_dbm: dbm, qualite_pct: pct, debit_mbps: w === "faible" ? 58 : 433, frequence_mhz: w === "faible" ? 2437 : 5180, niveau: pct >= 75 ? "excellent" : pct >= 50 ? "bon" : pct >= 25 ? "moyen" : "faible" };
}
function reseauDemo() {
  const enLigne = APPAREILS.filter((a) => a.en_ligne).length;
  const recents = APPAREILS.filter((a) => !a.moi).sort((a, b) => b.premiere_vue - a.premiere_vue).slice(0, 6)
    .map((a) => ({ nom: nomAppareil(a), ip: a.ip, en_ligne: a.en_ligne, premiere_vue: a.premiere_vue }));
  return {
    appareils: enLigne, tailscale: 2, maj: DEMO_DEBUT, connus: APPAREILS.length, nouveaux: NOUVEAUX, recents,
    debit: { en_cours: DEBIT.en_cours, dernier: DEBITS[DEBITS.length - 1], historique: DEBITS, erreur: DEBIT.erreur, heure_nuit: "03:30" },
    wifi: wifiDemo(),
    controle: controleDemo(),
  };
}

// 1.0.15 : contrôle réseau horaire simulé (168 points sur 7 jours, avec un petit creux la nuit et une coupure)
let CTRL_REG = { intervalle_min: 60, seuil_mbps: 50 };
let DEMO_CONNECTE = false;
function controleDemo() {
  const n = 7 * 24, pas = 3600, fin = maintenant();
  const h = [];
  for (let i = n - 1; i >= 0; i--) {
    const t = fin - i * pas;
    const d = new Date(t * 1000), heure = d.getHours();
    const nuit = heure >= 1 && heure <= 6;
    const coupe = i === 30 || i === 29;                       // une petite coupure il y a ~30 h
    const down = coupe ? null : Math.round((nuit ? 70 : 430) + Math.sin(i / 5) * 40 + (i % 7) * 3);
    h.push({ t, down_mbps: down, up_mbps: coupe ? null : Math.round((down || 0) / 6),
             ping_ms: coupe ? null : 11 + (i % 9), gigue_ms: coupe ? null : 1 + (i % 4),
             perte_pct: coupe ? 100 : (i % 17 === 0 ? 12.5 : 0), passerelle: !coupe, dns: !coupe, internet: !coupe });
  }
  const par_jour = CTRL_REG.intervalle_min ? (24 * 60 / CTRL_REG.intervalle_min) : 0;
  return { intervalle_min: CTRL_REG.intervalle_min, seuil_mbps: CTRL_REG.seuil_mbps,
           actif: CTRL_REG.intervalle_min > 0, en_cours: false, dernier: h[h.length - 1], historique: h,
           internet_coupe: false, conso_jour_mo: Math.round(par_jour * 15) };
}
const PIHOLE = { bloquees: 9120, requetes: 48211 };
function piholeDemo() {
  if (DEMO_Q.get("pihole") === "0") return null;
  const k = Math.floor((maintenant() - DEMO_DEBUT) / 4);
  const req = PIHOLE.requetes + k * 7, bl = PIHOLE.bloquees + k;
  return { bloquees: bl, requetes: req, pourcentage: Math.round((bl / req) * 1000) / 10, domaines_liste: 142387, maj: maintenant() };
}
function routeAppareil(mac, corps) {
  const a = APPAREILS.find((x) => x.mac === mac.toLowerCase());
  if (corps.oublier === true) {
    if (!a) return err(404, "appareil", "Cet appareil n'est pas (ou plus) connu.");
    if (a.moi) return err(400, "appareil", "Le Pi lui-même ne peut pas être oublié.");
    APPAREILS.splice(APPAREILS.indexOf(a), 1);
    return ok();
  }
  if (!("nom" in corps)) return err(400, "nom", "Indique « nom » (vide pour effacer) ou « oublier ».");
  if (corps.nom !== null && typeof corps.nom !== "string") return err(400, "nom", "Le nom doit être un texte.");
  const nom = String(corps.nom || "");
  if (/[\u0000-\u001f<>]/.test(nom)) return err(400, "nom", "Ce nom contient un caractère interdit.");
  const propre = nom.split(/\s+/).filter(Boolean).join(" ");
  if (propre.length > 40) return err(400, "nom", "Nom trop long : 40 caractères au plus.");
  if (!a) return err(404, "appareil", "Cet appareil n'est pas (ou plus) connu.");
  a.nom_perso = propre;
  return ok({ appareil: listeAppareils().find((x) => x.mac === a.mac) });
}
function lancerDebit() {
  if (DEBIT.en_cours) return err(409, "debit_en_cours", "Un test de débit est déjà en cours. Il dure moins d'une minute.");
  DEBIT.en_cours = true;
  setTimeout(() => {
    const r = { t: maintenant(), down_mbps: Math.round(alea(205, 275) * 10) / 10, up_mbps: Math.round(alea(52, 74) * 10) / 10, ping_ms: Math.round(alea(11, 17) * 10) / 10 };
    DEBITS.push(r);
    while (DEBITS.length > 30) DEBITS.shift();
    DEBIT.en_cours = false;
    notifier(`Test de débit : ↓ ${String(r.down_mbps).replace(".", ",")} Mbit/s · ↑ ${String(r.up_mbps).replace(".", ",")} Mbit/s · ${Math.round(r.ping_ms)} ms`, "ok");
    diffuser("surEtat", etat());
  }, 7000);
  return ok(reseauDemo().debit);
}

// ============================================================================ fiabilité (1.0.11)
// Historique des paquets (dont une sauvegarde d'avant 1.0.11, sans manifeste), sauvegarde vers le PC,
// notifications, joueurs Minecraft. (?sauvegarde=echec pour voir une sauvegarde ratée)
const HISTO = [
  { id: "20261119-191204", date: DEMO_DEBUT - 2 * H, nom: "YDZ OS 1.0.11 — Fiabilité", version: "1.0.11", version_avant: "1.0.10", fichiers: 41, remplaces: 37, nouveaux: 4, complet: true, annulee: null },
  { id: "20261117-213010", date: DEMO_DEBUT - 2 * J - 5 * H, nom: "YDZ OS 1.0.10 — Réseau", version: "1.0.10", version_avant: "1.0.9", fichiers: 36, remplaces: 36, nouveaux: 0, complet: false, annulee: null },
  { id: "20261112-180344", date: DEMO_DEBUT - 7 * J, nom: "YDZ OS 1.0.9 — Écran", version: "1.0.9", version_avant: "1.0.8", fichiers: 33, remplaces: 33, nouveaux: 0, complet: false, annulee: null },
];
function histoDemo() {
  let trouve = false;
  return HISTO.map((x) => {
    const annulable = !trouve && !x.annulee;
    if (!x.annulee) trouve = true;
    return { ...x, annulable };
  });
}
const SAUV = {
  actif: true, mode: "pc_allume", intervalle_h: 20, heure: "04:00", garder: 7, inclure_donnees_apps: false, hote: "192.168.1.23", partage: "YDZ-Sauvegardes",
  utilisateur: "ydz-sauvegarde", mot_de_passe: "k3QzR8mWv2LpX7nTa9Ye",
  derniere: DEMO_Q.get("sauvegarde") === "echec"
    ? { t: DEMO_DEBUT - 5 * H, ok: false, taille: null, erreur: "Le PC refuse le mot de passe : relance « Préparer le PC ».", fichier: null }
    : { t: DEMO_DEBUT - 5 * H, ok: true, taille: 3_480_000, erreur: null, fichier: "ydz-forge-" + new Date((DEMO_DEBUT - 5 * H) * 1000).toISOString().slice(0, 10).replace(/-/g, "") + "-0400.tar.gz" },
  dernier_ok: DEMO_DEBUT - (DEMO_Q.get("sauvegarde") === "echec" ? 29 : 5) * H, en_cours: false, etape: null,
};
function prochaineSauvegarde() {
  const [h, m] = SAUV.heure.split(":").map(Number);
  const d = new Date(); d.setHours(h, m, 0, 0);
  if (d.getTime() <= Date.now()) d.setDate(d.getDate() + 1);
  return Math.floor(d.getTime() / 1000);
}
function sauvegardeDemo() {
  const { mot_de_passe, dernier_ok, ...r } = SAUV;
  const auj = new Date(dernier_ok * 1000).toDateString() === new Date().toDateString();
  const prochaine = !SAUV.actif ? null : SAUV.mode === "pc_allume" ? Math.max(maintenant(), dernier_ok + SAUV.intervalle_h * H) : prochaineSauvegarde();
  return { ...r, mot_de_passe_defini: true, dernier_succes: dernier_ok, prochaine, fait_aujourdhui: auj, smbclient: true };
}
function scriptPC(mdp) {
  return [
    "$ErrorActionPreference = 'Stop'",
    `$u = '${SAUV.utilisateur}'; $p = ConvertTo-SecureString '${mdp}' -AsPlainText -Force`,
    `$d = 'C:\\YDZ-Sauvegardes'; $s = '${SAUV.partage}'`,
    "# 1. compte local dédié (mot de passe sans expiration), caché de l'écran de connexion",
    "if (Get-LocalUser -Name $u -ErrorAction SilentlyContinue) { Set-LocalUser -Name $u -Password $p -PasswordNeverExpires $true -UserMayChangePassword $false; Enable-LocalUser -Name $u }",
    "else { New-LocalUser -Name $u -Password $p -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires -Description 'Sauvegardes de la tour YDZ' | Out-Null }",
    "$k = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\\SpecialAccounts\\UserList'",
    "New-Item -Path $k -Force | Out-Null; New-ItemProperty -Path $k -Name $u -Value 0 -PropertyType DWord -Force | Out-Null",
    "# 2. dossier, avec le droit « Modifier » pour ce compte",
    "New-Item -ItemType Directory -Force -Path $d | Out-Null",
    "icacls $d /grant \"${u}:(OI)(CI)M\" | Out-Null",
    "# 3. partage réservé à ce compte",
    "if (Get-SmbShare -Name $s -ErrorAction SilentlyContinue) {",
    "  Grant-SmbShareAccess -Name $s -AccountName $u -AccessRight Full -Force | Out-Null",
    "  Get-SmbShareAccess -Name $s | Where-Object { $_.AccountName -notlike \"*\\$u\" } | ForEach-Object { Revoke-SmbShareAccess -Name $s -AccountName $_.AccountName -Force | Out-Null }",
    "} else { New-SmbShare -Name $s -Path $d -FullAccess $u -Description 'Sauvegardes YDZ' | Out-Null }",
    "# 4. pare-feu : port 445 ouvert pour forge seulement, quel que soit le profil réseau (Public compris)",
    "Remove-NetFirewallRule -Name 'YDZ-SMB-forge' -ErrorAction SilentlyContinue",
    "New-NetFirewallRule -Name 'YDZ-SMB-forge' -DisplayName 'YDZ : sauvegardes de forge (SMB)' -Direction Inbound -Protocol TCP -LocalPort 445 -RemoteAddress 192.168.1.41 -Action Allow -Profile Any | Out-Null",
    "Write-Host 'YDZ : le PC est pret a recevoir les sauvegardes.' -ForegroundColor Green",
  ].join("\n");
}
function lancerSauvegarde() {
  if (SAUV.en_cours) return err(409, "sauvegarde_en_cours", "Une sauvegarde est déjà en cours.");
  Object.assign(SAUV, { en_cours: true, etape: "Préparation de l'archive" });
  setTimeout(() => { SAUV.etape = "Envoi vers le PC"; diffuser("surEtat", etat()); }, 2500);
  setTimeout(() => {
    const t = maintenant();
    const f = new Date(t * 1000);
    const nom = `ydz-forge-${f.getFullYear()}${String(f.getMonth() + 1).padStart(2, "0")}${String(f.getDate()).padStart(2, "0")}-${String(f.getHours()).padStart(2, "0")}${String(f.getMinutes()).padStart(2, "0")}.tar.gz`;
    Object.assign(SAUV, { en_cours: false, etape: null, derniere: { t, ok: true, taille: 3_512_000, erreur: null, fichier: nom }, dernier_ok: t });
    notifier("Sauvegarde envoyée sur le PC (3,5 Mo)", "ok");
    diffuser("surEtat", etat());
  }, 6000);
  return ok(sauvegardeDemo());
}
function reglerSauvegarde(c) {
  if (c.heure !== undefined && !/^([01]\d|2[0-3]):[0-5]\d$/.test(c.heure)) return err(400, "heure", "Heure invalide (forme attendue : 04:00).");
  if (c.garder !== undefined && !(c.garder >= 1 && c.garder <= 60)) return err(400, "garder", "Garde de 1 à 60 sauvegardes sur le PC.");
  if (c.mode !== undefined && !["pc_allume", "heure"].includes(c.mode)) return err(400, "mode", "Mode inconnu.");
  if (c.intervalle_h !== undefined && !(c.intervalle_h >= 6 && c.intervalle_h <= 72)) return err(400, "intervalle_h", "Écart entre deux sauvegardes : de 6 à 72 heures.");
  for (const k of ["actif", "heure", "garder", "inclure_donnees_apps", "mode", "intervalle_h"]) if (c[k] !== undefined) SAUV[k] = c[k];
  return ok(sauvegardeDemo());
}
const TYPES_NOTIFS = [
  ["noeud", "Nœud hors ligne", "Un Pi de la tour ne répond plus, puis revient.", true],
  ["temperature", "Température", "Un Pi chauffe : 75 °C (attention), 80 °C (critique).", true],
  ["tour", "Tour en alerte", "La tour passe en état critique.", true],
  ["app", "Application en panne", "Une application s'arrête ou redémarre en boucle.", true],
  ["disque", "Disque presque plein", "Un disque dépasse 90 %.", true],
  ["nouvel_appareil", "Nouvel appareil", "Un appareil jamais vu arrive sur le Wi-Fi.", true],
  ["sauvegarde", "Sauvegarde", "La sauvegarde vers le PC échoue (ou réussit, quand tu la lances).", true],
  ["debit", "Test de débit", "Le test de débit Internet échoue.", true],
  ["minecraft", "Joueurs Minecraft", "Quelqu'un rejoint le serveur Minecraft.", false],
];
const NOTIFS = {
  discord: "", discord_actif: false, ntfy_actif: true, ntfy_sujet: "ydz-kqbtmzrwhpaxvle", ntfy_serveur: "https://ntfy.sh",
  types: Object.fromEntries(TYPES_NOTIFS.map((t) => [t[0], t[3]])),
};
const RECENTES = [
  { t: DEMO_DEBUT - 40 * 60, type: "nouvel_appareil", niveau: "warn", texte: "Nouvel appareil sur le réseau : 192.168.1.77 (02:7D:C4:19:E8:77)" },
  { t: DEMO_DEBUT - 5 * H, type: "sauvegarde", niveau: "ok", texte: "Sauvegarde envoyée sur le PC (3,5 Mo)" },
  { t: DEMO_DEBUT - 26 * H, type: "temperature", niveau: "warn", texte: "FORGE chauffe : 76 °C" },
];
function notifsDemo() {
  const n = NOTIFS;
  const m = /^(https:\/\/[^/]+\/api\/webhooks\/)(\d+)\//.exec(n.discord);
  return {
    discord: { defini: Boolean(n.discord), actif: Boolean(n.discord) && n.discord_actif, apercu: m ? `${m[1]}${m[2].slice(0, 2)}…${m[2].slice(-2)}/••••••` : null, erreur: null },
    ntfy: { actif: n.ntfy_actif, sujet: n.ntfy_sujet, serveur: n.ntfy_serveur, erreur: null },
    types: TYPES_NOTIFS.map(([id, nom, description]) => ({ id, nom, description, actif: n.types[id] })),
    recentes: RECENTES,
  };
}
function reglerNotifs(c) {
  if (c.discord !== undefined) {
    if (!c.discord) { NOTIFS.discord = ""; NOTIFS.discord_actif = false; }
    else if (c.discord !== "••••••") {
      if (!/^https:\/\/(?:(?:ptb|canary)\.)?discord(?:app)?\.com\/api\/webhooks\/\d{5,30}\/[A-Za-z0-9_-]{20,120}\/?$/.test(c.discord.trim())) return err(400, "discord", "Adresse de webhook Discord invalide. Elle commence par https://discord.com/api/webhooks/…");
      NOTIFS.discord = c.discord.trim(); NOTIFS.discord_actif = true;
    }
  }
  for (const k of ["discord_actif", "ntfy_actif"]) if (typeof c[k] === "boolean") NOTIFS[k] = c[k];
  if (c.ntfy_sujet !== undefined) {
    const v = c.ntfy_sujet || `ydz-${Array.from({ length: 16 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("")}`;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(v)) return err(400, "ntfy_sujet", "Sujet ntfy invalide : lettres, chiffres, tirets, 64 caractères au plus.");
    NOTIFS.ntfy_sujet = v;
  }
  if (c.ntfy_serveur !== undefined) {
    if (!/^https?:\/\/[A-Za-z0-9.-]+(:\d{1,5})?\/?$/.test(c.ntfy_serveur)) return err(400, "ntfy_serveur", "Adresse du serveur ntfy invalide (exemple : https://ntfy.sh).");
    NOTIFS.ntfy_serveur = c.ntfy_serveur.replace(/\/$/, "");
  }
  if (c.types) Object.assign(NOTIFS.types, c.types);
  return ok(notifsDemo());
}
function minecraftDemo() {
  const i = inst.forge.minecraft;
  if (!i) return null;
  if (i.etat !== "actif") return { en_ligne: false, joueurs: 0, max: 0, noms: [], version: null, motd: null, noeud: "forge", maj: maintenant() };
  return { en_ligne: true, joueurs: 2, max: Number(i.reglages.JOUEURS || 10), noms: ["Justin", "Tibo"], version: "NeoForge 1.21.1", motd: i.reglages.MOTD || "Serveur YDZ", noeud: "forge", maj: maintenant() };
}

// ============================================================================ apps et accès (1.0.12)
// Adresses HTTPS (tailscale serve), mises à jour des apps, historique 7 jours, journal d'activité.
// (?https=0 : adresses HTTPS coupées, ?https=indispo : certificats pas encore activés dans la console)
const NOM_TS = "forge.tail1234.ts.net";
const ACCES = { actif: DEMO_Q.get("https") !== "0" && DEMO_Q.get("https") !== "indispo" };
function adresseHttps(a) {
  if (!ACCES.actif || !a.web) return null;
  return `https://${NOM_TS}:${a.web.port}${a.web.chemin}`;
}
function accesDemo() {
  const indispo = DEMO_Q.get("https") === "indispo";
  const liens = ACCES.actif ? [{ app: "ydz", nom: "YDZ OS", noeud: "forge", url: `https://${NOM_TS}/`, actif: true, icone: "house" },
    ...ORDRE.flatMap((n) => Object.keys(inst[n]).map((id) => app(id)).filter((a) => a.web).map((a) => ({ app: a.id, nom: a.nom, noeud: n, url: adresseHttps(a), actif: true, icone: a.icone })))] : [];
  return {
    disponible: !indispo, installe: true, connecte: true, nom_dns: NOM_TS, ip: "100.75.50.89", magicdns: true, certificats: !indispo,
    https_actif: ACCES.actif, raison_si_indispo: indispo ? "Les certificats HTTPS ne sont pas activés dans la console Tailscale." : null,
    erreur: null, liens, console: "https://login.tailscale.com/admin/dns",
  };
}
// Espace visiteurs (1.0.16) : coupé au départ (?visiteur=1 pour l'ouvrir d'emblée dans l'aperçu).
const VISITEUR = {
  actif: DEMO_Q.get("visiteur") === "1",
  secret: "a1b2c3d4e5f6g7h8j9k0m1n2",
  adresses: { minecraft: "mc.exemple.fr" },
};
function lienVisiteur() { return `https://${NOM_TS}:8443/g/${VISITEUR.secret}`; }
function visiteurDemo(avecLien) {
  const base = {
    actif: VISITEUR.actif, lien_public: null, funnel_ok: VISITEUR.actif && ACCES.actif, disponible: ACCES.actif,
    raison_si_indispo: ACCES.actif ? null : "Les adresses HTTPS ne sont pas activées (Accès à distance).",
    erreur: null, derniere_activation: VISITEUR.actif ? maintenant() - 3600 : null,
    visites_recentes_n: VISITEUR.actif ? 4 : 0, adresses: { ...VISITEUR.adresses },
  };
  if (avecLien) base.lien_public = VISITEUR.actif ? lienVisiteur() : null;
  return base;
}

// « Perso » (1.0.18) : agenda du jour, rappels, Wi-Fi invités (QR code) — tout simulé dans la page.
const hhmmDemo = (t) => new Date(t * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
let RAPPELS = [
  { id: "r1", texte: "Sortir les poubelles (verre)", fait: false, cree: maintenant() - 3600 },
  { id: "r2", texte: "Rappeler le garagiste", fait: false, cree: maintenant() - 7200 },
  { id: "r3", texte: "Lancer une sauvegarde du PC", fait: true, cree: maintenant() - 9000 },
];
let AGENDA_CALS = [{ id: "demo1", nom: "Famille", hote: "p01.calendar.icloud.com",
  url: "https://p01.calendar.icloud.com/published/2/MTAw…demo.ics", maj: maintenant() - 600, erreur: null, evenements_n: 23 }];
let AGENDA_ALERTE = 15;
function agendaVue() {
  const now = maintenant();
  const lt = new Date(now * 1000);
  const minuit = Math.floor(new Date(lt.getFullYear(), lt.getMonth(), lt.getDate()).getTime() / 1000);
  const dans40 = now + 40 * 60;
  const soir = minuit + 19 * 3600 + 30 * 60;
  if (!AGENDA_CALS.length) return { actif: false, prochain: null, aujourdhui: [], jours_suivants_n: 0, erreur: null };
  const auj = [
    { debut: minuit, heure: "", titre: "Anniversaire de Léa", lieu: null, journee_entiere: true },
    { debut: dans40, heure: hhmmDemo(dans40), titre: "Rendez-vous dentiste", lieu: "Cabinet, 2e étage", journee_entiere: false },
    { debut: soir, heure: hhmmDemo(soir), titre: "Cours de guitare", lieu: "Maison", journee_entiere: false },
  ].filter((e) => e.journee_entiere || e.debut >= now - 3600);
  return {
    actif: true, erreur: null,
    prochain: { titre: "Rendez-vous dentiste", debut: dans40, lieu: "Cabinet, 2e étage", journee_entiere: false, bientot_min: 40 },
    aujourdhui: auj, jours_suivants_n: 4,
  };
}
function agendaComplet() {
  return { ...agendaVue(), calendriers: AGENDA_CALS, alerte_min: AGENDA_ALERTE };
}
let WIFI_INV = { ssid: "Maison-Invites", securite: "WPA", mdp: "mot-de-passe-invite", ecran: true };
function wifiInviteStatut() {
  return { ssid: WIFI_INV.ssid || null, securite: WIFI_INV.securite, defini: Boolean(WIFI_INV.ssid && (WIFI_INV.mdp || WIFI_INV.securite === "nopass")),
    mdp_defini: Boolean(WIFI_INV.mdp), mdp_masque: WIFI_INV.mdp ? "••••••" : "", ecran: Boolean(WIFI_INV.ecran) };
}
function chargeWifi() {
  if (!WIFI_INV.ssid) return null;
  const ech = (v) => String(v).replace(/([\\;,:"])/g, "\\$1");
  const t = WIFI_INV.securite === "nopass" || !WIFI_INV.mdp ? "nopass" : "WPA";
  return `WIFI:T:${t};S:${ech(WIFI_INV.ssid)};` + (t !== "nopass" ? `P:${ech(WIFI_INV.mdp)};` : "") + ";";
}
// QR « d'aperçu » : une matrice d'allure réaliste (motifs de détection + bruit déterministe tiré de la charge).
// La démonstration ne sort pas de la page ; le vrai QR scannable est fabriqué sur le Pi (lib/ydz/qr.py).
function qrDemoSvg(charge, taille = 360) {
  const n = 29, marge = 4, tot = n + 2 * marge, u = taille / tot;
  let h = 2166136261;
  for (const c of charge) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
  const bit = (x, y) => { let v = (Math.imul(x + 1, 73856093) ^ Math.imul(y + 1, 19349663) ^ h) >>> 0; return (v % 100) < 48 ? 1 : 0; };
  const finder = (x, y) => (x === 0 || x === 6 || y === 0 || y === 6 || (x >= 2 && x <= 4 && y >= 2 && y <= 4)) ? 1 : 0;
  const dansFinder = (x, y) => (x < 8 && y < 8) || (x >= n - 8 && y < 8) || (x < 8 && y >= n - 8);
  let d = "";
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    let on = 0;
    if (x < 7 && y < 7) on = finder(x, y);
    else if (x >= n - 7 && y < 7) on = finder(x - (n - 7), y);
    else if (x < 7 && y >= n - 7) on = finder(x, y - (n - 7));
    else if (!dansFinder(x, y)) on = bit(x, y);
    if (on) d += `M${((x + marge) * u).toFixed(2)} ${((y + marge) * u).toFixed(2)}h${u.toFixed(2)}v${u.toFixed(2)}h${(-u).toFixed(2)}z`;
  }
  return `<svg class="qr-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${taille} ${taille}" width="${taille}" height="${taille}" role="img" aria-label="QR code Wi-Fi (aperçu)" shape-rendering="crispEdges"><rect width="${taille}" height="${taille}" fill="#ffffff"/><path d="${d}" fill="#000000"/></svg>`;
}
function wifiInviteSnapshot() {
  const s = wifiInviteStatut();
  if (s.ecran && s.defini) s.qr_svg = qrDemoSvg(chargeWifi());
  return s;
}

// iPhone (1.0.20) : notifications iOS (ANCS) + musique (AMS) en BLE, affichage seul. Simulé dans la page.
// (?iphone=0 pour voir l'état « coupé ».)
let IPHONE = {
  actif: DEMO_Q.get("iphone") !== "0",
  notifs: [
    { uid: 5, app: "Messages", titre: "Maman", sous_titre: "", message: "Tu rentres manger ce soir ?", categorie: "Réseau social", categorie_icone: "message-square", important: false, silencieux: false, maj: DEMO_DEBUT - 120 },
    { uid: 4, app: "WhatsApp", titre: "Tibo", sous_titre: "", message: "On lance une partie Minecraft ? 😄", categorie: "Réseau social", categorie_icone: "message-square", important: false, silencieux: false, maj: DEMO_DEBUT - 600 },
    { uid: 3, app: "Calendrier", titre: "Rendez-vous dentiste", sous_titre: "", message: "Dans 40 minutes · Cabinet, 2e étage", categorie: "Agenda", categorie_icone: "calendar", important: false, silencieux: false, maj: DEMO_DEBUT - 900 },
    { uid: 2, app: "Mail", titre: "AT Auto", sous_titre: "", message: "Votre facture est disponible", categorie: "E-mail", categorie_icone: "mail", important: false, silencieux: true, maj: DEMO_DEBUT - 3600 },
    { uid: 1, app: "Téléphone", titre: "Appel manqué", sous_titre: "", message: "Nadia (auto-école)", categorie: "Appel manqué", categorie_icone: "phone-missed", important: true, silencieux: false, maj: DEMO_DEBUT - 7200 },
  ],
  media: { present: true, titre: "Blinding Lights", artiste: "The Weeknd", album: "After Hours", app: "Musique", etat: "lecture", en_lecture: true, maj: DEMO_DEBUT - 30 },
};
function iphoneDemo() {
  const a = IPHONE.actif;
  return {
    actif: a, experimental: true, bluetooth: true, lien: a ? "connecte" : "indisponible", raison: null,
    notifications: a ? IPHONE.notifs.map((n) => ({ ...n })) : [], notifications_n: a ? IPHONE.notifs.length : 0,
    media: a ? { ...IPHONE.media } : { present: false }, reponse_possible: false, iphone_appaire: true,
  };
}

const MAJ_APPS = { "pulse:pihole": true, "vault:jellyfin": true };
const MAJ_VERIF = maintenant() - 3 * 3600 - 1260;
function majInfo(n, id) {
  return { maj_disponible: Boolean(MAJ_APPS[`${n}:${id}`]), verifie_le: MAJ_VERIF, empreinte: null, erreur: null };
}

// 7 jours de mesures toutes les 5 minutes, avec le rythme d'une journée (calculées une fois, stables)
const SERIES_H = { cpu: ["Processeur", "%"], temp: ["Température", "°C"], ram: ["Mémoire", "%"], disque: ["Disque système", "%"], rx: ["Réception", "bit/s"], tx: ["Envoi", "bit/s"], ventilo: ["Ventilateur", "%"], watts: ["Consommation", "W"], wifi: ["Signal Wi-Fi", "dBm"] };
const HIST = {};
function bruit(i, k) { const x = Math.sin(i * 12.9898 + k * 78.233) * 43758.5453; return x - Math.floor(x); }
function histoSerie(noeud, serie) {
  const cle = `${noeud}/${serie}`;
  if (HIST[cle]) return HIST[cle];
  const fin = Math.floor(maintenant() / 300) * 300, pts = [];
  const b = base[noeud] || base.forge, k = ORDRE.indexOf(noeud) + Object.keys(SERIES_H).indexOf(serie) * 7;
  for (let i = 2015; i >= 0; i--) {
    const t = fin - i * 300, h = new Date(t * 1000).getHours() + new Date(t * 1000).getMinutes() / 60;
    const jour = Math.max(0, Math.sin(((h - 7) / 24) * 2 * Math.PI));          // activité : soirée haute, nuit basse
    const soir = Math.exp(-((h - 21) ** 2) / 5);                                // soirée de jeu, sans marche brusque
    const r = bruit(i, k);
    let v;
    switch (serie) {
      case "cpu": v = b.cpu * 0.5 + jour * b.cpu * 0.8 + soir * (noeud === "forge" ? 22 : 4) + r * 6; break;
      case "temp": v = b.temp - 4 + jour * 5 + soir * (noeud === "forge" ? 7 : 1.5) + r * 1.2; break;
      case "ram": v = b.ram + soir * (noeud === "forge" ? 9 : 2) + (i % 2016 < 600 ? 2 : 0) + r * 1.5; break;
      case "disque": v = (noeud === "forge" ? 41 : noeud === "vault" ? 23 : 18) + (2015 - i) * 0.0009; break;
      case "rx": v = (debitBase[noeud] || debitBase.forge)[0] * (0.2 + jour + soir * 1.4) * (0.6 + r * 0.8); break;
      case "tx": v = (debitBase[noeud] || debitBase.forge)[1] * (0.2 + jour + soir * 1.2) * (0.6 + r * 0.8); break;
      case "ventilo": v = 28 + jour * 14 + soir * 20 + r * 4; break;
      case "watts": v = 13.5 + jour * 2.5 + soir * 4.5 + r * 0.9; break;
      case "wifi": v = -70 - jour * 2 - r * 4; break;
      default: v = 0;
    }
    pts.push([t, serie === "rx" || serie === "tx" ? Math.round(v) : Math.round(v * 10) / 10]);
  }
  HIST[cle] = pts;
  return pts;
}
function reduirePts(pts, maxi = 300) {
  if (pts.length <= maxi) return pts;
  const taille = pts.length / maxi, out = [];
  for (let i = 0; i < maxi; i++) {
    const tr = pts.slice(Math.floor(i * taille), Math.max(Math.floor((i + 1) * taille), Math.floor(i * taille) + 1));
    out.push([Math.round(tr.reduce((s, p) => s + p[0], 0) / tr.length), Math.round((tr.reduce((s, p) => s + p[1], 0) / tr.length) * 10) / 10]);
  }
  return out;
}
function historiqueDemo(q) {
  const jours = q.get("jours") === "7" ? 7 : 1;
  const series = (q.get("serie") || "").split(",").filter(Boolean);
  if (!series.length) return lu({ jours, disponibles: ORDRE.map((n) => ({ noeud: n, series: n === "forge" ? Object.keys(SERIES_H) : ["cpu", "temp", "ram", "disque", "rx", "tx"] })) });
  const noeud = q.get("noeud") || "forge";
  const une = (serie) => {
    if (!SERIES_H[serie]) return null;
    const depuis = maintenant() - jours * 86400;
    const pts = (["ventilo", "watts", "wifi"].includes(serie) && noeud !== "forge") ? [] : histoSerie(noeud, serie).filter((p) => p[0] >= depuis);
    const v = pts.map((p) => p[1]);
    return { noeud, serie, nom: SERIES_H[serie][0], unite: SERIES_H[serie][1], jours, pas_s: 300, points: reduirePts(pts), nombre: pts.length,
      min: v.length ? Math.min(...v) : null, max: v.length ? Math.max(...v) : null, moy: v.length ? Math.round((v.reduce((a, b) => a + b, 0) / v.length) * 10) / 10 : null, derniere: v.length ? v[v.length - 1] : null };
  };
  const out = series.map(une);
  if (out.some((x) => !x)) return err(400, "serie", "Série inconnue.");
  return lu(out.length === 1 ? out[0] : { series: out, noeud, jours });
}

// ~40 événements sur 4 jours : actions (écran, réseau local, Tailscale), automatiques, quelques erreurs
const ACT = (() => {
  const L = [];
  const a = (h, texte, origine, qui, ok = true, erreur = null) => L.push({ t: DEMO_DEBUT - Math.round(h * H), type: "action", texte, origine, qui, ok, erreur });
  const auto = (h, texte, ok = true, categorie = null) => L.push({ t: DEMO_DEBUT - Math.round(h * H), type: "auto", texte, origine: "auto", qui: "YDZ", ok, categorie });
  a(0.3, "Réveil de PC de Justin", "tailscale", "justin@icloud.com");
  a(0.35, "Connexion avec le code PIN", "tailscale", "justin@icloud.com");
  auto(0.7, "Nouvel appareil sur le réseau : 192.168.1.77 (02:7D:C4:19:E8:77)", true, "nouvel_appareil");
  a(2, "Paquet 1.0.11 installé", "reseau", "192.168.1.40");
  a(2.02, "Connexion avec le code PIN", "reseau", "192.168.1.40");
  auto(3.4, "Mise à jour disponible : Jellyfin, Pi-hole", true, "maj_apps");
  auto(5, "Sauvegarde automatique envoyée sur le PC (3,5 Mo)", true, "sauvegarde");
  a(6.5, "Ventilateur : mode Équilibré", "ecran", "Écran du Pi");
  a(7, "Redémarrage de Minecraft sur FORGE", "reseau", "192.168.1.35");
  a(7.1, "Connexion avec le code PIN", "reseau", "192.168.1.35");
  a(7.12, "Connexion avec le code PIN", "reseau", "192.168.1.35", false, "Code incorrect. Encore 4 essais.");
  auto(9, "Test de débit de la nuit : ↓ 241,7 Mbit/s", true, "debit");
  auto(22, "Tibo a rejoint le serveur Minecraft (2/10)", true, "minecraft");
  a(23, "Extinction de PC de Justin", "tailscale", "justin@icloud.com");
  a(26, "Réglages de Minecraft modifiés sur FORGE", "reseau", "192.168.1.40");
  auto(26.5, "FORGE chauffe : 76 °C", false, "temperature");
  a(27, "Installation de WireGuard (wg-easy) sur PULSE", "reseau", "192.168.1.40");
  a(27.2, "Réglages des notifications", "reseau", "192.168.1.40");
  a(27.3, "Notification de test", "reseau", "192.168.1.40");
  auto(29, "Sauvegarde automatique envoyée sur le PC (3,4 Mo)", true, "sauvegarde");
  auto(33, "Test de débit de la nuit : ↓ 228,4 Mbit/s", true, "debit");
  a(46, "Réveil de PC de Justin", "ecran", "Écran du Pi");
  a(47, "Partage « photos » créé", "reseau", "192.168.1.40");
  a(47.5, "Installation de Jellyfin sur VAULT", "reseau", "192.168.1.40", false, "Le téléchargement a échoué. Ouvre le journal de la tâche pour le détail.");
  a(47.4, "Installation de Jellyfin sur VAULT", "reseau", "192.168.1.40");
  auto(53, "Sauvegarde ratée : le PC a refusé la connexion (pare-feu ?)", false, "sauvegarde");
  auto(57, "Test de débit de la nuit : ↓ 236,0 Mbit/s", true, "debit");
  a(70, "Code PIN modifié", "ecran", "Écran du Pi");
  a(70.5, "Réglages de l'écran d'affichage", "ecran", "Écran du Pi");
  a(71, "Annulation de paquet (retour à 1.0.9)", "reseau", "192.168.1.40");
  a(71.1, "Paquet 1.0.10 installé", "reseau", "192.168.1.40");
  auto(72, "PULSE ne répond plus", false, "noeud");
  auto(72.1, "PULSE est de nouveau en ligne", true, "noeud");
  a(74, "Redémarrage de PULSE", "reseau", "192.168.1.40");
  auto(77, "Sauvegarde automatique envoyée sur le PC (3,4 Mo)", true, "sauvegarde");
  auto(81, "Test de débit de la nuit : ↓ 117,3 Mbit/s", true, "debit");
  a(90, "Appareil renommé", "reseau", "192.168.1.35");
  a(91, "Mises à jour du système lancées", "ecran", "Écran du Pi");
  a(91.2, "Recherche de mises à jour du système", "ecran", "Écran du Pi");
  a(95, "Sauvegarde lancée", "reseau", "192.168.1.40");
  return L.sort((x, y) => y.t - x.t);
})();
function activiteDemo(q) {
  const f = q.get("filtre") || "tout";
  const l = ACT.filter((e) => f === "actions" ? e.type === "action" : f === "auto" ? e.type === "auto" : f === "erreurs" ? !e.ok : true);
  return lu({ entrees: l.slice(0, Number(q.get("limite") || 200)), filtre: f });
}
function noterAction(texte, ok = true) { ACT.unshift({ t: maintenant(), type: "action", texte, origine: "ecran", qui: "Écran du Pi", ok }); }

// --------------------------------------------------------------------------------------- tâches
function tache({ type, titre, cible = null, noeud }, etapes, fin) {
  const t = { id: id8(), type, titre, cible, noeud, etat: "en_cours", message: "", journal: [], complet: [], debut: maintenant(), fin: null };
  taches.unshift(t);
  let delai = 0;
  const publie = () => diffuser("surTache", vueTache(t));
  etapes.forEach(([ms, ligne]) => {
    delai += ms;
    setTimeout(() => { t.complet.push(ligne); t.journal = t.complet.slice(-3); publie(); }, delai);
  });
  setTimeout(() => {
    let message;
    try { message = fin(); t.etat = "ok"; } catch (e) { message = e.message; t.etat = "erreur"; }
    t.message = message;
    t.fin = maintenant();
    publie();
    diffuser("surEtat", etat());
  }, delai + 700);
  return t;
}
const vueTache = (t) => { const { complet, ...reste } = t; return reste; };
const occupe = (cible, noeud) => taches.some((t) => t.etat === "en_cours" && t.cible === cible && t.noeud === noeud);

// ----------------------------------------------------------------------------------------- apps
function valider(a, reglages, existants) {
  const sortie = { ...(existants || {}) };
  for (const r of a.reglages) {
    let v = reglages?.[r.cle];
    if (v === undefined || v === "••••••") v = sortie[r.cle];
    if (v === undefined || v === null) v = r.defaut !== undefined ? r.defaut : "";
    v = String(v);
    if (/['\n]/.test(v) || v.length > 256) return err(400, "reglages", `${r.nom} : caractère interdit ou valeur trop longue.`, { cle: r.cle });
    if (!v && r.type === "secret" && r.genere) v = id8() + id8();
    if (!v && r.requis) return err(400, "reglages", `${r.nom} : ce réglage est obligatoire.`, { cle: r.cle });
    if (v && r.type === "nombre" && !/^-?\d+$/.test(v)) return err(400, "reglages", `${r.nom} : un nombre est attendu.`, { cle: r.cle });
    if (v && r.type === "secret" && r.min && v.length < r.min) return err(400, "reglages", `${r.nom} : ${r.min} caractères au moins.`, { cle: r.cle });
    if (v && r.motif && !new RegExp(r.motif).test(v)) return err(400, "reglages", `${r.nom} : valeur refusée. ${r.aide || ""}`.trim(), { cle: r.cle });
    sortie[r.cle] = v;
  }
  return { reglages: sortie };
}

function actionApp(id, action, corps) {
  const a = app(id);
  if (!a) return err(404, "app_inconnue", "Cette application n'est pas au catalogue.");
  let noeud = corps.noeud;
  if (action === "installer" && !noeud) noeud = !horsLigne[a.noeud_conseille] ? a.noeud_conseille : "forge";
  if (!N[noeud]) return err(404, "noeud", "Ce nœud n'existe pas.");
  if (horsLigne[noeud]) return err(503, "hors_ligne", `${noeud.toUpperCase()} est hors ligne.`);
  if (occupe(id, noeud)) return err(409, "occupe", `Une opération est déjà en cours pour ${a.nom}.`);
  const i = inst[noeud][id];
  const lance = (titre, etapes, fin) => ok({ tache: vueTache(tache({ type: "app", titre, cible: id, noeud }, etapes, fin)), noeud });
  if (action === "installer") {
    if (i) return err(409, "deja_installee", `${a.nom} est déjà installée sur ${noeud.toUpperCase()}.`);
    const v = valider(a, corps.reglages);
    if (v.statut) return v;
    const libre = N[noeud].ram.total - N[noeud].ram.utilise;
    if (a.ram_mo * 2 ** 20 > libre) return err(409, "memoire", `Pas assez de mémoire libre sur ${noeud.toUpperCase()} pour ${a.nom}.`);
    return lance(`Installation de ${a.nom}`, [
      [300, `Téléchargement de ${a.nom}…`], [1100, ` ${a.image} Pulling`], [1300, ` ydz-${id} Pulled`], [500, "Démarrage…"], [500, ` Container ydz-${id}  Started`],
    ], () => {
      inst[noeud][id] = {
        etat: "actif", reglages: v.reglages, installe_le: maintenant(),
        donnees: corps.emplacement ? `${corps.emplacement}/apps/${id}` : `/var/lib/ydz/donnees/${id}`,
      };
      return `${a.nom} est installée.`;
    });
  }
  if (!i) return err(409, "non_installee", `${a.nom} n'est pas installée sur ${noeud.toUpperCase()}.`);
  switch (action) {
    case "demarrer": return lance(`Démarrage de ${a.nom}`, [[500, "Démarrage…"]], () => { i.etat = "actif"; return `${a.nom} est démarrée.`; });
    case "arreter": return lance(`Arrêt de ${a.nom}`, [[500, "Arrêt…"]], () => { i.etat = "arrete"; return `${a.nom} est arrêtée.`; });
    case "redemarrer": return lance(`Redémarrage de ${a.nom}`, [[400, "Arrêt…"], [800, "Démarrage…"]], () => { i.etat = "actif"; return `${a.nom} a redémarré.`; });
    case "mettre-a-jour": return lance(`Mise à jour de ${a.nom}`, [[400, `Téléchargement de la nouvelle version de ${a.nom}…`], [1500, ` ydz-${id} Pulled`], [600, `Redémarrage de ${a.nom}…`], [300, "Ancienne image retirée"]], () => { i.etat = "actif"; delete MAJ_APPS[`${noeud}:${id}`]; return `${a.nom} est à jour.`; });
    case "reglages": {
      const v = valider(a, corps.reglages, i.reglages);
      if (v.statut) return v;
      return lance(`Réglages de ${a.nom}`, [[400, "Enregistrement…"], [900, "Redémarrage…"]], () => { i.reglages = v.reglages; i.etat = "actif"; return `${a.nom} a pris ses nouveaux réglages.`; });
    }
    case "desinstaller": return lance(`Désinstallation de ${a.nom}`, [[500, "Arrêt…"], [900, " Container removed"]], () => {
      delete inst[noeud][id];
      return corps.supprimer_donnees ? `${a.nom} est désinstallée, données supprimées.` : `${a.nom} est désinstallée. Ses données sont gardées.`;
    });
    default: return err(404, "inconnu", "Action inconnue.");
  }
}

function journalApp(id, noeud) {
  const a = app(id);
  const h = (d) => new Date(Date.now() - d * 1000).toTimeString().slice(0, 8);
  const gabarits = {
    minecraft: ["[Server thread/INFO]: Starting minecraft server version 1.21.1", "[Server thread/INFO]: Loading NeoForge 21.1.90", "[Server thread/INFO]: Preparing level \"world\"", "[Worker-Main-2/INFO]: Preparing spawn area: 84%", "[Server thread/INFO]: Done (18.412s)! For help, type \"help\"", "[Server thread/INFO]: Justin joined the game", "[Server thread/INFO]: Justin has made the advancement [Stone Age]", "[Server thread/INFO]: Saving the game (this may take a moment!)", "[Server thread/INFO]: Saved the game"],
    pihole: ["FTL started!", "Imported 142 387 domains from gravity", "query[A] ads.example.net from 192.168.1.35", "gravity blocked ads.example.net is 0.0.0.0", "query[AAAA] forge.local from 192.168.1.20", "forwarded forge.local to 9.9.9.9"],
  }[id] || ["Démarrage du service", "Configuration chargée", `Écoute sur le port ${a.ports[0]?.port || 80}`, "Prêt", "Requête traitée en 12 ms", "Sauvegarde automatique terminée"];
  const lignes = [];
  for (let k = 0; k < 40; k++) lignes.push(`${id}  | [${h((40 - k) * 37)}] ${gabarits[k % gabarits.length]}`);
  return lu({ id, lignes, ok: true, noeud });
}

// ------------------------------------------------------------------------------- stockage : disque système (1.0.21)
const SYSTEME_DEMO = {
  forge: {
    total: 256e9, utilise: 254.9e9, libre: 1.1e9, pct: 99, docker_dispo: true, docker_total: 128e9,
    reserve_octets: 1.5e9, reserve_ok: false, seuil_warn: 85, seuil_crit: 95, garder_sauvegardes: 3,
    recuperable_total: 48.6e9, niveau: "crit",
    postes: [
      { id: "systeme", nom: "Système et données diverses", octets: 109.2e9, recuperable: 0, actif: false, categorie: "systeme", detail: "Système d'exploitation, apps hors Docker, données YDZ." },
      { id: "app:minecraft", nom: "minecraft", octets: 42e9, recuperable: 0, actif: true, categorie: "docker" },
      { id: "docker", nom: "Images, volumes et cache Docker", octets: 86e9, recuperable: 36e9, actif: false, categorie: "docker", detail: "Dont ~36 Go d'inutilisé (images sans conteneur, cache de build, volumes orphelins)." },
      { id: "sauvegardes", nom: "Sauvegardes de paquets YDZ", octets: 14e9, recuperable: 9e9, actif: false, categorie: "systeme", detail: "6 sauvegardes ; on garde les 3 plus récentes." },
      { id: "apt", nom: "Cache des paquets système (apt)", octets: 2.1e9, recuperable: 2.1e9, actif: false, categorie: "systeme", detail: "Fichiers .deb déjà installés : entièrement récupérable." },
      { id: "journaux", nom: "Journaux système (journald)", octets: 1.6e9, recuperable: 1.5e9, actif: false, categorie: "systeme", detail: "On garde ~100 Mo de journaux récents." },
    ],
  },
  vault: {
    total: 32e9, utilise: 9e9, libre: 23e9, pct: 28, docker_dispo: true, docker_total: 3.2e9,
    reserve_octets: 1.5e9, reserve_ok: true, seuil_warn: 85, seuil_crit: 95, garder_sauvegardes: 3,
    recuperable_total: 1.1e9, niveau: "ok",
    postes: [
      { id: "systeme", nom: "Système et données diverses", octets: 5.3e9, recuperable: 0, actif: false, categorie: "systeme", detail: "Système d'exploitation, apps hors Docker, données YDZ." },
      { id: "app:jellyfin", nom: "jellyfin", octets: 2.6e9, recuperable: 0, actif: false, categorie: "docker" },
      { id: "docker", nom: "Images, volumes et cache Docker", octets: 0.6e9, recuperable: 0.5e9, actif: false, categorie: "docker", detail: "Dont ~500 Mo d'inutilisé." },
      { id: "sauvegardes", nom: "Sauvegardes de paquets YDZ", octets: 0.5e9, recuperable: 0.2e9, actif: false, categorie: "systeme", detail: "4 sauvegardes ; on garde les 3 plus récentes." },
      { id: "apt", nom: "Cache des paquets système (apt)", octets: 0.4e9, recuperable: 0.4e9, actif: false, categorie: "systeme", detail: "Fichiers .deb déjà installés : entièrement récupérable." },
    ],
  },
  pulse: {
    total: 32e9, utilise: 8e9, libre: 24e9, pct: 28, docker_dispo: true, docker_total: 1.1e9,
    reserve_octets: 1.5e9, reserve_ok: true, seuil_warn: 85, seuil_crit: 95, garder_sauvegardes: 3,
    recuperable_total: 0, niveau: "ok",
    postes: [
      { id: "systeme", nom: "Système et données diverses", octets: 6.9e9, recuperable: 0, actif: false, categorie: "systeme", detail: "Système d'exploitation, apps hors Docker, données YDZ." },
      { id: "app:pihole", nom: "pihole", octets: 1.1e9, recuperable: 0, actif: true, categorie: "docker" },
    ],
  },
};
function systemeDemo(noeud) {
  return JSON.parse(JSON.stringify(SYSTEME_DEMO[noeud] || SYSTEME_DEMO.forge));
}
function apercuLiberer(noeud) {
  const sys = SYSTEME_DEMO[noeud] || SYSTEME_DEMO.forge;
  const actions = [];
  const d = sys.postes.find((p) => p.id === "docker");
  if (d && d.recuperable > 0) actions.push({ id: "docker", nom: "Nettoyer Docker (inutilisé seulement)", octets: d.recuperable, estimation: true, detail: "Images sans conteneur, cache de build et volumes orphelins. Les images et volumes des apps qui tournent ne sont jamais touchés." });
  const sv = sys.postes.find((p) => p.id === "sauvegardes");
  if (sv && sv.recuperable > 0) actions.push({ id: "sauvegardes", nom: "Supprimer les vieilles sauvegardes de paquets", octets: sv.recuperable, estimation: false, detail: "Les 3 plus récentes sont gardées (pour pouvoir annuler une mise à jour)." });
  const ap = sys.postes.find((p) => p.id === "apt");
  if (ap && ap.recuperable > 0) actions.push({ id: "apt", nom: "Vider le cache des paquets système (apt clean)", octets: ap.recuperable, estimation: false, detail: "Fichiers .deb déjà installés, re-téléchargés au besoin." });
  const jo = sys.postes.find((p) => p.id === "journaux");
  if (jo && jo.recuperable > 0) actions.push({ id: "journaux", nom: "Réduire les journaux système", octets: jo.recuperable, estimation: true, detail: "On garde ~100 Mo de journaux récents." });
  return { actions, total_octets: actions.reduce((n, a) => n + a.octets, 0), libre_avant: sys.libre, reserve_octets: sys.reserve_octets, garder_sauvegardes: 3 };
}

// ------------------------------------------------------------------------------------- stockage
function stockage(noeud) {
  const s = D.stockage[noeud];
  s.systeme = systemeDemo(noeud);
  if (noeud === "vault") {
    const vides = s.disques.filter((d) => d.vide && d.formatable && !d.systeme);
    s.suggestion = vides.length >= 2 && !s.miroirs.length
      ? { type: "miroir", disques: vides.slice(0, 2).map((d) => d.chemin), texte: "Deux disques vides de même taille : un miroir protégerait tes fichiers d'une panne." }
      : vides.length ? { type: "formater", disques: [vides[0].chemin], texte: "Un disque vide est prêt à être préparé pour YDZ." } : null;
  }
  return s;
}
function preparer(corps, miroir) {
  const noeud = corps.noeud || "vault";
  const s = D.stockage[noeud];
  if (!s) return err(404, "noeud", "Ce nœud n'existe pas.");
  const chemins = miroir ? corps.disques || [] : [corps.disque];
  const disques = chemins.map((c) => s.disques.find((d) => d.chemin === c));
  if (disques.some((d) => !d) || (miroir && disques.length !== 2)) return err(400, "disque", "Disque introuvable.");
  if (disques.some((d) => d.systeme)) return err(403, "disque_protege", "C'est le disque du système : il ne peut pas être effacé.");
  const nom = String(corps.nom || "");
  if (!/^[a-z0-9]{1,16}$/.test(nom)) return err(400, "nom", "Le nom doit faire 1 à 16 lettres minuscules ou chiffres, sans espace.");
  if (s.disques.some((d) => d.nom === nom && !disques.includes(d))) return err(409, "nom", `Le nom « ${nom} » est déjà pris par un autre disque.`);
  const perdues = disques.some((d) => !d.vide);
  const p = {
    jeton: id8() + id8(), phrase: `EFFACER ${disques.map((d) => d.id.toUpperCase()).join(" ")}`, expire_s: 120, nom, miroir,
    montage: `/srv/ydz/${nom}`, disques: disques.map((d) => ({ chemin: d.chemin, modele: d.modele, serie: d.serie, taille: d.taille })),
    donnees_perdues: perdues,
    avertissement: `TOUT le contenu de ${chemins.join(" et ")} sera effacé définitivement${perdues ? " (des données existent dessus)" : ""}.`,
    noeud,
  };
  jetons[p.jeton] = { p, limite: Date.now() + 120000 };
  return lu(p);
}
function confirmer(corps) {
  const j = jetons[corps.jeton];
  delete jetons[corps.jeton];
  if (!j || Date.now() > j.limite) return err(410, "jeton", "La confirmation a expiré. Recommence la préparation.");
  if (corps.phrase !== j.p.phrase) return err(400, "phrase", "La phrase ne correspond pas. Rien n'a été effacé.");
  const { p } = j;
  const s = D.stockage[p.noeud];
  const noms = p.disques.map((d) => d.chemin).join(" et ");
  const t = tache({ type: "stockage", titre: p.miroir ? `Création du miroir « ${p.nom} »` : `Préparation de ${noms}`, cible: p.nom, noeud: p.noeud }, [
    [400, "Table de partitions GPT…"], [1200, p.miroir ? "Assemblage du miroir (RAID 1)…" : "Partition créée"], [1500, "Formatage ext4…"], [900, `Montage dans ${p.montage}`],
  ], () => {
    p.disques.forEach((pd) => {
      const d = s.disques.find((x) => x.chemin === pd.chemin);
      s.partages = s.partages.filter((x) => x.disque !== d.nom);
      Object.assign(d, {
        montage: p.montage, nom: p.nom, vide: false, miroir: p.miroir, a_remonter: null,
        total: Math.round(d.taille * 0.983), libre: Math.round(d.taille * 0.983),
        partitions: [{ chemin: `${d.chemin}1`, type: "part", taille: d.taille - 1048576, fs: p.miroir ? "linux_raid_member" : "ext4", etiquette: `ydz-${p.nom}`, uuid: id8(), montage: [p.montage] }],
      });
    });
    if (p.miroir) s.miroirs.push({ nom: "md127", niveau: "raid1", actif: true, membres: p.disques.map((d) => `${d.chemin.slice(5)}1`), etat: "reconstruction", progression: 1.2 });
    return p.miroir ? `Le miroir « ${p.nom} » est prêt. La synchronisation continue en fond.` : `Le disque « ${p.nom} » est prêt.`;
  });
  return ok({ tache: vueTache(t), noeud: p.noeud });
}

// ---------------------------------------------------------------------------------- alimentation
function couper(nom, action) {
  setTimeout(() => { horsLigne[nom] = true; N[nom].etat = "halt"; diffuser("surEtat", etat()); }, 2000);
  setTimeout(() => {
    delete horsLigne[nom];
    allumage[nom] = maintenant();
    N[nom].etat = "ok";
    const m = D.maj.noeuds.find((x) => x.noeud === nom);
    if (m) m.redemarrage_requis = false;
    notifier(action === "eteindre" ? `Démonstration : ${nom.toUpperCase()} est rallumé pour la suite de la visite.` : `${nom.toUpperCase()} a redémarré.`);
    diffuser("surEtat", etat());
  }, action === "eteindre" ? 20000 : 11000);
}

// ---------------------------------------------------------------------------------------- routes
const PAQUETS = [["libssl3t64", "3.5.4-1~deb13u1"], ["openssl", "3.5.4-1~deb13u1"], ["linux-image-rpi-2712", "1:6.12.47-1+rpt1"], ["raspi-firmware", "1:1.20251028-1"], ["curl", "8.14.1-2+deb13u2"], ["libcurl4t64", "8.14.1-2+deb13u2"], ["tzdata", "2025b-4+deb13u1"]];


// ------------------------------------------------------------------- mises à jour de YDZ OS (1.0.14)
// Installée : 1.0.14 ; 1.0.15 et 1.0.16 publiées sur GitHub, téléchargées et vérifiées.
// ?majydz=0 : à jour ; ?majydz=cours : installation 2/2 en cours ; ?majydz=nuit : installée cette nuit.
D.etat.version = "1.0.14";
D.systeme.version = "1.0.14";
const NOTES_DEMO = {
  "1.0.15": "## Nouveau\n- **Météo à 3 jours** sur l'écran HDMI\n- Les apps arrêtées se relancent après une coupure de courant\n## Corrigé\n- La jauge du disque de Pi Two ne dépasse plus 100 %\n- Le Wi-Fi revient plus vite après un redémarrage de la box",
  "1.0.16": "## Nouveau\n- Sauvegarde **chiffrée** vers le PC\n- `ydz état` dans le terminal\n## Corrigé\n- L'horloge de nuit ne saute plus une seconde à minuit",
};
const MAJY = {
  installee: "1.0.14",
  releases: DEMO_Q.get("majydz") === "0" ? [] : ["1.0.15", "1.0.16"].map((v, i) => ({ version: v, nom: `YDZ OS ${v}`, notes: NOTES_DEMO[v], taille: [1984213, 2051877][i], prete: true, publiee: null, telechargement: false })),
  derniere: DEMO_DEBUT - 47 * 60, erreur: null, auto_nuit: DEMO_Q.get("majydz") === "nuit", verification: false, prog: null,
};
if (DEMO_Q.get("majydz") === "cours") {
  MAJY.prog = { id: "demo", etat: "en_cours", origine: "manuel", total: 2, index: 1, version: "1.0.16", texte: "Installation 2/2 : 1.0.16…", etape: "apres", erreur: null, installees: ["1.0.15"], debut: DEMO_DEBUT - 70, fin: null };
} else if (DEMO_Q.get("majydz") === "nuit") {
  MAJY.prog = { id: "demo", etat: "fini", origine: "nuit", total: 1, index: 1, version: "1.0.14", texte: "Terminé", etape: null, erreur: null, installees: ["1.0.14"], debut: DEMO_DEBUT - 4 * H, fin: DEMO_DEBUT - 4 * H + 95 };
  MAJY.releases = [];
}
function majYdzDemo() {
  const p = MAJY.prog;
  return { version_installee: MAJY.installee, depot: "Youdzyo/ydz-os", disponibles: MAJY.releases.map((r) => ({ ...r })),
    derniere_verification: MAJY.derniere, erreur: MAJY.erreur, auto_nuit: MAJY.auto_nuit, heure_nuit: "04:00",
    en_cours: Boolean(p && p.etat === "en_cours"), verification: MAJY.verification, progression: p ? { ...p, installees: [...p.installees] } : null };
}
function verifierYdz() {
  if (MAJY.prog?.etat === "en_cours") return err(409, "occupe", "Une installation est en cours : attends qu'elle se termine.");
  noterAction("Recherche de mises à jour de YDZ OS");
  MAJY.verification = true;
  const t = tache({ type: "maj_ydz", titre: "Recherche de mises à jour de YDZ OS", cible: "ydz:maj", noeud: "forge" },
    [[600, "Interrogation de GitHub (Youdzyo/ydz-os)…"], [500, MAJY.releases.length ? `${MAJY.releases.length} version(s) plus récente(s) que ${MAJY.installee}.` : `YDZ OS ${MAJY.installee} est la dernière version.`]],
    () => {
      MAJY.verification = false; MAJY.derniere = maintenant(); MAJY.erreur = null;
      const p = MAJY.releases.filter((r) => r.prete).map((r) => r.version);
      return p.length ? `Prête${p.length > 1 ? "s" : ""} à installer : ${p.join(", ")}.` : `Tu es à jour (YDZ OS ${MAJY.installee}).`;
    });
  return ok({ tache: vueTache(t), maj_ydz: majYdzDemo() });
}
function installerYdz(origine = "manuel") {
  if (MAJY.prog?.etat === "en_cours") return err(409, "occupe", "Une installation de mises à jour est déjà en cours.");
  const vs = MAJY.releases.filter((r) => r.prete).map((r) => r.version);
  if (!vs.length) return err(409, "rien", "Aucune mise à jour prête à installer.");
  noterAction(`Installation de YDZ OS ${vs.join(", ")} lancée`);
  const p = MAJY.prog = { id: id8(), etat: "en_cours", origine, total: vs.length, index: 0, version: vs[0], texte: `Installation 1/${vs.length} : ${vs[0]}…`, etape: "verification", erreur: null, installees: [], debut: maintenant(), fin: null };
  let delai = 0;
  const pousser = () => diffuser("surEtat", etat());
  vs.forEach((v, i) => {
    [["verification", 300], ["installation", 1200], ["apres", 2200], ["redemarrage", 2500]].forEach(([etape, ms]) => {
      delai += ms;
      setTimeout(() => { Object.assign(p, { index: i, version: v, etape, texte: `Installation ${i + 1}/${vs.length} : ${v}…` }); pousser(); }, delai);
    });
    delai += 1500;
    setTimeout(() => {
      p.installees.push(v); p.index = i + 1; MAJY.installee = v; D.etat.version = v; D.systeme.version = v;
      MAJY.releases = MAJY.releases.filter((r) => r.version !== v);
      HISTO.unshift({ id: `demo-${v}`, date: maintenant(), nom: `YDZ OS ${v}`, version: v, version_avant: i ? vs[i - 1] : "1.0.14", fichiers: 52, remplaces: 50, nouveaux: 2, complet: true, annulee: null });
      if (i === vs.length - 1) {
        Object.assign(p, { etat: "fini", fin: maintenant(), texte: "Terminé", etape: null });
        notifier(`Mise à jour installée${vs.length > 1 ? "s" : ""} : ${vs.join(", ")}. En cas de souci : Réglages > Mises à jour > Annuler ce paquet.`, "ok");
      }
      pousser();
    }, delai);
  });
  return ok({ versions: vs, maj_ydz: majYdzDemo() });
}

// ------------------------------------------------------------------------- réglages généraux (1.0.13)
// Noms (Home Pi Lab, Pi One…), Wi-Fi (bascule sûre : Freebox-7C21 échoue et revient), Bluetooth, heure, son.
const LABO = { nom: "Home Pi Lab", noeuds: { forge: { nom: "Pi One", numero: 1 }, vault: { nom: "Pi Two", numero: 2 }, pulse: { nom: "Pi Three", numero: 3 } } };
function laboDemo() {
  return { nom: LABO.nom, hub: "forge", noms: Object.fromEntries(Object.entries(LABO.noeuds).map(([k, v]) => [k, v.nom])),
    numeros: Object.fromEntries(Object.entries(LABO.noeuds).map(([k, v]) => [k, v.numero])), anciens_noms_hote: [] };
}
const DEMANDES = DEMO_Q.get("appairage") === "1" ? [{ nom: "raspberrypi", ip: "192.168.1.57", expire_s: 280 }] : [];
const WIFI = {
  actif: true, filaire: DEMO_Q.get("wifi") === "filaire", connecte: "MensHome5GHz", bascule: null, fin: 0,
  reseaux: [
    ["MensHome5GHz", 78, "WPA2", 5180], ["MensHome", 64, "WPA2", 2437], ["Livebox-A1B2", 41, "WPA2", 2412],
    ["Freebox-7C21", 33, "WPA3", 5500], ["Café ☕ invités", 22, "Ouvert", 2462], ["Bbox-3F9E2A", 15, "WPA2", 2437],
  ],
  enregistres: [{ nom: "MensHome5GHz", uuid: "4f6a1c2e-1b7d-4c55-9a3e-0d8e2b7c1a01", ssid: "MensHome5GHz" }, { nom: "preconfigured", uuid: "9b2d7e40-5c1a-4e8f-b6d3-71a2c4e9f002", ssid: "MensHome" }],
};
function wifiEtat() {
  const b = WIFI.bascule;
  if (b && b.etat === "en_cours" && Date.now() >= WIFI.fin) {
    if (b.ssid === "Freebox-7C21") Object.assign(b, { etat: "echec", raison: "mot_de_passe", raison_texte: "mot de passe incorrect ?", message: `Échec, retour sur ${b.ancien} : mot de passe incorrect ?` });
    else {
      Object.assign(b, { etat: "ok", message: `Connecté à ${b.ssid}` });
      WIFI.connecte = b.ssid;
      if (!WIFI.enregistres.some((x) => x.ssid === b.ssid)) WIFI.enregistres.push({ nom: b.ssid, uuid: `d3m0${id8()}-0000-4000-8000-000000000000`, ssid: b.ssid });
      notifier(b.message, "ok");
    }
  } else if (b && b.etat === "en_cours" && Date.now() >= WIFI.fin - 3000) b.etape = "verification";
  const enr = WIFI.enregistres.map((c) => ({ ...c, actif: c.ssid === WIFI.connecte && WIFI.actif, auto: true }));
  return {
    present: true, actif: WIFI.actif, connecte: WIFI.actif ? WIFI.connecte : null, connexion: WIFI.connecte,
    reseaux: WIFI.actif ? WIFI.reseaux.map(([ssid, signal, sec, f]) => ({ ssid, signal, securite: sec, pris_en_charge: true, cle: sec === "Ouvert" ? "aucune" : "wpa-psk", frequence_mhz: f, bande: f > 4900 ? "5 GHz" : "2,4 GHz", en_cours: ssid === WIFI.connecte, enregistre: enr.some((c) => c.ssid === ssid) }))
      .sort((a, b) => (b.en_cours - a.en_cours) || (b.signal - a.signal)) : [],
    enregistres: enr, filaire: WIFI.filaire, coupure_possible: WIFI.filaire,
    coupure_raison: WIFI.filaire ? null : "Le Wi-Fi est le seul lien de ce Pi avec la maison : le couper le rendrait injoignable.",
    bascule: b ? { ...b } : null, bascule_en_cours: Boolean(b && b.etat === "en_cours"),
  };
}
function wifiConnecter(c) {
  if (WIFI.bascule?.etat === "en_cours") return err(409, "occupe", "Un changement de réseau est déjà en cours.");
  let ssid = c.ssid;
  if (c.uuid) { const e = WIFI.enregistres.find((x) => x.uuid === c.uuid); if (!e) return err(404, "uuid", "Réseau enregistré inconnu."); ssid = e.ssid; }
  if (!ssid) return err(400, "ssid", "Nom de réseau (SSID) invalide.");
  if (ssid === WIFI.connecte) return err(409, "deja", `Le Pi est déjà connecté à ${ssid}.`);
  const r = WIFI.reseaux.find((x) => x[0] === ssid);
  const connu = WIFI.enregistres.some((x) => x.ssid === ssid);
  if (r && r[2] !== "Ouvert" && !connu && !c.uuid && !(typeof c.mot_de_passe === "string" && c.mot_de_passe.length >= 8 && c.mot_de_passe.length <= 63)) return err(400, "mot_de_passe", "Le mot de passe Wi-Fi fait entre 8 et 63 caractères.");
  WIFI.bascule = { ssid, ancien: WIFI.connecte, t: maintenant(), etat: "en_cours", etape: "activation", message: `Connexion à ${ssid} en cours…` };
  WIFI.fin = Date.now() + (ssid === "Freebox-7C21" ? 9000 : 7000);
  return ok({ ssid, ancien: WIFI.connecte, en_cours: true, delai_s: 60 });
}
const BT = {
  actif: true, rechercheFin: 0, appairage: null,
  appareils: [
    { mac: "98:7A:14:3C:51:0E", nom: "Manette Xbox", type: "manette", appaire: true, connecte: true, confiance: true, batterie: 80 },
    { mac: "F8:5C:7D:21:9A:44", nom: "JBL Flip 6", type: "enceinte", appaire: true, connecte: false, confiance: true, batterie: null },
  ],
  proches: [
    { mac: "E4:17:D8:6B:02:91", nom: "Clavier Logitech K380", type: "clavier", code: "482917", etape: "taper" },
    { mac: "5C:E9:1E:A0:33:7B", nom: "iPhone de Justin", type: "telephone", code: "715204", etape: "confirmer" },
    { mac: "0C:3B:50:8E:61:D2", nom: "AirPods Pro", type: "casque" },
    { mac: "C8:2E:18:44:9F:10", nom: "", type: "autre" },
  ],
};
const BT_TYPES = { manette: ["Manette", "gamepad-2"], clavier: ["Clavier", "keyboard"], souris: ["Souris", "mouse"], casque: ["Casque", "headphones"], enceinte: ["Enceinte", "speaker"], telephone: ["Téléphone", "smartphone"], autre: ["Appareil", "bluetooth"] };
function btEtat() {
  const recherche = Date.now() < BT.rechercheFin;
  const vus = BT.rechercheFin ? BT.proches : [];
  const liste = [...BT.appareils, ...vus.filter((p) => !BT.appareils.some((a) => a.mac === p.mac)).map((p) => ({ ...p, appaire: false, connecte: false, confiance: false, batterie: null }))];
  return {
    present: true, actif: BT.actif, mac: "D8:3A:DD:5E:10:42", recherche, recherche_reste_s: recherche ? Math.ceil((BT.rechercheFin - Date.now()) / 1000) : 0,
    appareils: BT.actif ? liste.map((a) => ({ mac: a.mac, nom: a.nom || "Appareil sans nom", anonyme: !a.nom, type: a.type, type_nom: BT_TYPES[a.type][0], icone: BT_TYPES[a.type][1], appaire: a.appaire, connecte: a.connecte, confiance: a.confiance, batterie: a.batterie })) : [],
    appairage: BT.appairage ? { ...BT.appairage } : null,
  };
}
function btAppairer(mac) {
  const p = BT.proches.find((x) => x.mac === mac);
  if (!p) return err(404, "mac", "Appareil introuvable : relance une recherche.");
  const nom = p.nom || mac;
  BT.appairage = { mac, nom, etat: "en_cours", code: null, message: `Appairage avec ${nom}…` };
  setTimeout(() => {
    if (p.etape) BT.appairage = { mac, nom, etat: p.etape, code: p.code, message: p.etape === "taper" ? `Tape ${p.code} sur l'appareil, puis Entrée.` : `Vérifie que l'appareil affiche ${p.code} et confirme dessus.` };
  }, 1500);
  setTimeout(() => {
    BT.appairage = { mac, nom, etat: "ok", code: null, message: `${nom} est appairé et connecté.` };
    BT.appareils.push({ mac, nom: p.nom || "Appareil sans nom", type: p.type, appaire: true, connecte: true, confiance: true, batterie: null });
    notifier(`Bluetooth : ${nom} appairé`, "ok");
  }, p.etape ? 7000 : 3500);
  return ok(btEtat());
}
const FUSEAUX = ["Africa/Casablanca", "America/Guadeloupe", "America/Martinique", "America/Montreal", "America/New_York", "America/Cayenne", "Asia/Tokyo", "Atlantic/Reunion", "Europe/Berlin", "Europe/Brussels", "Europe/Lisbon", "Europe/London", "Europe/Madrid", "Europe/Paris", "Europe/Rome", "Europe/Zurich", "Indian/Mayotte", "Pacific/Noumea", "Pacific/Tahiti", "UTC"];
let FUSEAU = "Europe/Paris";
const SON = { sortie: "pw:52", volume: 45, sorties: [{ id: "pw:48", nom: "HDMI 1 (écran du salon)" }, { id: "pw:52", nom: "JBL Flip 6" }] };
function generalDemo() {
  return {
    labo: laboDemo(), nom_hote: "forge", ip: "192.168.1.41", adresse: "http://forge.local/",
    heure: { fuseau: FUSEAU, fuseau_defini: true, defaut: "Europe/Paris", heure: new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }), decalage: "+0100" },
    son: { present: true, systeme: "pipewire", sorties: SON.sorties.map((x) => ({ ...x, defaut: x.id === SON.sortie, volume: SON.volume })), sortie: SON.sortie, volume: SON.volume, volume_reglable: true },
    ecran,
  };
}
function nomDemo(c) {
  if (c.labo !== undefined) { if (!String(c.labo).trim()) return err(400, "labo", "Le nom ne peut pas être vide."); LABO.nom = String(c.labo).trim().slice(0, 40); }
  if (c.nom !== undefined) {
    const cible = c.noeud || "forge";
    if (!LABO.noeuds[cible]) return err(404, "noeud", "Pi inconnu dans le labo.");
    if (!String(c.nom).trim()) return err(400, "nom", "Le nom ne peut pas être vide.");
    LABO.noeuds[cible].nom = String(c.nom).trim().slice(0, 32);
  }
  const plus = {};
  if (c.hote !== undefined) {
    if (!/^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$/.test(c.hote || "")) return err(400, "hote", "Nom d'hôte : lettres minuscules sans accent, chiffres et tirets seulement (pas au début ni à la fin).");
    Object.assign(plus, { nom_hote: c.hote, adresse: `http://${c.hote}.local/`, ip: "192.168.1.41", redemarrage: true });
  }
  return ok({ labo: LABO.nom, nom: c.nom !== undefined ? LABO.noeuds[c.noeud || "forge"].nom : undefined, ...plus, etat_labo: laboDemo() });
}

// --------------------------------------------------------------------- Agents + Bureaux de Claude (1.0.19)
const MASQUE_AG = "••••••";
let AGENTS = [
  { id: "ag1", nom: "Docteur", couleur: "#7FBF8E", perso: { peau: "#E8B98A", cheveux: "#5A3A22", vetements: "#7FBF8E", accessoire: "lunettes" }, bureau: { decor: "ecran" }, personnalite: "rassurant, méthodique", phrases: [],
    run: { etat: "travaille", tache: "Vérifie la température de la tour et dis-moi si tout va bien.", progression: 55, resultat: null, etapes: ["consulte : état actuel de la tour"], fin: null } },
  { id: "ag2", nom: "Scout", couleur: "#5AA9E6", perso: { peau: "#C68642", cheveux: "#2b2f3b", vetements: "#5AA9E6", accessoire: "casquette" }, bureau: { decor: "plante" }, personnalite: "curieux", phrases: [],
    run: { etat: "besoin_aide", tache: "Faut-il mettre à jour les applications maintenant ?", progression: 100, resultat: "3 mises à jour sont disponibles. Faut-il les installer tout de suite ou attendre la nuit ? À toi de décider.", etapes: ["consulte : applications installées"], fin: maintenant() - 40 } },
  { id: "ag3", nom: "Pixel", couleur: "#E8A33D", perso: { peau: "#F2D2B6", cheveux: "#C9A24B", vetements: "#E8A33D", accessoire: "casque" }, bureau: { decor: "ecran" }, personnalite: "taquin", phrases: ["Au boulot !"],
    run: { etat: "en_attente", tache: null, progression: 0, resultat: null, etapes: [], fin: null } },
];
const AGENTS_CERVEAU = { mode: "local", modele: "llama3.2:3b", online_base: "", online_cle_definie: false, online_actif: false, ollama_installe: true };
const AGENTS_CANAL = { actif: true, sujet: "ydz-claude-demo7h2k9x", serveur: "https://ntfy.sh", cle: "demo-cle-canal-claude-ne-pas-utiliser-123456", cle_definie: true, erreur: null };
const AGENTS_BUREAUX = { theme: "auto", dialogue_vitesse: 45, dialogue_taille: 20, panneau: true, boite: true };
const AGENTS_DISTANTS = [{ nom: "Claude-Recherche", couleur: "#B57BE0", tache: "Compare deux modpacks Minecraft", etape: "lecture des sources", progression: 70, termine: false, distant: true }];
const CAPACITES_AG = [["etat_tour", "état actuel de la tour (processeur, température, disque)"], ["noeuds", "liste des nœuds du labo et leur état"], ["alertes", "alertes et notifications récentes"], ["apps", "applications installées et leur état"], ["reseau", "réseau de la maison et dernier test de débit"], ["journal", "dernières actions enregistrées"], ["agenda", "rendez-vous du jour"]].map(([id, nom]) => ({ id, nom }));
function agentPublic(a) { return { id: a.id, nom: a.nom, couleur: a.couleur, perso: a.perso, bureau: a.bureau, personnalite: a.personnalite, phrases: a.phrases, run: a.run }; }
function agentsSnapshot() {
  return { equipe: AGENTS.map((a) => ({ id: a.id, nom: a.nom, couleur: a.couleur, perso: a.perso, bureau: a.bureau, run: a.run })),
    distants: AGENTS_DISTANTS, bureaux: AGENTS_BUREAUX,
    cerveau: { mode: AGENTS_CERVEAU.mode, modele: AGENTS_CERVEAU.modele, en_ligne_actif: AGENTS_CERVEAU.online_actif, ollama_installe: AGENTS_CERVEAU.ollama_installe }, canal_actif: AGENTS_CANAL.actif };
}
function agentsStatus(avecSecret) {
  const canal = { actif: AGENTS_CANAL.actif, sujet: AGENTS_CANAL.sujet, serveur: AGENTS_CANAL.serveur, cle_definie: true, erreur: null };
  if (avecSecret) canal.cle = AGENTS_CANAL.cle;
  return { equipe: AGENTS.map(agentPublic), distants: AGENTS_DISTANTS, cerveau: AGENTS_CERVEAU, canal, bureaux: AGENTS_BUREAUX, capacites: CAPACITES_AG };
}
// Petite tâche simulée : l'agent « travaille » puis « termine » (comme le vrai, mais sans modèle).
function lancerTacheAgent(a, tache) {
  a.run = { etat: "travaille", tache, progression: 10, resultat: null, etapes: [], fin: null };
  const etapes = ["consulte : état actuel de la tour", "consulte : alertes et notifications récentes"];
  etapes.forEach((e, i) => setTimeout(() => { if (a.run.etat === "travaille") { a.run.etapes.push(e); a.run.progression = 30 + i * 30; diffuser("surEtat", etat()); } }, 900 * (i + 1)));
  setTimeout(() => {
    a.run = { etat: "termine", tache, progression: 100, resultat: "D'après l'état de la tour, tout va bien : température correcte, aucun disque plein, aucune alerte en cours.", etapes, fin: maintenant() };
    notifier(`${a.nom} a terminé sa tâche.`, "ok"); diffuser("surEtat", etat());
  }, 3200);
}

function router(methode, chemin, corps) {
  const [voie, requete = ""] = chemin.split("?");
  const q = new URLSearchParams(requete);
  const p = voie.replace(/^\/api\//, "").split("/");
  const G = methode === "GET", P = methode === "POST";
  const noeudStockage = () => corps.noeud || q.get("noeud") || "vault";

  if (G && voie === "/api/etat") return lu(etat());
  if (G && voie === "/api/ping") return lu({ ydz: 1, nom: "forge", role: "forge", hub: true, version: D.etat.version });
  if (G && voie === "/api/noeuds") return lu({ noeuds: etat().noeuds });
  if (G && voie === "/api/systeme") return lu(D.systeme);
  if (G && voie === "/api/taches") return lu({ taches: taches.map(vueTache) });
  if (G && p[0] === "taches" && p[1]) {
    const t = taches.find((x) => x.id === p[1]);
    return t ? lu({ tache: { ...vueTache(t), journal: t.complet }, noeud: t.noeud }) : err(404, "tache", "Cette tâche n'existe plus.");
  }
  if (G && voie === "/api/diagnostic") {
    const n = q.get("noeud") || "forge";
    if (horsLigne[n]) return err(503, "hors_ligne", `${n.toUpperCase()} est hors ligne.`);
    return D.diagnostic[n] ? lu(D.diagnostic[n]) : err(404, "noeud", "Ce nœud n'existe pas.");
  }

  // Authentification : la démonstration se comporte comme l'écran de la tour (pas de code demandé).
  if (G && voie === "/api/auth") {
    if (window.YDZ_VERROU_DEMO && !DEMO_CONNECTE) return lu({ pin_defini: true, pin_court: false, pin_min: 6, local: false, connecte: false, attente_s: 0, verrou: { inactivite_min: 15, fond_min: 5 } });
    return lu({ pin_defini: true, pin_court: DEMO_Q.get("pincourt") === "1", pin_min: 6, local: !window.YDZ_VERROU_DEMO, connecte: true, attente_s: 0, reste_s: 900, verrou: { inactivite_min: 15, fond_min: 5 } });
  }
  if (P && voie === "/api/auth/connexion") { DEMO_CONNECTE = true; return ok({ jeton: "demo", expire_s: 43200 }); }
  if (P && voie === "/api/auth/deconnexion") return ok();
  if (P && voie === "/api/auth/deconnexion-partout") return ok();
  if (P && voie === "/api/auth/verrou") return ok({ verrou: { inactivite_min: corps.inactivite_min ?? 15, fond_min: corps.fond_min ?? 5 } });
  if (P && voie === "/api/auth/pin") {
    const pin = String(corps.pin || "");
    if (!/^\d{4,8}$/.test(pin)) return err(400, "pin_invalide", "Le code doit faire 4 à 8 chiffres.");
    if (/^(\d)\1+$/.test(pin) || "0123456789876543210".includes(pin)) return err(400, "pin_invalide", "Ce code est trop simple. Choisis-en un autre.");
    return ok();
  }

  // Ventilateur
  if (G && voie === "/api/ventilo") return lu(ventilo);
  if (P && voie === "/api/ventilo") {
    const m = ventilo.modes.find((x) => x.id === corps.mode);
    if (!m) return err(400, "mode", "Mode inconnu.");
    Object.assign(ventilo, { mode: m.id, mode_nom: m.nom, courbe: m.courbe });
    tic();
    return ok({ ventilo });
  }
  if (P && voie === "/api/ventilo/test") { testVentiloFin = maintenant() + (corps.secondes || 5); tic(); return ok(); }

  // 1.0.12 : apps et accès
  if (G && voie === "/api/acces") return lu(accesDemo());
  if (P && voie === "/api/acces") {
    if (corps.https === true && DEMO_Q.get("https") === "indispo") return err(409, "https_indispo", "Les certificats HTTPS ne sont pas activés dans la console Tailscale. Suis les 3 étapes ci-dessous, puis réessaie.");
    if (typeof corps.https === "boolean") { ACCES.actif = corps.https; noterAction(corps.https ? "Adresses HTTPS activées" : "Adresses HTTPS désactivées"); }
    return ok(accesDemo());
  }
  // Espace visiteurs (1.0.16)
  if (G && voie === "/api/visiteur") return lu(visiteurDemo(true));
  if (P && voie === "/api/visiteur") {
    if ("actif" in corps) {
      if (corps.actif && !ACCES.actif) return err(409, "indispo", "Les adresses HTTPS ne sont pas activées (Accès à distance).");
      VISITEUR.actif = Boolean(corps.actif);
      noterAction(VISITEUR.actif ? "Espace visiteurs ouvert" : "Espace visiteurs fermé");
    } else if ("app" in corps) {
      const a = String(corps.adresse || "").trim();
      if (a) VISITEUR.adresses[corps.app] = a; else delete VISITEUR.adresses[corps.app];
    }
    return ok(visiteurDemo(true));
  }
  if (P && voie === "/api/visiteur/lien") {
    VISITEUR.secret = Array.from({ length: 24 }, () => "abcdefghijklmnopqrstuvwxyz0123456789"[Math.floor(Math.random() * 36)]).join("");
    noterAction("Nouveau lien de l'espace visiteurs");
    return ok(visiteurDemo(true));
  }
  if (P && voie === "/api/visiteur/couper") {
    VISITEUR.actif = false;
    noterAction("Espace visiteurs coupé");
    return ok(visiteurDemo(true));
  }

  // « Perso » (1.0.18) : agenda, rappels, Wi-Fi invités
  if (G && voie === "/api/agenda") return lu(agendaComplet());
  if (P && voie === "/api/agenda") {
    const a = corps.action;
    if (a === "ajouter") {
      let url = String(corps.url || "").trim();
      if (url.toLowerCase().startsWith("webcal://")) url = "https://" + url.slice(9);
      if (!/^https:\/\/[A-Za-z0-9.\-]+(:\d+)?(\/\S*)?$/.test(url)) return err(400, "url", "Adresse de calendrier invalide : colle un lien iCloud « Calendrier public ».");
      if (AGENDA_CALS.length >= 5) return err(409, "trop", "Au plus 5 calendriers.");
      const hote = url.split("/")[2] || "";
      AGENDA_CALS.push({ id: id8(), nom: corps.nom || hote, hote, url, maj: maintenant(), erreur: null, evenements_n: Math.floor(alea(3, 30)) });
      noterAction("Calendrier ajouté à l'agenda");
      return ok(agendaComplet());
    }
    if (a === "retirer") { AGENDA_CALS = AGENDA_CALS.filter((c) => c.id !== corps.id); return ok(agendaComplet()); }
    if (a === "alerte") {
      if (typeof corps.minutes !== "number" || corps.minutes < 0 || corps.minutes > 120) return err(400, "alerte_min", "Alerte « bientôt » : de 0 à 120 minutes.");
      AGENDA_ALERTE = corps.minutes; return ok(agendaComplet());
    }
    return err(400, "action", "Action inconnue (ajouter, retirer, alerte).");
  }
  if (G && voie === "/api/rappels") return lu({ rappels: RAPPELS });
  if (P && voie === "/api/rappels") {
    const a = corps.action;
    if (a === "ajouter") {
      const t = String(corps.texte || "").replace(/[\x00-\x1f\x7f]/g, "").trim().slice(0, 120);
      if (!t) return err(400, "texte", "Écris le rappel.");
      if (RAPPELS.length >= 20) return err(409, "trop", "Trop de rappels (20 au plus).");
      RAPPELS.push({ id: id8(), texte: t, fait: false, cree: maintenant() });
      return ok({ rappels: RAPPELS });
    }
    if (a === "basculer") { const r = RAPPELS.find((x) => x.id === corps.id); if (!r) return err(404, "rappel", "Rappel inconnu."); r.fait = !r.fait; return ok({ rappels: RAPPELS }); }
    if (a === "retirer") { RAPPELS = RAPPELS.filter((x) => x.id !== corps.id); return ok({ rappels: RAPPELS }); }
    if (a === "vider") { RAPPELS = RAPPELS.filter((x) => !x.fait); return ok({ rappels: RAPPELS }); }
    return err(400, "action", "Action inconnue.");
  }
  if (G && voie === "/api/wifi-invite") return lu(wifiInviteStatut());
  if (G && voie === "/api/wifi-invite/qr") {
    if (!WIFI_INV.ssid || (WIFI_INV.securite !== "nopass" && !WIFI_INV.mdp)) return err(409, "non_defini", "Renseigne d'abord le nom du réseau et le mot de passe.");
    return lu({ ssid: WIFI_INV.ssid, securite: WIFI_INV.securite, svg: qrDemoSvg(chargeWifi()) });
  }
  if (P && voie === "/api/wifi-invite") {
    if ("ssid" in corps) {
      if (!corps.ssid) { WIFI_INV.ssid = ""; WIFI_INV.mdp = ""; }
      else if (typeof corps.ssid !== "string" || corps.ssid.length > 32) return err(400, "ssid", "Nom de réseau invalide.");
      else WIFI_INV.ssid = corps.ssid.trim();
    }
    if ("securite" in corps) { if (!["WPA", "nopass"].includes(corps.securite)) return err(400, "securite", "Sécurité invalide."); WIFI_INV.securite = corps.securite; }
    if ("mdp" in corps) { if (!corps.mdp) WIFI_INV.mdp = ""; else if (corps.mdp.length > 63) return err(400, "mdp", "Mot de passe trop long."); else WIFI_INV.mdp = corps.mdp; }
    if ("ecran" in corps) { if (typeof corps.ecran !== "boolean") return err(400, "ecran", "Champ « ecran » invalide."); WIFI_INV.ecran = corps.ecran; }
    noterAction("Réglage du Wi-Fi invités");
    return ok(wifiInviteStatut());
  }

  // iPhone (1.0.20) : notifications iOS + musique (BLE, affichage seul)
  if (G && voie === "/api/iphone") return lu(iphoneDemo());
  if (P && voie === "/api/iphone") {
    if ("commande" in corps) {
      if (!["lecture", "pause", "lecture_pause", "suivant", "precedent", "volume_plus", "volume_moins"].includes(corps.commande)) return err(400, "commande", "Commande média inconnue.");
      if (corps.commande === "lecture_pause") IPHONE.media.en_lecture = !IPHONE.media.en_lecture, IPHONE.media.etat = IPHONE.media.en_lecture ? "lecture" : "pause";
      if (corps.commande === "suivant") Object.assign(IPHONE.media, { titre: "Save Your Tears", artiste: "The Weeknd", album: "After Hours", en_lecture: true, etat: "lecture" });
      if (corps.commande === "precedent") Object.assign(IPHONE.media, { titre: "In Your Eyes", artiste: "The Weeknd", album: "After Hours", en_lecture: true, etat: "lecture" });
      return ok({ commande: corps.commande });
    }
    if (corps.effacer === true) { IPHONE.notifs = []; return ok(iphoneDemo()); }
    if ("actif" in corps) { if (typeof corps.actif !== "boolean") return err(400, "actif", "Champ « actif » invalide."); IPHONE.actif = corps.actif; noterAction(corps.actif ? "Notifications iPhone activées" : "Notifications iPhone coupées"); }
    if ("mac" in corps && corps.mac && !/^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(corps.mac)) return err(400, "mac", "Adresse Bluetooth invalide.");
    return ok(iphoneDemo());
  }

  if (G && voie === "/api/historique") return historiqueDemo(q);
  if (G && voie === "/api/activite") return activiteDemo(q);
  if (P && voie === "/api/apps/maj/verifier") {
    noterAction("Recherche de mises à jour des apps");
    return ok({ noeuds: ORDRE.filter((n) => Object.keys(inst[n]).length).map((n) => ({ noeud: n, ok: true, tache: vueTache(tache({ type: "maj_apps", titre: "Recherche de mises à jour des apps", noeud: n },
      Object.keys(inst[n]).map((id) => [400, `Vérification de ${app(id).nom}…`]), () => {
        const t = Object.keys(inst[n]).filter((id) => MAJ_APPS[`${n}:${id}`]).map((id) => app(id).nom);
        return t.length ? `Mise à jour disponible : ${t.join(", ")}.` : "Toutes les apps sont à jour.";
      })) })) });
  }
  if (P && voie === "/api/apps/maj/tout") {
    const cibles = ORDRE.filter((n) => Object.keys(inst[n]).some((id) => MAJ_APPS[`${n}:${id}`]));
    if (!cibles.length) return err(409, "rien_a_faire", "Aucune app à mettre à jour.");
    noterAction("Mise à jour de toutes les apps");
    return ok({ noeuds: cibles.map((n) => {
      const ids = Object.keys(inst[n]).filter((id) => MAJ_APPS[`${n}:${id}`]);
      return { noeud: n, ok: true, tache: vueTache(tache({ type: "app", titre: `Mise à jour de ${ids.length} app${ids.length > 1 ? "s" : ""}`, noeud: n },
        ids.flatMap((id) => [[500, `Téléchargement de la nouvelle version de ${app(id).nom}…`], [1400, ` ydz-${id} Pulled`], [500, `Redémarrage de ${app(id).nom}…`], [300, "Ancienne image retirée"]]),
        () => { ids.forEach((id) => { delete MAJ_APPS[`${n}:${id}`]; }); return `${ids.map((id) => app(id).nom).join(", ")} à jour.`; })) };
    }) });
  }

  // Applications
  if (G && voie === "/api/apps") return lu({ catalogue: CATALOGUE.map((a) => ({ ...a, installations: installations(a, false) })), noeuds: noeudsApps(), erreurs: [] });
  if (p[0] === "apps" && p[1]) {
    const a = app(p[1]);
    if (!a) return err(404, "app_inconnue", "Cette application n'est pas au catalogue.");
    if (G && !p[2]) return lu({ ...a, installations: installations(a, true), noeuds: noeudsApps() });
    if (G && p[2] === "journal") return journalApp(a.id, q.get("noeud"));
    if (P) return actionApp(a.id, p[2], corps);
  }

  // Stockage
  if (G && voie === "/api/stockage") {
    const n = noeudStockage();
    if (horsLigne[n]) return err(503, "hors_ligne", `${n.toUpperCase()} est hors ligne.`);
    return D.stockage[n] ? lu(stockage(n)) : err(404, "noeud", "Ce nœud n'existe pas.");
  }
  if (P && voie === "/api/stockage/liberer/apercu") return lu(apercuLiberer(noeudStockage()));
  if (P && voie === "/api/stockage/liberer") {
    const n = noeudStockage(), sys = SYSTEME_DEMO[n] || SYSTEME_DEMO.forge;
    const postes = Array.isArray(corps.postes) ? corps.postes.filter((p) => ["docker", "sauvegardes", "apt", "journaux"].includes(p)) : [];
    if (!postes.length) return err(400, "postes", "Indique au moins un poste à libérer.");
    const gagne = postes.reduce((acc, id) => acc + (sys.postes.find((p) => p.id === id)?.recuperable || 0), 0);
    const t = tache({ type: "stockage", titre: "Libération d'espace sur le disque système", noeud: n }, [
      [500, "> docker image prune -f"],
      [500, `Espace récupéré sur Docker.`],
      [400, "Vieilles sauvegardes supprimées."],
    ], () => `Espace libéré : ${(gagne / 1e9).toFixed(1).replace(".", ",")} Go.`);
    return ok({ tache: vueTache(t) });
  }
  if (P && voie === "/api/stockage/formater/preparer") return preparer(corps, false);
  if (P && voie === "/api/stockage/miroir/preparer") return preparer(corps, true);
  if (P && voie === "/api/stockage/confirmer") return confirmer(corps);
  if (P && voie === "/api/stockage/remonter") {
    const s = D.stockage[noeudStockage()];
    const d = s.disques.find((x) => x.chemin === corps.disque);
    if (!d || !d.a_remonter) return err(409, "disque", "Ce disque n'a pas été préparé par YDZ : rien à remonter.");
    const nom = d.a_remonter;
    Object.assign(d, { nom, montage: `/srv/ydz/${nom}`, a_remonter: null, total: Math.round(d.taille * 0.983), libre: Math.round(d.taille * 0.41) });
    d.partitions[0].montage = [d.montage];
    return ok({ nom, montage: d.montage });
  }
  if (P && voie === "/api/stockage/samba/mot-de-passe") {
    if (String(corps.mot_de_passe || "").length < 8) return err(400, "mot_de_passe", "Le mot de passe doit faire 8 caractères au moins.");
    D.stockage[noeudStockage()].samba.mot_de_passe_defini = true;
    return ok();
  }
  if (P && voie === "/api/stockage/partages") {
    const n = noeudStockage(), s = D.stockage[n];
    const nom = String(corps.nom || "");
    if (!/^[a-z0-9][a-z0-9_-]{0,30}$/.test(nom)) return err(400, "nom", "Nom de partage refusé : lettres minuscules, chiffres et tirets seulement.");
    if (s.partages.some((x) => x.nom === nom)) return err(409, "nom", `Le partage « ${nom} » existe déjà.`);
    if (!s.disques.some((d) => d.nom === corps.disque && d.montage)) return err(400, "disque", "Choisis un disque préparé et monté.");
    if (!s.acces.some((a) => a.id === corps.acces)) return err(400, "acces", "Type d'accès inconnu.");
    if (corps.acces === "prive" && !s.samba.mot_de_passe_defini) return err(409, "mot_de_passe_requis", "Choisis d'abord le mot de passe des partages.");
    const partage = { nom, disque: corps.disque, chemin: `/srv/ydz/${corps.disque}/partages/${nom}`, acces: corps.acces, adresse: `\\\\${n}\\${nom}`, adresse_mac: `smb://${n}.local/${nom}` };
    s.partages.push(partage);
    return ok({ partage });
  }
  if (P && voie === "/api/stockage/partages/supprimer") {
    const s = D.stockage[noeudStockage()];
    if (!s.partages.some((x) => x.nom === corps.nom)) return err(404, "partage", "Ce partage n'existe pas.");
    s.partages = s.partages.filter((x) => x.nom !== corps.nom);
    return ok();
  }

  // Wake-on-LAN
  if (G && voie === "/api/wol") return lu(D.wol);
  if (P && voie === "/api/wol") {
    if (!/^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$/i.test(corps.mac || "")) return err(400, "mac", "Adresse MAC invalide. Exemple : 20:0B:74:6A:0B:6C.");
    if (!String(corps.nom || "").trim()) return err(400, "nom", "Donne un nom à cette machine.");
    const cible = { id: `m${D.wol.cibles.length}`, nom: corps.nom.trim(), mac: corps.mac.toUpperCase(), diffusion: corps.diffusion || "192.168.1.255", ip: corps.ip || null, fixe: false, en_ligne: corps.ip ? false : null };
    D.wol.cibles.push(cible);
    return ok({ cible });
  }
  if (P && p[0] === "wol" && p[2]) {
    const c = D.wol.cibles.find((x) => x.id === p[1]);
    if (!c) return err(404, "cible", "Cette machine n'est pas enregistrée.");
    if (p[2] === "reveiller") {
      if (c.en_ligne === false) setTimeout(() => { c.en_ligne = true; notifier(`${c.nom} est en ligne.`); }, 6000);
      return ok({ message: `Signal de réveil envoyé à ${c.nom}.` });
    }
    if (p[2] === "supprimer") {
      if (c.fixe) return err(403, "cible", "Cette machine se règle dans ydz.conf (clés wol_*).");
      D.wol.cibles = D.wol.cibles.filter((x) => x !== c);
      return ok();
    }
  }

  // Réseau de la maison
  if (G && voie === "/api/appareils") return lu({ appareils: listeAppareils(), maj: DEMO_DEBUT, nouveaux: NOUVEAUX });
  if (P && p[0] === "appareils" && /^[0-9a-f:]{17}$/i.test(p[1] || "")) return routeAppareil(p[1], corps);
  if (G && voie === "/api/reseau") return lu({ reseau_maison: reseauDemo(), pihole: piholeDemo() });
  if (P && voie === "/api/reseau/debit") return lancerDebit();
  if (P && voie === "/api/reseau/controle") {
    if (corps.action === "lancer") return ok({ controle: controleDemo() });
    if ("intervalle_min" in corps) CTRL_REG.intervalle_min = corps.intervalle_min;
    if ("seuil_mbps" in corps) CTRL_REG.seuil_mbps = corps.seuil_mbps;
    return ok({ controle: controleDemo() });
  }

  // Fiabilité (1.0.11)
  if (G && voie === "/api/maj/sauvegardes") return lu({ sauvegardes: histoDemo(), version: D.etat.version });
  if (P && voie === "/api/maj/annuler") {
    const h = histoDemo();
    const x = h.find((y) => y.id === (corps.sauvegarde || h.find((z) => z.annulable)?.id));
    if (!x) return err(409, "annulation", "Sauvegarde inconnue.");
    if (!x.annulable) return err(409, "annulation", "Seul le dernier paquet installé (et pas encore annulé) peut être annulé. Annule d'abord les plus récents, un par un.");
    HISTO.find((y) => y.id === x.id).annulee = maintenant();
    return ok({ sauvegarde: x.id, nom: x.nom, version_avant: x.version_avant, restaures: x.remplaces, effaces: x.nouveaux, refuses: [], complet: x.complet });
  }
  if (G && voie === "/api/sauvegarde") return lu(sauvegardeDemo());
  if (P && voie === "/api/sauvegarde") return reglerSauvegarde(corps);
  if (P && voie === "/api/sauvegarde/lancer") return lancerSauvegarde();
  if (P && voie === "/api/sauvegarde/preparer-pc") {
    if (corps.nouveau_mot_de_passe) SAUV.mot_de_passe = Array.from({ length: 20 }, () => "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"[Math.floor(Math.random() * 56)]).join("");
    return ok({ script: scriptPC(SAUV.mot_de_passe), utilisateur: SAUV.utilisateur, partage: SAUV.partage, hote: SAUV.hote, dossier: "C:\\YDZ-Sauvegardes", ip_pi: "192.168.1.41", regle: "YDZ-SMB-forge" });
  }
  if (G && voie === "/api/notifications") return lu(notifsDemo());
  if (P && voie === "/api/notifications") return reglerNotifs(corps);
  if (P && voie === "/api/notifications/test") {
    const canal = corps.canal;
    if (canal === "discord" && !NOTIFS.discord) return err(400, "discord", "Colle d'abord l'adresse du webhook Discord.");
    const canaux = canal ? [canal] : [NOTIFS.discord && NOTIFS.discord_actif ? "discord" : null, NOTIFS.ntfy_actif ? "ntfy" : null].filter(Boolean);
    if (!canaux.length) return err(400, "canal", "Active d'abord Discord ou ntfy.");
    return ok({ resultats: Object.fromEntries(canaux.map((c) => [c, { ok: true, erreur: null }])) });
  }
  if (G && voie === "/api/minecraft") return lu({ minecraft: minecraftDemo() });

  // Agents + Bureaux de Claude (1.0.19)
  if (G && voie === "/api/agents") return lu(agentsStatus(true));   // démonstration = écran local : clé visible
  if (P && voie === "/api/agents") {
    const a = corps.action;
    if (a === "creer") {
      if (AGENTS.length >= 6) return err(409, "trop", "L'équipe est au complet (6 agents au plus).");
      const nom = String(corps.nom || "Agent").slice(0, 24) || "Agent";
      const couleur = ["#E5484D", "#4FC3C7", "#EC6FA0", "#8FBF3F"][AGENTS.length % 4];
      const ag = { id: "ag" + id8(), nom, couleur, perso: { peau: "#E8B98A", cheveux: "#5A3A22", vetements: couleur, accessoire: "aucun" }, bureau: { decor: "ecran" }, personnalite: "", phrases: [], run: { etat: "en_attente", tache: null, progression: 0, resultat: null, etapes: [], fin: null } };
      AGENTS.push(ag);
      return ok({ agent: agentPublic(ag) });
    }
    if (a === "configurer") {
      const ag = AGENTS.find((x) => x.id === corps.id);
      if (!ag) return err(404, "agent", "Agent inconnu.");
      if (corps.nom) ag.nom = String(corps.nom).slice(0, 24);
      if (/^#[0-9A-Fa-f]{6}$/.test(corps.couleur || "")) ag.couleur = corps.couleur;
      if (typeof corps.personnalite === "string") ag.personnalite = corps.personnalite.slice(0, 280);
      if (Array.isArray(corps.phrases)) ag.phrases = corps.phrases.slice(0, 8).map((p) => String(p).slice(0, 80));
      if (corps.perso && typeof corps.perso === "object") ag.perso = { ...ag.perso, ...corps.perso };
      if (corps.bureau && typeof corps.bureau === "object") ag.bureau = { ...ag.bureau, ...corps.bureau };
      return ok({ agent: agentPublic(ag) });
    }
    if (a === "supprimer") { AGENTS = AGENTS.filter((x) => x.id !== corps.id); return ok(); }
    if (a === "tache") {
      const ag = AGENTS.find((x) => x.id === corps.id);
      if (!ag) return err(404, "agent", "Agent inconnu.");
      if (ag.run?.etat === "travaille") return err(409, "occupe", "Cet agent travaille déjà.");
      const t = String(corps.tache || "").trim();
      if (!t) return err(400, "tache", "Écris une tâche pour l'agent.");
      lancerTacheAgent(ag, t.slice(0, 4000));
      return ok({ agent: ag.id, etat: ag.run.etat });
    }
    if (a === "annuler") { const ag = AGENTS.find((x) => x.id === corps.id); if (ag) ag.run = { etat: "en_attente", tache: null, progression: 0, resultat: null, etapes: [], fin: null }; return ok(); }
    if (a === "canal") {
      if (typeof corps.actif === "boolean") AGENTS_CANAL.actif = corps.actif;
      if (corps.rotation === true) { AGENTS_CANAL.cle = "demo-cle-" + id8() + id8(); AGENTS_CANAL.sujet = "ydz-claude-" + id8(); }
      if (corps.serveur) AGENTS_CANAL.serveur = String(corps.serveur).replace(/\/$/, "");
      return ok({ actif: AGENTS_CANAL.actif, sujet: AGENTS_CANAL.sujet, serveur: AGENTS_CANAL.serveur, cle_definie: true, cle: AGENTS_CANAL.cle, erreur: null });
    }
    if (a === "bureaux") {
      if (["auto", "jour", "nuit"].includes(corps.theme)) AGENTS_BUREAUX.theme = corps.theme;
      if (typeof corps.dialogue_vitesse === "number") AGENTS_BUREAUX.dialogue_vitesse = Math.max(5, Math.min(120, corps.dialogue_vitesse));
      if (typeof corps.dialogue_taille === "number") AGENTS_BUREAUX.dialogue_taille = Math.max(12, Math.min(40, corps.dialogue_taille));
      if (typeof corps.panneau === "boolean") AGENTS_BUREAUX.panneau = corps.panneau;
      if (typeof corps.boite === "boolean") AGENTS_BUREAUX.boite = corps.boite;
      return ok({ bureaux: AGENTS_BUREAUX });
    }
    return err(400, "action", "Action inconnue.");
  }
  if (P && voie === "/api/agents/cerveau") {
    if (corps.mode && !["local", "online"].includes(corps.mode)) return err(400, "mode", "Mode inconnu.");
    if (corps.mode) AGENTS_CERVEAU.mode = corps.mode;
    if (corps.modele) AGENTS_CERVEAU.modele = String(corps.modele).slice(0, 60);
    if (corps.online_base !== undefined) AGENTS_CERVEAU.online_base = String(corps.online_base).slice(0, 160);
    if (corps.online_cle !== undefined && corps.online_cle !== MASQUE_AG) AGENTS_CERVEAU.online_cle_definie = Boolean(corps.online_cle);
    if (typeof corps.online_actif === "boolean") {
      if (corps.online_actif && (!AGENTS_CERVEAU.online_base || !AGENTS_CERVEAU.online_cle_definie)) return err(400, "online_cle", "Renseigne l'adresse de l'API et la clé avant de l'activer.");
      AGENTS_CERVEAU.online_actif = corps.online_actif;
    }
    return ok({ mode: AGENTS_CERVEAU.mode, modele: AGENTS_CERVEAU.modele, online_base: AGENTS_CERVEAU.online_base, online_cle_definie: AGENTS_CERVEAU.online_cle_definie, online_actif: AGENTS_CERVEAU.online_actif, ollama_installe: AGENTS_CERVEAU.ollama_installe });
  }

  if (G && voie === "/api/maj/ydz") return lu(majYdzDemo());
  if (P && voie === "/api/maj/ydz/verifier") return verifierYdz();
  if (P && voie === "/api/maj/ydz/installer") return installerYdz();
  if (P && voie === "/api/maj/reglages") {
    if (typeof corps.auto_nuit !== "boolean") return err(400, "auto_nuit", "« auto_nuit » doit valoir vrai ou faux.");
    MAJY.auto_nuit = corps.auto_nuit;
    noterAction(corps.auto_nuit ? "Mises à jour de YDZ OS : installation automatique la nuit activée" : "Mises à jour de YDZ OS : installation automatique la nuit coupée");
    return ok(majYdzDemo());
  }
  if (P && voie === "/api/affichage/test-veille") { noterAction("Test de mise en veille de l'écran (10 s)"); return ok({ secondes: 10 }); }
  // Mises à jour
  if (G && voie === "/api/maj") return lu({ noeuds: D.maj.noeuds.map((m) => (horsLigne[m.noeud] ? { noeud: m.noeud, erreur: "hors ligne" } : m)) });
  if (P && (voie === "/api/maj/verifier" || voie === "/api/maj/appliquer")) {
    const cibles = (corps.noeuds || ORDRE).filter((n) => !horsLigne[n]);
    const nombre = { forge: 7, vault: 3, pulse: 0 };
    if (voie.endsWith("verifier")) {
      return ok({ noeuds: cibles.map((n) => ({
        noeud: n, ok: true,
        tache: vueTache(tache({ type: "maj", titre: "Recherche de mises à jour", noeud: n }, [[600, "Lecture des listes de paquets…"]], () => {
          const m = D.maj.noeuds.find((x) => x.noeud === n);
          m.paquets = m.fait ? 0 : nombre[n];
          m.liste = PAQUETS.slice(0, m.paquets).map(([paquet, version]) => ({ paquet, version }));
          m.derniere_verif = maintenant();
          return m.paquets ? `${m.paquets} mises à jour disponibles sur ${n.toUpperCase()}.` : `${n.toUpperCase()} est à jour.`;
        })),
      })) });
    }
    cibles.forEach((n, k) => {
      const m = D.maj.noeuds.find((x) => x.noeud === n);
      m.en_cours = true;
      m.journal = ["Lecture des listes de paquets…"];
      const lignes = (m.liste.length ? m.liste : [{ paquet: "base-files", version: "13.8" }]).map((x) => `Paramétrage de ${x.paquet} (${x.version}) …`);
      lignes.forEach((l, i) => setTimeout(() => m.journal.push(l), 800 * (i + 1)));
      setTimeout(() => {
        Object.assign(m, { en_cours: false, fait: true, redemarrage_requis: m.liste.some((x) => x.paquet.startsWith("linux-image")), paquets: 0, liste: [], derniere_maj: maintenant(), derniere_verif: maintenant() });
        m.journal.push("Mise à jour terminée.");
        notifier(`${n.toUpperCase()} est à jour.`);
      }, 800 * (lignes.length + 1) + k * 300);
    });
    return ok({ noeuds: cibles.map((n) => ({ noeud: n, ok: true })) });
  }

  // Réglages généraux (1.0.13)
  if (G && voie === "/api/general") return lu(generalDemo());
  if (P && voie === "/api/general/nom") return nomDemo(corps);
  if (G && voie === "/api/general/fuseaux") return lu({ fuseaux: FUSEAUX, actuel: FUSEAU });
  if (P && voie === "/api/general/heure") {
    if (!FUSEAUX.includes(corps.fuseau)) return err(400, "fuseau", "Fuseau horaire inconnu.");
    FUSEAU = corps.fuseau;
    return ok(generalDemo().heure);
  }
  if (P && voie === "/api/general/son") {
    if (corps.sortie !== undefined) { if (!SON.sorties.some((x) => x.id === corps.sortie)) return err(400, "sortie", "Sortie son inconnue."); SON.sortie = corps.sortie; }
    if (corps.volume !== undefined) { if (!(corps.volume >= 0 && corps.volume <= 100)) return err(400, "volume", "Le volume va de 0 à 100."); SON.volume = corps.volume; }
    return ok(generalDemo().son);
  }
  if (G && voie === "/api/wifi") return lu(wifiEtat());
  if (P && voie === "/api/wifi/scan") return ok(wifiEtat());
  if (P && voie === "/api/wifi/connecter") return wifiConnecter(corps);
  if (P && voie === "/api/wifi/oublier") {
    const e = WIFI.enregistres.find((x) => x.uuid === corps.uuid);
    if (!e) return err(404, "uuid", "Réseau enregistré inconnu.");
    if (e.ssid === WIFI.connecte) return err(409, "actif", "C'est le réseau utilisé en ce moment : connecte d'abord le Pi à un autre réseau.");
    WIFI.enregistres = WIFI.enregistres.filter((x) => x !== e);
    return ok({ ssid: e.ssid });
  }
  if (P && voie === "/api/wifi/radio") {
    if (corps.actif === false && !WIFI.filaire) return err(409, "seul_lien", "Le Wi-Fi est le seul lien de ce Pi avec la maison : le couper le rendrait injoignable. Branche d'abord un câble Ethernet relié à la box.");
    WIFI.actif = Boolean(corps.actif);
    return ok({ actif: WIFI.actif });
  }
  if (G && voie === "/api/bluetooth") return lu(btEtat());
  if (P && voie === "/api/bluetooth") { BT.actif = Boolean(corps.actif); return ok(btEtat()); }
  if (P && voie === "/api/bluetooth/scan") { BT.rechercheFin = Date.now() + 15000; return ok({ duree_s: 15 }); }
  if (P && voie === "/api/bluetooth/pin") return ok();
  if (P && voie === "/api/bluetooth/confirmer") return ok(btEtat());
  if (P && voie === "/api/bluetooth/appareil") {
    const a = BT.appareils.find((x) => x.mac === corps.mac);
    if (corps.action === "appairer") return btAppairer(corps.mac);
    if (!a) return err(404, "mac", "Appareil inconnu.");
    if (corps.action === "connecter") a.connecte = true;
    else if (corps.action === "deconnecter") a.connecte = false;
    else if (corps.action === "oublier") BT.appareils = BT.appareils.filter((x) => x !== a);
    else return err(400, "action", "Action inconnue.");
    return ok(btEtat());
  }
  if (P && p[0] === "appairage" && p[2] === "accepter") {
    const i = DEMANDES.findIndex((x) => x.nom === p[1]);
    if (i < 0) return err(404, "appairage_aucune", "Aucune demande d'appairage en cours.");
    DEMANDES.splice(i, 1);
    if (corps.remplace) { const h = LABO.noeuds[corps.remplace]; return ok({ noeud: p[1], remplace: corps.remplace, nom_affiche: h?.nom, nom_hote: corps.remplace, nom_hote_change: true }); }
    LABO.noeuds[p[1]] = { nom: "Pi Four", numero: 4 };
    return ok({ noeud: p[1], nom_affiche: "Pi Four" });
  }
  if (P && p[0] === "appairage" && p[2] === "refuser") { const i = DEMANDES.findIndex((x) => x.nom === p[1]); if (i >= 0) DEMANDES.splice(i, 1); return ok(); }

  // Écran et badges
  if (G && voie === "/api/ecran") return lu(ecran);
  if (P && voie === "/api/ecran/activite") return ok({ reveil: false });
  if (P && voie === "/api/affichage") {
    if (corps.curseur !== undefined && typeof corps.curseur !== "boolean") return err(400, "curseur", "Champ « curseur » invalide (vrai ou faux).");
    for (const k of Object.keys(DEMO_REG)) if (corps[k] !== undefined) DEMO_REG[k] = typeof corps[k] === "string" ? corps[k].trim() : corps[k];
    return ok(affichageDemo(etat().noeuds));
  }
  if (P && voie === "/api/ecran") {
    const avant = ecran.rotation;
    if (corps.luminosite !== undefined) {
      if (!(corps.luminosite >= 5 && corps.luminosite <= 100)) return err(400, "luminosite", "La luminosité va de 5 à 100.");
      ecran.luminosite = Math.round(corps.luminosite);
    }
    if (corps.veille_min !== undefined) ecran.veille_min = corps.veille_min;
    if (corps.rotation !== undefined) {
      if (!ecran.rotations.includes(corps.rotation)) return err(400, "rotation", "Rotation inconnue.");
      ecran.rotation = corps.rotation;
    }
    return ok({ redemarrage_kiosque: avant !== ecran.rotation, ecran });
  }
  if (P && voie === "/api/badges") {
    const cibles = (corps.noeud ? [corps.noeud] : ORDRE).filter((n) => !horsLigne[n]);
    cibles.forEach((n) => {
      const b = N[n].badge;
      if (corps.luminosite !== undefined) b.luminosite = corps.luminosite;
      if (corps.accent) b.accent = corps.accent;
      if (corps.retourne !== undefined) b.retourne = corps.retourne;
    });
    return ok({ noeuds: cibles.map((n) => ({ noeud: n, ok: true, badge: N[n].badge })) });
  }

  // Alimentation
  if (P && p[0] === "noeuds" && N[p[1]] && ["redemarrer", "eteindre"].includes(p[2])) {
    if (horsLigne[p[1]]) return err(503, "hors_ligne", `${p[1].toUpperCase()} est hors ligne.`);
    couper(p[1], p[2]);
    notifier(`${p[1].toUpperCase()} : ${p[2] === "eteindre" ? "extinction" : "redémarrage"} en cours`);
    return ok({ noeud: p[1], action: p[2], delai_s: 2 });
  }
  if (P && p[0] === "tour" && ["redemarrer", "eteindre"].includes(p[1])) {
    arretTour = true;
    const liste = ["vault", "pulse", "forge"].filter((n) => !horsLigne[n]);
    liste.forEach((n) => couper(n, p[1]));
    setTimeout(() => { arretTour = false; }, p[1] === "eteindre" ? 20500 : 11500);
    return ok({ action: p[1], noeuds: liste.map((n) => ({ noeud: n, ok: true })) });
  }

  return err(404, "inconnu", "Route inconnue.");
}

export function requete(methode, chemin, corps) {
  return new Promise((resoudre) => {
    setTimeout(() => {
      let r;
      try { r = router(methode, chemin, corps || {}); } catch (e) { r = err(500, "interne", `Erreur de la démonstration : ${e.message}`); }
      resoudre(clone(r));
    }, alea(90, 220));
  });
}

export function abonner(cibles) {
  abonnes.add(cibles);
  const envoyer = () => cibles.surEtat && cibles.surEtat(clone(etat()));
  const debut = setTimeout(envoyer, 0);
  const rythme = setInterval(envoyer, 2000);
  return () => { abonnes.delete(cibles); clearTimeout(debut); clearInterval(rythme); };
}
