"use strict";
const $ = id => document.getElementById(id);
const query = new URLSearchParams(location.search);

function stored(key, make) {
  try {
    let value = localStorage.getItem(key);
    if (!value && make) { value = make(); localStorage.setItem(key, value); }
    return value || "";
  } catch (_) { return make ? make() : ""; }
}
const hex = n => [...crypto.getRandomValues(new Uint8Array(n))].map(b => b.toString(16).padStart(2, "0")).join("");
const clientId = stored("dropAirClientId", () => hex(12));
function describeDevice() {
  const ua = navigator.userAgent;
  const iPad = /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const name = /iPhone/.test(ua) ? "iPhone" : iPad ? "iPad"
    : /Android/.test(ua) ? (/Mobile/.test(ua) ? "Android phone" : "Android tablet")
    : /Windows/.test(ua) ? "Windows PC" : /Macintosh/.test(ua) ? "Mac" : /CrOS/.test(ua) ? "Chromebook" : /Linux/.test(ua) ? "Linux PC" : "Browser";
  const pick = pattern => (ua.match(pattern) || [])[1] || "";
  const ios = pick(/(?:iPhone OS|CPU OS) (\d+[_.]\d+)/).replace("_", ".") || (iPad ? pick(/Version\/(\d+\.\d+)/) : "");
  // Chrome on Android reports the placeholder model "K"; other browsers may give the real one.
  const model = pick(/Android [\d.]+; (?!K\)|wv\))([^;)]+?)(?: Build|\)|;)/);
  const android = /; K\)/.test(ua) ? "" : pick(/Android (\d+)/);
  const os = name === "iPhone" || name === "iPad" ? `iOS ${ios}`.trim()
    : name.startsWith("Android") ? [`Android ${android}`.trim(), model].filter(Boolean).join(" · ")
    : name === "Windows PC" ? "Windows" : name === "Mac" ? "macOS" : "";
  const browser = /SamsungBrowser/.test(ua) ? "Samsung Internet" : /Edg(e|A|iOS)?\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera"
    : /Firefox|FxiOS/.test(ua) ? "Firefox" : /Chrome|CriOS/.test(ua) ? "Chrome" : /Safari/.test(ua) ? "Safari" : "Unknown";
  return { name, os, browser, screen: `${screen.width} × ${screen.height}` };
}
const myDevice = describeDevice();
let key = query.get("k") || "";
let code = query.get("code") || "";
let state = null;
let target = null; // {kind:"host"|"client"|"peer", id, name, icon}; remembered until unselected
try { target = JSON.parse(localStorage.getItem("dropAirTarget")) || null; } catch (_) {}
let pickTarget = null;
const peerLinks = new Map();

const authQuery = () => `k=${encodeURIComponent(key)}${code ? `&code=${encodeURIComponent(code)}` : ""}`;

async function api(path, { method = "GET", body } = {}) {
  const headers = { "X-Drop-Air-Key": key, "X-Drop-Air-Code": encodeURIComponent(code) };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(data.error || `Request failed (${response.status})`), { status: response.status });
  return data;
}

function toast(message) {
  const el = $("toast");
  const host = document.querySelector("dialog[open]") || document.body;
  if (el.parentNode !== host) host.append(el);
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 3200);
}
const report = error => toast(error.message || "Something went wrong");
const motion = () => !matchMedia("(prefers-reduced-motion: reduce)").matches;
function remove(button, path) {
  const row = button.closest(".item");
  row.classList.add("leave");
  api(path, { method: "DELETE" }).then(() => setTimeout(refresh, 280)).catch(error => { row.classList.remove("leave"); report(error); });
}
function closeDialog(dialog) {
  if (!dialog.open) return;
  if (!motion()) return dialog.close();
  dialog.classList.add("closing");
  setTimeout(() => { dialog.classList.remove("closing"); dialog.close(); }, 200);
}

function confirmBox(title, text, yes) {
  const dialog = $("confirmDialog");
  $("confirmTitle").textContent = title;
  $("confirmText").textContent = text;
  $("confirmYes").textContent = yes;
  dialog.returnValue = "no";
  dialog.showModal();
  return new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "yes"), { once: true }));
}

function human(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return `${bytes >= 100 || i === 0 ? Math.round(bytes) : bytes.toFixed(1)} ${units[i]}`;
}
const duration = s => s < 60 ? `${Math.ceil(s)} s` : s < 3600 ? `${Math.ceil(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
function ago(seconds) {
  const diff = Date.now() / 1000 - seconds;
  return diff < 60 ? "just now" : diff < 3600 ? `${Math.floor(diff / 60)} min ago` : diff < 86400 ? `${Math.floor(diff / 3600)} h ago` : new Date(seconds * 1000).toLocaleDateString();
}
const TYPES = { image: "jpg jpeg png gif webp heic bmp svg avif", video: "mp4 mov mkv avi webm m4v", audio: "mp3 wav flac m4a ogg aac",
  zip: "zip rar 7z tar gz xz", code: "js ts py rs c cpp h java html css json xml yml yaml sh ps1 bat" };
function typeIcon(name) {
  const ext = name.split(".").pop().toLowerCase();
  return Object.keys(TYPES).find(type => TYPES[type].split(" ").includes(ext)) || "file";
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children.filter(Boolean));
  return node;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.setAttribute("aria-hidden", "true");
  svg.append(use);
  return svg;
}
function iconButton(name, label, onclick, extra = "") {
  const button = el("button", { className: `icon ${extra}`, type: "button", title: label, onclick }, icon(name));
  button.setAttribute("aria-label", label);
  return button;
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    // Plain HTTP on the LAN has no clipboard API, so fall back to a hidden field.
    const area = el("textarea", { value: text, className: "sr" });
    (document.querySelector("dialog[open]") || document.body).append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (!ok) return toast("Select the text and copy it manually");
  }
  toast("Copied");
}

/* ---------- rendering ---------- */

const seen = { files: new Set(), texts: new Set(), nodes: new Set(), first: true };
const signatures = {};
function changed(name, value) {
  const next = JSON.stringify(value);
  if (signatures[name] === next) return false;
  signatures[name] = next;
  return true;
}
const enter = (set, id) => { const fresh = !seen.first && !set.has(id); set.add(id); return fresh; };

// Fixed spots on the rings, filled in order so devices don't jump around.
const SPOTS = [[25, 40], [75, 40], [11, 56], [89, 56], [36, 30], [64, 30]];

function renderRadar() {
  const devices = [
    ...state.clients.filter(c => !c.ids.includes(clientId)).map(c => ({ kind: "client", ids: c.ids, id: c.id, name: c.name, sub: (c.os || "Connected").split(" · ")[0], icon: /PC|Mac|Browser|Chromebook/.test(c.name) ? "pc" : "phone" })),
    ...state.peers.map(p => ({ kind: "peer", id: p.device_id, name: p.device_name, sub: peerLinks.has(p.device_id) ? "Paired" : "Nearby PC", icon: "pc" })),
  ].slice(0, SPOTS.length);
  if (!state.admin) devices.unshift({ kind: "host", id: "host", name: state.device_name, sub: "Host PC", icon: "pc" });
  // A host cannot send to itself, e.g. a choice left over from using the phone view.
  if (target?.kind === "host" && state.admin) target = null;
  const moved = target && devices.find(d => d.id !== target.id && d.ids?.includes(target.id));
  if (moved) target.id = moved.id;
  renderTargets(devices);

  $("radarHint").textContent = target ? `Files go only to ${target.name} until you unselect it`
    : devices.length ? (matchMedia("(pointer: coarse)").matches || !state.admin ? "Tap a device to always send to it" : "Drop files on a device to send only to it") : "No devices yet. Tap the QR button to connect your phone.";
  if (!changed("radar", [devices, target, state.admin])) return;

  const me = el("div", { className: "node me" }, el("b", {}, icon(state.admin ? "pc" : "phone")),
    el("span", { textContent: state.admin ? state.device_name : "This device" }));
  me.style.left = "50%";
  me.style.top = "46%";
  $("nodes").replaceChildren(me, ...devices.map((device, index) => {
    const node = el("button", { className: "node", type: "button" }, el("b", {}, icon(device.icon)),
      el("span", { textContent: device.name }), el("small", { textContent: device.sub }));
    node.style.left = `${SPOTS[index][0]}%`;
    node.style.top = `${SPOTS[index][1]}%`;
    if (enter(seen.nodes, device.id)) node.classList.add("new");
    if (target?.id === device.id) node.classList.add("selected");
    // The host gets a details sheet for connected devices; everywhere else a tap selects or unselects.
    node.onclick = device.kind === "client" && state.admin ? () => showDevice(device.id) : () => toggleTarget(device);
    node.ondragover = event => { event.preventDefault(); event.stopPropagation(); node.classList.add("over"); };
    node.ondragleave = () => node.classList.remove("over");
    node.ondrop = event => {
      event.preventDefault();
      event.stopPropagation();
      node.classList.remove("over");
      send([...event.dataTransfer.files], device);
    };
    return node;
  }));
}

function showDevice(id) {
  const client = state.clients.find(c => c.id === id);
  if (!client) return;
  const minutes = Math.floor(client.connected_seconds / 60);
  $("deviceName").textContent = client.name;
  $("deviceFacts").replaceChildren(...[["System", client.os || "Unknown"], ["Browser", client.browser || "Unknown"], ["Screen", client.screen || "Unknown"],
    ["Address", client.ip], ["Connected", minutes ? `${minutes} min` : "just now"]]
    .flatMap(([label, value]) => [el("dt", { textContent: label }), el("dd", { textContent: value })]));
  const device = { kind: "client", id, name: client.name, icon: /PC|Mac|Browser|Chromebook/.test(client.name) ? "pc" : "phone" };
  $("deviceSelect").textContent = target?.id === id ? "Stop sending here" : "Always send here";
  $("deviceSelect").onclick = () => { $("deviceDialog").close(); toggleTarget(device); };
  $("deviceSend").onclick = () => {
    $("deviceDialog").close();
    pickTarget = device;
    $("picker").click();
  };
  $("kickBtn").onclick = async () => {
    $("deviceDialog").close();
    if (!await confirmBox(`Remove ${client.name}?`, "It is blocked until you quit Drop Air or allow it again in Settings. The QR code changes.", "Remove")) return;
    api(`/api/admin/kick/${id}`, { method: "POST" }).then(() => { toast(`${client.name} removed`); if (target?.id === id) setTarget(null); refresh(); }).catch(report);
  };
  $("deviceDialog").showModal();
  $("deviceDialog").focus();
}

function renderBlocked() {
  const blocked = state.blocked || [];
  if (!changed("blocked", blocked)) return;
  $("blockedList").replaceChildren(...blocked.map(device => el("div", { className: "blocked" },
    el("span", { textContent: `Removed: ${device.name} (${device.ip})` }),
    el("button", { className: "btn small", type: "button", textContent: "Allow again",
      onclick: () => api("/api/admin/unblock", { method: "POST", body: { ip: device.ip } }).then(refresh).catch(report) }))));
}

function setTarget(device) {
  target = device ? { kind: device.kind, id: device.id, name: device.name, icon: device.icon } : null;
  try { localStorage.setItem("dropAirTarget", JSON.stringify(target)); } catch (_) {}
  signatures.radar = signatures.targets = "";
  if (state) renderRadar();
}
const toggleTarget = device => setTarget(target?.id === device.id ? null : device);

// "Send to" buttons above the drop zone. A chosen device that went away stays chosen,
// so files meant for one person are never quietly shared with everyone.
function renderTargets(devices) {
  const online = !target || devices.some(d => d.id === target.id);
  $("targetLabel").textContent = !target ? "Everyone connected can download them"
    : online ? `Only for ${target.name}` : `Only for ${target.name}, which is not connected right now`;
  if (!changed("targets", [devices.map(d => [d.id, d.name]), target, online])) return;
  const choices = online ? devices : [...devices, target];
  const chip = (label, iconName, pressed, onclick, offline) => {
    const button = el("button", { type: "button", onclick, className: offline ? "offline" : "" }, iconName && icon(iconName), label);
    button.setAttribute("aria-pressed", pressed);
    return button;
  };
  $("toBar").hidden = !choices.length;
  $("toBar").replaceChildren(el("span", { textContent: "Send to" }), chip("Everyone", "", !target, () => setTarget(null)),
    ...choices.map(device => chip(device.name, device.icon, target?.id === device.id, () => toggleTarget(device), target?.id === device.id && !online)));
}

function renderFiles() {
  if (!changed("files", [state.files, state.admin, Math.floor(Date.now() / 60000)])) return;
  $("files").replaceChildren(...state.files.map(file => {
    const url = `/files/${encodeURIComponent(file.name)}?${authQuery()}`;
    let get;
    if (state.admin) {
      get = iconButton("download", `Save ${file.name} to Downloads`, () =>
        api(`/api/admin/save/${encodeURIComponent(file.name)}`, { method: "POST" })
          .then(result => toast(`Saved to Downloads as ${result.filename}`)).catch(report));
    } else {
      get = el("a", { className: "icon accent", href: url, download: file.name, title: `Download ${file.name}`,
        onclick: () => toast(`Downloading ${file.name}. Check your browser's downloads.`) }, icon("download"));
      get.setAttribute("aria-label", `Download ${file.name}`);
    }
    const route = file.to ? (file.to === clientId ? " · just for you" : " · private send") : "";
    const row = el("li", { className: "item" }, el("span", { className: `type ${typeIcon(file.name)}` }, icon(typeIcon(file.name))),
      el("div", { className: "meta" }, el("span", { className: "name", textContent: file.name, title: file.name }),
        el("span", { className: "sub", textContent: `${human(file.size)} · ${ago(file.modified)}${route}` })),
      el("div", { className: "actions" }, get,
        iconButton("copy", "Copy download link", () => copy(new URL(url, state.public_url).href), "copy-link"),
        iconButton("trash", `Delete ${file.name}`, event => remove(event.currentTarget, `/api/files/${encodeURIComponent(file.name)}`), "del")));
    if (enter(seen.files, file.name)) row.classList.add("enter");
    return row;
  }));
}

function renderTexts() {
  if (!changed("texts", [state.texts, Math.floor(Date.now() / 60000)])) return;
  $("texts").replaceChildren(...state.texts.map(item => {
    const row = el("li", { className: "item text-item" },
      el("div", { className: "meta" }, el("div", { className: "text-body", textContent: item.text }), el("span", { className: "sub", textContent: ago(item.created) })),
      el("div", { className: "actions" }, iconButton("copy", "Copy text", () => copy(item.text)),
        iconButton("trash", "Delete text", event => remove(event.currentTarget, `/api/text/${encodeURIComponent(item.id)}`), "del")));
    if (enter(seen.texts, item.id)) row.classList.add("enter");
    return row;
  }));
}

function renderRequests() {
  const requests = state.requests || [];
  if (!changed("requests", requests)) return;
  $("requests").replaceChildren(...requests.map(request => {
    const decide = decision => api(`/api/admin/pair/${request.id}`, { method: "POST", body: { decision } }).then(refresh).catch(report);
    return el("div", { className: "notice" },
      el("div", {}, el("strong", { textContent: `${request.device_name} wants to connect.` }), el("span", { textContent: ` ${request.address}` })),
      el("div", { className: "row" }, el("button", { className: "btn", textContent: "Decline", onclick: () => decide("decline") }),
        el("button", { className: "btn primary", textContent: "Allow", onclick: () => decide("accept") })));
  }));
}

function render() {
  const others = state.clients.filter(c => !c.ids.includes(clientId)).length;
  $("status").textContent = others ? `${others} device${others > 1 ? "s" : ""} connected` : "Ready on your Wi-Fi";
  $("status").className = others ? "on" : "ready";
  $("limit").textContent = `Up to ${state.limit_label}`;
  $("settingsBtn").hidden = $("folderBtn").hidden = !state.admin;
  $("allBtn").hidden = state.files.length < 2;
  $("finalNotice").hidden = state.edition !== "python";
  $("switchRust").hidden = !state.admin;
  $("shareUrl").textContent = state.public_url;
  const left = state.seconds_remaining;
  $("qrTimer").textContent = `New code in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  const qr = `/qr.svg?url=${encodeURIComponent(state.public_url)}`;
  if ($("qrImage").getAttribute("src") !== qr) $("qrImage").src = qr;
  renderRadar();
  renderFiles();
  renderTexts();
  renderRequests();
  renderBlocked();
  const count = state.files.length + state.texts.length;
  $("emptyReceive").hidden = count > 0;
  $("tabCount").hidden = !count;
  $("tabCount").textContent = count;
  const rules = [state.cleanup_minutes ? `files after ${state.cleanup_minutes} min` : "",
    state.text_ttl_minutes ? `text after ${state.text_ttl_minutes} min` : ""].filter(Boolean).join(", ");
  $("expiry").textContent = rules ? `Removed automatically: ${rules}` : "";
  $("discoveryStatus").textContent = state.discovery_status;
  $("about").textContent = `Drop Air ${state.version} · ${state.edition === "rust" ? "Rust" : "Python"} edition`;
  seen.first = false;
}

/* ---------- polling ---------- */

function showGate(wrongCode) {
  $("gate").hidden = false;
  $("gateTitle").textContent = wrongCode ? "Enter access code" : "This link has expired";
  $("gateText").textContent = wrongCode ? "Ask the person hosting Drop Air for the code." : "Scan the current QR code on the host PC.";
  $("gateCode").hidden = $("gateForm").querySelector("button").hidden = !wrongCode;
}

let timer = 0;
let busy = false;
async function refresh() {
  clearTimeout(timer);
  if (busy) return;
  busy = true;
  try {
    state = await api(`/api/state?${new URLSearchParams({ client_id: clientId, ...myDevice })}`);
    if (state.session_key) key = state.session_key;
    $("gate").hidden = true;
    render();
    hideSplash();
  } catch (error) {
    if (error.status) hideSplash();
    if (error.message === "code not found") showGate(true);
    else if (error.message === "invalid session link") showGate(false);
    else if (error.message === "sharing is off") {
      showGate(false);
      $("gateTitle").textContent = "Sharing is off";
      $("gateText").textContent = "Open Drop Air on the PC. This page continues by itself if it was connected before, otherwise scan the new QR code.";
    }
    else if (error.message === "removed by host") {
      showGate(false);
      $("gateTitle").textContent = "You were removed";
      $("gateText").textContent = "The host disconnected this device.";
    }
    else { $("status").textContent = "Reconnecting…"; $("status").className = ""; }
  } finally {
    busy = false;
    clearTimeout(timer);
    // Hidden pages poll rarely to keep CPU and battery use low.
    timer = setTimeout(refresh, document.hidden ? 15000 : 1200);
  }
}
document.addEventListener("visibilitychange", () => {
  document.body.classList.toggle("paused", document.hidden);
  if (!document.hidden) refresh();
});
// Keep the start-up animation on screen long enough to be seen once.
function hideSplash() {
  const splash = $("splash");
  if (!splash || splash.classList.contains("done")) return;
  setTimeout(() => {
    splash.classList.add("done");
    setTimeout(() => splash.remove(), 600);
  }, Math.max(0, (motion() ? 1100 : 0) - performance.now()));
}
window.dropAirResume = refresh;

/* ---------- sending ---------- */

const queue = [];
let active = 0;

async function pairWith(device) {
  if (peerLinks.has(device.id)) return peerLinks.get(device.id);
  toast(`Asking ${device.name} to accept…`);
  const base = `/api/discovery/${device.id}/pair`;
  const { request } = await api(base, { method: "POST", body: { device_id: state.device_id, device_name: state.device_name } });
  for (let tries = 0; tries < 80; tries++) {
    await new Promise(resolve => setTimeout(resolve, 1500));
    const result = await api(`${base}/${request.id}`);
    if (result.decision === "accepted") {
      const url = new URL(result.share_url);
      const link = { origin: url.origin, key: url.searchParams.get("k"), code: url.searchParams.get("code") || "" };
      peerLinks.set(device.id, link);
      return link;
    }
    if (result.decision === "declined") break;
  }
  throw new Error(`${device.name} did not accept`);
}

async function send(files, to = target) {
  if (!files.length || !state) return;
  const tooBig = files.find(file => file.size > state.limit_bytes);
  if (tooBig) return toast(`${tooBig.name} is larger than ${state.limit_label}`);
  let peer = null;
  if (to?.kind === "peer" && !state.peers.some(p => p.device_id === to.id)) return toast(`${to.name} is not nearby right now`);
  if (to?.kind === "peer") {
    try { peer = await pairWith(to); } catch (error) { return report(error); }
  }
  flyTo(to);
  for (const file of files) {
    const job = { file, to, peer, sent: 0, xhr: null, done: false };
    job.sub = el("span", { className: "sub", textContent: `Waiting · ${human(file.size)}` });
    job.fill = el("i");
    job.row = el("li", { className: "item enter" }, el("span", { className: "type" }, icon(typeIcon(file.name))),
      el("div", { className: "meta" }, el("span", { className: "name", textContent: file.name }), job.sub, el("div", { className: "bar" }, job.fill)),
      iconButton("x", `Cancel ${file.name}`, () => { job.cancelled = true; job.xhr ? job.xhr.abort() : finish(job, "Cancelled", "bad"); }, "del"));
    $("queue").append(job.row);
    queue.push(job);
  }
  pump();
}

// A paper plane leaves the drop zone for the receiving device.
function flyTo(device) {
  if (!motion()) return;
  const from = $("drop").getBoundingClientRect();
  const nodes = [...$("nodes").children];
  const aim = (device && nodes.find(node => node.querySelector("span").textContent === device.name)) || nodes[0];
  if (!aim || !from.width) return;
  const to = aim.querySelector("b").getBoundingClientRect();
  const start = [from.left + from.width / 2 - 15, from.top + from.height / 3];
  const end = [to.left + to.width / 2 - 15, to.top + to.height / 2 - 15];
  const plane = icon("send");
  plane.classList.add("plane");
  document.body.append(plane);
  plane.animate([
    { transform: `translate(${start[0]}px, ${start[1]}px) scale(.6)`, opacity: 0 },
    { transform: `translate(${(start[0] + end[0]) / 2 + 60}px, ${(start[1] + end[1]) / 2}px) scale(1.4)`, opacity: 1 },
    { transform: `translate(${end[0]}px, ${end[1]}px) scale(.4)`, opacity: .2 },
  ], { duration: 750, easing: "cubic-bezier(.4,0,.2,1)" }).onfinish = () => plane.remove();
}

function pump() {
  while (active < 4 && queue.length) {
    const job = queue.shift();
    if (job.cancelled) continue;
    active++;
    upload(job);
  }
}

function finish(job, message, tone) {
  if (job.done) return;
  job.done = true;
  job.sub.textContent = message;
  job.sub.className = `sub ${tone}`;
  if (tone === "good") {
    job.fill.style.transform = "scaleX(1)";
    job.row.classList.add("success");
    job.row.querySelector(".type").replaceChildren(icon("check"));
  }
  const wait = tone === "good" ? 2500 : 8000;
  setTimeout(() => job.row.classList.add("leave"), wait);
  setTimeout(() => job.row.remove(), wait + 300);
}

function upload(job) {
  const { file, to, peer } = job;
  const xhr = job.xhr = new XMLHttpRequest();
  const params = new URLSearchParams({ name: file.name, from: clientId });
  if (to && !peer) params.set("to", to.id);
  if (peer) { params.set("k", peer.key); if (peer.code) params.set("code", peer.code); }
  xhr.open("POST", `${peer ? peer.origin : ""}/api/upload?${params}`);
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  if (!peer) {
    xhr.setRequestHeader("X-Drop-Air-Key", key);
    xhr.setRequestHeader("X-Drop-Air-Code", encodeURIComponent(code));
  }
  const samples = [[performance.now(), 0]];
  xhr.upload.onprogress = event => {
    const now = performance.now();
    samples.push([now, event.loaded]);
    while (samples.length > 2 && now - samples[0][0] > 3000) samples.shift();
    const speed = (event.loaded - samples[0][1]) / Math.max((now - samples[0][0]) / 1000, .001);
    job.fill.style.transform = `scaleX(${file.size ? event.loaded / file.size : 1})`;
    job.sub.textContent = `${human(event.loaded)} of ${human(file.size)} · ${human(speed)}/s · ${speed ? duration((file.size - event.loaded) / speed) : "…"} left`;
  };
  xhr.onloadend = () => {
    active--;
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (_) {}
    if (xhr.status >= 200 && xhr.status < 300) finish(job, to ? `Sent to ${to.name}` : "Sent", "good");
    else {
      if (peer) peerLinks.delete(to.id);
      finish(job, job.cancelled ? "Cancelled" : data.error === "missing file" ? `${to?.name} runs an older Drop Air` : data.error || "Upload failed", "bad");
    }
    pump();
    refresh();
  };
  xhr.send(file);
}

/* ---------- inputs ---------- */

$("pickBtn").onclick = () => { pickTarget = target; $("picker").click(); };
$("picker").onclick = () => { $("picker").value = ""; };
$("picker").onchange = () => { send([...$("picker").files], pickTarget || target); pickTarget = null; };

const drop = $("drop");
let depth = 0;
window.addEventListener("dragenter", () => { depth++; drop.classList.add("over"); });
window.addEventListener("dragleave", () => { if (--depth <= 0) drop.classList.remove("over"); });
window.addEventListener("dragover", event => event.preventDefault());
window.addEventListener("drop", event => {
  event.preventDefault();
  depth = 0;
  drop.classList.remove("over");
  send([...event.dataTransfer.files]);
});

async function sendText(text) {
  await api("/api/text", { method: "POST", body: { text } });
  toast("Text shared");
  await refresh();
}
$("textForm").onsubmit = event => {
  event.preventDefault();
  const text = $("textInput").value;
  if (text.trim()) sendText(text).then(() => { $("textInput").value = ""; }).catch(report);
};
$("textInput").onkeydown = event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) $("textForm").requestSubmit(); };

const stamp = () => new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-");
const named = file => file.name === "image.png" || !file.name ? new File([file], `pasted-${stamp()}.${(file.type.split("/")[1] || "bin")}`, { type: file.type }) : file;
// Sends whatever a paste event carries. Returns false when it carried nothing usable.
function sendPasted(data) {
  const files = [...data.files].map(named);
  const text = data.getData("text/plain");
  if (files.length) send(files);
  else if (text.trim()) sendText(text).catch(report);
  else return false;
  return true;
}
document.addEventListener("paste", event => {
  const box = event.target.closest?.("#pasteBox");
  const typing = !box && event.target.closest?.("input,textarea");
  // Text typed into a field pastes normally; a picture pasted there is sent.
  if (typing && !(event.target.id === "textInput" && event.clipboardData.files.length)) return;
  if (sendPasted(event.clipboardData)) {
    event.preventDefault();
    closeDialog($("pasteDialog"));
  }
});
// Some phone browsers insert the picture instead of handing it to the paste event.
$("pasteBox").addEventListener("input", async () => {
  const image = $("pasteBox").querySelector("img");
  if (!image) return;
  try {
    const blob = await (await fetch(image.src)).blob();
    send([named(new File([blob], "image.png", { type: blob.type }))]);
    closeDialog($("pasteDialog"));
  } catch (_) { toast("This picture could not be read. Use Choose files."); }
  $("pasteBox").replaceChildren();
});
function openPasteBox() {
  $("pasteBox").replaceChildren();
  $("pasteDialog").showModal();
  $("pasteBox").focus();
}
$("pasteBtn").onclick = async () => {
  // Reading the clipboard from a button only works on the host (http://127.0.0.1).
  // Phones reach Drop Air over plain http, where browsers do not allow it.
  if (!window.isSecureContext || !navigator.clipboard) return openPasteBox();
  try {
    const items = navigator.clipboard.read ? await navigator.clipboard.read() : [];
    for (const item of items) {
      const type = item.types.find(t => t.startsWith("image/"));
      if (type) return send([named(new File([await item.getType(type)], "image.png", { type }))]);
    }
    const text = await navigator.clipboard.readText();
    if (text.trim()) return await sendText(text);
    toast("Clipboard is empty");
  } catch (_) {
    openPasteBox();
  }
};

for (const [tab, name] of [[$("tabSend"), "send"], [$("tabReceive"), "receive"]]) {
  tab.onclick = () => {
    document.querySelector(".lanes").dataset.tab = name;
    document.querySelector(".tabs").dataset.tab = name;
    $("tabSend").setAttribute("aria-selected", name === "send");
    $("tabReceive").setAttribute("aria-selected", name === "receive");
  };
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("dropAirTheme", theme); } catch (_) {}
  document.querySelectorAll("[data-theme-value]").forEach(button => button.setAttribute("aria-pressed", button.dataset.themeValue === theme));
}
const currentTheme = () => document.documentElement.dataset.theme === "light" ? "light" : "dark";
$("themeBtn").onclick = () => setTheme(currentTheme() === "dark" ? "light" : "dark");
document.querySelectorAll("[data-theme-value]").forEach(button => { button.onclick = () => setTheme(button.dataset.themeValue); });
document.querySelectorAll("[data-close]").forEach(button => { button.onclick = () => closeDialog(button.closest("dialog")); });
document.querySelectorAll("dialog:not(#confirmDialog)").forEach(dialog => {
  // Escape closes with the same animation as the close button.
  dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(dialog); });
});
$("allBtn").onclick = async () => {
  for (const button of $("files").querySelectorAll('.actions [title^="Save "], .actions a[download]')) {
    button.click();
    await new Promise(resolve => setTimeout(resolve, 350));
  }
};
$("qrBtn").onclick = () => { $("qrDialog").showModal(); $("qrDialog").focus(); };
$("copyLink").onclick = () => copy(state.public_url);
$("gateForm").onsubmit = event => { event.preventDefault(); code = $("gateCode").value.trim(); refresh(); };

/* ---------- host settings ---------- */

const form = $("settingsForm");
// Chips and custom inputs edit the same value; the inputs hold the truth.
const fields = () => [...form.querySelectorAll("input[name]")];
const field = name => form.querySelector(`input[name="${name}"]`);
function markChips() {
  document.querySelectorAll(".chips").forEach(group => {
    const value = Number(field(group.dataset.for).value);
    group.querySelectorAll("button").forEach(button => button.setAttribute("aria-pressed", Number(button.dataset.value) === value));
  });
}
const changedSettings = () => { markChips(); $("saveBar").hidden = false; };
document.querySelectorAll(".chips button").forEach(button => {
  button.onclick = () => {
    field(button.parentNode.dataset.for).value = button.dataset.value;
    changedSettings();
  };
});
form.addEventListener("input", changedSettings);
$("settingsBtn").onclick = () => {
  for (const input of fields()) {
    if (input.type === "checkbox") input.checked = !!state.settings[input.name];
    else input.value = state.settings[input.name] ?? "";
  }
  markChips();
  setTheme(currentTheme());
  $("saveBar").hidden = true;
  $("settingsDialog").showModal();
  $("settingsDialog").focus();
};
form.onsubmit = async event => {
  event.preventDefault();
  const body = {};
  for (const input of fields()) {
    body[input.name] = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value;
  }
  try {
    const { settings } = await api("/api/settings", { method: "POST", body });
    code = settings.access_code;
    closeDialog($("settingsDialog"));
    setTimeout(() => toast("Settings saved"), 220);
    refresh();
  } catch (error) { report(error); }
};
document.querySelectorAll("[data-clean]").forEach(button => {
  button.onclick = async () => {
    const what = button.dataset.clean === "uploads" ? "all shared files" : "all shared text";
    $("settingsDialog").close();
    if (await confirmBox("Delete?", `This deletes ${what} on the host PC.`, "Delete")) {
      api("/api/admin/cleanup", { method: "POST", body: { target: button.dataset.clean } }).then(() => { toast("Cleared"); refresh(); }).catch(report);
    }
  };
});
$("rediscover").onclick = () => api("/api/admin/rediscover", { method: "POST" }).then(() => toast("Searching again")).catch(report);
$("firewall").onclick = () => api("/api/admin/firewall", { method: "POST" }).catch(report);
$("folderBtn").onclick = () => api("/api/admin/reveal", { method: "POST" }).catch(report);
$("quitBtn").onclick = async () => {
  $("settingsDialog").close();
  if (!await confirmBox("Quit Drop Air?", "Sharing stops and shared files are removed.", "Quit")) return;
  await api("/api/admin/quit", { method: "POST" }).catch(report);
  clearTimeout(timer);
  refresh = async () => {};
  $("gate").hidden = false;
  $("gateTitle").textContent = "Drop Air stopped";
  $("gateText").textContent = "You can close this window.";
  $("gateCode").hidden = $("gateForm").querySelector("button").hidden = true;
};

async function watchUpdate() {
  for (;;) {
    const status = await api("/api/admin/update/status");
    $("updateText").textContent = status.message;
    $("updateProgress").hidden = !status.running;
    $("updateProgress").value = status.percent;
    if (!status.running) return status;
    await new Promise(resolve => setTimeout(resolve, 600));
  }
}
$("checkUpdate").onclick = async () => {
  $("updateText").textContent = "Checking…";
  try {
    const info = await api("/api/admin/update");
    $("updateText").textContent = info.message;
    $("installUpdate").hidden = !info.update_available;
  } catch (error) { $("updateText").textContent = error.message; }
};
async function install() {
  try {
    await api("/api/admin/update", { method: "POST" });
    await watchUpdate();
  } catch (error) { report(error); }
}
$("installUpdate").onclick = install;
$("switchRust").onclick = async () => {
  if (!await confirmBox("Switch to the Rust version?", "Drop Air downloads the Rust installer, closes, and starts it. Your settings stay.", "Download and switch")) return;
  $("settingsDialog").showModal();
  $("settingsDialog").focus();
  $("installUpdate").hidden = true;
  install();
};
refresh();
