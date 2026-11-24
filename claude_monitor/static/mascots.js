// Pixel Claude Code mascots in costume.
//
// The mascot is a 13x8 sprite: body cols 2-10 rows 0-5, arms rows 2-3, legs rows 6-7, eyes at
// (3,1) and (9,1). A costume is four independent slots (head, face, outfit, held item) chosen
// from a hash of the session id, so a session keeps its costume across restarts and reloads.

function hash(str) {
  let h = 2166136261;
  for (const c of String(str)) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  h ^= h >>> 13; h = Math.imul(h, 0x5bd1e995); h ^= h >>> 15;
  return h >>> 0;
}

const CORAL = "#d97757";
const SHINY = "#f0b429";
const px = (x, y, w, h, c, cls = "") =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${c}"${cls ? ` class="${cls}"` : ""}/>`;
const P = (...rects) => rects.map((r) => px(...r)).join("");
const G = (cls, inner) => `<g class="${cls}">${inner}</g>`;

const HEADS = {
  none: "",
  party: P([4, -1, 5, 1, "#7c5cff"], [5, -2, 3, 1, "#ffd34d"], [6, -3, 1, 1, "#7c5cff"], [6, -4, 1, 1, "#ff5d8f"]),
  crown: P([3, -1, 7, 1, "#f5b700"], [3, -2, 1, 1, "#f5b700"], [6, -2, 1, 1, "#f5b700"],
    [9, -2, 1, 1, "#f5b700"], [6, -1, 1, 1, "#e0245e"]),
  "top hat": P([2, -1, 9, 1, "#222"], [4, -4, 5, 3, "#222"], [4, -2, 5, 1, "#c0392b"]),
  santa: P([3, -2, 6, 1, "#d62828"], [4, -3, 4, 1, "#d62828"], [7, -4, 2, 1, "#d62828"],
    [9, -4, 1, 1, "#fff"], [2, -1, 9, 1, "#fff"]),
  wizard: P([2, -1, 9, 1, "#3b2a8c"], [4, -2, 5, 1, "#4c37b5"], [5, -3, 3, 1, "#4c37b5"],
    [6, -4, 2, 1, "#4c37b5"], [7, -5, 1, 1, "#4c37b5"], [5, -2, 1, 1, "#ffe36e"]),
  witch: P([1, -1, 11, 1, "#222"], [4, -2, 5, 1, "#3ecf6e"], [5, -3, 3, 1, "#222"],
    [5, -4, 2, 1, "#222"], [4, -5, 1, 1, "#222"]),
  beanie: P([2, -1, 9, 1, "#1b5fa8"], [3, -2, 7, 1, "#2e86de"], [4, -3, 5, 1, "#2e86de"], [6, -4, 1, 1, "#feca57"]),
  propeller: P([3, -1, 4, 1, "#f44336"], [7, -1, 3, 1, "#2a9df4"], [4, -2, 5, 1, "#2a9df4"], [6, -3, 1, 1, "#555"])
    + G("prop", P([3, -4, 3, 1, "#ffd34d"], [7, -4, 3, 1, "#4caf50"])),
  cowboy: P([0, -1, 13, 1, "#8b5a2b"], [3, -3, 7, 2, "#a0693a"], [3, -2, 7, 1, "#5c3a1a"]),
  chef: P([3, -1, 7, 1, "#bdbdbd"], [3, -3, 7, 2, "#f2f2f2"], [2, -4, 3, 1, "#e6e6e6"],
    [5, -5, 3, 2, "#e6e6e6"], [8, -4, 3, 1, "#e6e6e6"], [3, -2, 7, 1, "#d6d6d6"]),
  halo: G("halo", P([4, -3, 5, 1, "#ffd34d"])),
  tricorn: P([2, -1, 9, 1, "#222"], [4, -2, 5, 1, "#222"], [6, -2, 1, 1, "#fff"]),
  headphones: P([2, -2, 9, 1, "#333"], [1, -1, 1, 2, "#333"], [11, -1, 1, 2, "#333"],
    [0, 0, 2, 2, "#00b894"], [11, 0, 2, 2, "#00b894"]),
  flowers: [2, 4, 6, 8, 10].map((x) => px(x, -1, 1, 1, "#ff5fa2")).join("")
    + [3, 5, 7, 9].map((x) => px(x, -1, 1, 1, "#ffd23f")).join(""),
  viking: P([3, -2, 7, 2, "#9aa3ad"], [3, -1, 7, 1, "#6c757d"], [1, -3, 1, 2, "#f1e9d2"],
    [2, -2, 1, 1, "#f1e9d2"], [11, -3, 1, 2, "#f1e9d2"], [10, -2, 1, 1, "#f1e9d2"]),
  cap: P([3, -2, 7, 1, "#e63946"], [3, -1, 10, 1, "#c1121f"], [6, -3, 1, 1, "#e63946"]),
  antenna: P([6, -3, 1, 3, "#888"]) + G("twinkle", P([5, -4, 3, 1, "#ff3b3b"])),
  bunny: P([3, -4, 2, 4, "#f5f5f5"], [4, -3, 1, 2, "#ffb3c7"], [8, -4, 2, 4, "#f5f5f5"], [8, -3, 1, 2, "#ffb3c7"]),
  "cat ears": P([2, -1, 2, 1, "#b85a3c"], [2, -2, 1, 1, "#b85a3c"], [9, -1, 2, 1, "#b85a3c"], [10, -2, 1, 1, "#b85a3c"]),
  mortarboard: P([1, -2, 11, 1, "#222"], [4, -1, 5, 1, "#222"], [6, -3, 1, 1, "#222"], [11, -1, 1, 2, "#ffd34d"]),
  "hard hat": P([3, -3, 7, 2, "#ffcc00"], [2, -1, 9, 1, "#e6b800"], [6, -3, 1, 2, "#e6b800"]),
  mohawk: P([5, -3, 3, 1, "#ff4fa3"], [5, -2, 3, 1, "#3ddc84"], [5, -1, 3, 1, "#2bb36b"]),
  bandana: P([2, -1, 9, 1, "#d62828"], [3, -2, 7, 1, "#d62828"], [4, -1, 1, 1, "#fff"], [7, -1, 1, 1, "#fff"],
    [5, -2, 1, 1, "#fff"], [11, -1, 1, 1, "#d62828"], [12, 0, 1, 1, "#d62828"]),
  sombrero: P([-1, -1, 15, 1, "#d4a373"], [4, -3, 5, 2, "#e9c46a"], [4, -2, 5, 1, "#e76f51"]),
  tiara: P([4, -1, 5, 1, "#cfd8dc"], [4, -2, 1, 1, "#cfd8dc"], [6, -2, 1, 1, "#cfd8dc"], [8, -2, 1, 1, "#cfd8dc"],
    [6, -1, 1, 1, "#ff6fae"]),
  sprout: P([6, -2, 1, 2, "#4caf50"], [4, -3, 2, 1, "#66bb6a"], [7, -4, 2, 1, "#66bb6a"], [7, -3, 1, 1, "#4caf50"]),
  fez: P([5, -3, 3, 3, "#c1121f"], [4, -1, 5, 1, "#a10e1a"], [8, -3, 1, 1, "#222"], [9, -2, 1, 2, "#222"]),
  "bucket hat": P([2, -1, 9, 1, "#7a9e7e"], [3, -3, 7, 2, "#8fb996"]),
  nightcap: P([3, -1, 7, 1, "#fff"], [4, -2, 5, 1, "#5c7cfa"], [6, -3, 4, 1, "#5c7cfa"], [9, -2, 2, 1, "#5c7cfa"],
    [11, -1, 1, 1, "#fff"]),
  tuft: P([5, -1, 1, 1, "#3b2314"], [6, -2, 1, 2, "#3b2314"], [7, -1, 1, 1, "#3b2314"]),
  goggles: P([2, -1, 9, 1, "#5c4033"], [3, -2, 2, 1, "#7fd1ff"], [8, -2, 2, 1, "#7fd1ff"]),
  unicorn: P([6, -4, 1, 1, "#ffe066"], [6, -3, 1, 1, "#ffb3e6"], [6, -2, 1, 1, "#ffe066"], [5, -1, 3, 1, "#ffb3e6"]),
};

// Faces may recolour or move the eyes, or sit under them (a mask the eyes show through).
const rim = (x, c) => P([x, 0, 3, 0.35, c], [x, 1.65, 3, 0.35, c], [x, 0, 0.35, 2, c], [x + 2.65, 0, 0.35, 2, c]);
const FACES = {
  none: {},
  shades: { art: P([2, 1, 3, 1, "#111"], [8, 1, 3, 1, "#111"], [5, 1, 3, 0.4, "#111"]) },
  specs: { art: rim(2, "#222") + rim(8, "#222") + px(5, 0.8, 3, 0.3, "#222") },
  monocle: { art: rim(8, "#c9a227") + px(10.5, 2, 0.3, 2, "#c9a227") },
  mustache: { art: P([4, 2, 5, 1, "#3b2314"], [4, 3, 1, 0.6, "#3b2314"], [8, 3, 1, 0.6, "#3b2314"]) },
  blush: { art: P([2, 2, 1, 1, "#ff8fa3"], [10, 2, 1, 1, "#ff8fa3"]) },
  eyepatch: { art: P([2, 1, 3, 1, "#111"], [2, 0.3, 9, 0.3, "#111"]) },
  "3d glasses": { art: P([2, 1, 3, 1, "#e63946"], [8, 1, 3, 1, "#3a86ff"], [5, 1, 3, 0.4, "#eee"]) },
  "bandit mask": { under: P([1, 0.5, 11, 2, "#111"]), eye: "#fff" },
  "heart eyes": { eye: "#ff2d6f" },
  "star eyes": { eye: "#ffd400" },
  "clown nose": { art: px(6, 2, 1, 1, "#ff2d2d") },
  beard: { art: P([3, 3, 7, 2, "#f1f1f1"], [4, 5, 5, 1, "#f1f1f1"]) },
  fangs: { art: P([5, 3, 0.6, 0.8, "#fff"], [7.4, 3, 0.6, 0.8, "#fff"]) },
  tongue: { art: px(7, 3, 1, 1, "#ff6b8b") },
  freckles: { art: P([2, 2, 0.5, 0.5, "#8a4b2f"], [3, 2.6, 0.5, 0.5, "#8a4b2f"],
    [9.5, 2, 0.5, 0.5, "#8a4b2f"], [10.3, 2.6, 0.5, 0.5, "#8a4b2f"]) },
  "laser eyes": { eye: "#ff1e1e", eyeCls: "laser" },
  cyclops: { eyes: [6] },
  wink: { eyes: [3], art: px(8, 1.4, 2, 0.35, "#111") },
};

const OUTFITS = {
  none: {},
  "bow tie": { art: P([5, 4, 1, 1, "#e84393"], [6, 4, 1, 1, "#b3236f"], [7, 4, 1, 1, "#e84393"]) },
  necktie: { art: P([5.5, 3, 2, 0.6, "#8d0f1a"], [6, 3.5, 1, 2.5, "#c1121f"]) },
  scarf: { art: P([1, 3, 11, 1, "#e63946"], [3, 3, 1, 1, "#fff"], [6, 3, 1, 1, "#fff"], [9, 3, 1, 1, "#fff"],
    [9, 4, 1, 2, "#e63946"]) },
  belt: { art: P([2, 5, 9, 1, "#6b4423"], [6, 5, 1, 1, "#ffd34d"]) },
  cape: { under: P([1, 0, 11, 7, "#6a4c93"]) },
  heart: { art: px(6, 4, 1, 1, "#ff2d6f") },
  badge: { art: P([8, 4, 1, 1, "#ffd34d"], [8, 5, 1, 0.5, "#3a86ff"]) },
  apron: { art: P([4, 4, 5, 2, "#fafafa"], [4, 3, 1, 1, "#fafafa"], [8, 3, 1, 1, "#fafafa"]) },
  sailor: { art: P([2, 4, 9, 0.5, "#1d3557"], [2, 5, 9, 0.5, "#1d3557"]) },
  overalls: { art: P([3, 4, 7, 2, "#3a6ea5"], [3, 2, 1, 2, "#3a6ea5"], [9, 2, 1, 2, "#3a6ea5"],
    [3, 3, 1, 1, "#ffd34d"], [9, 3, 1, 1, "#ffd34d"]) },
  medal: { art: P([6, 3, 1, 1, "#3a86ff"], [6, 4, 1, 1, "#ffd34d"]) },
  suspenders: { art: P([3, 2, 1, 4, "#d62828"], [9, 2, 1, 4, "#d62828"]) },
  ladybug: { art: P([4, 4, 1, 1, "#5a1e12"], [8, 3, 1, 1, "#5a1e12"], [6, 5, 1, 1, "#5a1e12"], [3, 2, 1, 1, "#5a1e12"]) },
  tutu: { art: [1, 3, 5, 7, 9, 11].map((x) => px(x, 5, 1, 1, "#ffb3d9")).join("")
    + [2, 4, 6, 8, 10].map((x) => px(x, 5.4, 1, 0.6, "#ff8cc6")).join("") },
  lei: { art: [2, 3, 4, 5, 6, 7, 8, 9, 10].map((x, i) =>
    px(x, 3, 1, 1, ["#ff5fa2", "#ffd23f", "#ff8c42", "#c77dff"][i % 4])).join("") },
};

// Held in the right hand (x >= 13) unless noted.
const HELD = {
  none: "",
  coffee: P([13, 2, 2, 2, "#fff"], [15, 2.5, 0.6, 1, "#fff"])
    + G("steam", P([13.5, 0, 0.5, 1, "#bbb"], [14.3, -0.8, 0.5, 1, "#bbb"])),
  balloon: G("float", P([12.5, -5, 3, 3, "#e63946"], [13.5, -2, 1, 0.5, "#e63946"])) + px(13.9, -1.5, 0.2, 3.5, "#666"),
  sword: P([13, -3, 1, 5, "#cfd8dc"], [12.5, 2, 2, 0.6, "#8d6e63"], [13, 2.6, 1, 1, "#6d4c41"]),
  wand: P([13, 0, 1, 3, "#6d4c41"]) + G("twinkle", P([12.5, -1.5, 2, 1.5, "#ffe066"])),
  flag: P([13, -3, 0.5, 6, "#888"], [13.5, -3, 3, 2, "#3a86ff"], [13.5, -3, 1.5, 1, "#ffd34d"]),
  lollipop: P([13.4, 0, 0.4, 3, "#eee"], [12.5, -2, 2.2, 2.2, "#ff6fae"], [13.2, -1.3, 0.8, 0.8, "#fff"]),
  umbrella: P([-3, -4, 6, 1, "#3a86ff"], [-2, -5, 4, 1, "#3a86ff"], [-0.2, -3, 0.4, 5, "#555"]),
  wrench: P([12.5, -1, 2, 1, "#9e9e9e"], [13.2, -1, 0.6, 0.5, "#555"], [13, 0, 1, 3, "#9e9e9e"]),
  pizza: P([13, 1, 3, 1, "#e9c46a"], [13, 2, 2, 1, "#f4a261"], [13, 3, 1, 1, "#f4a261"],
    [13.5, 1.4, 0.6, 0.6, "#d62828"], [14.6, 1.2, 0.6, 0.6, "#d62828"]),
  flower: P([13.4, 0, 0.4, 3, "#2e7d32"], [12.6, -1.6, 2, 2, "#ff7eb6"], [13.3, -0.9, 0.6, 0.6, "#ffd400"]),
  laptop: P([13, 2, 3, 0.5, "#555"], [13.5, 0, 2.5, 2, "#90a4ae"]) + G("twinkle", P([13.8, 0.3, 1.9, 1.4, "#cfe8ff"])),
  book: P([13, 1, 2.5, 3, "#8e44ad"], [13.3, 1, 0.3, 3, "#fff"]),
  guitar: P([13, 1, 2, 2, "#d35400"], [13.7, 1.7, 0.6, 0.6, "#3b2314"], [14.5, -2, 0.5, 3.5, "#6d4c41"]),
  fish: P([13, 2, 2, 1, "#4fc3f7"], [15, 1.5, 1, 2, "#4fc3f7"], [13.3, 2.2, 0.3, 0.3, "#000"]),
  "ice cream": P([13.3, 1, 1.4, 2, "#d4a373"], [13, 0, 2, 1, "#ffb3d9"], [13.3, -0.8, 1.4, 0.8, "#fff3b0"]),
  trophy: P([12.8, 0, 2.4, 1.5, "#f5b700"], [13.6, 1.5, 0.8, 1, "#f5b700"], [13.2, 2.5, 1.6, 0.6, "#b8860b"]),
};

const SLOTS = [
  ["head", HEADS, 0.08],
  ["face", FACES, 0.3],
  ["outfit", OUTFITS, 0.35],
  ["held", HELD, 0.35],
];
const COMBOS = SLOTS.reduce((n, [, table]) => n * Object.keys(table).length, 1);

function pick(table, bits, noneShare) {
  const names = Object.keys(table);
  if ((bits % 1000) / 1000 < noneShare) return "none";
  return names[1 + ((bits >>> 10) % (names.length - 1))];
}

function costume(seed) {
  const c = {};
  for (const [slot, table, none] of SLOTS) c[slot] = pick(table, hash(`${seed}|${slot}`), none);
  c.shiny = hash(`${seed}|shiny`) % 30 === 0;
  return c;
}

const costumeKey = (c) => `${c.head}/${c.face}/${c.outfit}/${c.held}/${c.shiny}`;
let ASSIGNED = new Map();

// Same costume for a session on every render; an exact duplicate among the ids on screen is
// re-rolled for whichever id sorts later.
function assignCostumes(ids) {
  const next = new Map(), used = new Set();
  for (const id of [...new Set(ids.map(String))].sort()) {
    let c = costume(id);
    for (let n = 1; used.has(costumeKey(c)) && n < 50; n++) c = costume(`${id}#${n}`);
    used.add(costumeKey(c));
    next.set(id, c);
  }
  ASSIGNED = next;
}

function describe(c) {
  const parts = [c.head, c.face, c.outfit, c.held].filter((p) => p !== "none");
  return (parts.join(" · ") || "plain") + (c.shiny ? " · ✨ shiny" : "");
}

function spark(seed, mood, small, given) {
  const c = given || ASSIGNED.get(String(seed)) || costume(seed);
  const face = FACES[c.face], outfit = OUTFITS[c.outfit];
  const color = c.shiny ? SHINY : CORAL;
  const body = px(2, 0, 9, 6, color) + px(0, 2, 13, 2, color);
  const legs = [3, 7].map((x) => px(x, 6, 1, 2, color, "leg a")).join("")
    + [5, 9].map((x) => px(x, 6, 1, 2, color, "leg b")).join("");
  let eyeXs = face.eyes || [3, 9];
  if (c.face === "eyepatch") eyeXs = [9];
  const eyes = eyeXs.map((x) => px(x, 1, 1, 1, face.eye || "#111", "eye " + (face.eyeCls || ""))).join("");
  const sparkle = c.shiny ? G("twinkle", P([11.5, -1.5, 1, 1, "#fff8c4"], [0, 4.5, 0.8, 0.8, "#fff8c4"])) : "";
  const zz = mood === "quiet"
    ? `<g class="zz"><text x="11" y="-1">z</text><text x="11" y="-1">z</text><text x="11" y="-1">z</text></g>` : "";
  const t = -(performance.now() / 1000).toFixed(2);
  return `<svg class="spark ${mood} ${small ? "small" : ""}" style="--t:${t}s" viewBox="-4 -6 21 15"
    aria-hidden="true"><g class="body">${outfit.under || ""}${body}${face.under || ""}${eyes}${face.art || ""}${
    outfit.art || ""}${HEADS[c.head]}${HELD[c.held]}${sparkle}</g>${legs}${zz}<title>${describe(c)}</title></svg>`;
}

function renderGallery(el, count = 120) {
  const moods = ["idle", "busy", "quiet"];
  const salt = Math.random().toString(36).slice(2);
  let html = `<p class="note">${COMBOS.toLocaleString()} costume combinations (plus a 1-in-30 shiny).
    Hover a mascot for its outfit. <button class="btn" id="reroll">re-roll</button>
    <a href="#">back to sessions</a></p><div class="gallery">`;
  for (let i = 0; i < count; i++) {
    const c = costume(`${salt}-${i}`);
    html += `<figure>${spark("", moods[i % 3], false, c)}<figcaption>${describe(c)}</figcaption></figure>`;
  }
  el.innerHTML = html + "</div>";
  el.querySelector("#reroll").onclick = () => renderGallery(el, count);
}
