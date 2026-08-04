const FONT_STACKS = {
  "Bahnschrift": '"Bahnschrift", "Segoe UI", Arial, sans-serif',
  "Segoe UI": '"Segoe UI", Arial, sans-serif',
  "Arial": 'Arial, sans-serif',
  "Trebuchet MS": '"Trebuchet MS", Arial, sans-serif',
  "Tahoma": 'Tahoma, "Segoe UI", sans-serif',
  "Verdana": 'Verdana, "Segoe UI", sans-serif',
};

let plateSvg = "";
let currentAccent = "#f3135e";

const clamp = (value) => Math.max(0, Math.min(255, Math.round(value)));

const parseHexColor = (color) => {
  if (typeof color !== "string" || !/^#[0-9a-fA-F]{6}$/.test(color)) {
    return null;
  }
  return {
    r: parseInt(color.slice(1, 3), 16),
    g: parseInt(color.slice(3, 5), 16),
    b: parseInt(color.slice(5, 7), 16),
  };
};

const toHex = ({ r, g, b }) =>
  `#${clamp(r).toString(16).padStart(2, "0")}${clamp(g)
    .toString(16)
    .padStart(2, "0")}${clamp(b).toString(16).padStart(2, "0")}`;

const toRgba = ({ r, g, b }, alpha) =>
  `rgba(${clamp(r)}, ${clamp(g)}, ${clamp(b)}, ${alpha})`;

const mix = (color, target, amount) => ({
  r: color.r + (target.r - color.r) * amount,
  g: color.g + (target.g - color.g) * amount,
  b: color.b + (target.b - color.b) * amount,
});

const renderPlate = (accent) => {
  const host = document.getElementById("plate-host");
  if (!host || !plateSvg) {
    return;
  }
  const base = parseHexColor(accent) || parseHexColor("#f3135e");
  const svg = plateSvg.replaceAll("#ff00ff", toHex(base));
  host.innerHTML = svg;
};

const applyAccentVariables = (accent) => {
  const base = parseHexColor(accent) || parseHexColor("#f3135e");
  document.body.style.setProperty("--accent", toHex(base));
  document.body.style.setProperty("--accent-light", toHex(mix(base, { r: 255, g: 255, b: 255 }, 0.42)));
  document.body.style.setProperty("--accent-shadow", toRgba(base, 0.7));
};

const applyOpacityVariables = (opacity) => {
  const parsed = Number(opacity);
  const clamped = Number.isFinite(parsed) ? Math.max(0, Math.min(1, parsed)) : 1;
  document.body.style.setProperty("--plate-opacity", String(clamped));
  document.body.style.setProperty("--accent-opacity", String(clamped));
};

const loadPlate = () => {
  fetch("assets/scoreboard.svg", { cache: "no-store" })
    .then((response) => response.text())
    .then((text) => {
      plateSvg = text
        .replace(/<\?xml[^>]*>\s*/i, "")
        .replace(/<!DOCTYPE[^>]*>\s*/i, "");
      renderPlate(currentAccent);
    })
    .catch(() => {});
};

const updateText = (id, value) => {
  const element = document.getElementById(id);
  if (!element) {
    return;
  }
  const next = value == null ? "" : String(value);
  if (element.textContent === next) {
    return;
  }
  element.textContent = next;
  if (element.dataset.ready !== "true") {
    element.dataset.ready = "true";
    return;
  }
  element.classList.remove("fade");
  window.requestAnimationFrame(() => element.classList.add("fade"));
};

const applyState = (state) => {
  document.body.style.setProperty(
    "--overlay-font",
    FONT_STACKS[state.font] || FONT_STACKS.Bahnschrift,
  );
  if (typeof state.accent_color === "string" && /^#[0-9a-fA-F]{6}$/.test(state.accent_color)) {
    currentAccent = state.accent_color;
    applyAccentVariables(currentAccent);
    renderPlate(currentAccent);
  }
  applyOpacityVariables(state.opacity);
  [
    "description",
    "subtitle",
    "p1name",
    "p1score",
    "p2name",
    "p2score",
  ].forEach((key) => updateText(key, state[key]));
};

const poll = () => {
  fetch("state.json", { cache: "no-store" })
    .then((response) => response.json())
    .then(applyState)
    .catch(() => {});
};

loadPlate();
poll();
setInterval(poll, 1000);
