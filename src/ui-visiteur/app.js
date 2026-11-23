// Espace visiteurs — rendu de etat.json, SANS jamais injecter de HTML (textContent seulement).
// Aucun lien vers l'interface d'admin, aucune adresse interne. Rafraîchit toutes les ~15 s.
"use strict";

var DEMO = {
  labo: "Home Pi Lab",
  tour: { etat: "ok" },
  uptime: 183600,
  temperature: 45,
  jeux: [
    { nom: "Minecraft", jeu: "Minecraft", en_ligne: true, adresse: "mc.exemple.fr",
      joueurs: 2, max: 10, motd: "Serveur entre amis — viens jouer !" },
    { nom: "Minecraft", jeu: "Serveur créatif", en_ligne: false, adresse: null,
      joueurs: null, max: null, motd: null }
  ],
  maj: Math.floor(Date.now() / 1000)
};

function enDemo() {
  try { return new URLSearchParams(location.search).get("demo") === "1"; } catch (e) { return false; }
}

// --- petites fabriques d'éléments (jamais innerHTML) ----------------------------------------------
function el(tag, cls, texte) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (texte != null) e.textContent = texte;
  return e;
}

function ligne(cle, valeurEl) {
  var l = el("div", "jeu-ligne");
  l.appendChild(el("span", "cle", cle));
  if (typeof valeurEl === "string") valeurEl = el("span", "val", valeurEl);
  else valeurEl.classList.add("val");
  l.appendChild(valeurEl);
  return l;
}

function carteJeu(j) {
  var c = el("div", "carte-jeu");

  var tete = el("div", "jeu-tete");
  var textes = el("div");
  textes.appendChild(el("h3", "jeu-nom", j.nom || "Serveur"));
  if (j.jeu && j.jeu !== j.nom) textes.appendChild(el("p", "jeu-type", j.jeu));
  tete.appendChild(textes);

  var past = el("span", "jeu-pastille " + (j.en_ligne ? "en-ligne" : "hors"));
  past.appendChild(el("i", "pt"));
  past.appendChild(el("span", null, j.en_ligne ? "En ligne" : "Hors ligne"));
  tete.appendChild(past);
  c.appendChild(tete);

  if (j.en_ligne && j.motd) c.appendChild(el("p", "jeu-motd", j.motd));

  var lignes = el("div", "jeu-lignes");
  if (j.adresse) {
    lignes.appendChild(ligne("Adresse", el("span", "val adresse", j.adresse)));
  } else {
    lignes.appendChild(ligne("Adresse", "demande-la à l'hôte"));
  }
  if (j.en_ligne && j.joueurs != null && j.max != null) {
    lignes.appendChild(ligne("Joueurs", j.joueurs + " / " + j.max));
    var jauge = el("div", "jauge-joueurs");
    var i = el("i");
    var pct = j.max > 0 ? Math.max(4, Math.min(100, Math.round((j.joueurs / j.max) * 100))) : 0;
    i.style.width = pct + "%";
    jauge.appendChild(i);
    lignes.appendChild(jauge);
  }
  c.appendChild(lignes);
  return c;
}

// --- durée lisible ---------------------------------------------------------------------------------
function dureeFr(s) {
  if (s == null) return "";
  var j = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (j > 0) return j + " j " + h + " h";
  if (h > 0) return h + " h " + m + " min";
  return m + " min";
}

var ETATS = {
  ok: ["ok", "Tout va bien"],
  warn: ["warn", "Petit souci"],
  crit: ["crit", "Problème"]
};

function rendre(d) {
  if (!d || typeof d !== "object") return;
  document.getElementById("labo").textContent = d.labo || "Home Pi Lab";
  document.title = d.labo || "Home Pi Lab";

  var et = (d.tour && ETATS[d.tour.etat]) || ETATS.ok;
  document.getElementById("point-tour").className = "point " + et[0];
  document.getElementById("texte-tour").textContent = et[1];

  var jeux = document.getElementById("jeux");
  jeux.textContent = "";
  var liste = Array.isArray(d.jeux) ? d.jeux : [];
  if (!liste.length) {
    jeux.appendChild(el("div", "vide", "Aucun serveur de jeu pour le moment."));
  } else {
    liste.forEach(function (j) { jeux.appendChild(carteJeu(j)); });
  }

  var chaleur = document.getElementById("chaleur");
  chaleur.textContent = (d.temperature != null ? "Tour ~" + d.temperature + " °C" : "")
    + (d.uptime != null ? (d.temperature != null ? " · " : "") + "allumée depuis " + dureeFr(d.uptime) : "");
  document.getElementById("horloge").textContent = "Mis à jour à "
    + new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function charger() {
  if (enDemo()) { rendre(DEMO); return; }
  fetch("etat.json", { cache: "no-store" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d) rendre(d); })
    .catch(function () { /* le serveur reviendra */ });
}

charger();
if (!enDemo()) setInterval(charger, 15000);
