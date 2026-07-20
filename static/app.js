const API_BASE = "/api";
const DEFAULT_APP_TAB = "manage";

// ---------- Tab switching ----------
async function activateTab(tabName) {
  document.querySelectorAll(".tab-link").forEach((el) => {
    const active = el.dataset.tab === tabName;
    el.classList.toggle("bg-slate-800", active);
    el.classList.toggle("text-indigo-400", active);
    el.classList.toggle("text-slate-300", !active);
  });

  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
  document.getElementById(`tab-${tabName}`).classList.remove("hidden");

  if (tabName === "search") loadDeckList();
  if (tabName === "manage") { loadInventory(); loadPricingSummary(); }
  if (tabName === "decks") await loadDecksTab();
}

// ---------- Side drawer ----------
function openDrawer() {
  document.getElementById("side-drawer").classList.remove("-translate-x-full");
  document.getElementById("drawer-backdrop").classList.remove("hidden");
}

function closeDrawer() {
  document.getElementById("side-drawer").classList.add("-translate-x-full");
  document.getElementById("drawer-backdrop").classList.add("hidden");
}

document.getElementById("menu-btn").addEventListener("click", openDrawer);
document.getElementById("drawer-backdrop").addEventListener("click", closeDrawer);
document.getElementById("drawer-close-btn").addEventListener("click", closeDrawer);

document.querySelectorAll(".nav-link[data-nav='home']").forEach((el) => {
  el.addEventListener("click", () => {
    showHomeView();
    closeDrawer();
  });
});

document.querySelectorAll(".tab-link").forEach((el) => {
  el.addEventListener("click", () => {
    showAppView(el.dataset.tab);
    closeDrawer();
  });
});

// ---------- Homepage / app view switching ----------
function showHomeView() {
  document.getElementById("view-home").classList.remove("hidden");
  document.getElementById("view-app").classList.add("hidden");
  document.getElementById("view-card").classList.add("hidden");
  loadHomepage();
}

async function showAppView(tabName) {
  document.getElementById("view-home").classList.add("hidden");
  document.getElementById("view-card").classList.add("hidden");
  document.getElementById("view-app").classList.remove("hidden");
  await activateTab(tabName || DEFAULT_APP_TAB);
}

function showCardView() {
  document.getElementById("view-home").classList.add("hidden");
  document.getElementById("view-app").classList.add("hidden");
  document.getElementById("view-card").classList.remove("hidden");
}

document.getElementById("site-title-btn").addEventListener("click", showHomeView);

async function loadHomepage() {
  try {
    const [summaryRes, shortcutsRes, recentCardsRes] = await Promise.all([
      fetch(`${API_BASE}/homepage/summary`),
      fetch(`${API_BASE}/homepage/deck-shortcuts`),
      fetch(`${API_BASE}/homepage/recent-cards`),
    ]);
    const summary = await summaryRes.json();
    const shortcuts = await shortcutsRes.json();
    const recentCards = await recentCardsRes.json();

    document.getElementById("home-total-cards").textContent = summary.total_quantity.toLocaleString();
    document.getElementById("home-unique-cards").textContent = `${summary.unique_cards.toLocaleString()} unique`;
    document.getElementById("home-deck-count").textContent = summary.deck_count.toLocaleString();
    document.getElementById("home-collection-value").textContent = `$${summary.collection_value_usd.toFixed(2)}`;

    renderDeckShortcuts(shortcuts.decks);
    renderRecentCards(recentCards.cards);
  } catch (err) {
    console.error("Failed to load homepage:", err);
  }
}

function renderRecentCards(cards) {
  const container = document.getElementById("home-recent-cards");
  const emptyMsg = document.getElementById("home-recent-cards-empty");
  container.innerHTML = "";

  if (cards.length === 0) {
    emptyMsg.classList.remove("hidden");
    return;
  }
  emptyMsg.classList.add("hidden");

  cards.forEach((card) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "bg-slate-900 border border-slate-700 hover:border-indigo-500 rounded-lg p-3 text-left transition-colors flex items-center gap-3";
    const thumb = card.image_url
      ? `<img src="${card.image_url}" alt="" class="w-10 h-14 object-cover rounded border border-slate-700 flex-shrink-0">`
      : `<div class="w-10 h-14 rounded border border-slate-700 bg-slate-800 flex-shrink-0"></div>`;
    btn.innerHTML = `
      ${thumb}
      <div class="min-w-0">
        <div class="font-medium text-slate-100 truncate">${escapeHtml(card.card_name)}</div>
        <div class="text-xs text-slate-500 truncate">${escapeHtml(card.type_line || "")}</div>
      </div>
    `;
    btn.addEventListener("click", () => openRecentCard(card.card_name));
    container.appendChild(btn);
  });
}

async function openRecentCard(cardName) {
  document.getElementById("card-search-input").value = cardName;
  await searchCard();
}

function renderDeckShortcuts(decks) {
  const container = document.getElementById("home-deck-shortcuts");
  const emptyMsg = document.getElementById("home-deck-shortcuts-empty");
  container.innerHTML = "";

  if (decks.length === 0) {
    emptyMsg.classList.remove("hidden");
    return;
  }
  emptyMsg.classList.add("hidden");

  decks.forEach((deck) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "bg-slate-900 border border-slate-700 hover:border-indigo-500 rounded-lg p-3 text-left transition-colors";
    btn.innerHTML = `
      <div class="font-medium text-slate-100 truncate">${deck.is_favorite ? "★ " : ""}${escapeHtml(deck.deck_name)}</div>
      <div class="text-xs text-slate-500 mt-1">${deck.is_favorite ? "Favorited" : "Recently changed"}</div>
    `;
    btn.addEventListener("click", () => openDeckShortcut(deck.deck_name));
    container.appendChild(btn);
  });
}

async function openDeckShortcut(deckName) {
  await showAppView("decks");
  const select = document.getElementById("deck-select");
  select.value = deckName;
  select.dispatchEvent(new Event("change"));
}

// ---------- Card Search ----------
document.getElementById("card-back-btn").addEventListener("click", showHomeView);

function legalityBadge(fmt, status) {
  const label = fmt.charAt(0).toUpperCase() + fmt.slice(1);
  const legal = status === "legal";
  const color = legal
    ? "bg-emerald-950/40 text-emerald-400 border-emerald-800"
    : "bg-slate-800 text-slate-500 border-slate-700";
  return `<span class="px-2 py-1 rounded border text-xs ${color}">${label}</span>`;
}

function renderCardFace(face) {
  const img = face.image_url
    ? `<img src="${face.image_url}" alt="${escapeHtml(face.name || "")}" class="w-full max-w-xs rounded-lg border border-slate-700">`
    : `<div class="w-full max-w-xs aspect-[5/7] rounded-lg border border-slate-700 bg-slate-900 flex items-center justify-center text-slate-600 text-sm">No image</div>`;

  const pt = face.power != null && face.toughness != null
    ? `<div class="text-sm text-slate-300 mb-2">${escapeHtml(face.power)}/${escapeHtml(face.toughness)}</div>`
    : "";
  const loyalty = face.loyalty != null
    ? `<div class="text-sm text-slate-300 mb-2">Loyalty: ${escapeHtml(String(face.loyalty))}</div>`
    : "";

  return `
    <div class="flex flex-col sm:flex-row gap-4">
      ${img}
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline justify-between gap-2 flex-wrap">
          <h2 class="text-xl font-bold">${escapeHtml(face.name || "")}</h2>
          <span class="text-slate-400 text-sm">${escapeHtml(face.mana_cost || "")}</span>
        </div>
        <div class="text-slate-400 text-sm mb-2">${escapeHtml(face.type_line || "")}</div>
        <p class="text-sm whitespace-pre-line mb-2">${escapeHtml(face.oracle_text || "")}</p>
        ${pt}
        ${loyalty}
        ${face.flavor_text ? `<p class="text-xs italic text-slate-500">${escapeHtml(face.flavor_text)}</p>` : ""}
      </div>
    </div>
  `;
}

let currentCardInventoryName = null;

function renderCardDetail(card) {
  currentCardInventoryName = card.inventory_name;

  const faces = card.faces
    ? card.faces.map(renderCardFace).join(`<div class="my-4 border-t border-slate-800"></div>`)
    : renderCardFace(card.primary);

  const priceLine = (card.price_usd != null || card.price_usd_foil != null)
    ? `
      <div class="flex gap-4 text-sm mt-4">
        ${card.price_usd != null ? `<div><span class="text-slate-500">USD:</span> $${Number(card.price_usd).toFixed(2)}</div>` : ""}
        ${card.price_usd_foil != null ? `<div><span class="text-slate-500">Foil:</span> $${Number(card.price_usd_foil).toFixed(2)}</div>` : ""}
      </div>
    `
    : `<div class="text-sm text-slate-500 mt-4">No pricing available.</div>`;

  const ownedLine = `
    <div class="flex items-center gap-3 mt-4">
      <div class="text-sm">
        <span class="text-slate-500"># in inventory:</span>
        <span id="card-owned-qty" class="font-semibold text-slate-100">${card.owned_quantity}</span>
      </div>
      <button id="card-add-to-inventory-btn" class="bg-indigo-600 hover:bg-indigo-500 px-3 py-1.5 rounded-lg font-medium text-xs">
        + Add to Inventory
      </button>
    </div>
  `;

  const legalities = Object.entries(card.legalities || {})
    .map(([fmt, status]) => legalityBadge(fmt, status))
    .join(" ");

  const meta = `
    <div class="text-xs text-slate-500 mt-4 space-y-1">
      <div>${escapeHtml(card.set_name || "")} (${escapeHtml(card.set_code || "")}) #${escapeHtml(card.collector_number || "")} · ${escapeHtml(card.rarity || "")}</div>
      ${card.artist ? `<div>Illustrated by ${escapeHtml(card.artist)}</div>` : ""}
      ${card.scryfall_uri ? `<a href="${card.scryfall_uri}" target="_blank" rel="noopener" class="text-indigo-400 hover:text-indigo-300">View on Scryfall →</a>` : ""}
    </div>
  `;

  document.getElementById("card-detail-content").innerHTML = `
    ${faces}
    ${priceLine}
    ${ownedLine}
    <div class="flex flex-wrap gap-2 mt-3">${legalities}</div>
    ${meta}
  `;

  document.getElementById("card-add-to-inventory-btn").addEventListener("click", addCurrentCardToInventory);
}

async function addCurrentCardToInventory() {
  if (!currentCardInventoryName) return;

  const btn = document.getElementById("card-add-to-inventory-btn");
  const qtyEl = document.getElementById("card-owned-qty");
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Adding...";

  try {
    const res = await fetch(`${API_BASE}/inventory/quick-add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_name: currentCardInventoryName }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    qtyEl.textContent = data.total_quantity;
  } catch (err) {
    alert(`Failed to add to inventory: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

async function searchCard() {
  const input = document.getElementById("card-search-input");
  const msgEl = document.getElementById("card-search-msg");
  const name = input.value.trim();

  if (!name) {
    msgEl.innerHTML = `<span class="text-rose-400">Enter a card name.</span>`;
    return;
  }

  const btn = document.getElementById("card-search-btn");
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Searching...";
  msgEl.textContent = "";

  try {
    const res = await fetch(`${API_BASE}/card-lookup?name=${encodeURIComponent(name)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    renderCardDetail(data);
    showCardView();
  } catch (err) {
    msgEl.innerHTML = `<span class="text-rose-400">${err.message}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

document.getElementById("card-search-btn").addEventListener("click", searchCard);
document.getElementById("card-search-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchCard();
});

// ---------- Tab 1: Search ----------
const searchThreshold = document.getElementById("search-threshold");
const searchThresholdVal = document.getElementById("search-threshold-val");

// UI shows an easy 1 (loose) - 10 (strict) scale; the API expects a
// 60-100 fuzzy match score. Map linearly between the two.
function scaleToApiThreshold(scale) {
  const clamped = Math.min(10, Math.max(1, scale));
  return Math.round(60 + (clamped - 1) * (40 / 9));
}

function clampScale(value) {
  const n = parseInt(value, 10);
  if (isNaN(n)) return 7;
  return Math.min(10, Math.max(1, n));
}

// Slider drives the number box
searchThreshold.addEventListener("input", () => {
  searchThresholdVal.value = searchThreshold.value;
});

// Number box drives the slider, and self-corrects out-of-range typing
searchThresholdVal.addEventListener("input", () => {
  const scale = clampScale(searchThresholdVal.value);
  searchThreshold.value = scale;
});
searchThresholdVal.addEventListener("blur", () => {
  const scale = clampScale(searchThresholdVal.value);
  searchThresholdVal.value = scale;
  searchThreshold.value = scale;
});

document.getElementById("search-btn").addEventListener("click", async () => {
  const decklist_text = document.getElementById("search-input").value;
  const fuzzy_threshold = scaleToApiThreshold(clampScale(searchThresholdVal.value));
  const ignore_basic_lands = document.getElementById("search-ignore-basics").checked;

  const btn = document.getElementById("search-btn");
  btn.disabled = true;
  btn.textContent = "Searching...";

  try {
    const res = await fetch(`${API_BASE}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decklist_text, fuzzy_threshold, ignore_basic_lands }),
    });
    if (!res.ok) throw new Error(`Server error: ${res.status}`);
    const data = await res.json();

    document.getElementById("output-available").value = data.available.join("\n");
    document.getElementById("output-missing").value = data.missing.join("\n");

    const warnEl = document.getElementById("search-warnings");
    warnEl.innerHTML = "";
    if (data.skipped_basic_lands > 0) {
      const div = document.createElement("div");
      div.className = "text-slate-500";
      div.textContent = `(Skipped ${data.skipped_basic_lands} basic land line${data.skipped_basic_lands > 1 ? "s" : ""}.)`;
      warnEl.appendChild(div);
    }
    data.warnings.forEach((w) => {
      const div = document.createElement("div");
      div.textContent = `⚠ ${w}`;
      warnEl.appendChild(div);
    });
  } catch (err) {
    alert(`Search failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Search";
  }
});

// ---------- Tab 2: Checkout / Check-in ----------
const checkoutThreshold = document.getElementById("checkout-threshold");
const checkoutThresholdVal = document.getElementById("checkout-threshold-val");

checkoutThreshold.addEventListener("input", () => {
  checkoutThresholdVal.value = checkoutThreshold.value;
});
checkoutThresholdVal.addEventListener("input", () => {
  const scale = clampScale(checkoutThresholdVal.value);
  checkoutThreshold.value = scale;
});
checkoutThresholdVal.addEventListener("blur", () => {
  const scale = clampScale(checkoutThresholdVal.value);
  checkoutThresholdVal.value = scale;
  checkoutThreshold.value = scale;
});

async function loadDeckList() {
  try {
    const res = await fetch(`${API_BASE}/decks`);
    const data = await res.json();
    const datalist = document.getElementById("deck-list");
    datalist.innerHTML = "";
    data.decks.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      datalist.appendChild(opt);
    });
  } catch (err) {
    console.error("Failed to load deck list:", err);
  }
}

function renderCheckoutResults(lines, warnings, containerId = "checkout-results") {
  const container = document.getElementById(containerId);
  container.innerHTML = "";

  warnings.forEach((w) => {
    const div = document.createElement("div");
    div.className = "text-amber-400";
    div.textContent = `⚠ ${w}`;
    container.appendChild(div);
  });

  const statusColor = {
    ok: "text-emerald-400",
    partial: "text-amber-400",
    not_found: "text-rose-400",
    unparseable: "text-rose-400",
  };

  lines.forEach((line) => {
    const div = document.createElement("div");
    div.className = statusColor[line.status] || "text-slate-300";
    const label = line.status.replace("_", " ");
    div.textContent = line.message
      ? `[${label}] ${line.raw_line} — ${line.message}`
      : `[${label}] ${line.raw_line}`;
    container.appendChild(div);
  });
}

async function loadDeckIntoCheckoutBox(deckName) {
  try {
    const res = await fetch(`${API_BASE}/decks/${encodeURIComponent(deckName)}/cards`);
    const data = await res.json();
    const lines = data.cards
      .slice()
      .sort((a, b) => a.card_name.localeCompare(b.card_name))
      .map((c) => `${c.quantity} ${c.card_name}`);
    document.getElementById("checkout-input").value = lines.join("\n");
  } catch (err) {
    console.error("Failed to load deck contents into checkout box:", err);
  }
}

// getEffectiveDeckName() and the unified #deck-select change handling
// live down in the "Decks" section below — this bulk-edit panel shares
// the same deck selector as the rest of the tab now, rather than
// having its own.

function renderSyncResults(lines, warnings, errors) {
  const container = document.getElementById("checkout-results");
  container.innerHTML = "";

  warnings.forEach((w) => {
    const div = document.createElement("div");
    div.className = "text-amber-400";
    div.textContent = `⚠ ${w}`;
    container.appendChild(div);
  });

  const statusColor = {
    ok: "text-emerald-400",
    unavailable: "text-rose-400",
  };

  const changed = lines.filter((line) => line.status !== "no_change");

  changed.forEach((line) => {
    const div = document.createElement("div");
    div.className = statusColor[line.status] || "text-slate-300";
    const deltaLabel = line.applied_delta > 0 ? `+${line.applied_delta}` : `${line.applied_delta}`;
    div.textContent = line.message
      ? `[${line.status}] ${line.card_name}: ${line.current_qty} → ${line.target_qty} (${deltaLabel}) — ${line.message}`
      : `[${line.status}] ${line.card_name}: ${line.current_qty} → ${line.target_qty} (${deltaLabel})`;
    container.appendChild(div);
  });

  if (lines.length > 0 && changed.length === 0) {
    const div = document.createElement("div");
    div.className = "text-slate-500";
    div.textContent = "No changes — the deck already matches this list.";
    container.appendChild(div);
  }

  if (errors.length > 0) {
    alert(`Some cards couldn't be fully checked out:\n\n${errors.join("\n")}`);
  }
}

async function runSyncAction(path, actionLabel, btnEl) {
  const decklist_text = document.getElementById("checkout-input").value;
  const deck_name = getEffectiveDeckName();
  const fuzzy_threshold = scaleToApiThreshold(clampScale(checkoutThresholdVal.value));

  if (!deck_name) {
    alert("Choose a deck, or enter a name for a new one.");
    return;
  }

  const originalText = btnEl.textContent;
  btnEl.disabled = true;
  btnEl.textContent = "Working...";

  try {
    const res = await fetch(`${API_BASE}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decklist_text, deck_name, fuzzy_threshold }),
    });
    if (!res.ok) throw new Error(`Server error: ${res.status}`);
    const data = await res.json();

    renderSyncResults(data.lines, data.warnings, data.errors);
    await afterDeckMutation(deck_name);
  } catch (err) {
    alert(`${actionLabel} failed: ${err.message}`);
  } finally {
    btnEl.disabled = false;
    btnEl.textContent = originalText;
  }
}

document.getElementById("checkout-btn").addEventListener("click", (e) => {
  runSyncAction("checkout/sync", "Check Out", e.target);
});
document.getElementById("checkin-btn").addEventListener("click", (e) => {
  runSyncAction("checkin/sync", "Check In", e.target);
});

// ---------- Tab 4 (cont'd): Bulk Update (CSV import) ----------
document.getElementById("bulk-update-toggle-btn").addEventListener("click", () => {
  document.getElementById("bulk-update-panel").classList.toggle("hidden");
});

let selectedFile = null;

document.getElementById("csv-choose-btn").addEventListener("click", () => {
  document.getElementById("csv-file-input").click();
});

document.getElementById("csv-file-input").addEventListener("change", (e) => {
  selectedFile = e.target.files[0] || null;
  document.getElementById("csv-filename").textContent = selectedFile ? selectedFile.name : "";
  document.getElementById("csv-upload-btn").disabled = !selectedFile;
});

document.getElementById("csv-upload-btn").addEventListener("click", async () => {
  if (!selectedFile) return;

  if (!confirm("This will replace your entire inventory. Deck assignments will be preserved. Continue?")) {
    return;
  }

  const btn = document.getElementById("csv-upload-btn");
  btn.disabled = true;
  btn.textContent = "Uploading...";

  const formData = new FormData();
  formData.append("file", selectedFile);
  formData.append("ignore_basic_lands", document.getElementById("bulk-ignore-basics").checked ? "true" : "false");

  try {
    const res = await fetch(`${API_BASE}/bulk-upload`, { method: "POST", body: formData });
    const data = await res.json();

    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    const container = document.getElementById("bulk-result");
    container.innerHTML = `
      <div class="text-emerald-400">
        ✓ Loaded ${data.unique_cards_loaded} unique cards (${data.total_quantity_loaded} total copies).
        ${data.assignments_preserved} deck assignments preserved.
      </div>
    `;
    if (data.skipped_basic_lands > 0) {
      const div = document.createElement("div");
      div.className = "text-slate-500";
      div.textContent = `(Skipped ${data.skipped_basic_lands} basic land row${data.skipped_basic_lands > 1 ? "s" : ""} — not tracked in collection inventory.)`;
      container.appendChild(div);
    }
    data.warnings.forEach((w) => {
      const div = document.createElement("div");
      div.className = "text-amber-400";
      div.textContent = `⚠ ${w}`;
      container.appendChild(div);
    });
  } catch (err) {
    document.getElementById("bulk-result").innerHTML =
      `<div class="text-rose-400">✗ Upload failed: ${err.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Upload & Replace Inventory";
    selectedFile = null;
    document.getElementById("csv-file-input").value = "";
    document.getElementById("csv-filename").textContent = "";
    document.getElementById("csv-upload-btn").disabled = true;
  }
});

// ---------- Tab 4: Manage Collection ----------
let manageSearchDebounce = null;

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function loadPricingSummary() {
  try {
    const res = await fetch(`${API_BASE}/pricing/summary`);
    const data = await res.json();
    document.getElementById("collection-value").textContent = `$${data.total_value_usd.toFixed(2)}`;

    let detail = data.unpriced_cards > 0
      ? `${data.priced_cards} priced, ${data.unpriced_cards} not yet priced`
      : `${data.priced_cards} cards priced`;

    if (data.last_updated) {
      const when = new Date(data.last_updated);
      detail += ` · as of ${when.toLocaleDateString()} ${when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    }

    document.getElementById("collection-value-detail").textContent = detail;
  } catch (err) {
    console.error("Failed to load pricing summary:", err);
  }
}

function formatStage(stage) {
  const labels = {
    fetching_index: "Contacting Scryfall...",
    downloading: "Downloading bulk price data...",
    matching: "Matching against your collection...",
    committing: "Saving prices...",
  };
  return labels[stage] || "Working...";
}

async function pollRefreshStatus(progressEl) {
  try {
    const res = await fetch(`${API_BASE}/pricing/status`);
    const status = await res.json();

    if (!status.in_progress) return false;

    let text = formatStage(status.stage);
    if (status.stage === "matching" && status.total_cards_in_file) {
      text += ` (${status.cards_processed.toLocaleString()} / ${status.total_cards_in_file.toLocaleString()})`;
    }
    progressEl.textContent = text;
    return true;
  } catch (err) {
    return false;
  }
}

document.getElementById("refresh-prices-btn").addEventListener("click", async () => {
  const btn = document.getElementById("refresh-prices-btn");
  const progressEl = document.getElementById("refresh-progress");
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Refreshing...";
  progressEl.classList.remove("hidden");
  progressEl.textContent = "Starting...";

  const pollInterval = setInterval(() => pollRefreshStatus(progressEl), 1500);

  try {
    const res = await fetch(`${API_BASE}/pricing/refresh-bulk`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    loadInventory();
    loadPricingSummary();

    if (data.skipped_errors > 0) {
      alert(`Price refresh complete, but ${data.skipped_errors} card(s) were skipped due to bad data from Scryfall.`);
    }
  } catch (err) {
    alert(`Price refresh failed: ${err.message}`);
  } finally {
    clearInterval(pollInterval);
    btn.disabled = false;
    btn.textContent = originalText;
    progressEl.classList.add("hidden");
  }
});

let managePage = 1;
let managePageSize = 50;

async function loadInventory() {
  const search = document.getElementById("manage-search").value.trim();
  const params = new URLSearchParams({ page: managePage, page_size: managePageSize });
  if (search) params.set("search", search);

  try {
    const res = await fetch(`${API_BASE}/inventory?${params.toString()}`);
    const data = await res.json();

    // If a delete/filter change left us past the last page, snap back
    // instead of showing an empty table with working Prev/Next buttons.
    if (data.total_count > 0 && managePage > data.total_pages) {
      managePage = data.total_pages;
      return loadInventory();
    }

    renderInventoryTable(data.cards);
    renderManagePagination(data);
  } catch (err) {
    console.error("Failed to load inventory:", err);
  }
}

function renderManagePagination(data) {
  const info = document.getElementById("manage-pagination-info");
  const prevBtn = document.getElementById("manage-prev-btn");
  const nextBtn = document.getElementById("manage-next-btn");

  if (data.total_count === 0) {
    info.textContent = "No cards.";
  } else {
    const start = (data.page - 1) * data.page_size + 1;
    const end = Math.min(data.page * data.page_size, data.total_count);
    info.textContent = `${start}–${end} of ${data.total_count}`;
  }

  prevBtn.disabled = data.page <= 1;
  nextBtn.disabled = data.page >= data.total_pages;
}

document.getElementById("manage-prev-btn").addEventListener("click", () => {
  if (managePage > 1) {
    managePage -= 1;
    loadInventory();
  }
});
document.getElementById("manage-next-btn").addEventListener("click", () => {
  managePage += 1;
  loadInventory();
});
document.getElementById("manage-page-size").addEventListener("change", (e) => {
  managePageSize = parseInt(e.target.value, 10);
  managePage = 1;
  loadInventory();
});

function renderInventoryTable(cards) {
  const tbody = document.getElementById("manage-table-body");
  const emptyMsg = document.getElementById("manage-empty");
  tbody.innerHTML = "";

  if (cards.length === 0) {
    emptyMsg.classList.remove("hidden");
    return;
  }
  emptyMsg.classList.add("hidden");

  cards.forEach((card) => {
    const tr = document.createElement("tr");
    tr.className = "border-b border-slate-800";

    const deckTitle = card.decks.length
      ? card.decks.map((d) => `${d.quantity}x ${d.deck_name}`).join(", ")
      : "";

    const priceDisplay = card.price_usd != null ? `$${card.price_usd.toFixed(2)}` : "—";
    const valueDisplay = card.line_value != null ? `$${card.line_value.toFixed(2)}` : "—";

    tr.innerHTML = `
      <td class="py-2 pr-2">
        ${escapeHtml(card.card_name)}
        ${card.decks.length ? `<span class="text-xs text-slate-500" title="${escapeHtml(deckTitle)}"> (in ${card.decks.length} deck${card.decks.length > 1 ? "s" : ""})</span>` : ""}
      </td>
      <td class="py-2 px-2">
        <div class="flex items-center gap-1">
          <button class="qty-nudge w-6 h-6 rounded bg-slate-800 hover:bg-slate-700" data-delta="-1">−</button>
          <input type="number" min="0" value="${card.total_quantity}"
            class="qty-input w-14 bg-slate-800 border border-slate-700 rounded px-1 py-0.5 text-center">
          <button class="qty-nudge w-6 h-6 rounded bg-slate-800 hover:bg-slate-700" data-delta="1">+</button>
        </div>
      </td>
      <td class="py-2 px-2 text-slate-400">${card.checked_out}</td>
      <td class="py-2 px-2 ${card.available > 0 ? "text-emerald-400" : "text-slate-500"}">${card.available}</td>
      <td class="py-2 px-2 text-slate-300">${priceDisplay}</td>
      <td class="py-2 px-2 text-slate-300">${valueDisplay}</td>
      <td class="py-2 px-2">
        <div class="flex gap-2">
          <button class="qty-save bg-indigo-600 hover:bg-indigo-500 px-2 py-1 rounded text-xs">Save</button>
          <button class="price-refresh bg-slate-700 hover:bg-slate-600 px-2 py-1 rounded text-xs" title="Refresh this card's price">$</button>
          <button class="card-delete bg-rose-900 hover:bg-rose-800 px-2 py-1 rounded text-xs">Delete</button>
        </div>
      </td>
    `;

    const qtyInput = tr.querySelector(".qty-input");

    tr.querySelectorAll(".qty-nudge").forEach((btn) => {
      btn.addEventListener("click", () => {
        const delta = parseInt(btn.dataset.delta, 10);
        const next = Math.max(0, parseInt(qtyInput.value || "0", 10) + delta);
        qtyInput.value = next;
      });
    });

    tr.querySelector(".qty-save").addEventListener("click", async () => {
      const newQty = parseInt(qtyInput.value, 10);
      if (isNaN(newQty) || newQty < 0) {
        alert("Enter a valid quantity (0 or higher).");
        return;
      }
      await saveQuantity(card.card_name, newQty);
    });

    tr.querySelector(".card-delete").addEventListener("click", async () => {
      await deleteCard(card.card_name);
    });

    tr.querySelector(".price-refresh").addEventListener("click", async (e) => {
      const btn = e.target;
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "...";
      try {
        const res = await fetch(`${API_BASE}/pricing/refresh-card/${encodeURIComponent(card.card_name)}`, {
          method: "POST",
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);
        loadInventory();
        loadPricingSummary();
      } catch (err) {
        alert(`Price lookup failed: ${err.message}`);
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });

    tbody.appendChild(tr);
  });
}

async function saveQuantity(cardName, newQty) {
  try {
    const res = await fetch(`${API_BASE}/inventory/${encodeURIComponent(cardName)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ total_quantity: newQty }),
    });
    const data = await res.json();

    if (res.status === 409) {
      const decks = (data.detail && data.detail.decks) || [];
      const breakdown = decks.map((d) => `${d.quantity}x in "${d.deck_name}"`).join(", ");
      alert(
        `Can't reduce '${cardName}' below what's checked out (${breakdown}). ` +
        `Check those cards in first, then try again.`
      );
      return;
    }

    if (!res.ok) {
      throw new Error((data.detail && data.detail.message) || data.detail || `Server error: ${res.status}`);
    }

    loadInventory();
  } catch (err) {
    alert(`Failed to update quantity: ${err.message}`);
  }
}

async function deleteCard(cardName) {
  try {
    // First attempt: blocked by default if checked out anywhere.
    let res = await fetch(`${API_BASE}/inventory/${encodeURIComponent(cardName)}`, { method: "DELETE" });

    if (res.status === 409) {
      const data = await res.json();
      const decks = (data.detail && data.detail.decks) || [];
      const breakdown = decks.map((d) => `${d.quantity}x from "${d.deck_name}"`).join(", ");

      const confirmed = confirm(
        `'${cardName}' is currently checked out (${breakdown}).\n\n` +
        `Deleting will remove it from both those decks AND the main inventory. This cannot be undone.\n\n` +
        `Continue?`
      );

      if (!confirmed) return;

      res = await fetch(`${API_BASE}/inventory/${encodeURIComponent(cardName)}?force=true`, { method: "DELETE" });
    } else if (res.ok) {
      // No deck holds — still confirm since deletion is permanent either way.
      // (res already succeeded above, nothing further needed.)
    }

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data.detail && data.detail.message) || data.detail || `Server error: ${res.status}`);
    }

    loadInventory();
  } catch (err) {
    alert(`Failed to delete card: ${err.message}`);
  }
}

document.getElementById("manage-search").addEventListener("input", () => {
  clearTimeout(manageSearchDebounce);
  manageSearchDebounce = setTimeout(() => {
    managePage = 1;
    loadInventory();
  }, 300);
});

document.getElementById("add-card-btn").addEventListener("click", async () => {
  const nameInput = document.getElementById("add-card-name");
  const qtyInput = document.getElementById("add-card-qty");
  const msgEl = document.getElementById("add-card-msg");

  const card_name = nameInput.value.trim();
  const total_quantity = parseInt(qtyInput.value, 10);

  if (!card_name) {
    msgEl.innerHTML = `<span class="text-rose-400">Enter a card name.</span>`;
    return;
  }
  if (isNaN(total_quantity) || total_quantity < 0) {
    msgEl.innerHTML = `<span class="text-rose-400">Enter a valid quantity.</span>`;
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/inventory`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_name, total_quantity }),
    });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || `Server error: ${res.status}`);
    }

    msgEl.innerHTML = `<span class="text-emerald-400">Added '${escapeHtml(card_name)}'.</span>`;
    nameInput.value = "";
    qtyInput.value = "1";
    loadInventory();
  } catch (err) {
    msgEl.innerHTML = `<span class="text-rose-400">${err.message}</span>`;
  }
});

// ---------- Tab 4 (cont'd): Bulk Add / Remove ----------
function renderBulkInvResults(lines, warnings, skippedBasics) {
  const container = document.getElementById("bulk-inv-results");
  container.innerHTML = "";

  if (skippedBasics > 0) {
    const div = document.createElement("div");
    div.className = "text-slate-500";
    div.textContent = `(Skipped ${skippedBasics} basic land line${skippedBasics > 1 ? "s" : ""}.)`;
    container.appendChild(div);
  }

  warnings.forEach((w) => {
    const div = document.createElement("div");
    div.className = "text-amber-400";
    div.textContent = `⚠ ${w}`;
    container.appendChild(div);
  });

  const statusColor = {
    ok: "text-emerald-400",
    created: "text-emerald-400",
    partial: "text-amber-400",
    not_found: "text-rose-400",
    unparseable: "text-rose-400",
  };

  lines.forEach((line) => {
    const div = document.createElement("div");
    div.className = statusColor[line.status] || "text-slate-300";
    const label = line.status.replace("_", " ");
    div.textContent = line.message
      ? `[${label}] ${line.raw_line} — ${line.message}`
      : `[${label}] ${line.raw_line}`;
    container.appendChild(div);
  });
}

async function runBulkInventoryAction(endpoint, btnEl) {
  const decklist_text = document.getElementById("bulk-inv-input").value;
  const ignore_basic_lands = document.getElementById("bulk-inv-ignore-basics").checked;

  if (!decklist_text.trim()) {
    alert("Paste a list of cards first.");
    return;
  }

  const originalText = btnEl.textContent;
  btnEl.disabled = true;
  btnEl.textContent = "Working...";

  try {
    const res = await fetch(`${API_BASE}/inventory/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decklist_text, ignore_basic_lands }),
    });
    if (!res.ok) throw new Error(`Server error: ${res.status}`);
    const data = await res.json();
    renderBulkInvResults(data.lines, data.warnings, data.skipped_basic_lands);
    loadInventory();
  } catch (err) {
    alert(`${endpoint} failed: ${err.message}`);
  } finally {
    btnEl.disabled = false;
    btnEl.textContent = originalText;
  }
}

document.getElementById("bulk-inv-add-btn").addEventListener("click", (e) => {
  runBulkInventoryAction("bulk-add", e.target);
});
document.getElementById("bulk-inv-remove-btn").addEventListener("click", (e) => {
  runBulkInventoryAction("bulk-remove", e.target);
});

// ---------- Decks ----------
const DECK_ACTION_THRESHOLD = 90; // fixed high-confidence threshold for quick single-card actions

function populateDeckSelectOptions(deckNames) {
  const select = document.getElementById("deck-select");
  const currentSelection = select.value;
  select.innerHTML = `
    <option value="">-- choose a deck --</option>
    <option value="__new__">+ New Deck</option>
  `;
  deckNames.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
  return currentSelection;
}

function getEffectiveDeckName() {
  const select = document.getElementById("deck-select");
  if (select.value === "__new__") {
    return document.getElementById("deck-new-name-input").value.trim();
  }
  return select.value;
}

function setDeckManageButtonsDisabled(disabled) {
  document.getElementById("deck-rename-btn").disabled = disabled;
  document.getElementById("deck-delete-btn").disabled = disabled;
}

// Shows/hides the deck-view panel and loads the right data for
// whatever is currently chosen in #deck-select — "" (nothing),
// "__new__", or a real deck name. Shared by the tab loader, the
// change listener, and afterDeckMutation() (which re-settles the UI
// once a "+ New Deck" in progress becomes real after its first
// successful add/checkout).
function applyDeckSelection(value) {
  const view = document.getElementById("deck-view");
  const newNameInput = document.getElementById("deck-new-name-input");

  if (value === "__new__") {
    newNameInput.classList.remove("hidden");
    view.classList.remove("hidden");
    renderDeckTable(null, []);
    document.getElementById("checkout-input").value = "";
    setFavoriteBtnState(false, true);
    setDeckManageButtonsDisabled(true);
    return;
  }

  newNameInput.classList.add("hidden");

  if (!value) {
    view.classList.add("hidden");
    setFavoriteBtnState(false, true);
    setDeckManageButtonsDisabled(true);
    return;
  }

  view.classList.remove("hidden");
  setDeckManageButtonsDisabled(false);
  loadDeckContents(value);
  loadDeckFavoriteState(value);
  loadDeckIntoCheckoutBox(value);
}

async function loadDecksTab() {
  try {
    const [decksRes, invRes] = await Promise.all([
      fetch(`${API_BASE}/decks`),
      fetch(`${API_BASE}/inventory/names`),
    ]);
    const decksData = await decksRes.json();
    const invData = await invRes.json();

    const select = document.getElementById("deck-select");
    const currentSelection = populateDeckSelectOptions(decksData.decks);
    if (decksData.decks.includes(currentSelection) || currentSelection === "__new__") {
      select.value = currentSelection;
    }

    const datalist = document.getElementById("inventory-card-list");
    datalist.innerHTML = "";
    invData.card_names.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      datalist.appendChild(opt);
    });

    applyDeckSelection(select.value);
  } catch (err) {
    console.error("Failed to load decks tab:", err);
  }
}

// Refreshes the deck list (in case an action just created a new deck)
// and settles the UI on `deckName` as a real, selected deck. Called
// after any action that might have turned "+ New Deck" into a real one.
async function afterDeckMutation(deckName) {
  try {
    const res = await fetch(`${API_BASE}/decks`);
    const data = await res.json();
    populateDeckSelectOptions(data.decks);
    document.getElementById("deck-select").value = deckName;
    applyDeckSelection(deckName);
  } catch (err) {
    console.error("Failed to refresh deck list:", err);
  }
}

function setFavoriteBtnState(isFavorite, disabled) {
  const favBtn = document.getElementById("deck-favorite-btn");
  favBtn.disabled = disabled;
  favBtn.dataset.favorite = isFavorite ? "true" : "false";
  favBtn.classList.toggle("text-amber-400", isFavorite);
  favBtn.classList.toggle("text-slate-500", !isFavorite);
}

async function loadDeckFavoriteState(deckName) {
  setFavoriteBtnState(false, false);
  try {
    const res = await fetch(`${API_BASE}/decks/${encodeURIComponent(deckName)}/meta`);
    const data = await res.json();
    setFavoriteBtnState(data.is_favorite, false);
  } catch (err) {
    console.error("Failed to load deck favorite state:", err);
  }
}

document.getElementById("deck-favorite-btn").addEventListener("click", async () => {
  const deckName = document.getElementById("deck-select").value;
  if (!deckName || deckName === "__new__") return;
  const favBtn = document.getElementById("deck-favorite-btn");
  const next = favBtn.dataset.favorite !== "true";
  favBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/decks/${encodeURIComponent(deckName)}/favorite`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_favorite: next }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(`Server error: ${res.status}`);
    setFavoriteBtnState(data.is_favorite, false);
  } catch (err) {
    alert(`Failed to update favorite: ${err.message}`);
    favBtn.disabled = false;
  }
});

document.getElementById("deck-rename-btn").addEventListener("click", async () => {
  const currentName = document.getElementById("deck-select").value;
  if (!currentName || currentName === "__new__") return;

  const newName = prompt("Rename deck to:", currentName);
  if (newName === null) return; // cancelled
  const trimmed = newName.trim();
  if (!trimmed || trimmed === currentName) return;

  try {
    const res = await fetch(`${API_BASE}/decks/${encodeURIComponent(currentName)}/rename`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_name: trimmed }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);
    await afterDeckMutation(data.deck_name);
  } catch (err) {
    alert(`Failed to rename deck: ${err.message}`);
  }
});

document.getElementById("deck-delete-btn").addEventListener("click", async () => {
  const deckName = document.getElementById("deck-select").value;
  if (!deckName || deckName === "__new__") return;

  const confirmMsg = currentDeckCardsTotal > 0
    ? `Delete '${deckName}'? ${currentDeckCardsTotal} checked-out card(s) will be returned to available inventory. This cannot be undone.`
    : `Delete '${deckName}'? This cannot be undone.`;
  if (!confirm(confirmMsg)) return;

  try {
    const res = await fetch(`${API_BASE}/decks/${encodeURIComponent(deckName)}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    document.getElementById("deck-select").value = "";
    await loadDecksTab();
  } catch (err) {
    alert(`Failed to delete deck: ${err.message}`);
  }
});

document.getElementById("deck-select").addEventListener("change", (e) => {
  if (e.target.value === "__new__") {
    document.getElementById("deck-new-name-input").value = "";
  }
  applyDeckSelection(e.target.value);
  if (e.target.value === "__new__") {
    document.getElementById("deck-new-name-input").focus();
  }
});

document.getElementById("deck-bulk-toggle-btn").addEventListener("click", () => {
  document.getElementById("deck-bulk-panel").classList.toggle("hidden");
});

async function loadDeckContents(deckName) {
  try {
    const res = await fetch(`${API_BASE}/decks/${encodeURIComponent(deckName)}/cards`);
    const data = await res.json();
    renderDeckTable(deckName, data.cards);
  } catch (err) {
    console.error("Failed to load deck contents:", err);
  }
}

let currentDeckCardsTotal = 0;

function renderDeckTable(deckName, cards) {
  const tbody = document.getElementById("deck-table-body");
  const emptyMsg = document.getElementById("deck-empty");
  tbody.innerHTML = "";
  currentDeckCardsTotal = cards.reduce((sum, c) => sum + c.quantity, 0);

  if (cards.length === 0) {
    emptyMsg.classList.remove("hidden");
    return;
  }
  emptyMsg.classList.add("hidden");

  cards.forEach((card) => {
    const tr = document.createElement("tr");
    tr.className = "border-b border-slate-800";

    const canAddOne = card.available_more > 0;

    tr.innerHTML = `
      <td class="py-2 pr-2">${escapeHtml(card.card_name)}</td>
      <td class="py-2 px-2">${card.quantity}</td>
      <td class="py-2 px-2">
        <div class="flex gap-1">
          <button class="deck-minus-one bg-slate-800 hover:bg-slate-700 px-2 py-1 rounded text-xs">−1</button>
          <button class="deck-plus-one bg-slate-800 hover:bg-slate-700 px-2 py-1 rounded text-xs disabled:opacity-40 disabled:cursor-not-allowed" ${canAddOne ? "" : "disabled"}>+1</button>
          <button class="deck-remove-all bg-rose-900 hover:bg-rose-800 px-2 py-1 rounded text-xs">Remove all</button>
        </div>
      </td>
    `;

    tr.querySelector(".deck-minus-one").addEventListener("click", () => {
      quickCheckin(deckName, card.card_name, 1);
    });
    tr.querySelector(".deck-plus-one").addEventListener("click", () => {
      if (canAddOne) quickCheckout(deckName, card.card_name, 1);
    });
    tr.querySelector(".deck-remove-all").addEventListener("click", () => {
      if (confirm(`Remove all ${card.quantity}x '${card.card_name}' from '${deckName}'?`)) {
        quickCheckin(deckName, card.card_name, card.quantity);
      }
    });

    tbody.appendChild(tr);
  });
}

async function quickCheckout(deckName, cardName, qty) {
  try {
    const res = await fetch(`${API_BASE}/checkout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decklist_text: `${qty} ${cardName}`,
        deck_name: deckName,
        fuzzy_threshold: DECK_ACTION_THRESHOLD,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    const line = data.lines[0];
    if (line && line.status !== "ok") {
      alert(line.message || `Could not fully check out '${cardName}'.`);
    }
    loadDeckContents(deckName);
  } catch (err) {
    alert(`Failed to check out card: ${err.message}`);
  }
}

async function quickCheckin(deckName, cardName, qty) {
  try {
    const res = await fetch(`${API_BASE}/checkin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decklist_text: `${qty} ${cardName}`,
        deck_name: deckName,
        fuzzy_threshold: DECK_ACTION_THRESHOLD,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    const line = data.lines[0];
    if (line && line.status !== "ok") {
      alert(line.message || `Could not fully check in '${cardName}'.`);
    }
    loadDeckContents(deckName);
  } catch (err) {
    alert(`Failed to check in card: ${err.message}`);
  }
}

document.getElementById("deck-add-card-btn").addEventListener("click", async () => {
  const deckName = getEffectiveDeckName();
  const nameInput = document.getElementById("deck-add-card-name");
  const qtyInput = document.getElementById("deck-add-card-qty");
  const msgEl = document.getElementById("deck-add-msg");

  if (!deckName) {
    msgEl.innerHTML = `<span class="text-rose-400">Select a deck first, or name your new deck above.</span>`;
    return;
  }

  const cardName = nameInput.value.trim();
  const qty = parseInt(qtyInput.value, 10);

  if (!cardName) {
    msgEl.innerHTML = `<span class="text-rose-400">Enter a card name.</span>`;
    return;
  }
  if (isNaN(qty) || qty < 1) {
    msgEl.innerHTML = `<span class="text-rose-400">Enter a valid quantity.</span>`;
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/checkout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decklist_text: `${qty} ${cardName}`,
        deck_name: deckName,
        fuzzy_threshold: DECK_ACTION_THRESHOLD,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    const line = data.lines[0];
    if (line && line.status === "ok") {
      msgEl.innerHTML = `<span class="text-emerald-400">Added ${qty}x '${escapeHtml(cardName)}' to '${escapeHtml(deckName)}'.</span>`;
    } else {
      msgEl.innerHTML = `<span class="text-amber-400">${line ? line.message : "Nothing was added."}</span>`;
    }

    nameInput.value = "";
    qtyInput.value = "1";
    await afterDeckMutation(deckName);
  } catch (err) {
    msgEl.innerHTML = `<span class="text-rose-400">${err.message}</span>`;
  }
});

// ---------- Tab 1 (cont'd): Add Output 1 to a deck ----------
document.getElementById("output-add-to-deck-btn").addEventListener("click", async (e) => {
  const decklist_text = document.getElementById("output-available").value;
  const deck_name = document.getElementById("output-deck-name").value.trim();
  const fuzzy_threshold = scaleToApiThreshold(clampScale(searchThresholdVal.value));

  if (!decklist_text.trim()) {
    alert("Output 1 is empty — run a search first.");
    return;
  }
  if (!deck_name) {
    alert("Enter or select a deck name.");
    return;
  }

  const btn = e.target;
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Adding...";

  try {
    const res = await fetch(`${API_BASE}/checkout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decklist_text, deck_name, fuzzy_threshold }),
    });
    if (!res.ok) throw new Error(`Server error: ${res.status}`);
    const data = await res.json();
    renderCheckoutResults(data.lines, data.warnings, "output-add-to-deck-results");
    loadDeckList(); // refresh datalist in case a new deck name was created
  } catch (err) {
    alert(`Add to deck failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

// ---------- Auth ----------
function showAuthView() {
  document.getElementById("menu-btn").classList.add("hidden");
  document.getElementById("site-title-btn").classList.add("hidden");
  document.getElementById("view-home").classList.add("hidden");
  document.getElementById("view-app").classList.add("hidden");
  document.getElementById("view-card").classList.add("hidden");
  document.getElementById("view-auth").classList.remove("hidden");
}

function onAuthenticated(username) {
  document.getElementById("menu-btn").classList.remove("hidden");
  document.getElementById("site-title-btn").classList.remove("hidden");
  document.getElementById("view-auth").classList.add("hidden");
  document.getElementById("drawer-username").textContent = username;
  loadDeckList(); // populate the Search/Add-to-Deck datalist
  showHomeView();
}

async function checkAuthAndInit() {
  try {
    const res = await fetch(`${API_BASE}/auth/me`);
    if (res.ok) {
      const data = await res.json();
      onAuthenticated(data.username);
    } else {
      showAuthView();
    }
  } catch (err) {
    showAuthView();
  }
}

let authMode = "login"; // "login" | "register"

document.getElementById("auth-toggle-mode-btn").addEventListener("click", () => {
  authMode = authMode === "login" ? "register" : "login";
  document.getElementById("auth-heading").textContent = authMode === "login" ? "Log In" : "Register";
  document.getElementById("auth-submit-btn").textContent = authMode === "login" ? "Log In" : "Register";
  document.getElementById("auth-toggle-mode-btn").textContent =
    authMode === "login" ? "Need an account? Register" : "Already have an account? Log in";
  document.getElementById("auth-msg").textContent = "";
});

document.getElementById("auth-submit-btn").addEventListener("click", async () => {
  const username = document.getElementById("auth-username").value.trim();
  const password = document.getElementById("auth-password").value;
  const msgEl = document.getElementById("auth-msg");
  const btn = document.getElementById("auth-submit-btn");

  if (!username || !password) {
    msgEl.innerHTML = `<span class="text-rose-400">Enter a username and password.</span>`;
    return;
  }

  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Working...";

  try {
    const res = await fetch(`${API_BASE}/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    document.getElementById("auth-password").value = "";
    onAuthenticated(data.username);
  } catch (err) {
    msgEl.innerHTML = `<span class="text-rose-400">${err.message}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

document.getElementById("auth-password").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("auth-submit-btn").click();
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  try {
    await fetch(`${API_BASE}/auth/logout`, { method: "POST" });
  } finally {
    closeDrawer();
    location.reload();
  }
});

checkAuthAndInit();
