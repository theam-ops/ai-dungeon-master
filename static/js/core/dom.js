/* Finding, building and showing elements; the toast. Part of the front end - see js/main.js. */

export const $ = (id) => document.getElementById(id);
/* SVG needs its own namespace: document.createElement("svg") builds an inert
   HTMLUnknownElement that renders nothing, and `className` on an SVG element is a
   read-only SVGAnimatedString, so `el()` below cannot make one. */
export const SVGNS = "http://www.w3.org/2000/svg";
export function icon(name, cls) {
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", "ic " + (cls || ""));
  svg.setAttribute("aria-hidden", "true");     // always beside a label that carries it
  const use = document.createElementNS(SVGNS, "use");
  use.setAttribute("href", "#ic-" + name);
  svg.append(use);
  return svg;
}

export const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};


export const SCREENS = ["login", "lobby", "create", "pick", "game"];
export function show(name) {
  SCREENS.forEach((s) => $("screen-" + s).classList.toggle("hidden", s !== name));
}

export let toastTimer;
export function toast(msg) {
  const t2 = $("toast");
  t2.textContent = msg;
  t2.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t2.classList.add("hidden"), 2600);
}
