"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STORE_KEY = "flight-agent-prefs-v2";

const fmtPrice = (p, cur = "EUR") =>
  p == null ? "–" : new Intl.NumberFormat("de-DE", { style: "currency", currency: cur || "EUR", maximumFractionDigits: 0 }).format(p);
const fmtNum = (n) => (n == null ? "–" : new Intl.NumberFormat("de-DE").format(n));
const fmtDur = (m) => (m == null ? "–" : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`);
const fmtTime = (t) => {
  if (!t) return "–";
  const [d, hm] = t.split(" ");
  const [, mo, da] = d.split("-");
  return `${da}.${mo}. ${hm ?? ""}`.trim();
};
const tripLabel = (t) => ({ round_trip: "Hin/Rück", open_jaw: "Gabelflug" }[t] || "One-Way");
const SOURCE = { google_flights: "Google Flights", duffel: "Duffel", demo: "Demo", seats_aero: "seats.aero", explore: "Günstigste Daten" };
const route = (segs) => (segs?.length ? [segs[0].from_airport, ...segs.map((s) => s.to_airport)].join(" → ") : "");
let CONFIG = { programs: {}, mr_transfers: {} };

// Sehr kleiner, sicherer Markdown-Renderer (Überschriften, Listen, fett, kursiv, Links).
function md(text) {
  const inline = (s) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<em>$2</em>")
      .replace(/`(.+?)`/g, "<code>$1</code>")
      .replace(/\[(.+?)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const out = [];
  let list = null;
  for (const raw of (text || "").split("\n")) {
    const line = raw.trimEnd();
    const item = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (item) { (list ||= []).push(`<li>${inline(item[1])}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) out.push(`<h3>${inline(h[2])}</h3>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}

// ------------------------------------------------------------------ Präferenzen
const NUM = ["adults", "flex_days", "carry_on_bags", "checked_bags", "min_layover_min"];
const OPT_NUM = ["max_stops", "max_duration_h", "max_layover_h", "max_ground_travel_h"];
const BOOL = ["allow_overnight_layover", "allow_separate_tickets", "allow_positioning", "consider_miles", "try_other_markets"];

function readPrefs() {
  const p = { priority: document.querySelector('input[name="priority"]:checked').value, cabin: $("#cabin").value, notes: $("#notes").value };
  for (const k of NUM) p[k] = Number($("#" + k).value || 0);
  for (const k of OPT_NUM) p[k] = $("#" + k).value === "" ? null : Number($("#" + k).value);
  for (const k of BOOL) p[k] = $("#" + k).checked;
  if (p.min_layover_min === 0) p.min_layover_min = null;
  p.avoid_airlines = $("#avoid_airlines").value.split(",").map((s) => s.trim()).filter(Boolean);
  return p;
}

function writePrefs(p) {
  if (!p) return;
  const r = document.querySelector(`input[name="priority"][value="${p.priority}"]`);
  if (r) r.checked = true;
  if (p.cabin) $("#cabin").value = p.cabin;
  for (const k of [...NUM, ...OPT_NUM]) if (k in p) $("#" + k).value = p[k] ?? "";
  for (const k of BOOL) if (k in p) $("#" + k).checked = !!p[k];
  $("#avoid_airlines").value = (p.avoid_airlines || []).join(", ");
  $("#notes").value = p.notes || "";
}

function savePrefsLocal(p) { try { localStorage.setItem(STORE_KEY, JSON.stringify(p)); } catch { /* egal */ } }
function loadPrefsLocal() { try { return JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch { return null; } }

// ------------------------------------------------------------------ Profil
async function loadProfile() {
  const p = await (await fetch("/api/profile")).json();
  $("#home_airports").value = p.home_airports;
  $("#home_location").value = p.home_location || "";
  $("#car_cost_per_km").value = p.car_cost_per_km;
  $("#max_drive_h").value = p.max_drive_h;
  PROFILE_OVERRIDES = { ...(p.airport_overrides || {}) };
  PROFILE_ACCESS = JSON.parse(JSON.stringify(p.access || {}));
  loadNearby();
  $("#card").value = p.card;
  $("#card_fx_fee_pct").value = p.card_fx_fee_pct;
  $("#min_value").value = p.min_value_cent_per_mile;
  $("#profile_notes").value = p.notes || "";
  ACCOUNTS = Object.fromEntries(p.accounts.map((a) => [a.program, { ...a }]));
  // Programme mit Stand bleiben sichtbar, auch wenn sie nicht ausgewählt sind
  VISIBLE = [...new Set([...(p.visible_programs || DEFAULT_PROGRAMS), ...p.accounts.filter((a) => a.balance > 0).map((a) => a.program)])];
  renderAccounts();
  const t = CONFIG.mr_transfers;
  const fmtT = (k, v) => `${CONFIG.programs[k] || k} ${v.mr}:${v.miles}${v.via ? " via Payback" : ""}`;
  $("#transfers").textContent = `Amex-Transfers (DE, Stand ${CONFIG.loyalty_stand}, vor Transfer in der Amex-App prüfen): ` +
    Object.entries(t).filter(([k]) => VISIBLE.includes(k)).map(([k, v]) => fmtT(k, v)).join(" · ");
  $("#transfers_all").textContent = "Alle Amex-Partner: " + Object.entries(t).map(([k, v]) => fmtT(k, v)).join(" · ") +
    " · Hotels: " + Object.entries(CONFIG.mr_hotel_transfers || {}).map(([k, v]) => `${k} ${v.mr}:${v.miles}`).join(" · ");
  PROFILE_BONUSES = p.transfer_bonuses || [];
  $("#bonuses").innerHTML = PROFILE_BONUSES.map(bonusRow).join("");
  $("#credits").innerHTML = (p.credits || []).map(creditRow).join("");
  $("#sweet_spots").innerHTML = (CONFIG.sweet_spots || []).map((x) => `<li><strong>${esc(x.title)}</strong> <span class="src">${esc(CONFIG.programs[x.program] || x.program)}</span><br>${esc(x.detail)}${x.caveat ? `<br><span class="fine">Haken: ${esc(x.caveat)}</span>` : ""}</li>`).join("");
}

let PROFILE_BONUSES = [];
const DEFAULT_PROGRAMS = ["amex_mr", "miles_and_more", "ba_club"];
let ACCOUNTS = {};
let VISIBLE = [...DEFAULT_PROGRAMS];

function readAccountRows() {
  document.querySelectorAll("#accounts tr").forEach((tr) => {
    ACCOUNTS[tr.dataset.program] = { program: tr.dataset.program, balance: Number(tr.querySelector(".bal").value || 0),
      status: tr.querySelector(".status").value.trim() };
  });
}

function renderAccounts(highlight = []) {
  $("#accounts").innerHTML = Object.entries(CONFIG.programs).filter(([key]) => VISIBLE.includes(key)).map(([key, name]) => `
    <tr data-program="${esc(key)}" class="${highlight.includes(key) ? "picked" : ""}">
      <td>${esc(name)}</td>
      <td><input type="number" min="0" step="1" class="bal" value="${ACCOUNTS[key]?.balance ?? 0}"></td>
      <td><input class="status" value="${esc(ACCOUNTS[key]?.status ?? "")}" placeholder="–"></td>
    </tr>`).join("");
  $("#more_programs").innerHTML = Object.entries(CONFIG.programs).map(([key, name]) =>
    `<label><input type="checkbox" class="prog_toggle" value="${esc(key)}" ${VISIBLE.includes(key) ? "checked" : ""}> ${esc(name)}</label>`).join("");
}

document.addEventListener("change", (ev) => {
  if (!ev.target.classList.contains("prog_toggle")) return;
  readAccountRows();
  const k = ev.target.value;
  VISIBLE = ev.target.checked ? [...new Set([...VISIBLE, k])] : VISIBLE.filter((x) => x !== k);
  renderAccounts();
});

// Screenshot verkleinern (max. 2000 px, JPEG) und auswerten lassen
async function shrinkImage(file) {
  const img = await createImageBitmap(file);
  const scale = Math.min(1, 2000 / Math.max(img.width, img.height));
  const c = document.createElement("canvas");
  c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return new Promise((res) => c.toBlob(res, "image/jpeg", 0.88));
}

document.addEventListener("click", (ev) => { if (ev.target.id === "shot_btn") $("#shot_file").click(); });
document.addEventListener("change", async (ev) => {
  if (ev.target.id !== "shot_file" || !ev.target.files.length) return;
  const file = ev.target.files[0];
  ev.target.value = "";
  const msg = $("#shot_msg"), btn = $("#shot_btn");
  btn.disabled = true; msg.textContent = "Werte Screenshot aus …";
  try {
    let blob = file;
    try { blob = await shrinkImage(file); } catch { /* z. B. HEIC ohne Decoder: Original schicken */ }
    const fd = new FormData();
    fd.append("image", blob, "screenshot.jpg");
    const r = await fetch("/api/profile/accounts/from-screenshot", { method: "POST", body: fd });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `Fehler ${r.status}`);
    readAccountRows();
    const found = data.accounts || [];
    for (const a of found) ACCOUNTS[a.program] = { program: a.program, balance: a.balance, status: a.status || ACCOUNTS[a.program]?.status || "" };
    VISIBLE = [...new Set([...VISIBLE, ...found.map((a) => a.program)])];
    renderAccounts(found.map((a) => a.program));
    const notes = found.map((a) => a.note).filter(Boolean).concat(data.note ? [data.note] : []);
    msg.textContent = found.length
      ? `Erkannt: ${found.map((a) => `${CONFIG.programs[a.program] || a.program} ${fmtNum(a.balance)}`).join(", ")}. Bitte prüfen und „Profil speichern“.${notes.length ? " " + notes.join(" ") : ""}`
      : (data.note || "Kein Kontostand erkannt.");
  } catch (e) { msg.textContent = e.message; }
  btn.disabled = false;
});
const bonusRow = (b) => `<tr>
  <td><select class="b_prog">${Object.keys(CONFIG.mr_transfers).filter((k) => k !== "miles_and_more").map((k) => `<option value="${esc(k)}" ${k === b.program ? "selected" : ""}>${esc(CONFIG.programs[k] || k)}</option>`).join("")}</select></td>
  <td><input class="b_pct" type="number" min="1" max="200" value="${esc(b.bonus_pct ?? "")}"></td>
  <td><input class="b_until" type="date" value="${esc(b.valid_until || "")}"></td>
  <td><button type="button" class="copy row_del">✕</button><input type="hidden" class="b_note" value="${esc(b.note || "")}"><input type="hidden" class="b_link" value="${esc(b.link || "")}"></td></tr>`;
const creditRow = (c) => `<tr>
  <td><input class="c_name" value="${esc(c.name || "")}" placeholder="z. B. Reiseguthaben Karte"></td>
  <td><input class="c_amount" type="number" min="0" step="1" value="${esc(c.amount_eur ?? "")}"></td>
  <td><input class="c_cond" value="${esc(c.condition || "")}" placeholder="z. B. nur über das Reiseportal der Karte"></td>
  <td><input class="c_until" type="date" value="${esc(c.valid_until || "")}"></td>
  <td><button type="button" class="copy row_del">✕</button></td></tr>`;
document.addEventListener("click", (ev) => { if (ev.target.classList.contains("row_del")) ev.target.closest("tr").remove(); });
document.addEventListener("click", (ev) => {
  if (ev.target.id === "add_bonus") $("#bonuses").insertAdjacentHTML("beforeend", bonusRow({ program: "flying_blue" }));
  if (ev.target.id === "add_credit") $("#credits").insertAdjacentHTML("beforeend", creditRow({}));
});

async function saveProfile() {
  readAccountRows();
  const accounts = Object.values(ACCOUNTS).filter((a) => VISIBLE.includes(a.program) && (a.balance > 0 || a.status || DEFAULT_PROGRAMS.includes(a.program)));
  const body = {
    home_airports: $("#home_airports").value.toUpperCase().replace(/\s/g, "") || "FRA",
    home_location: $("#home_location").value.trim(),
    car_cost_per_km: Number($("#car_cost_per_km").value || 0),
    max_drive_h: Number($("#max_drive_h").value || 0),
    airport_overrides: PROFILE_OVERRIDES,
    access: readAccess(),
    accounts,
    visible_programs: VISIBLE,
    card: $("#card").value,
    card_fx_fee_pct: Number($("#card_fx_fee_pct").value || 0),
    min_value_cent_per_mile: Number($("#min_value").value || 0),
    notes: $("#profile_notes").value,
    transfer_bonuses: [...document.querySelectorAll("#bonuses tr")].map((tr) => ({
      program: tr.querySelector(".b_prog").value, bonus_pct: Number(tr.querySelector(".b_pct").value || 0),
      valid_until: tr.querySelector(".b_until").value || null, note: tr.querySelector(".b_note").value,
      link: tr.querySelector(".b_link").value,
    })).filter((b) => b.bonus_pct > 0),
    credits: [...document.querySelectorAll("#credits tr")].map((tr) => ({
      name: tr.querySelector(".c_name").value.trim(), amount_eur: Number(tr.querySelector(".c_amount").value || 0),
      condition: tr.querySelector(".c_cond").value.trim(), valid_until: tr.querySelector(".c_until").value || null,
    })).filter((c) => c.name),
  };
  const r = await fetch("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  $("#profile_msg").textContent = r.ok ? "Gespeichert ✓" : `Fehler: ${(await r.json().catch(() => ({}))).detail || r.status}`;
  if (r.ok) { loadNearby(); PROFILE_BONUSES = body.transfer_bonuses; }
  setTimeout(() => ($("#profile_msg").textContent = ""), 3000);
}

let PROFILE_OVERRIDES = {};
let PROFILE_ACCESS = {};
let NEARBY = [];

async function loadNearby() {
  NEARBY = await (await fetch("/api/nearby")).json();
  renderAirports();
}

function renderAirports() {
  const max = Number($("#max_drive_h").value || 0);
  if (!NEARBY.length) {
    $("#nearby").textContent = "Trag deinen Wohnort ein und speichere, dann erscheinen hier die Flughäfen in Fahrweite.";
    $("#airport_list").innerHTML = "";
    return;
  }
  const sel = (a) => (a.code in PROFILE_OVERRIDES ? PROFILE_OVERRIDES[a.code] : a.drive_h <= max);
  const chosen = NEARBY.filter(sel);
  $("#nearby").textContent = `${chosen.length} Flughäfen ausgewählt: ${chosen.map((a) => a.code).join(", ")}. Fahrzeiten sind Schätzungen. Häkchen setzen oder entfernen, dann speichern.`;
  $("#airport_list").innerHTML = NEARBY.map((a) => `<label class="${a.code in PROFILE_OVERRIDES ? "manual" : ""}">
      <input type="checkbox" data-code="${esc(a.code)}" ${sel(a) ? "checked" : ""}>
      <strong>${esc(a.code)}</strong> ${esc(a.name)} <span class="dur">~${a.drive_h.toLocaleString("de-DE")} h</span></label>`).join("");
  renderAccess(chosen);
}

function renderAccess(chosen) {
  $("#access_rows").innerHTML = chosen.map((a) => {
    const acc = PROFILE_ACCESS[a.code] || {};
    const mode = acc.mode || a.default_mode;
    const park = acc.parking || [];
    return `<tr data-code="${esc(a.code)}">
      <td><strong>${esc(a.code)}</strong> <span class="dur">${esc(a.name)}</span></td>
      <td><select class="acc_mode"><option value="car" ${mode === "car" ? "selected" : ""}>Auto + Parken</option><option value="taxi" ${mode === "taxi" ? "selected" : ""}>Taxi</option></select></td>
      <td><input class="acc_taxi" type="number" min="0" step="5" value="${acc.taxi_cost ?? ""}" data-default="${a.default_taxi}" placeholder="${mode === "taxi" ? a.default_taxi : "–"}" ${mode === "taxi" ? "" : "disabled"}></td>
      <td class="park">${[0, 1, 2, 3].map((i) => `<input class="acc_park" type="number" min="0" step="1" value="${park[i] ?? ""}" placeholder="${a.default_parking[i]}" ${mode === "car" ? "" : "disabled"}>`).join("")}</td>
    </tr>`;
  }).join("");
}

function readAccess() {
  const out = {};
  for (const tr of document.querySelectorAll("#access_rows tr")) {
    const a = NEARBY.find((x) => x.code === tr.dataset.code);
    const mode = tr.querySelector(".acc_mode").value;
    const taxi = tr.querySelector(".acc_taxi").value;
    const park = [...tr.querySelectorAll(".acc_park")].map((i) => i.value);
    const parkAll = park.every((v) => v !== "");
    if (mode !== a.default_mode || taxi !== "" || parkAll) {
      out[a.code] = { mode, taxi_cost: taxi === "" ? null : Number(taxi), parking: parkAll ? park.map(Number) : null };
    }
  }
  // Einstellungen für gerade nicht ausgewählte Flughäfen behalten
  for (const [k, v] of Object.entries(PROFILE_ACCESS)) if (!(k in out) && !document.querySelector(`#access_rows tr[data-code="${k}"]`)) out[k] = v;
  return out;
}

document.addEventListener("change", (ev) => {
  if (!ev.target.classList.contains("acc_mode")) return;
  const tr = ev.target.closest("tr");
  const taxi = ev.target.value === "taxi";
  const t = tr.querySelector(".acc_taxi");
  t.disabled = !taxi;
  t.placeholder = taxi ? t.dataset.default : "–";
  tr.querySelectorAll(".acc_park").forEach((i) => (i.disabled = taxi));
});

document.addEventListener("change", (ev) => {
  const cb = ev.target.closest("#airport_list input[type=checkbox]");
  if (!cb) return;
  const a = NEARBY.find((x) => x.code === cb.dataset.code);
  const auto = a.drive_h <= Number($("#max_drive_h").value || 0);
  if (cb.checked === auto) delete PROFILE_OVERRIDES[a.code];
  else PROFILE_OVERRIDES[a.code] = cb.checked;
  renderAirports();
});
document.addEventListener("input", (ev) => { if (ev.target.id === "max_drive_h") renderAirports(); });

// ------------------------------------------------------------------ Ergebnisse
function legHtml(o) {
  const block = (label, segs, lays, stops) => {
    if (!segs?.length) return "";
    const s = stops === 0 ? "Nonstop" : `${stops} Umstieg${stops > 1 ? "e" : ""}`;
    const lay = lays.map((l) => `${l.airport} ${fmtDur(l.duration_min)}${l.overnight ? " (über Nacht)" : ""}`).join(", ");
    return `<div>${label ? `<span class="src">${label}</span> ` : ""}${esc(route(segs))}</div>
      <div class="meta">${esc(fmtTime(segs[0].departure))} → ${esc(fmtTime(segs.at(-1).arrival))} · ${esc(s)}${lay ? ` (${esc(lay)})` : ""}</div>
      <div class="meta">${esc(segs.map((x) => x.flight_number).filter(Boolean).join(", "))}</div>`;
  };
  const notes = [...new Set(o.segments.flatMap((s) => s.extensions).concat(o.extensions))].filter((e) => /gepäck|bag|carry|verspät|delayed/i.test(e));
  if (o.baggage_note) notes.push(o.baggage_note);
  if (o.bags_note) notes.push(o.bags_note);
  const hasRet = o.return_segments?.length;
  return `<div class="leg">
    <div class="airline">${esc(o.airlines.join(", "))} · ${esc(tripLabel(o.trip_type))} <span class="src">${esc(SOURCE[o.source] || o.source)}${o.point_of_sale !== "de" ? ` · Markt ${esc(o.point_of_sale.toUpperCase())}` : ""}</span></div>
    ${block(hasRet ? "Hin" : "", o.segments, o.layovers, o.stops)}
    ${hasRet ? block("Rück", o.return_segments, o.return_layovers, o.return_layovers.length) : ""}
    ${["round_trip", "open_jaw"].includes(o.trip_type) && !hasRet ? '<div class="meta">Rückflug wählst du bei Google Flights (Preis gilt für beide Strecken).</div>' : ""}
    ${o.history_note ? `<div class="meta">Preisverlauf: ${esc(o.history_note)}</div>` : ""}
    ${o.fare_brand ? `<div class="meta">Tarif: ${esc(o.fare_brand)}</div>` : ""}
    ${notes.length ? `<div class="chips">${notes.map((n) => `<span class="chip warn">${esc(n)}</span>`).join("")}</div>` : ""}
  </div>`;
}

function groundHtml(g, total, cur) {
  if (!g) return "";
  const head = total != null ? `<div class="ground-total">Gesamt inkl. Anreise: <strong>${fmtPrice(total, cur)}</strong></div>` : "";
  return `${head}<div class="fineprint">${esc(g.note)}</div>`;
}

function awardHtml(a) {
  return `<div class="leg">
    <div class="airline">Prämie · ${esc(a.program)} <span class="src">seats.aero</span></div>
    <div>${esc(a.origin)} → ${esc(a.destination)} · ${esc(a.date)} · ${esc(a.cabin)}</div>
    <div class="meta">${fmtNum(a.miles)} Meilen + ${fmtPrice(a.taxes, a.taxes_currency)} · ${esc(a.airlines)}${a.direct ? " · direkt" : ""}${a.seats ? ` · ${a.seats} Plätze` : ""}</div>
  </div>`;
}

function briefing(p, prefs) {
  const lines = ["Hallo, bitte folgende Verbindung anbieten/buchen:", ""];
  lines.push(`Reisende: ${prefs.adults} Erwachsene · Kabine: ${prefs.cabin} · Gepäck: ${prefs.carry_on_bags} Handgepäck, ${prefs.checked_bags} Aufgabegepäck p. P.`, "");
  for (const o of p.options) {
    for (const [label, segs] of [["Hin", o.segments], ["Rück", o.return_segments || []]]) {
      for (const s of segs) lines.push(`${label}: ${s.flight_number || s.airline} ${s.from_airport}–${s.to_airport} ${s.departure} → ${s.arrival}`);
    }
    if (o.trip_type === "round_trip" && !(o.return_segments || []).length) lines.push("Rück: wie bei Google Flights gewählt (Hin/Rück-Tarif)");
    lines.push(`Gefundener Preis: ${fmtPrice(o.price, o.currency)} (${SOURCE[o.source] || o.source}, ${tripLabel(o.trip_type)})`, "");
  }
  for (const a of p.awards || []) lines.push(`Prämie: ${a.program} ${a.origin}–${a.destination} ${a.date}, ${fmtNum(a.miles)} Meilen + ${fmtPrice(a.taxes, a.taxes_currency)}`);
  lines.push("Hinweis: Bei Lufthansa-Group-Flügen bitte möglichst als NDC-Buchung (ohne GDS-Aufschlag).");
  lines.push(`Recherchiert am ${new Date().toLocaleDateString("de-DE")}. Preise können sich geändert haben.`);
  return lines.join("\n");
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* z. B. kein HTTPS */ }
  const ta = document.createElement("textarea");
  ta.value = text; document.body.appendChild(ta); ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  if (!ok) window.prompt("Text kopieren:", text);
  return ok;
}

function renderResult(r) {
  $("#results").hidden = false;
  $("#summary").innerHTML = md(r.summary_md) || "<p>Keine Zusammenfassung erhalten.</p>";

  const picked = new Set(r.picks.flatMap((p) => p.option_ids));
  $("#picks").innerHTML = r.picks.length
    ? r.picks.map((p, idx) => {
        const cur = p.options[0]?.currency || "EUR";
        const links = [...new Set(p.options.map((o) => o.booking_url).filter(Boolean))];
        const head = p.awards.length
          ? `<div class="price">${fmtNum(p.total_miles)} Meilen</div>
             <div class="note">+ ${fmtPrice(p.total_taxes, p.awards[0]?.taxes_currency)} Steuern/Gebühren für alle Reisenden</div>
             ${p.cents_per_mile != null ? (p.miles_worth_it
               ? `<div class="miles">Gegenwert: ${p.cents_per_mile.toLocaleString("de-DE")} ct pro Meile ✓</div>`
               : `<div class="miles bad">Gegenwert nur ${Math.max(0, p.cents_per_mile).toLocaleString("de-DE")} ct pro Meile (unter deinen ${r.min_value_cent_per_mile} ct): bar zahlen lohnt eher</div>`) : ""}`
          : `<div class="price">${fmtPrice(p.total_price, cur)}</div>
             <div class="note">${esc(p.options.length > 1 ? "Summe der Einzeltickets" : p.options[0]?.price_note)}</div>
             ${p.ground ? groundHtml(p.ground, p.total_with_ground, cur) : p.total_with_ground != null ? `<div class="ground-total">Gesamt inkl. Gepäck: <strong>${fmtPrice(p.total_with_ground, cur)}</strong></div>` : ""}
             ${p.bags_eur ? `<div class="fineprint">davon Aufgabegepäck geschätzt ${fmtPrice(p.bags_eur, cur)}</div>` : ""}`;
        return `<article class="pick">
          <div class="title">${esc(p.title)}</div>
          ${head}
          <div class="reason">${esc(p.reason)}</div>
          ${p.awards.map(awardHtml).join("")}
          ${p.options.map(legHtml).join("")}
          ${links.map((u, i) => `<a class="book" href="${esc(u)}" target="_blank" rel="noopener">Bei Google Flights ansehen${links.length > 1 ? ` (${i + 1})` : ""}</a>`).join("")}
          <button type="button" class="copy" data-pick="${idx}">Reisebüro-Anfrage kopieren</button>
        </article>`;
      }).join("")
    : '<p class="card">Der Agent hat keine Empfehlung abgegeben.</p>';
  document.querySelectorAll("button.copy").forEach((b) => b.addEventListener("click", async () => {
    const ok = await copyText(briefing(r.picks[Number(b.dataset.pick)], r.prefs));
    b.textContent = ok ? "Kopiert ✓" : "Reisebüro-Anfrage kopieren";
  }));

  $("#count").textContent = `(${r.options.length})`;
  $("#all tbody").innerHTML = r.options.map((o) => `
    <tr class="${picked.has(o.id) ? "picked" : ""}">
      <td class="num">${o.booking_url ? `<a href="${esc(o.booking_url)}" target="_blank" rel="noopener">${fmtPrice(o.price, o.currency)}</a>` : fmtPrice(o.price, o.currency)}</td>
      <td title="${esc([o.ground?.note, o.bags_note].filter(Boolean).join(" · "))}">${o.price != null && ((o.ground && o.ground.days != null) || o.bags_eur) ? fmtPrice(o.price + (o.ground?.days != null ? o.ground.total : 0) + (o.bags_eur || 0), o.currency) : "–"}</td>
      <td>${esc(SOURCE[o.source] || o.source)}${o.point_of_sale !== "de" ? ` (${esc(o.point_of_sale.toUpperCase())})` : ""}</td>
      <td>${esc(o.airlines.join(", "))}</td>
      <td>${esc(route(o.segments))}${o.return_segments?.length ? ` / ${esc(route(o.return_segments))}` : ""}</td>
      <td>${esc(fmtTime(o.segments[0]?.departure))}</td>
      <td>${esc(fmtTime(o.segments.at(-1)?.arrival))}</td>
      <td>${esc(fmtDur(o.total_duration_min))}</td>
      <td>${o.stops}</td>
      <td>${esc(tripLabel(o.trip_type))}</td>
    </tr>`).join("");

  $("#awards_block").hidden = !r.awards.length;
  $("#award_count").textContent = `(${r.awards.length})`;
  $("#awards tbody").innerHTML = r.awards.map((a) => `
    <tr><td class="num">${fmtNum(a.miles)}</td><td>${fmtPrice(a.taxes, a.taxes_currency)}</td><td>${esc(a.program)}</td>
    <td>${esc(a.origin)} → ${esc(a.destination)}</td><td>${esc(a.date)}</td><td>${esc(a.airlines)}</td>
    <td>${a.direct ? "ja" : "nein"}</td><td>${a.seats ?? "–"}</td></tr>`).join("");

  $("#searches").innerHTML = r.searches.map((s) => {
    const ins = s.insights;
    const level = ins?.price_level ? ` · Preisniveau: <span class="level-${esc(ins.price_level)}">${esc(ins.price_level)}</span>` : "";
    const range = ins?.typical_range ? ` (typisch ${ins.typical_range.map((v) => fmtPrice(v)).join("–")})` : "";
    const link = s.url ? ` · <a href="${esc(s.url)}" target="_blank" rel="noopener">Google Flights</a>` : "";
    const filt = s.filtered_out ? ` · ${s.filtered_out} aussortiert` : "";
    const dates = s.dates?.length ? `<br><span class="fine">${s.dates.slice(0, 5).map((d) => `${esc(d.origin)} ${fmtDate(d.outbound)}${d.inbound ? "–" + fmtDate(d.inbound) : ""}: ${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${fmtPrice(d.price)}</a>` : fmtPrice(d.price)}`).join(" · ")}</span>` : "";
    return `<li><strong>${esc(s.search_id)}</strong> <span class="src">${esc(SOURCE[s.source] || s.source)}</span> ${esc(s.label)} · ${s.count} Treffer${filt}${s.cached ? " (Cache)" : ""}${level}${range}${link}${s.error ? ` · <span class="level-high">${esc(s.error)}</span>` : ""}${dates}</li>`;
  }).join("");
}

// ------------------------------------------------------------------ Suche
async function run(ev) {
  ev.preventDefault();
  const btn = $("#go");
  btn.disabled = true;
  btn.textContent = "Suche läuft …";
  $("#log").innerHTML = "";
  $("#progress").hidden = false;
  $("#results").hidden = true;
  $("#error").hidden = true;
  const prefs = readPrefs();
  savePrefsLocal(prefs);
  const pending = {};

  try {
    const resp = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: $("#request").value, prefs, max_searches: $("#budget").value ? Number($("#budget").value) : null }),
    });
    if (!resp.ok) throw new Error(`Server antwortet mit ${resp.status}: ${await resp.text()}`);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) handle(JSON.parse(line), pending);
      }
    }
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Günstigste Flüge finden";
  }
}

function log(text, cls = "") {
  const li = document.createElement("li");
  li.className = cls;
  li.textContent = text;
  $("#log").appendChild(li);
  return li;
}

function handle(e, pending) {
  if (e.type === "status") log(e.text, "muted");
  else if (e.type === "search_started") pending[e.search_id] = log(`🔎 ${e.search_id}: ${e.label} …`);
  else if (e.type === "search_done") {
    const li = pending[e.search_id] || log("");
    let info;
    if (e.error && !e.count) info = e.error;
    else if (e.miles_min != null) info = `${e.count} Prämien, ab ${fmtNum(e.miles_min)} Meilen`;
    else info = `${e.count} Treffer, ab ${fmtPrice(e.cheapest, e.currency)}${e.filtered_out ? `, ${e.filtered_out} aussortiert` : ""}`;
    li.textContent = `✓ ${e.search_id}: ${e.label} – ${info}${e.cached ? " (Cache)" : ""}`;
  } else if (e.type === "result") {
    log("Fertig.", "muted");
    renderResult(e);
  } else if (e.type === "error") showError(e.text);
}

function showError(text) {
  const box = $("#error");
  box.hidden = false;
  box.textContent = text;
}

(async () => {
  writePrefs(loadPrefsLocal());
  try {
    CONFIG = await (await fetch("/api/config")).json();
    $("#budget").placeholder = CONFIG.max_searches;
    $("#sources").textContent = "Quellen: " + CONFIG.sources.map((s) => SOURCE[s] || s).join(", ") + (CONFIG.awards ? " + Prämien" : "");
    $("#logout").hidden = !CONFIG.login;
    const b = $("#mode");
    if (CONFIG.demo) { b.hidden = false; b.textContent = "DEMO-Daten"; }
    else if (!CONFIG.claude_configured) { b.hidden = false; b.textContent = "ANTHROPIC_API_KEY fehlt"; }
    await loadProfile();
  } catch (err) { showError("Konfiguration konnte nicht geladen werden: " + err.message); }
})();
$("#form").addEventListener("submit", run);
$("#save_profile").addEventListener("click", saveProfile);

// ================================================================== Tabs
function showTab(name) {
  const tabs = ["suche", "alarme", "community", "profil"];
  if (!tabs.includes(name)) name = "suche";
  for (const t of tabs) $("#tab-" + t).hidden = t !== name;
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
  if (name === "alarme") loadWatches();
  if (name === "community") loadCommunity();
}
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
showTab(location.hash.slice(1));

async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `HTTP ${r.status}`);
  return data;
}

// ================================================================== Preisalarme
let WATCHES = [];
const LEVEL = { low: "niedrig", typical: "typisch", high: "hoch" };
const fmtDate = (d) => (d ? new Date(d + "T00:00").toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) : "");

function sparkline(history, threshold) {
  if (!history || history.length < 2) return "";
  const w = 160, h = 36, pts = history.slice(-60).map((p) => p.price);
  const min = Math.min(...pts, threshold), max = Math.max(...pts, threshold), span = max - min || 1;
  const x = (i) => (i / (pts.length - 1)) * w, y = (v) => h - 2 - ((v - min) / span) * (h - 4);
  const path = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-label="Preisverlauf">
    <line x1="0" x2="${w}" y1="${y(threshold)}" y2="${y(threshold)}" class="spark-th"/>
    <path d="${path}" class="spark-line"/></svg>`;
}

function awardWatchCard(w) {
  const b = w.best;
  const hit = b && b.price <= w.max_price;
  const best = b
    ? `<div class="price ${hit ? "good" : ""}">${fmtNum(b.price)} Meilen</div>
       <div class="fineprint">${b.taxes != null ? `+ ${fmtPrice(b.taxes)} Steuern · ` : ""}über ${esc(b.program)}${b.seats ? ` · ${b.seats} Plätze` : ""}</div>
       <div class="meta">${esc(b.origin)} → ${esc(b.destination)} · ${fmtDate(b.outbound)} · ${esc(b.airline || "")}</div>`
    : `<div class="meta">Noch nichts gefunden.</div>`;
  return `<article class="watch ${w.active ? "" : "inactive"}">
    <div class="watch-head"><strong>${esc(w.name)}</strong><span class="src">Prämie ${esc(w.cabin)} · ${esc(w.origins_resolved.join(","))} → ${esc(w.destinations)}</span></div>
    <div class="watch-body">
      <div>${best}</div>
      <div class="watch-side">${sparkline(w.history, w.max_price)}
        <div class="meta">Schwelle ${fmtNum(w.max_price)} Meilen${w.best_ever ? ` · Bestwert ${fmtNum(w.best_ever.price)}` : ""}</div>
        <div class="meta">${w.last_checked ? "geprüft " + new Date(w.last_checked).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : ""} · ~${w.searches_per_month} seats.aero-Abfragen/Monat</div>
      </div>
    </div>
    ${w.last_error ? `<div class="meta level-high">${esc(w.last_error)}</div>` : ""}
    <div class="row"><button type="button" class="secondary" data-act="check" data-id="${w.id}">Jetzt prüfen</button>
      <button type="button" class="secondary" data-act="edit" data-id="${w.id}">Bearbeiten</button>
      <button type="button" class="secondary" data-act="delete" data-id="${w.id}">Löschen</button></div>
  </article>`;
}

function watchCard(w) {
  if (w.kind === "award") return awardWatchCard(w);
  const b = w.best;
  const hit = b && b.price + (b.ground || 0) <= w.max_price;
  const what = w.kind === "radar" ? `Deal-Radar${w.destinations ? ": " + w.destinations : ": überall"}` : `${w.origins_resolved.join(",")} → ${w.destinations}`;
  const best = b
    ? `<div class="price ${hit ? "good" : ""}">${fmtPrice(b.price + (b.ground || 0), b.currency)}</div>
       ${b.ground ? `<div class="fineprint">Flug ${fmtPrice(b.price, b.currency)} + ${esc(b.ground_note)}</div>` : ""}
       <div class="meta">${esc(b.origin)} → ${esc(b.destination_name || b.destination)} · ${fmtDate(b.outbound)}${b.inbound ? "–" + fmtDate(b.inbound) : ""} · ${esc(b.airline || "")}${b.price_level ? ` · Preisniveau <span class="level-${esc(b.price_level)}">${esc(LEVEL[b.price_level] || b.price_level)}</span>` : ""}</div>
       ${b.url ? `<a href="${esc(b.url)}" target="_blank" rel="noopener">Ansehen</a>` : ""}`
    : `<div class="meta">Noch nicht geprüft.</div>`;
  return `<article class="watch ${w.active ? "" : "inactive"}">
    <div class="watch-head"><strong>${esc(w.name)}</strong><span class="src">${esc(what)}</span></div>
    <div class="watch-body">
      <div>${best}</div>
      <div class="watch-side">${sparkline(w.history, w.max_price)}
        <div class="meta">Schwelle ${fmtPrice(w.max_price)}${w.best_ever ? ` · Bestwert ${fmtPrice(w.best_ever.price)}` : ""}</div>
        <div class="meta">${w.last_checked ? "geprüft " + new Date(w.last_checked).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : ""} · ~${w.searches_per_month} Suchen/Monat</div>
      </div>
    </div>
    ${w.last_error ? `<div class="meta level-high">${esc(w.last_error)}</div>` : ""}
    <div class="row"><button type="button" class="secondary" data-act="check" data-id="${w.id}">Jetzt prüfen</button>
      <button type="button" class="secondary" data-act="edit" data-id="${w.id}">Bearbeiten</button>
      <button type="button" class="secondary" data-act="delete" data-id="${w.id}">Löschen</button></div>
  </article>`;
}

async function loadWatches() {
  try {
    WATCHES = await api("GET", "/api/watches");
    $("#watches").innerHTML = WATCHES.length ? WATCHES.map(watchCard).join("") : '<p class="fine">Noch keine Alarme. Lege z. B. „New York unter 300 €“ an.</p>';
    $("#ntfy_state").textContent = CONFIG.ntfy ? "Push über ntfy ist eingerichtet." : "Push ist noch nicht eingerichtet (NTFY_TOPIC); Funde erscheinen nur hier.";
  } catch (e) { $("#watches").textContent = e.message; }
}

function openWatchForm(kind, w) {
  const f = $("#watch_form");
  f.hidden = false;
  const award = kind === "award";
  $("#watch_form_title").textContent = (w ? "Bearbeiten: " : "Neu: ") + ({ radar: "Deal-Radar", award: "Prämien-Alarm" }[kind] || "Route-Alarm");
  $("#w_kind").value = kind;
  $("#w_dest_label").textContent = kind === "radar" ? "Nur diese Länder/Ziele (optional)" : "Ziel-Flughäfen";
  $("#w_destinations").placeholder = kind === "radar" ? "z. B. USA, Japan, BKK" : award ? "z. B. JFK,EWR,BOS" : "JFK,EWR";
  $("#w_price_label").textContent = award ? "Alarm unter (Meilen pro Person und Richtung)" : "Alarm unter (€ inkl. Anreise/Parken)";
  $("#w_date_from_label").textContent = award ? "Reisedatum frühestens" : "Hinflug frühestens";
  $("#w_date_to_label").textContent = award ? "Reisedatum spätestens" : "Hinflug spätestens";
  document.querySelectorAll("#watch_form .not-award").forEach((el) => (el.hidden = award));
  $("#w_award_hint").hidden = !award;
  const v = w || { name: "", origins: "", destinations: "", max_price: "", adults: 1, date_from: "", date_to: "", nights_min: 5, nights_max: 9, cabin: award ? "business" : "economy", stops: "any", samples_per_check: kind === "radar" ? 1 : 2, interval_hours: kind === "radar" ? 12 : award ? 12 : 24, active: true, id: "" };
  for (const [k, id] of [["id", "w_id"], ["name", "w_name"], ["origins", "w_origins"], ["destinations", "w_destinations"], ["max_price", "w_max_price"], ["adults", "w_adults"], ["date_from", "w_date_from"], ["date_to", "w_date_to"], ["nights_min", "w_nights_min"], ["nights_max", "w_nights_max"], ["cabin", "w_cabin"], ["stops", "w_stops"], ["samples_per_check", "w_samples"], ["interval_hours", "w_interval"]]) $("#" + id).value = v[k] ?? "";
  $("#w_active").checked = v.active !== false;
  updateUsage();
  f.scrollIntoView({ behavior: "smooth" });
}

function updateUsage() {
  const per = 24 / Number($("#w_interval").value || 24) * 30;
  if ($("#w_kind").value === "award") {
    $("#w_usage").textContent = `Verbraucht ca. ${Math.round(per)} seats.aero-Abfragen pro Monat (keine Flugsuchen).`;
    return;
  }
  $("#w_usage").textContent = `Verbraucht ca. ${Math.round(Number($("#w_samples").value || 1) * per)} Flugsuchen pro Monat.`;
}
["#w_samples", "#w_interval"].forEach((s) => $(s).addEventListener("input", updateUsage));

$("#watch_form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = {
    kind: $("#w_kind").value, name: $("#w_name").value, origins: $("#w_origins").value.toUpperCase().replace(/\s/g, ""),
    destinations: $("#w_kind").value !== "radar" ? $("#w_destinations").value.toUpperCase().replace(/\s/g, "") : $("#w_destinations").value.trim(),
    max_price: Number($("#w_max_price").value), adults: Number($("#w_adults").value || 1),
    date_from: $("#w_date_from").value || null, date_to: $("#w_date_to").value || null,
    nights_min: Number($("#w_nights_min").value || 0), nights_max: Number($("#w_nights_max").value || 0),
    cabin: $("#w_cabin").value, stops: $("#w_stops").value, samples_per_check: Number($("#w_samples").value || 1),
    interval_hours: Number($("#w_interval").value || 24), active: $("#w_active").checked,
  };
  if ($("#w_id").value) body.id = $("#w_id").value;
  try { await api("POST", "/api/watches", body); $("#watch_form").hidden = true; loadWatches(); }
  catch (e) { alert(e.message); }
});
$("#w_cancel").addEventListener("click", () => ($("#watch_form").hidden = true));
$("#new_route").addEventListener("click", () => openWatchForm("route"));
$("#new_radar").addEventListener("click", () => openWatchForm("radar"));
$("#new_award").addEventListener("click", () => openWatchForm("award"));
$("#test_push").addEventListener("click", async (ev) => {
  try { await api("POST", "/api/notify/test"); ev.target.textContent = "Push gesendet ✓"; } catch (e) { alert(e.message); }
});
$("#watches").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-act]");
  if (!b) return;
  const w = WATCHES.find((x) => x.id === b.dataset.id);
  if (b.dataset.act === "edit") return openWatchForm(w.kind, w);
  if (b.dataset.act === "delete" && !confirm(`„${w.name}“ löschen?`)) return;
  b.disabled = true;
  try {
    if (b.dataset.act === "check") { b.textContent = "Prüfe …"; await api("POST", `/api/watches/${w.id}/check`); }
    if (b.dataset.act === "delete") await api("DELETE", `/api/watches/${w.id}`);
  } catch (e) { alert(e.message); }
  loadWatches();
});

// ================================================================== Community
let COMMUNITY = null;
const bonusKnown = (b) => (PROFILE_BONUSES || []).some((x) => x.program === b.program && x.bonus_pct === b.bonus_pct);

async function loadCommunity() {
  try { COMMUNITY = await api("GET", "/api/community"); } catch (e) { $("#digests").textContent = e.message; return; }
  const c = COMMUNITY;
  $("#digest_last").textContent = c.last_run ? `Letzte Auswertung: ${new Date(c.last_run).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })}.` : "Noch keine Auswertung.";
  const known = new Set(c.tips.map((t) => t.title));
  $("#digests").innerHTML = c.digests.map((d, di) => {
    const errs = Object.entries(d.feed_errors || {}).map(([k, v]) => `${k}: ${v}`).join("; ");
    const ct = d.content;
    return `<div class="digest">
      <h3>${new Date(d.created).toLocaleDateString("de-DE")} · ${d.items_read} neue Beiträge</h3>
      ${d.error ? `<p class="level-high">${esc(d.error)}</p>` : ""}
      ${errs ? `<p class="fine">Feed-Probleme: ${esc(errs)}</p>` : ""}
      ${ct ? `<p>${esc(ct.summary)}</p>
        ${ct.deals.length ? `<h4>Deals</h4><ul>${ct.deals.map((x) => `<li><a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.title)}</a> · ${esc(x.route)} · <strong>${esc(x.price)}</strong>${x.why_relevant ? `<br><span class="fine">${esc(x.why_relevant)}</span>` : ""}</li>`).join("")}</ul>` : ""}
        ${ct.transfer_bonuses?.length ? `<h4>Transfer-Boni</h4><ul>${ct.transfer_bonuses.map((b, bi) => `<li><strong>${esc(CONFIG.programs[b.program] || b.program)} +${b.bonus_pct} %</strong>${b.valid_until ? ` bis ${esc(b.valid_until)}` : ""} ${esc(b.note)} <a href="${esc(b.link)}" target="_blank" rel="noopener">Quelle</a>
            ${bonusKnown(b) ? ' <span class="chip">übernommen</span>' : ` <button type="button" class="copy" data-bonus="${di}:${bi}">Übernehmen</button>`}</li>`).join("")}</ul>` : ""}
        ${ct.tips.length ? `<h4>Tipps</h4><ul>${ct.tips.map((t, ti) => `<li><strong>${esc(t.title)}</strong> <span class="src">${esc(t.category)}</span><br>${esc(t.detail)} <a href="${esc(t.link)}" target="_blank" rel="noopener">Quelle</a>
            ${known.has(t.title) ? ' <span class="chip">übernommen</span>' : ` <button type="button" class="copy" data-adopt="${di}:${ti}">Übernehmen</button>`}</li>`).join("")}</ul>` : ""}` : ""}
    </div>`;
  }).join("") || '<p class="fine">Noch kein Digest vorhanden.</p>';
  $("#tips").innerHTML = c.tips.map((t) => `<li><strong>${esc(t.title)}</strong> <span class="src">${esc(t.category)} · ${esc(t.added)}</span><br>${esc(t.detail)} ${t.link ? `<a href="${esc(t.link)}" target="_blank" rel="noopener">Quelle</a>` : ""} <button type="button" class="copy" data-tipdel="${t.id}">Entfernen</button></li>`).join("") || '<li class="fine">Noch keine Tipps übernommen.</li>';
  $("#feeds").innerHTML = c.feeds.map(feedRow).join("");
}

const feedRow = (f) => `<tr><td><input type="checkbox" class="f_active" ${f.active ? "checked" : ""}></td>
  <td><input class="f_name" value="${esc(f.name)}"></td><td><input class="f_url" value="${esc(f.url)}"></td>
  <td><button type="button" class="copy f_del">✕</button></td></tr>`;

$("#tab-community").addEventListener("click", async (ev) => {
  const t = ev.target;
  if (t.dataset.bonus) {
    const [di, bi] = t.dataset.bonus.split(":").map(Number);
    const b = COMMUNITY.digests[di].content.transfer_bonuses[bi];
    try { await api("POST", "/api/profile/bonuses", b); await loadProfile(); } catch (e) { alert(e.message); }
    loadCommunity();
  } else if (t.dataset.adopt) {
    const [di, ti] = t.dataset.adopt.split(":").map(Number);
    const tip = COMMUNITY.digests[di].content.tips[ti];
    await api("POST", "/api/community/tips", { title: tip.title, detail: tip.detail, category: tip.category, link: tip.link });
    loadCommunity();
  } else if (t.dataset.tipdel) {
    await api("DELETE", `/api/community/tips/${t.dataset.tipdel}`);
    loadCommunity();
  } else if (t.classList.contains("f_del")) {
    t.closest("tr").remove();
  }
});
$("#add_feed").addEventListener("click", () => $("#feeds").insertAdjacentHTML("beforeend", feedRow({ name: "", url: "", active: true })));
$("#save_feeds").addEventListener("click", async () => {
  const feeds = [...document.querySelectorAll("#feeds tr")].map((tr) => ({ name: tr.querySelector(".f_name").value.trim(), url: tr.querySelector(".f_url").value.trim(), active: tr.querySelector(".f_active").checked })).filter((f) => f.name && f.url);
  try { await api("PUT", "/api/community/feeds", feeds); $("#feeds_msg").textContent = "Gespeichert ✓"; } catch (e) { $("#feeds_msg").textContent = e.message; }
});
$("#run_digest").addEventListener("click", async (ev) => {
  ev.target.disabled = true; ev.target.textContent = "Werte aus … (kann 1-2 Minuten dauern)";
  try { await api("POST", "/api/community/run"); } catch (e) { alert(e.message); }
  ev.target.disabled = false; ev.target.textContent = "Jetzt auswerten";
  loadCommunity();
});

// ================================================================== Spracheingabe
// Browser-eigene Spracherkennung (Chrome, Safari, Edge). Ohne Unterstützung bleibt der Knopf versteckt;
// die Diktierfunktion der Handy-Tastatur funktioniert immer.
(function setupVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = $("#mic"), box = $("#request"), msg = $("#mic_msg");
  if (!SR || !btn) return;
  btn.hidden = false;
  let rec = null, base = "";
  const say = (t) => { msg.textContent = t; msg.hidden = !t; };
  const stopUi = () => { btn.classList.remove("listening"); btn.setAttribute("aria-label", "Spracheingabe starten"); rec = null; };
  const ERRORS = {
    "not-allowed": "Mikrofon ist nicht erlaubt. In den Browser-Einstellungen freigeben oder die Diktierfunktion der Tastatur nutzen.",
    "service-not-allowed": "Spracherkennung ist hier nicht verfügbar. Nutze die Diktierfunktion der Tastatur (Mikrofon-Taste).",
    "audio-capture": "Kein Mikrofon gefunden.",
    "network": "Spracherkennung braucht eine Internetverbindung.",
  };
  btn.addEventListener("click", () => {
    if (rec) { rec.stop(); return; }
    rec = new SR();
    rec.lang = "de-DE";
    rec.interimResults = true;
    rec.continuous = false;
    base = box.value.trim();
    rec.onresult = (ev) => {
      const text = [...ev.results].map((r) => r[0].transcript).join(" ").trim();
      box.value = (base ? base + " " : "") + text;
    };
    rec.onerror = (ev) => { if (ev.error !== "no-speech" && ev.error !== "aborted") say(ERRORS[ev.error] || `Spracheingabe fehlgeschlagen (${ev.error}).`); };
    rec.onend = stopUi;
    try {
      rec.start();
      say("");
      btn.classList.add("listening");
      btn.setAttribute("aria-label", "Spracheingabe beenden");
    } catch (e) { say("Spracheingabe konnte nicht starten."); stopUi(); }
  });
})();
