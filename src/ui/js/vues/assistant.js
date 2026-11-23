// Assistant de premier démarrage : bienvenue, code PIN, nœuds et badges, test du ventilateur, premières apps.
import { esc, ic } from "../outils.js";
import { S, NOM, btn, ouvrir, pave, actionsPave, toucheClavierPave, toast, pastille, erreur } from "../noyau.js";
import { session, agir, changerPin } from "../api.js";

const ETAPES = ["Bienvenue", "Code PIN", "Nœuds", "Ventilateur", "Applications"];
const PROPOSEES = ["pihole", "jellyfin", "portainer", "uptime-kuma", "homeassistant", "wg-easy"];

function terminer() {
  try { localStorage.setItem("ydz_assistant", "fait"); } catch { /* stockage indisponible */ }
}

export function ouvrirAssistant() {
  const d = ouvrir({
    classe: "plein-ecran assistant",
    fermable: false,
    donnees: { etape: 0, pin: "", premier: "", erreur: "", secoue: 0, pinFait: false, test: "", choix: {} },
    surTouche(ev) { if (d.donnees.etape === 1 && saisiePin()) toucheClavierPave(d, ev); },
    rendu: () => {
      const x = d.donnees;
      const corps = [bienvenue, codePin, noeuds, ventilateur, applications][x.etape]();
      return `
        <header class="as-tete">
          <span class="logo">YDZ</span>
          <ol class="as-fil" aria-label="Étapes">${ETAPES.map((e, i) => `<li class="${i === x.etape ? "actif" : i < x.etape ? "fait" : ""}"><i></i><span>${e}</span></li>`).join("")}</ol>
          ${btn("quitter", "Plus tard", { classe: "discret" })}
        </header>
        <div class="as-corps" data-cle="etape-${x.etape}">${corps.html}</div>
        <footer class="as-pied">
          ${x.etape ? btn("retour", "Retour", { icone: "chevron-left", classe: "discret" }) : "<span></span>"}
          ${corps.suite}
        </footer>`;
    },
    actions: {
      ...actionsPave,
      quitter() { terminer(); d.fermer(); },
      retour() { d.donnees.etape = Math.max(0, d.donnees.etape - 1); },
      suite() { d.donnees.etape++; },
      async "ventilo-test"() {
        await agir("/api/ventilo/test", { secondes: 5 });
        if (S.etat) S.etat.ventilo.test = true;
        d.donnees.test = "fait";
      },
      "ventilo-oui"() { d.donnees.test = "oui"; },
      "ventilo-non"() { d.donnees.test = "non"; },
      cocher({ id }) { d.donnees.choix[id] = !d.donnees.choix[id]; },
      async finir() {
        const ids = Object.keys(d.donnees.choix).filter((id) => d.donnees.choix[id]);
        let lancees = 0;
        for (const id of ids) {
          try { await agir(`/api/apps/${id}/installer`, {}); lancees++; } catch (e) { erreur(e); }
        }
        terminer();
        d.fermer();
        if (lancees) { toast(`${lancees} installation${lancees > 1 ? "s" : ""} lancée${lancees > 1 ? "s" : ""}. Suis-les dans Activité.`, "ok"); location.hash = "#/apps"; }
        else toast("La tour est prête. Bonne découverte.", "ok");
      },
    },
    async valider(pin) {
      const x = d.donnees;
      if (!x.premier) { x.premier = pin; x.pin = ""; x.erreur = ""; return; }
      if (pin !== x.premier) { Object.assign(x, { premier: "", pin: "", erreur: "Les deux codes sont différents. Recommence.", secoue: Date.now() }); return; }
      try {
        await changerPin(pin);
        Object.assign(x, { pinFait: true, pin: "", premier: "", erreur: "" });
        if (S.etat?.auth) S.etat.auth.pin_defini = true;
        x.etape = 2;
      } catch (e) {
        Object.assign(x, { premier: "", pin: "", erreur: e.message, secoue: Date.now() });
      }
    },
  });

  const pinDefini = () => d.donnees.pinFait || S.etat?.auth?.pin_defini;
  const saisiePin = () => session.auth.local && !pinDefini();
  const suivant = (libelle = "Continuer", o = {}) => btn("suite", libelle, { icone: "arrow-right", classe: `plein grand${o.classe || ""}`, off: o.off });

  function bienvenue() {
    const n = S.etat.noeuds.filter((x) => x.en_ligne).length;
    return {
      html: `<div class="as-centre">
        <span class="as-embleme"><i></i><i></i><i></i></span>
        <h1>Bienvenue sur ta tour</h1>
        <p>YDZ OS est installé. ${n} nœud${n > 1 ? "s répondent" : " répond"} déjà. En cinq étapes courtes, on protège la tour, on vérifie le matériel et on installe tes premières applications.</p>
      </div>`,
      suite: suivant("Commencer"),
    };
  }

  function codePin() {
    const x = d.donnees;
    if (!session.auth.local) {
      return { html: `<div class="as-centre"><span class="grand-icone">${ic("lock")}</span><h1>Code PIN</h1>
        <p>${pinDefini() ? "Un code PIN protège déjà la tour." : "Le premier code PIN se choisit sur l'écran de la tour : Réglages, Code PIN."}</p></div>`, suite: suivant() };
    }
    if (pinDefini()) {
      return { html: `<div class="as-centre"><span class="grand-icone ok">${ic("shield-check")}</span><h1>La tour est protégée</h1>
        <p>Sur cet écran, tout reste permis sans code. Depuis ton téléphone ou ton PC, le code sera demandé pour agir. Tu peux le changer dans Réglages, Code PIN.</p></div>`, suite: suivant() };
    }
    return {
      html: `<div class="as-deux">
        <div><h1>${x.premier ? "Encore une fois" : "Choisis un code PIN"}</h1>
          <p>${x.premier ? "Retape le même code pour vérifier." : "Il sera demandé pour agir sur la tour depuis un téléphone ou un ordinateur. Sur cet écran, il n'est jamais demandé."}</p>
          <p class="note-fine">${ic("info")}4 à 8 chiffres. 1234 et 0000 sont refusés.</p></div>
        ${pave(x.pin, { erreur: x.erreur, secoue: x.secoue })}
      </div>`,
      suite: btn("suite", "Passer cette étape", { classe: "discret" }),
    };
  }

  function noeuds() {
    const liste = S.etat.noeuds;
    const pret = liste.filter((n) => n.en_ligne).length;
    return {
      html: `<div class="as-large"><h1>Nœuds et badges</h1>
        <p>${pret} nœud${pret > 1 ? "s" : ""} sur ${liste.length} en ligne. Un nœud absent n'empêche rien : il rejoindra la tour tout seul après son installation.</p>
        <ul class="as-noeuds">${liste.map((n) => {
          const b = n.badge;
          return `<li class="${n.en_ligne ? "ok" : ""}" data-cle="asn-${n.nom}">
            <span class="voyant ${n.en_ligne ? "ok" : "off"}"></span>
            <div><b>${NOM(n.nom)}</b><small>${esc(n.role_nom || n.role)}${n.ip ? ` · ${esc(n.ip)}` : ""}</small></div>
            ${n.en_ligne ? pastille("ok", "En ligne") : pastille("off", n.connu ? "Hors ligne" : "Pas encore installé")}
            ${n.en_ligne ? (b?.present ? pastille("ok", "Badge branché", "badge-check") : pastille("warn", b?.actif === false ? "Badge désactivé" : "Badge absent", "usb")) : ""}
          </li>`;
        }).join("")}</ul>
        <p class="note-fine">${ic("info")}Badge absent ? Vérifie que son câble USB transmet les données, pas seulement la charge.</p></div>`,
      suite: suivant(),
    };
  }

  function ventilateur() {
    const v = S.etat.ventilo;
    const x = d.donnees;
    const tour = v.vitesse > 0 ? (2.4 - (v.vitesse / 100) * 1.9).toFixed(2) : 0;
    let question = "";
    if (x.test === "fait" || x.test === "oui" || x.test === "non") {
      question = x.test === "oui" ? `<p class="bandeau ok">${ic("circle-check")}Parfait : le Pi pilote bien le ventilateur.</p>`
        : x.test === "non" ? `<p class="bandeau warn">${ic("triangle-alert")}S'il tournait déjà à fond, c'est normal de ne rien entendre. Sinon, vérifie le fil entre la broche 12 (GPIO18) du Pi et la base, puis Réglages, Diagnostic.</p>`
        : `<div class="as-question"><span>Tu l'as entendu accélérer ?</span>${btn("ventilo-oui", "Oui", { icone: "check" })}${btn("ventilo-non", "Non", { icone: "x" })}</div>`;
    }
    return {
      html: `<div class="as-deux">
        <div><h1>Test du ventilateur</h1>
          <p>Le ventilateur de la base refroidit toute la tour. Lance le test : il tourne à fond pendant 5 secondes, puis reprend sa vitesse normale.</p>
          ${v.pwm === false ? `<p class="bandeau warn">${ic("triangle-alert")}Le ventilateur n'est pas piloté : il tourne à fond par sécurité.</p>` : ""}
          <div class="actions">${btn("ventilo-test", v.test ? "Test en cours…" : x.test ? "Relancer le test" : "Lancer le test", { icone: "wind", classe: x.test ? "" : "plein grand", off: v.test })}</div>
          ${question}</div>
        <div class="as-helice"><span class="helice geante${v.test ? " test" : ""}" style="--tour:${tour}s">${ic("fan")}</span><b class="chiffre">${v.vitesse}<small>%</small></b><span>${esc(v.mode_nom)}</span></div>
      </div>`,
      suite: suivant(),
    };
  }

  function applications() {
    const cat = (S.apps?.catalogue || []).filter((a) => PROPOSEES.includes(a.id) && !a.installations.length && !a.reglages.some((r) => r.requis && (r.defaut === undefined || r.defaut === "")));
    const n = Object.values(d.donnees.choix).filter(Boolean).length;
    return {
      html: `<div class="as-large"><h1>Tes premières applications</h1>
        <p>Coche celles qui te tentent : elles s'installent avec leurs réglages par défaut, sur le nœud conseillé. Tu en trouveras d'autres dans Applications.</p>
        <div class="as-apps">${cat.map((a) => `<button type="button" class="as-app${d.donnees.choix[a.id] ? " actif" : ""}" data-action="cocher" data-id="${a.id}" aria-pressed="${!!d.donnees.choix[a.id]}">
          <span class="app-icone">${ic(a.icone)}</span><span class="as-app-textes"><b>${esc(a.nom)}</b><small>${esc(a.description)}</small></span>
          <i class="coche">${ic("check")}</i></button>`).join("") || `<p class="note-fine">${ic("circle-check")}Les applications proposées sont déjà installées.</p>`}</div></div>`,
      suite: btn("finir", n ? `Installer ${n} app${n > 1 ? "s" : ""} et terminer` : "Terminer", { icone: n ? "download" : "check", classe: "plein grand" }),
    };
  }
  return d;
}
