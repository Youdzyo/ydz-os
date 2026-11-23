// YDZ Shell : accès à l'API du hub (TECHNIQUE.md §3), session PIN, flux d'événements, bascule en démonstration.

export class ErreurApi extends Error {
  constructor(message, code, statut, donnees) {
    super(message);
    this.code = code;
    this.statut = statut;
    this.donnees = donnees || {};
  }
}

const MESSAGE_RESEAU = "La tour ne répond pas. Vérifie qu'elle est allumée et reliée au réseau.";
const CLE_JETON = "ydz_jeton";

export const session = {
  demo: false,
  demoForcee: false,
  verrouille: false,          // 1.0.15 : un appareil du réseau non déverrouillé (écran de verrouillage)
  jeton: null,
  auth: { pin_defini: false, local: false, connecte: false, attente_s: 0 },
};

let simulateur = null; // module demo.js, chargé seulement si besoin
let surPinRequis = null; // posé par l'interface : () => Promise<bool>

try { session.jeton = sessionStorage.getItem(CLE_JETON); } catch { /* stockage indisponible */ }

export function definirDemandePin(fn) { surPinRequis = fn; }

function memoriserJeton(j) {
  session.jeton = j;
  try {
    if (j) sessionStorage.setItem(CLE_JETON, j);
    else sessionStorage.removeItem(CLE_JETON);
  } catch { /* sans importance */ }
}

async function brut(methode, chemin, corps, delai = 20000) {
  if (session.demo) {
    const r = await simulateur.requete(methode, chemin, corps);
    if (r.statut >= 400) throw new ErreurApi(r.json.erreur, r.json.code, r.statut, r.json);
    return r.json;
  }
  const entetes = {};
  if (session.jeton) entetes.Authorization = `Bearer ${session.jeton}`;
  const fichier = typeof Blob !== "undefined" && corps instanceof Blob;
  if (fichier) { entetes["Content-Type"] = "application/zip"; delai = Math.max(delai, 180000); }
  else if (corps !== undefined) entetes["Content-Type"] = "application/json";
  const ctl = new AbortController();
  const minuterie = setTimeout(() => ctl.abort(), delai);
  let rep, json;
  try {
    rep = await fetch(chemin.replace(/^\//, ""), {
      method: methode, headers: entetes, signal: ctl.signal, cache: "no-store",
      body: corps === undefined ? undefined : fichier ? corps : JSON.stringify(corps),
    });
    json = await rep.json();
  } catch {
    throw new ErreurApi(MESSAGE_RESEAU, "reseau", 0);
  } finally {
    clearTimeout(minuterie);
  }
  if (!rep.ok) throw new ErreurApi(json.erreur || `Erreur ${rep.status}.`, json.code || "erreur", rep.status, json);
  return json;
}

// Lecture ou action. Sur 401 « pin_requis », l'interface demande le code puis l'appel est rejoué une fois.
async function appel(methode, chemin, corps, protege) {
  // Depuis le réseau, on demande le code avant d'envoyer une action : pas d'aller-retour refusé pour rien.
  if (protege && !session.demo && !session.auth.connecte && chemin !== "/api/ecran/activite") {
    if (!session.auth.pin_defini) {
      throw new ErreurApi("Aucun code PIN n'est défini. Choisis-le d'abord sur l'écran de la tour : Réglages, Code PIN.", "pin_non_defini", 403);
    }
    if (surPinRequis && !(await surPinRequis())) throw new ErreurApi("Action annulée : code PIN non saisi.", "annule", 0);
  }
  try {
    return await brut(methode, chemin, corps);
  } catch (e) {
    if (e.code === "pin_requis" && surPinRequis) {
      memoriserJeton(null);
      session.auth.connecte = false;
      if (await surPinRequis()) return brut(methode, chemin, corps);
      throw new ErreurApi("Action annulée : code PIN non saisi.", "annule", 0);
    }
    if (e.code === "pin_non_defini") {
      e.message = "Aucun code PIN n'est défini. Choisis-le d'abord sur l'écran de la tour : Réglages, Code PIN.";
    }
    throw e;
  }
}

export const lire = (chemin) => appel("GET", chemin);
export const lireProtege = (chemin) => appel("GET", chemin, undefined, true); // journal, diagnostic
export const agir = (chemin, corps) => appel("POST", chemin, corps === undefined ? {} : corps, true);
// Paquet de mise à jour YDZ (.zip) : envoyé brut, protégé par le code PIN.
export const envoyerPaquet = (fichier) => appel("POST", "/api/maj/paquet", fichier, true);

export async function rafraichirAuth() {
  session.auth = await brut("GET", "/api/auth");
  return session.auth;
}

export async function connexion(pin) {
  const r = await brut("POST", "/api/auth/connexion", { pin });
  memoriserJeton(r.jeton);
  await rafraichirAuth();
  return r;
}

export async function deconnexion() {
  try { await brut("POST", "/api/auth/deconnexion", {}); } catch { /* la session tombera seule */ }
  memoriserJeton(null);
  await rafraichirAuth().catch(() => {});
}

export async function changerPin(pin, ancien) {
  const r = await brut("POST", "/api/auth/pin", ancien ? { pin, ancien } : { pin });
  memoriserJeton(null); // toutes les sessions sont fermées par le hub
  await rafraichirAuth();
  return r;
}

// ---------------------------------------------------------------------------------------------
// Démarrage : API réelle si elle répond, démonstration sinon (ou si elle est demandée).
export async function demarrer() {
  const p = new URLSearchParams(location.search);
  session.demoForcee = Boolean(window.YDZ_DEMO) || p.get("demo") === "1";
  if (!session.demoForcee) {
    try {
      const e = await brut("GET", "/api/etat", undefined, 3500);
      if (e && Array.isArray(e.noeuds)) {
        await rafraichirAuth();
        return e;
      }
    } catch (err) {
      // 1.0.15 : la tour répond mais exige le code (ou aucun code n'est défini) -> écran de verrouillage,
      // JAMAIS la démonstration (les données privées ne sont pas chargées avant déverrouillage).
      if (["pin_requis", "pin_non_defini", "verrouille"].includes(err.code)) {
        session.verrouille = true;
        await rafraichirAuth().catch(() => {});
        return { verrouille: true };
      }
      /* sinon : tour injoignable -> démonstration */
    }
  }
  simulateur = await import("./demo.js");
  session.demo = true;
  memoriserJeton(null);
  await rafraichirAuth();
  // démonstration : écran de verrouillage seulement si on le force (captures d'écran)
  if (window.YDZ_VERROU_DEMO) { session.verrouille = true; return { verrouille: true }; }
  return brut("GET", "/api/etat");
}

// En démonstration « subie » (API injoignable au chargement), on guette le retour de la vraie tour.
export async function tourRevenue() {
  if (!session.demo || session.demoForcee) return false;
  try {
    const rep = await fetch("api/ping", { cache: "no-store" });
    const j = await rep.json();
    return j.ydz === 1;
  } catch { return false; }
}

// ---------------------------------------------------------------------------------------------
// Flux d'événements. Se met en pause (veille de l'écran, onglet caché) et reprend tout seul.
export function flux({ surEtat, surTache, surNotification, surLien, surVerrou }) {
  let source = null, secours = null, reprise = null, actif = false, arretDemo = null;

  const lien = (ok) => surLien && surLien(ok);

  function sonder() {
    if (secours) return;
    const tour = async () => {
      try { surEtat(await brut("GET", "/api/etat", undefined, 5000)); lien(true); }
      // 1.0.15 : la session a expiré côté serveur -> on reverrouille au lieu d'afficher « connexion perdue »
      catch (e) { if (["pin_requis", "pin_non_defini"].includes(e.code) && surVerrou) surVerrou(); else lien(false); }
    };
    secours = setInterval(tour, 3000);
  }
  function finSonde() { clearInterval(secours); secours = null; }

  function ouvrir() {
    if (!actif) return;
    if (session.demo) {
      arretDemo = simulateur.abonner({ surEtat, surTache, surNotification });
      lien(true);
      return;
    }
    source = new EventSource("api/events");
    source.addEventListener("etat", (ev) => { finSonde(); lien(true); surEtat(JSON.parse(ev.data)); });
    source.addEventListener("tache", (ev) => surTache && surTache(JSON.parse(ev.data)));
    source.addEventListener("notification", (ev) => surNotification && surNotification(JSON.parse(ev.data)));
    source.onerror = () => {
      // Trop de clients (503) ou hub en cours de redémarrage : on lit /api/etat en attendant.
      if (source && source.readyState === EventSource.CLOSED) {
        source = null;
        sonder();
        clearTimeout(reprise);
        reprise = setTimeout(ouvrir, 10000);
      } else {
        lien(false);
      }
    };
  }
  function fermer() {
    if (source) { source.close(); source = null; }
    if (arretDemo) { arretDemo(); arretDemo = null; }
    finSonde();
    clearTimeout(reprise);
  }
  return {
    demarrer() { if (!actif) { actif = true; ouvrir(); } },
    suspendre() { if (actif) { actif = false; fermer(); } },
    get actif() { return actif; },
  };
}
