// YDZ Shell : petits outils (échappement, formats, icônes, mise à jour du DOM sans tout reconstruire).

const ECHAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ECHAP[c]);

export const ic = (nom, classe = "") => `<svg class="ic ${classe}" aria-hidden="true"><use href="#i-${esc(nom)}"/></svg>`;

const nf = (n, d = 0) => n.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });

export function octets(n) {
  if (n == null || isNaN(n)) return "–";
  const u = ["o", "ko", "Mo", "Go", "To"];
  let i = 0, v = Number(n);
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${nf(v, v < 10 && i > 1 ? 1 : 0)} ${u[i]}`;
}
// La mémoire vive se compte en puissances de 2 (8 Gio s'affiche « 8 Go », comme sur la boîte).
export function memoire(n) {
  if (n == null) return "–";
  const go = n / 2 ** 30;
  return go >= 1 ? `${nf(go, go < 10 && go % 1 > 0.05 ? 1 : 0)} Go` : `${nf(n / 2 ** 20)} Mo`;
}
export function debit(bps) {
  if (bps == null) return "–";
  const u = ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"];
  let i = 0, v = Number(bps);
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${nf(v, v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}
export function debitCourt(bps) {
  const [v, u] = debit(bps).split(" ");
  return { v, u };
}
export function duree(s) {
  if (s == null) return "–";
  s = Math.floor(s);
  const j = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (j) return `${j} j ${h} h`;
  if (h) return `${h} h ${String(m).padStart(2, "0")}`;
  if (m) return `${m} min`;
  return `${s} s`;
}
export function ilYA(ts, maintenant) {
  if (!ts) return "jamais";
  const d = Math.max(0, (maintenant || Date.now() / 1000) - ts);
  if (d < 60) return "à l'instant";
  if (d < 3600) return `il y a ${Math.floor(d / 60)} min`;
  if (d < 86400) return `il y a ${Math.floor(d / 3600)} h`;
  return `il y a ${Math.floor(d / 86400)} j`;
}
export const pct = (v) => (v == null ? "–" : `${Math.round(v)} %`);
export const temp = (v) => (v == null ? "–" : `${nf(v, 0)}°`);
export const borne = (v, a, b) => Math.min(b, Math.max(a, v));
export const maj1 = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
export const attendre = (ms) => new Promise((r) => setTimeout(r, ms));

// Courbe lissée pour un petit graphique (valeurs -> chemin SVG dans une boîte l × h).
export function trace(valeurs, l, h, min, max) {
  if (!valeurs || valeurs.length < 2) return "";
  const lo = min ?? Math.min(...valeurs), hi = max ?? Math.max(...valeurs);
  const e = hi - lo || 1;
  const pas = l / (valeurs.length - 1);
  return valeurs.map((v, i) => `${i ? "L" : "M"}${(i * pas).toFixed(1)} ${(h - ((v - lo) / e) * h).toFixed(1)}`).join(" ");
}

// ---------------------------------------------------------------------------------------------
// morph : applique un nouveau rendu HTML à un élément en ne touchant que ce qui change.
// Les nœuds existants sont conservés (défilement, focus, animations en cours), les nouveaux
// jouent leur animation d'entrée. Clé d'appariement : attribut data-cle (ou id).
const cleDe = (n) => (n.nodeType === 1 ? n.getAttribute("data-cle") || n.id || null : null);

function memeType(a, b) {
  return a.nodeType === b.nodeType && (a.nodeType !== 1 || a.nodeName === b.nodeName);
}

function morphAttributs(a, b) {
  for (const { name, value } of [...b.attributes]) {
    if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  }
  for (const { name } of [...a.attributes]) {
    if (!b.hasAttribute(name)) a.removeAttribute(name);
  }
  if (a.nodeName === "INPUT" || a.nodeName === "TEXTAREA") {
    if (a.type === "checkbox" || a.type === "radio") a.checked = b.hasAttribute("checked");
    else if (a !== document.activeElement) {
      const v = b.getAttribute("value") ?? "";
      if (a.value !== v) a.value = v;
    }
  }
}

// Textes en cours d'animation (chiffres qui montent, 1.0.13) : morph note la vraie valeur sans l'écrire,
// l'animation la pose à la fin. Un rendu ordinaire ne relance ni ne casse donc jamais l'animation.
export const textesAnimes = new WeakMap();

function morphNoeud(a, b) {
  if (a.nodeType !== 1) {
    if (textesAnimes.has(a)) { textesAnimes.set(a, b.nodeValue); return; }
    if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue;
    return;
  }
  morphAttributs(a, b);
  if (a.hasAttribute("data-fige")) return;
  morphEnfants(a, b);
}

function morphEnfants(a, b) {
  let ca = a.firstChild, cb = b.firstChild;
  while (cb) {
    const suivant = cb.nextSibling;
    const cle = cleDe(cb);
    let cible = null;
    if (cle) {
      for (let x = ca; x; x = x.nextSibling) if (cleDe(x) === cle && memeType(x, cb)) { cible = x; break; }
    } else if (ca && !cleDe(ca) && memeType(ca, cb)) {
      cible = ca;
    }
    if (cible) {
      if (cible === ca) ca = ca.nextSibling;
      else a.insertBefore(cible, ca);
      morphNoeud(cible, cb);
    } else {
      a.insertBefore(cb, ca);
    }
    cb = suivant;
  }
  while (ca) { const s = ca.nextSibling; a.removeChild(ca); ca = s; }
}

export function morph(el, html) {
  const t = document.createElement("template");
  t.innerHTML = html;
  morphEnfants(el, t.content);
}
