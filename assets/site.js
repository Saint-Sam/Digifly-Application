"use strict";

const motionPreference = matchMedia("(prefers-reduced-motion: reduce)");
const motionButton = document.querySelector("[data-motion]");
const circuits = [...document.querySelectorAll("canvas.circuit")];
const nodes = [
  [104, 120], [104, 238], [239, 81], [246, 179], [239, 279],
  [369, 112], [367, 242], [500, 179],
];
const edges = [[0, 2], [0, 3], [1, 3], [1, 4], [2, 5], [3, 5], [3, 6], [4, 6], [5, 7], [6, 7]];
let paused = motionPreference.matches;
let frameId = 0;

function drawGrid(ctx, width, height) {
  ctx.fillStyle = "#111918";
  ctx.fillRect(0, 0, width, height);
  ctx.beginPath();
  for (let x = 20.5; x < width; x += 20) {
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
  }
  for (let y = 20.5; y < height; y += 20) {
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
  }
  ctx.strokeStyle = "#273a32";
  ctx.lineWidth = 1;
  ctx.stroke();
}

function drawCircuit(canvas, elapsed) {
  const ctx = canvas.getContext("2d");
  drawGrid(ctx, canvas.width, canvas.height);
  ctx.lineWidth = 1.7;

  for (const [index, [from, to]] of edges.entries()) {
    const [x, y] = nodes[from];
    const [endX, endY] = nodes[to];
    const middleX = (endX + x) / 2;
    ctx.strokeStyle = "#89aa8e";
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.bezierCurveTo(middleX, y, middleX, endY, endX, endY);
    ctx.stroke();

    const phase = ((elapsed / 3200) + index * .23) % 1;
    const remaining = 1 - phase;
    const dotX = remaining ** 3 * x + 3 * remaining * phase * middleX + phase ** 3 * endX;
    const dotY = y + (endY - y) * (3 * phase ** 2 - 2 * phase ** 3);
    ctx.fillStyle = "#ffd29a";
    ctx.beginPath();
    ctx.arc(dotX, dotY, 2.8, 0, Math.PI * 2);
    ctx.fill();
  }

  for (const [index, [x, y]] of nodes.entries()) {
    ctx.beginPath();
    ctx.arc(x, y, index === 7 ? 17 : 11, 0, Math.PI * 2);
    ctx.fillStyle = "#263b31";
    ctx.fill();
    ctx.strokeStyle = index === 7 ? "#ffc478" : "#b0c3a1";
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.fill();
  }
}

function paintCircuits(time = 0) {
  for (const canvas of circuits) {
    if (canvas.getClientRects().length) drawCircuit(canvas, time);
  }
  if (!paused && circuits.length) frameId = requestAnimationFrame(paintCircuits);
}

function updateMotion() {
  document.body.toggleAttribute("data-motion-paused", paused);
  if (motionButton) {
    motionButton.setAttribute("aria-pressed", String(paused));
    motionButton.textContent = paused ? "Resume motion" : "Pause motion";
    motionButton.disabled = motionPreference.matches;
    if (motionPreference.matches) motionButton.textContent = "Motion reduced";
  }
  cancelAnimationFrame(frameId);
  paintCircuits();
}

motionButton?.addEventListener("click", () => {
  paused = !paused;
  updateMotion();
});
motionPreference.addEventListener("change", event => {
  paused = event.matches;
  updateMotion();
});

updateMotion();

const workbench = document.querySelector("[data-session-url]");
if (workbench) {
  const local = location.hostname === "127.0.0.1" || location.hostname === "localhost";
  const configured = workbench.dataset.sessionUrl.trim();
  let session = null;
  try {
    if (configured) {
      const url = new URL(configured);
      if (url.protocol === "https:" && !url.username && !url.password) session = url;
    } else if (local) {
      session = new URL("http://127.0.0.1:6081/vnc.html?autoconnect=1&resize=scale");
    }
  } catch {
    console.warn("Invalid DigiFly Workstation session URL.");
  }

  if (session) {
    const frame = workbench.querySelector("[data-session-frame]");
    frame.src = session.href;
    frame.hidden = false;
    for (const link of document.querySelectorAll("[data-session-link]")) {
      link.href = session.href;
      link.hidden = false;
    }
    workbench.querySelector("[data-session-launch]").hidden = false;
    workbench.querySelector("[data-session-notice]").hidden = true;
  }
}

const clock = document.querySelector("[data-clock]");
if (clock) {
  const timeFormat = new Intl.DateTimeFormat(undefined, {hour: "2-digit", minute: "2-digit"});
  function showTime() {
    const now = new Date();
    clock.textContent = timeFormat.format(now);
    clock.dateTime = now.toISOString();
  }
  showTime();
  setInterval(showTime, 1000);
}
