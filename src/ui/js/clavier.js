// YDZ Shell : clavier virtuel. Le kiosque n'a pas de clavier système : celui-ci apparaît dès qu'un champ
// de saisie prend le focus sur l'écran de la tour. Il écrit dans le champ et émet un événement « input ».
import { ic } from "./outils.js";

const LETTRES = [
  ["a", "z", "e", "r", "t", "y", "u", "i", "o", "p"],
  ["q", "s", "d", "f", "g", "h", "j", "k", "l", "m"],
  ["MAJ", "w", "x", "c", "v", "b", "n", "é", "è", "EFF"],
  ["123", "-", "ESPACE", ".", "OK"],
];
const SIGNES = [
  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
  ["@", "#", "€", "_", "&", "-", "+", "(", ")", "/"],
  ["*", "\"", ":", ";", "!", "?", "=", "%", ",", "EFF"],
  ["abc", "$", "ESPACE", ".", "OK"],
];

let boite = null, cible = null, signes = false, maj = false, actif = false;

const estChamp = (el) => el && (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && ["text", "password", "search", "tel", "url", "email"].includes(el.type))) && !el.readOnly;

function touche(t) {
  const speciales = {
    MAJ: `<button type="button" class="k large${maj ? " actif" : ""}" data-k="MAJ" aria-label="Majuscules">${ic("arrow-big-up")}</button>`,
    EFF: `<button type="button" class="k large" data-k="EFF" aria-label="Effacer">${ic("delete")}</button>`,
    ESPACE: `<button type="button" class="k espace" data-k=" " aria-label="Espace">${ic("space")}</button>`,
    OK: `<button type="button" class="k large ok" data-k="OK" aria-label="Terminer">${ic("corner-down-left")}</button>`,
    123: `<button type="button" class="k large" data-k="123">123</button>`,
    abc: `<button type="button" class="k large" data-k="abc">abc</button>`,
  };
  if (speciales[t]) return speciales[t];
  const c = maj && !signes ? t.toUpperCase() : t;
  return `<button type="button" class="k" data-k="${c === '"' ? "&quot;" : c}">${c === "&" ? "&amp;" : c}</button>`;
}

function dessiner() {
  const rangs = signes ? SIGNES : LETTRES;
  boite.innerHTML = `<div class="clavier-rangs">${rangs.map((r) => `<div class="rang">${r.map(touche).join("")}</div>`).join("")}</div>`;
}

function ecrire(texte) {
  const debut = cible.selectionStart ?? cible.value.length, fin = cible.selectionEnd ?? cible.value.length;
  const max = cible.maxLength > 0 ? cible.maxLength : Infinity;
  if (cible.value.length - (fin - debut) + texte.length > max) return;
  cible.value = cible.value.slice(0, debut) + texte + cible.value.slice(fin);
  try { cible.setSelectionRange(debut + texte.length, debut + texte.length); } catch { /* type sans sélection */ }
  cible.dispatchEvent(new Event("input", { bubbles: true }));
}
function effacer() {
  let debut = cible.selectionStart ?? cible.value.length;
  const fin = cible.selectionEnd ?? cible.value.length;
  if (debut === fin) debut = Math.max(0, debut - 1);
  cible.value = cible.value.slice(0, debut) + cible.value.slice(fin);
  try { cible.setSelectionRange(debut, debut); } catch { /* idem */ }
  cible.dispatchEvent(new Event("input", { bubbles: true }));
}

function appui(ev) {
  const b = ev.target.closest(".k");
  ev.preventDefault(); // garde le focus dans le champ
  if (!b || !cible) return;
  const k = b.dataset.k;
  if (k === "MAJ") { maj = !maj; dessiner(); }
  else if (k === "123") { signes = true; dessiner(); }
  else if (k === "abc") { signes = false; dessiner(); }
  else if (k === "EFF") effacer();
  else if (k === "OK") {
    const action = cible.dataset.entree;
    const c = cible;
    c.blur();
    if (action) c.dispatchEvent(new CustomEvent("ydz-entree", { bubbles: true }));
  } else {
    ecrire(k);
    if (maj && !signes) { maj = false; dessiner(); }
  }
}

function montrer(el) {
  cible = el;
  signes = el.dataset.clavier === "chiffres";
  maj = false;
  dessiner();
  boite.classList.add("ouvert");
  document.body.classList.add("clavier-ouvert");
  setTimeout(() => cible === el && el.scrollIntoView({ block: "center", behavior: "smooth" }), 60);
}
function cacher() {
  cible = null;
  boite.classList.remove("ouvert");
  document.body.classList.remove("clavier-ouvert");
}

export function installerClavier() {
  if (actif) return;
  actif = true;
  boite = document.createElement("div");
  boite.className = "clavier";
  boite.setAttribute("aria-label", "Clavier");
  document.body.appendChild(boite);
  boite.addEventListener("pointerdown", appui);
  boite.addEventListener("mousedown", (ev) => ev.preventDefault());
  document.addEventListener("focusin", (ev) => { if (estChamp(ev.target)) montrer(ev.target); });
  document.addEventListener("focusout", (ev) => {
    if (ev.target === cible) setTimeout(() => { if (!estChamp(document.activeElement)) cacher(); }, 0);
  });
}
