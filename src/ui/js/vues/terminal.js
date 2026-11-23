// Terminal : le terminal web (ttyd, port 7681) du Pi, avec ses statistiques au-dessus.
import { esc, ic } from "../outils.js";
import { S, NOM } from "../noyau.js";
import { entete } from "../cadre.js";

const PORT = 7681;
const pct = (v) => (v == null ? "–" : `${Math.round(v)} %`);

function puce(icone, nom, valeur) {
  return `<div class="term-stat">${ic(icone)}<span>${nom}</span><b>${valeur}</b></div>`;
}

const vue = {
  rendu() {
    const n = S.etat.noeuds.find((x) => x.nom === S.etat.hub) || S.etat.noeuds[0] || {};
    const url = `${location.protocol}//${location.hostname}:${PORT}/`;
    return `${entete("Terminal", { sous: `Connecte-toi avec ton compte Linux · ${esc(location.hostname)}` })}
    <section class="carte bloc terminal-bloc">
      <div class="term-stats">
        ${puce("server", "Nœud", esc(NOM(n.nom || "forge")))}
        ${puce("thermometer", "Température", n.temp == null ? "–" : `${n.temp.toFixed(1)} °C`)}
        ${puce("cpu", "Processeur", pct(n.cpu))}
        ${puce("memory-stick", "Mémoire", pct(n.ram?.pct))}
        ${puce("fan", "Ventilateur", S.etat.ventilo?.vitesse == null ? "–" : pct(S.etat.ventilo.vitesse))}
        <a class="btn discret" href="${esc(url)}" target="_blank" rel="noopener">${ic("external-link")}<span>Plein écran</span></a>
      </div>
      <iframe class="terminal-cadre" src="${esc(url)}" title="Terminal" allow="clipboard-read; clipboard-write"></iframe>
      <p class="note-fine">${ic("info")}Écran vide : clique dans le terminal puis Entrée. Rien du tout : sudo systemctl restart ydz-terminal. Réservé au réseau local, n'ouvre jamais le port ${PORT} sur Internet.</p>
    </section>`;
  },
  actions: {},
};
export default vue;
