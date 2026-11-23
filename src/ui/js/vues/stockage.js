// Stockage : disques, santé, préparation guidée (double confirmation), miroir, remonter, partages Samba.
import { esc, ic, octets } from "../outils.js";
import { S, NOM, btn, champ, jauge, pastille, vide, ouvrir, enteteDialogue, confirmer, toast, rendre } from "../noyau.js";
import { entete, tachesEnCours } from "../cadre.js";
import { lire, agir } from "../api.js";

const TYPES = { ssd: ["SSD", "hard-drive"], hdd: ["Disque dur", "hard-drive"], nvme: ["SSD NVMe", "memory-stick"], sd: ["Carte SD", "memory-stick"], usb: ["USB", "usb"] };
const nomDisque = (d) => (d.modele && d.modele !== "Disque" ? d.modele : (TYPES[d.type] || ["Disque"])[0]);

function sante(d) {
  const s = d.smart;
  if (!s) return `<span class="sante">${ic("circle-help")}Santé inconnue</span>`;
  const details = [s.temp != null ? `${s.temp} °C` : null, s.heures != null ? `${s.heures.toLocaleString("fr-FR")} h` : null, s.usure_pct != null ? `usure ${s.usure_pct} %` : null].filter(Boolean).join(" · ");
  if (s.avis || !s.sain) return `<span class="sante crit">${ic("heart-pulse")}${esc(s.avis || "Santé dégradée : sauvegarde ce disque")}</span>`;
  return `<span class="sante ok">${ic("heart-pulse")}Sain${details ? ` · ${details}` : ""}</span>`;
}

function carteDisque(d, occupe) {
  const [typeNom, icone] = TYPES[d.type] || ["Disque", "hard-drive"];
  const utilise = d.total != null && d.libre != null ? d.total - d.libre : null;
  let etat, actions = "";
  if (d.systeme) etat = pastille("off", "Système", "lock");
  else if (d.montage) etat = pastille("ok", d.miroir ? "Miroir" : "En service", d.miroir ? "layers" : null);
  else if (d.a_remonter) etat = pastille("warn", "À remonter");
  else if (d.vide) etat = pastille("accent", "Vide");
  else etat = pastille("off", "Non préparé");

  if (!d.systeme) {
    if (d.a_remonter) {
      actions = `${btn("remonter", "Remonter", { icone: "hard-drive-download", classe: "plein", d: { disque: d.chemin }, off: occupe })}
        ${btn("preparer", "Préparer", { classe: "discret danger-texte", d: { disque: d.chemin }, off: occupe || !d.formatable })}`;
    } else if (d.formatable) {
      actions = d.montage
        ? btn("preparer", "Effacer et préparer", { classe: "discret danger-texte", d: { disque: d.chemin }, off: occupe })
        : btn("preparer", "Préparer", { icone: "wrench", classe: "plein", d: { disque: d.chemin }, off: occupe });
    }
  }
  return `<article class="carte disque${d.systeme ? " systeme" : ""}" data-cle="d-${d.id}">
    <span class="disque-icone">${ic(icone)}</span>
    <div class="disque-textes">
      <h3>${d.nom ? `<b>${esc(d.nom)}</b>` : esc(nomDisque(d))}<small>${esc(d.id)} · ${typeNom} · ${octets(d.taille)}</small></h3>
      <p>${d.nom ? `${esc(nomDisque(d))} · ` : ""}${d.montage ? esc(d.montage) : d.a_remonter ? `Déjà préparé par YDZ sous le nom « ${esc(d.a_remonter)} ». Remonte-le pour retrouver tes fichiers.` : d.systeme ? esc(d.raison || "Disque du système") : d.vide ? "Prêt à être préparé pour YDZ." : d.raison ? esc(d.raison) : "Contient des données qui ne viennent pas de YDZ."}</p>
      ${utilise != null ? `<div class="usage">${jauge((utilise / d.total) * 100)}<span>${octets(d.libre)} libres sur ${octets(d.total)}</span></div>` : ""}
      ${sante(d)}
    </div>
    <div class="disque-cote">${etat}<div class="actions">${actions}</div></div>
  </article>`;
}

function carteMiroir(m) {
  const tons = { sain: ["ok", "Sain"], degrade: ["crit", "Dégradé : un disque manque"], reconstruction: ["warn", "Synchronisation"] };
  const [ton, nom] = tons[m.etat] || ["off", m.etat];
  return `<article class="carte miroir" data-cle="m-${m.nom}">
    <span class="disque-icone">${ic("layers")}</span>
    <div class="disque-textes"><h3>Miroir ${esc(m.nom)}<small>${esc(m.niveau).toUpperCase()} · ${m.membres.map(esc).join(" + ")}</small></h3>
      ${m.etat === "reconstruction" ? `<div class="usage">${jauge(m.progression, "accent")}<span>${(m.progression ?? 0).toFixed(1)} % synchronisés, utilisable dès maintenant</span></div>` : `<p>Chaque fichier est écrit sur les deux disques.</p>`}
    </div>
    <div class="disque-cote">${pastille(ton, nom)}</div>
  </article>`;
}

function partages(s, occupe) {
  const montes = s.disques.filter((d) => d.montage && d.nom);
  const nomAcces = (id) => s.acces.find((a) => a.id === id)?.nom || id;
  if (!s.samba.installe) return `<p class="bandeau">${ic("info")}Le partage de fichiers n'est pas installé sur ${NOM(s.noeud)}. Il l'est sur vault.</p>`;
  return `
    <div class="carte ligne-reglage">
      <span class="disque-icone">${ic("key-round")}</span>
      <div><h3>Mot de passe des partages</h3><p>Utilisateur <b>${esc(s.samba.utilisateur)}</b> · ${s.samba.mot_de_passe_defini ? "mot de passe défini" : "pas encore de mot de passe : choisis-le avant de créer un partage protégé"}</p></div>
      ${btn("mot-de-passe", s.samba.mot_de_passe_defini ? "Changer" : "Choisir", { classe: s.samba.mot_de_passe_defini ? "" : "plein" })}
    </div>
    ${s.partages.map((p) => `<article class="carte partage" data-cle="p-${esc(p.nom)}">
      <span class="disque-icone">${ic("folder")}</span>
      <div class="disque-textes"><h3><b>${esc(p.nom)}</b><small>disque « ${esc(p.disque)} » · ${esc(nomAcces(p.acces))}</small></h3>
        <p class="adresses"><span>Windows <code>${esc(p.adresse)}</code></span><span>Mac <code>${esc(p.adresse_mac)}</code></span></p></div>
      <div class="disque-cote">${btn("supprimer-partage", "", { icone: "trash-2", classe: "rond discret danger-texte", titre: `Supprimer le partage ${p.nom}`, d: { nom: p.nom } })}</div>
    </article>`).join("")}
    ${montes.length
      ? `<button type="button" class="btn discret seul" data-action="nouveau-partage"${occupe ? " disabled" : ""}>${ic("plus")}<span>Nouveau partage</span></button>`
      : `<p class="note-fine">${ic("info")}Prépare d'abord un disque : les partages vivent sur un disque de données.</p>`}`;
}

// ------------------------------------------------------------- préparation guidée d'un disque
function ouvrirPreparation(vue, s, chemins, miroirInitial) {
  const disque = s.disques.find((x) => x.chemin === chemins[0]);
  const partenaires = s.mdadm ? s.disques.filter((x) => x !== disque && x.formatable && !x.systeme && x.vide && Math.abs(x.taille - disque.taille) / disque.taille < 0.1) : [];
  const d = ouvrir({
    classe: "large assistant-disque",
    fermable: false,
    donnees: { etape: 1, nom: disque.a_remonter || (disque.nom ?? ""), miroir: miroirInitial ? chemins[1] : "", phrase: "", p: null, reste: 0, erreur: "" },
    surFermer: () => clearInterval(d.rythme),
    rendu: () => {
      const x = d.donnees;
      const etapes = ["Nom", "Avertissement", "Confirmation"];
      const fil = `<ol class="fil">${etapes.map((e, i) => `<li class="${x.etape === i + 1 ? "actif" : x.etape > i + 1 ? "fait" : ""}"><i>${x.etape > i + 1 ? ic("check") : i + 1}</i>${e}</li>`).join("")}</ol>`;
      let corps, pied;
      if (x.etape === 1) {
        corps = `
          <div class="disque-vise">${ic("hard-drive")}<div><b>${esc(nomDisque(disque))}</b><small>${esc(disque.chemin)} · ${octets(disque.taille)} · n° ${esc(disque.serie || "inconnu")}</small></div></div>
          <label class="reglage${x.erreur ? " erreur" : ""}"><span class="reglage-nom">Nom du disque</span>
            ${champ("nom", x.nom, { placeholder: "donnees", max: 16, entree: "suite", label: "Nom du disque", erreur: !!x.erreur })}
            <small>${x.erreur ? esc(x.erreur) : "1 à 16 lettres minuscules ou chiffres, sans espace. Il servira de dossier : /srv/ydz/<i>nom</i>."}</small></label>
          ${partenaires.length ? `<div class="reglage"><span class="reglage-nom">Protection</span>
            <div class="segments">
              <button type="button" class="${!x.miroir ? "actif" : ""}" data-action="mode" data-avec="">Disque seul</button>
              ${partenaires.map((p) => `<button type="button" class="${x.miroir === p.chemin ? "actif" : ""}" data-action="mode" data-avec="${esc(p.chemin)}">Miroir avec ${esc(p.id)}</button>`).join("")}
            </div>
            <small>${x.miroir ? "Miroir : chaque fichier est écrit sur les deux disques. Si l'un tombe en panne, tu ne perds rien. Tu as la place d'un seul disque." : "Disque seul : toute la place, sans protection contre une panne."}</small></div>` : ""}`;
        pied = `${btn("fermer", "Annuler", { classe: "discret" })}${btn("suite", "Continuer", { icone: "arrow-right", classe: "plein" })}`;
      } else if (x.etape === 2) {
        corps = `
          <div class="alerte-forte">${ic("triangle-alert")}<div><b>${esc(x.p.avertissement)}</b>
            <p>${x.p.donnees_perdues ? "Ce disque contient des données. Si tu veux les garder, annule et copie-les ailleurs d'abord." : "Le disque semble vide, mais vérifie que c'est bien le bon."}</p></div></div>
          <ul class="liste-fine">${x.p.disques.map((k) => `<li><span>${esc(k.modele)}<small>${esc(k.chemin)} · n° ${esc(k.serie || "inconnu")}</small></span><span>${octets(k.taille)}</span></li>`).join("")}</ul>
          <p class="texte">${x.p.miroir ? `Les deux disques formeront le miroir « ${esc(x.p.nom)} »` : `Le disque deviendra « ${esc(x.p.nom)} »`}, disponible dans <code>${esc(x.p.montage)}</code>.</p>`;
        pied = `${btn("fermer", "Annuler, ne rien effacer", { classe: "plein" })}${btn("compris", "J'ai compris, continuer", { classe: "discret danger-texte" })}`;
      } else {
        const juste = x.phrase.trim() === x.p.phrase;
        corps = `
          <p class="texte">Dernière étape. Pour confirmer, retape exactement cette phrase :</p>
          <p class="phrase-modele">${esc(x.p.phrase)}</p>
          <label class="reglage${x.erreur ? " erreur" : ""}">
            <input class="champ phrase" type="text" data-champ="phrase" data-rendre data-majuscules value="${esc(x.phrase)}" placeholder="Retape la phrase ici" maxlength="40"
              inputmode="${S.clavier ? "none" : "text"}" autocomplete="off" autocapitalize="characters" spellcheck="false" aria-label="Phrase de confirmation">
            <small>${x.erreur ? esc(x.erreur) : `Cette confirmation expire dans ${x.reste} s.`}</small></label>`;
        pied = `${btn("fermer", "Annuler, ne rien effacer", { classe: "discret" })}${btn("effacer", x.p.miroir ? "Effacer et créer le miroir" : "Effacer et préparer", { icone: "trash-2", classe: "danger", off: !juste })}`;
      }
      return `${enteteDialogue(miroirInitial || x.miroir ? "Créer un miroir" : "Préparer un disque", { icone: "hard-drive", sous: `Sur ${NOM(s.noeud)}`, sansCroix: true })}
        ${fil}<div class="dlg-corps">${corps}</div><footer class="dlg-pied">${pied}</footer>`;
    },
    actions: {
      mode({ avec }) { d.donnees.miroir = avec; },
      async suite() {
        const x = d.donnees;
        x.erreur = "";
        const nom = x.nom.trim();
        if (!/^[a-z0-9]{1,16}$/.test(nom)) { x.erreur = "Nom refusé : 1 à 16 lettres minuscules ou chiffres, sans espace ni accent."; return; }
        try {
          x.p = x.miroir
            ? await agir("/api/stockage/miroir/preparer", { noeud: s.noeud, disques: [disque.chemin, x.miroir], nom })
            : await agir("/api/stockage/formater/preparer", { noeud: s.noeud, disque: disque.chemin, nom });
        } catch (e) {
          if (["nom", "disque"].includes(e.code)) { x.erreur = e.message; return; }
          throw e;
        }
        x.reste = x.p.expire_s;
        clearInterval(d.rythme);
        d.rythme = setInterval(() => {
          x.reste--;
          if (x.reste <= 0) { d.fermer(); toast("La confirmation a expiré. Rien n'a été effacé.", "info"); }
          rendre();
        }, 1000);
        x.etape = 2;
      },
      compris() { d.donnees.etape = 3; d.donnees.erreur = ""; },
      async effacer() {
        const x = d.donnees;
        try {
          const r = await agir("/api/stockage/confirmer", { noeud: s.noeud, jeton: x.p.jeton, phrase: x.phrase.trim() });
          if (r.tache && S.etat && !S.etat.taches.some((t) => t.id === r.tache.id)) S.etat.taches.unshift(r.tache);
          d.fermer();
          toast("Préparation lancée. Suis-la dans Activité.");
          vue.charger().then(rendre);
        } catch (e) {
          // Le jeton ne sert qu'une fois : après un refus, il faut repartir du début.
          d.fermer();
          throw e;
        }
      },
    },
  });
}

function ouvrirMotDePasse(s) {
  const d = ouvrir({
    classe: "etroit",
    donnees: { mdp: "", erreur: "" },
    rendu: () => `${enteteDialogue("Mot de passe des partages", { icone: "key-round", sous: `Utilisateur « ${esc(s.samba.utilisateur)} » sur ${NOM(s.noeud)}` })}
      <div class="dlg-corps"><label class="reglage${d.donnees.erreur ? " erreur" : ""}"><span class="reglage-nom">Nouveau mot de passe</span>
        ${champ("mdp", d.donnees.mdp, { type: d.donnees.voir ? "texte" : "secret", entree: "valider", label: "Mot de passe", max: 64, rendre: true })}
        <small>${d.donnees.erreur ? esc(d.donnees.erreur) : "8 caractères au moins. Il te sera demandé par Windows ou le Mac à la première connexion."}</small></label>
        <button type="button" class="btn discret seul" data-action="voir">${ic("eye")}<span>${d.donnees.voir ? "Masquer" : "Afficher"} le mot de passe</span></button></div>
      <footer class="dlg-pied">${btn("fermer", "Annuler", { classe: "discret" })}${btn("valider", "Enregistrer", { classe: "plein", off: d.donnees.mdp.length < 8 })}</footer>`,
    actions: {
      voir() { d.donnees.voir = !d.donnees.voir; },
      async valider() {
        if (d.donnees.mdp.length < 8) { d.donnees.erreur = "Encore trop court : 8 caractères au moins."; return; }
        await agir("/api/stockage/samba/mot-de-passe", { noeud: s.noeud, mot_de_passe: d.donnees.mdp });
        d.fermer();
        toast("Mot de passe des partages enregistré.", "ok");
        vue.charger().then(rendre);
      },
    },
  });
}

function ouvrirPartage(s) {
  const montes = s.disques.filter((x) => x.montage && x.nom);
  const d = ouvrir({
    classe: "large",
    donnees: { nom: "", disque: montes[0]?.nom, acces: s.samba.mot_de_passe_defini ? "prive" : "invite_lecture", erreur: "" },
    rendu: () => {
      const x = d.donnees;
      const bloque = x.acces === "prive" && !s.samba.mot_de_passe_defini;
      return `${enteteDialogue("Nouveau partage", { icone: "folder", sous: `Un dossier de ${NOM(s.noeud)} visible depuis tes ordinateurs.` })}
      <div class="dlg-corps formulaire">
        <label class="reglage${x.erreur ? " erreur" : ""}"><span class="reglage-nom">Nom du partage</span>
          ${champ("nom", x.nom, { placeholder: "films", max: 31, entree: "creer", label: "Nom du partage", rendre: true })}
          <small>${x.erreur ? esc(x.erreur) : `Adresse : \\\\${esc(s.noeud)}\\${esc(x.nom || "nom")}`}</small></label>
        ${montes.length > 1 ? `<div class="reglage"><span class="reglage-nom">Sur quel disque ?</span>
          <div class="segments">${montes.map((m) => `<button type="button" class="${x.disque === m.nom ? "actif" : ""}" data-action="disque" data-nom="${esc(m.nom)}">${esc(m.nom)}</button>`).join("")}</div></div>` : ""}
        <div class="reglage"><span class="reglage-nom">Qui peut y accéder ?</span>
          <div class="liste-choix cadre">${s.acces.map((a) => `<button type="button" class="choix${x.acces === a.id ? " actif" : ""}" data-action="acces" data-id="${a.id}"><span><b>${esc(a.nom)}</b></span>${x.acces === a.id ? ic("check") : ""}</button>`).join("")}</div>
          ${bloque ? `<small class="alerte">${ic("triangle-alert")}Choisis d'abord le mot de passe des partages.</small>` : ""}</div>
      </div>
      <footer class="dlg-pied">${btn("fermer", "Annuler", { classe: "discret" })}${btn("creer", "Créer le partage", { icone: "plus", classe: "plein", off: !x.nom.trim() || bloque })}</footer>`;
    },
    actions: {
      disque({ nom }) { d.donnees.disque = nom; },
      acces({ id }) { d.donnees.acces = id; },
      async creer() {
        const x = d.donnees;
        try {
          await agir("/api/stockage/partages", { noeud: s.noeud, nom: x.nom.trim(), disque: x.disque, acces: x.acces });
        } catch (e) {
          if (["nom", "mot_de_passe_requis"].includes(e.code)) { x.erreur = e.message; return; }
          throw e;
        }
        d.fermer();
        toast(`Partage « ${x.nom.trim()} » créé.`, "ok");
        vue.charger().then(rendre);
      },
    },
  });
}

// ------------------------------------------------------------- disque système : répartition (1.0.21)
const COULEUR_POSTE = (p) => (p.categorie === "docker" ? (p.id === "docker" ? "c-docker-reste" : "c-docker") : ({ sauvegardes: "c-sauv", apt: "c-apt", journaux: "c-journaux", systeme: "c-reste" }[p.id] || "c-reste"));

function sectionSysteme(sys, occupe) {
  const postes = (sys.postes || []).filter((p) => p.octets > 0);
  const tot = sys.utilise || 1;
  const barre = postes.map((p) => `<span class="seg ${COULEUR_POSTE(p)}" style="width:${Math.max(0.5, (p.octets / tot) * 100).toFixed(2)}%" title="${esc(p.nom)} · ${octets(p.octets)}"></span>`).join("");
  const alerte = sys.niveau === "crit" ? "crit" : sys.niveau === "warn" ? "warn" : "";
  const bandeauReserve = !sys.reserve_ok
    ? `<p class="bandeau ${sys.niveau === "crit" ? "crit" : "warn"}">${ic("triangle-alert")}Il reste moins de ${octets(sys.reserve_octets)} libres : garde de la place pour que les mises à jour puissent s'installer. Libère de l'espace dès que possible.</p>`
    : "";
  const lignes = postes.map((p) => `<li class="poste-ligne">
      <span class="pastille-couleur ${COULEUR_POSTE(p)}"></span>
      <span class="poste-nom">${esc(p.nom)}${p.actif ? ` <small class="actif-tag">${ic("circle-play")}en service</small>` : ""}${p.detail ? `<small>${esc(p.detail)}</small>` : ""}</span>
      <span class="poste-taille">${octets(p.octets)}${p.recuperable > 0 ? `<small class="recup">${octets(p.recuperable)} récupérable${p.recuperable > 1 ? "s" : ""}</small>` : ""}</span>
    </li>`).join("");
  return `
    <h2 class="rubrique">Disque système</h2>
    <article class="carte systeme-repartition${alerte ? " " + alerte : ""}">
      <div class="systeme-entete">
        <div><h3>Espace occupé${!sys.docker_dispo ? ` <small>(Docker non détecté)</small>` : ""}</h3>
          <p>${octets(sys.libre)} libres sur ${octets(sys.total)} · occupé à ${sys.pct} %</p></div>
        ${sys.recuperable_total > 0 ? btn("liberer", `Libérer de l'espace`, { icone: "sparkles", classe: "plein", off: occupe }) : `<span class="pastille ok">${ic("check")}Rien à nettoyer</span>`}
      </div>
      ${bandeauReserve}
      <div class="barre-repartition">${barre || `<span class="seg c-reste" style="width:100%"></span>`}</div>
      <ul class="postes">${lignes}</ul>
      ${sys.recuperable_total > 0 ? `<p class="note-fine">${ic("info")}Jusqu'à ~${octets(sys.recuperable_total)} récupérables sans toucher aux apps qui tournent.</p>` : ""}
    </article>`;
}

function ouvrirLiberer(vue, s) {
  const sys = s.systeme;
  const d = ouvrir({
    classe: "large",
    donnees: { chargement: true, erreur: "", actions: null, choix: {}, garder: sys?.garder_sauvegardes ?? 3, enCours: false },
    rendu: () => {
      const x = d.donnees;
      let corps;
      if (x.chargement) {
        corps = `<div class="attente">${ic("loader-circle", "tourne")}</div><p class="texte centre">Calcul de ce qui peut être libéré…</p>`;
      } else if (x.erreur) {
        corps = `<p class="bandeau crit">${ic("triangle-alert")}${esc(x.erreur)}</p>`;
      } else if (!x.actions.length) {
        corps = `<p class="texte centre">Rien à libérer pour l'instant : le disque est déjà propre.</p>`;
      } else {
        corps = `<p class="texte">Coche ce que tu veux nettoyer. Aucune image ni aucun volume d'une app qui tourne n'est touché.</p>
          <ul class="liste-liberer">${x.actions.map((a) => `<li class="choix-liberer${x.choix[a.id] ? " actif" : ""}" data-action="bascule" data-id="${a.id}">
            <span class="case">${x.choix[a.id] ? ic("check") : ""}</span>
            <span class="choix-textes"><b>${esc(a.nom)}</b><small>${esc(a.detail || "")}</small></span>
            <span class="choix-taille">${a.estimation ? "~" : ""}${octets(a.octets)}</span></li>`).join("")}</ul>`;
      }
      const total = x.actions ? x.actions.filter((a) => x.choix[a.id]).reduce((n, a) => n + a.octets, 0) : 0;
      const rien = !x.actions || !x.actions.some((a) => x.choix[a.id]);
      return `${enteteDialogue("Libérer de l'espace", { icone: "sparkles", sous: `Disque système de ${NOM(s.noeud)}` })}
        <div class="dlg-corps">${corps}</div>
        <footer class="dlg-pied">${btn("fermer", "Annuler", { classe: "discret" })}${x.actions && x.actions.length ? btn("liberer", `Libérer ~${octets(total)}`, { icone: "trash-2", classe: "plein", off: rien || x.enCours }) : ""}</footer>`;
    },
    actions: {
      bascule({ id }) { d.donnees.choix[id] = !d.donnees.choix[id]; },
      async liberer() {
        const x = d.donnees;
        const postes = x.actions.filter((a) => x.choix[a.id]).map((a) => a.id);
        if (!postes.length || x.enCours) return;
        x.enCours = true;
        try {
          const r = await agir("/api/stockage/liberer", { noeud: s.noeud, postes, garder: x.garder });
          if (r.tache && S.etat && !S.etat.taches.some((t) => t.id === r.tache.id)) S.etat.taches.unshift(r.tache);
          d.fermer();
          toast("Nettoyage lancé. Suis-le dans Activité.");
          vue.charger().then(rendre);
        } catch (e) { x.enCours = false; d.fermer(); throw e; }
      },
    },
  });
  // Chargement de l'aperçu (rien n'est effacé).
  agir("/api/stockage/liberer/apercu", { noeud: s.noeud, garder: d.donnees.garder })
    .then((r) => { d.donnees.chargement = false; d.donnees.actions = r.actions || []; (r.actions || []).forEach((a) => { d.donnees.choix[a.id] = true; }); rendre(); })
    .catch((e) => { d.donnees.chargement = false; d.donnees.erreur = e.message; rendre(); });
}

// ------------------------------------------------------------------------------------------ vue
const vue = {
  donnees: { noeud: null },
  stockage: null,
  erreur: "",
  noeudChoisi() {
    const enLigne = (S.etat?.noeuds || []).filter((n) => n.en_ligne);
    return this.donnees.noeud || (enLigne.find((n) => n.role === "vault") || enLigne[0])?.nom || "forge";
  },
  async charger() {
    const n = this.noeudChoisi();
    try {
      const r = await lire(`/api/stockage?noeud=${encodeURIComponent(n)}`);
      if (n === this.noeudChoisi()) { this.stockage = r; this.erreur = ""; }
    } catch (e) {
      if (n === this.noeudChoisi()) { this.stockage = null; this.erreur = e.message; }
    }
  },
  rendu() {
    const choisi = this.noeudChoisi();
    const noeuds = (S.etat.noeuds || []).filter((n) => n.connu);
    const s = this.stockage && this.stockage.noeud === choisi ? this.stockage : null;
    const occupe = tachesEnCours().some((t) => t.type === "stockage" && t.noeud === choisi);
    const onglets = `<div class="segments onglets">${noeuds.map((n) => `<button type="button" class="${n.nom === choisi ? "actif" : ""}" data-action="noeud" data-nom="${n.nom}"${n.en_ligne ? "" : " disabled"}>${NOM(n.nom)}</button>`).join("")}</div>`;
    let corps;
    if (!s) {
      corps = this.erreur ? vide("wifi-off", "Stockage indisponible", esc(this.erreur)) : `<div class="attente">${ic("loader-circle", "tourne")}</div>`;
    } else {
      const sug = s.suggestion;
      corps = `
        ${sug ? `<div class="bandeau accent suggestion">${ic(sug.type === "miroir" ? "layers" : "sparkles")}<span>${esc(sug.texte)}</span>
          ${btn("suggestion", sug.type === "miroir" ? "Créer le miroir" : "Préparer", { classe: "plein", off: occupe })}</div>` : ""}
        ${occupe ? `<p class="bandeau">${ic("loader-circle", "tourne")}Une opération sur les disques est en cours sur ${NOM(choisi)}.</p>` : ""}
        ${s.systeme ? sectionSysteme(s.systeme, occupe) : ""}
        <h2 class="rubrique">Disques</h2>
        <div class="pile">${s.disques.map((d) => carteDisque(d, occupe)).join("")}${s.miroirs.map(carteMiroir).join("")}</div>
        <h2 class="rubrique" id="partages">Partages</h2>
        <div class="pile">${partages(s, occupe)}</div>`;
    }
    return `${entete("Stockage", { sous: s ? `${s.disques.length} disque${s.disques.length > 1 ? "s" : ""} sur ${NOM(choisi)}` : "", droite: onglets })}${corps}`;
  },
  actions: {
    noeud({ nom }) { this.donnees.noeud = nom; this.stockage = null; this.erreur = ""; return this.charger(); },
    preparer({ disque }) { ouvrirPreparation(this, this.stockage, [disque], false); },
    liberer() { if (this.stockage?.systeme) ouvrirLiberer(this, this.stockage); },
    suggestion() {
      const sug = this.stockage.suggestion;
      ouvrirPreparation(this, this.stockage, sug.disques, sug.type === "miroir");
    },
    async remonter({ disque }) {
      const d = this.stockage.disques.find((x) => x.chemin === disque);
      const oui = await confirmer({
        titre: `Remonter « ${d.a_remonter} » ?`, icone: "hard-drive-download", libelle: "Remonter",
        texte: `Le disque ${esc(d.id)} a déjà été préparé par YDZ. Il est remis en service dans /srv/ydz/${esc(d.a_remonter)}, sans rien effacer. Les partages seront à recréer.`,
      });
      if (!oui) return;
      const r = await agir("/api/stockage/remonter", { noeud: this.stockage.noeud, disque });
      toast(`Disque « ${r.nom} » remonté dans ${r.montage}.`, "ok");
      await this.charger();
    },
    "mot-de-passe"() { ouvrirMotDePasse(this.stockage); },
    "nouveau-partage"() { ouvrirPartage(this.stockage); },
    async "supprimer-partage"({ nom }) {
      const oui = await confirmer({
        titre: `Supprimer le partage « ${nom} » ?`, danger: true, libelle: "Supprimer le partage",
        texte: "Il ne sera plus visible depuis tes ordinateurs. Les fichiers, eux, restent sur le disque.",
      });
      if (!oui) return;
      await agir("/api/stockage/partages/supprimer", { noeud: this.stockage.noeud, nom });
      toast(`Partage « ${nom} » supprimé. Les fichiers sont toujours sur le disque.`);
      await this.charger();
    },
  },
};
export default vue;
