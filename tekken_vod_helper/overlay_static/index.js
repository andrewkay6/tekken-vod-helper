const FONT_STACKS = {
  "Bahnschrift": '"Bahnschrift", "Segoe UI", Arial, sans-serif',
  "Segoe UI": '"Segoe UI", Arial, sans-serif',
  "Arial": 'Arial, sans-serif',
  "Trebuchet MS": '"Trebuchet MS", Arial, sans-serif',
  "Tahoma": 'Tahoma, "Segoe UI", sans-serif',
  "Verdana": 'Verdana, "Segoe UI", sans-serif',
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
  element.classList.remove("fade");
  window.requestAnimationFrame(() => element.classList.add("fade"));
};

const applyState = (state) => {
  document.body.style.setProperty(
    "--overlay-font",
    FONT_STACKS[state.font] || FONT_STACKS.Bahnschrift,
  );
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

poll();
setInterval(poll, 1000);
