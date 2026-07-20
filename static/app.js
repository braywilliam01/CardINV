const API_BASE = "/api";
const DEFAULT_APP_TAB = "manage";
const GAME_LABELS = { mtg: "Magic: The Gathering", pokemon: "Pokémon" };

let currentGame = "mtg";

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

// ---------- Game switching ----------
function applyGameUIState() {
  document.querySelectorAll(".game-switch-btn").forEach((btn) => {
    const active = btn.dataset.gameSelect === currentGame;
    btn.classList.toggle("bg-indigo-600", active);
    btn.classList.toggle("text-white", active);
    btn.classList.toggle("text-slate-400", !active);
  });
  document.querySelectorAll(".game-mtg-only").forEach((el) => {
    el.classList.toggle("hidden", currentGame !== "mtg");
  });
}

async function switchGame(game) {
  if (game === "everything") {
    closeDrawer();
    showEverythingView();
    return;
  }

  if (game !== currentGame) {
    try {
      await fetch(`${API_BASE}/session/game`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game }),
      });
    } catch (err) {
      console.error("Failed to switch game:", err);
      return;
    }
    currentGame = game;
    applyGameUIState();
  }

  closeDrawer();
  showHomeView();
}

document.querySelectorAll(".game-switch-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchGame(btn.dataset.gameSelect));
});

// ---------- Homepage / app / everything / settings view switching ----------
const ALL_VIEW_IDS = ["view-auth", "view-home", "view-app", "view-card", "view-everything", "view-settings"];

function hideAllViews() {
  ALL_VIEW_IDS.forEach((id) => document.getElementById(id).classList.add("hidden"));
}

function showHomeView() {
  hideAllViews();
  document.getElementById("view-home").classList.remove("hidden");
  loadHomepage();
}

async function showAppView(tabName) {
  hideAllViews();
  document.getElementById("view-app").classList.remove("hidden");
  await activateTab(tabName || DEFAULT_APP_TAB);
}

function showCardView() {
  hideAllViews();
  document.getElementById("view-card").classList.remove("hidden");
}

function showEverythingView() {
  hideAllViews();
  document.getElementById("view-everything").classList.remove("hidden");
  loadEverythingView();
}

function showSettingsView() {
  hideAllViews();
  document.getElementById("view-settings").classList.remove("hidden");
  loadSettingsView();
}

async function loadEverythingView() {
  try {
    const res = await fetch(`${API_BASE}/homepage/everything`);
    const data = await res.json();

    document.getElementById("everything-total-cards").textContent = data.total_quantity.toLocaleString();
    document.getElementById("everything-unique-cards").textContent = `${data.unique_cards.toLocaleString()} unique`;
    document.getElementById("everything-deck-count").textContent = data.deck_count.toLocaleString();
    document.getElementById("everything-collection-value").textContent = `$${data.collection_value_usd.toFixed(2)}`;

    const mtg = data.per_game.mtg;
    const pokemon = data.per_game.pokemon;
    document.getElementById("everything-mtg-stats").textContent =
      `${mtg.total_quantity.toLocaleString()} cards · ${mtg.deck_count} deck${mtg.deck_count === 1 ? "" : "s"} · $${mtg.collection_value_usd.toFixed(2)}`;
    document.getElementById("everything-pokemon-stats").textContent =
      `${pokemon.total_quantity.toLocaleString()} cards · ${pokemon.deck_count} deck${pokemon.deck_count === 1 ? "" : "s"} · $${pokemon.collection_value_usd.toFixed(2)}`;
  } catch (err) {
    console.error("Failed to load Everything view:", err);
  }
}

document.getElementById("everything-mtg-card").addEventListener("click", () => switchGame("mtg"));
document.getElementById("everything-pokemon-card").addEventListener("click", () => switchGame("pokemon"));

document.getElementById("site-title-btn").addEventListener("click", showHomeView);

async function loadHomepage() {
  document.getElementById("home-game-label").textContent = GAME_LABELS[currentGame] || currentGame;
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

// Price is a whole-card property (not per-face — see card_lookup.py),
// so this renders once at the top of the detail view rather than
// interleaved with per-face bodies. Leads with the price, big and bold,
// so it's readable at a glance before scrolling past rules text —
// especially on mobile, where the primary price is the first thing on
// screen after the card name.
function renderPriceHero(card) {
  // Not every printing has a USD price (e.g. some Pokemon promos only
  // carry a Cardmarket/EUR price) — pick whichever price actually
  // exists as the big primary figure instead of hard-coding USD, so
  // the hero never shows "No pricing available" right next to a
  // contradicting secondary price.
  const candidates = [];
  if (card.price_usd != null) candidates.push({ label: "USD", symbol: "$", value: card.price_usd });
  if (card.price_usd_foil != null) candidates.push({ label: "Foil", symbol: "$", value: card.price_usd_foil });
  if (card.price_eur != null) candidates.push({ label: "EUR", symbol: "€", value: card.price_eur });

  const [primary, ...secondary] = candidates;

  const priceDisplay = primary
    ? `
      <div class="flex items-baseline gap-2">
        <span class="text-4xl font-extrabold text-emerald-400 leading-none">${primary.symbol}${Number(primary.value).toFixed(2)}</span>
        <span class="text-xs text-slate-500 uppercase tracking-wide">${primary.label}</span>
      </div>
    `
    : `<div class="text-lg font-medium text-slate-500">No pricing available</div>`;

  const secondaryHtml = secondary
    .map((s) => `<span><span class="text-slate-500">${s.label}</span> ${s.symbol}${Number(s.value).toFixed(2)}</span>`)
    .join("");

  return `
    <div class="bg-slate-900 border border-slate-700 rounded-lg p-4 mb-4">
      <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          ${priceDisplay}
          ${secondaryHtml ? `<div class="flex flex-wrap gap-3 mt-1.5 text-sm text-slate-400">${secondaryHtml}</div>` : ""}
        </div>
        <div class="flex items-center justify-between sm:justify-end gap-3">
          <div class="text-sm text-slate-400">
            <span class="text-slate-500"># owned:</span>
            <span id="card-owned-qty" class="font-semibold text-slate-100 text-base">${card.owned_quantity}</span>
          </div>
          <button id="card-add-to-inventory-btn"
            class="bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 px-4 py-2.5 rounded-lg font-medium text-sm whitespace-nowrap">
            + Add to Inventory
          </button>
        </div>
      </div>
    </div>
  `;
}

function renderMetaLine(card) {
  return `
    <div class="text-xs text-slate-500 mt-4 space-y-1">
      <div>${escapeHtml(card.set_name || "")} (${escapeHtml(card.set_code || "")}) #${escapeHtml(card.collector_number || "")} · ${escapeHtml(card.rarity || "")}</div>
      ${card.artist ? `<div>Illustrated by ${escapeHtml(card.artist)}</div>` : ""}
      ${card.external_url ? `<a href="${card.external_url}" target="_blank" rel="noopener" class="text-indigo-400 hover:text-indigo-300">${escapeHtml(card.external_url_label || "View source")} →</a>` : ""}
    </div>
  `;
}

function renderMtgCardBody(card) {
  return card.faces
    ? card.faces.map(renderCardFace).join(`<div class="my-4 border-t border-slate-800"></div>`)
    : renderCardFace(card.primary);
}

function renderPokemonCardBody(card) {
  const img = card.primary.image_url
    ? `<img src="${card.primary.image_url}" alt="${escapeHtml(card.name || "")}" class="w-full max-w-xs rounded-lg border border-slate-700">`
    : `<div class="w-full max-w-xs aspect-[5/7] rounded-lg border border-slate-700 bg-slate-900 flex items-center justify-center text-slate-600 text-sm">No image</div>`;

  const abilities = (card.abilities || []).map((a) => `
    <div class="mb-2">
      <div class="text-sm font-semibold text-indigo-300">${escapeHtml(a.type || "Ability")}: ${escapeHtml(a.name || "")}</div>
      <div class="text-sm">${escapeHtml(a.text || "")}</div>
    </div>
  `).join("");

  const attacks = (card.attacks || []).map((atk) => `
    <div class="mb-2">
      <div class="flex items-baseline justify-between gap-2 flex-wrap">
        <span class="text-sm font-semibold">
          ${escapeHtml(atk.name || "")}${atk.cost && atk.cost.length ? ` (${atk.cost.map(escapeHtml).join(", ")})` : ""}
        </span>
        ${atk.damage ? `<span class="text-sm text-slate-300">${escapeHtml(atk.damage)}</span>` : ""}
      </div>
      ${atk.text ? `<div class="text-sm text-slate-400">${escapeHtml(atk.text)}</div>` : ""}
    </div>
  `).join("");

  const weaknesses = (card.weaknesses || []).map((w) => `${escapeHtml(w.type)} ${escapeHtml(w.value)}`).join(", ");
  const resistances = (card.resistances || []).map((r) => `${escapeHtml(r.type)} ${escapeHtml(r.value)}`).join(", ");
  const retreatCost = (card.retreat_cost || []).length;
  const typeLine = card.primary.type_line || "";
  const types = (card.types || []).join(", ");

  return `
    <div class="flex flex-col sm:flex-row gap-4">
      ${img}
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline justify-between gap-2 flex-wrap">
          <h2 class="text-xl font-bold">${escapeHtml(card.name || "")}</h2>
          ${card.hp ? `<span class="text-slate-400 text-sm">HP ${escapeHtml(card.hp)}</span>` : ""}
        </div>
        <div class="text-slate-400 text-sm mb-2">${escapeHtml(typeLine)}${types ? ` · ${escapeHtml(types)}` : ""}</div>
        ${card.evolves_from ? `<div class="text-xs text-slate-500 mb-2">Evolves from ${escapeHtml(card.evolves_from)}</div>` : ""}
        ${abilities}
        ${attacks}
        ${weaknesses ? `<div class="text-sm text-slate-400 mt-2">Weakness: ${weaknesses}</div>` : ""}
        ${resistances ? `<div class="text-sm text-slate-400">Resistance: ${resistances}</div>` : ""}
        ${retreatCost ? `<div class="text-sm text-slate-400">Retreat Cost: ${retreatCost}</div>` : ""}
        ${card.flavor_text ? `<p class="text-xs italic text-slate-500 mt-2">${escapeHtml(card.flavor_text)}</p>` : ""}
      </div>
    </div>
  `;
}

let currentCardInventoryName = null;
let currentCardSetCode = "";
let currentCardCollectorNumber = "";

function renderCardDetail(card) {
  currentCardInventoryName = card.inventory_name;
  currentCardSetCode = card.set_code || "";
  currentCardCollectorNumber = card.collector_number || "";

  const body = currentGame === "pokemon" ? renderPokemonCardBody(card) : renderMtgCardBody(card);
  const legalities = Object.entries(card.legalities || {})
    .map(([fmt, status]) => legalityBadge(fmt, status))
    .join(" ");

  document.getElementById("card-detail-content").innerHTML = `
    ${renderPriceHero(card)}
    ${body}
    <div class="flex flex-wrap gap-2 mt-3">${legalities}</div>
    ${renderMetaLine(card)}
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
      body: JSON.stringify({
        card_name: currentCardInventoryName,
        set_code: currentCardSetCode,
        collector_number: currentCardCollectorNumber,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    // Show this specific printing's count, not the card's aggregate
    // total across every printing.
    const printing = (data.printings || []).find(
      (p) => p.set_code === currentCardSetCode && p.collector_number === currentCardCollectorNumber
    );
    qtyEl.textContent = printing ? printing.total_quantity : data.total_quantity;
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
    // Round-trips through parser.py: a resolved printing renders with
    // its "(SET) NUM" pin, so editing quantities and syncing back
    // targets the same printings rather than re-drawing cheapest-first.
    const lines = data.cards
      .slice()
      .sort((a, b) =>
        a.card_name.localeCompare(b.card_name)
        || a.set_code.localeCompare(b.set_code)
        || a.collector_number.localeCompare(b.collector_number)
      )
      .map((c) => buildDecklistLine(c.quantity, c.card_name, c.set_code, c.collector_number));
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
    const printingNote = line.set_code || line.collector_number ? ` (${line.set_code} #${line.collector_number})` : "";
    div.textContent = line.message
      ? `[${line.status}] ${line.card_name}${printingNote}: ${line.current_qty} → ${line.target_qty} (${deltaLabel}) — ${line.message}`
      : `[${line.status}] ${line.card_name}${printingNote}: ${line.current_qty} → ${line.target_qty} (${deltaLabel})`;
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
        ✓ Reconciled ${data.unique_cards_loaded} unique cards (${data.total_quantity_loaded} total copies):
        ${data.printings_added} printing${data.printings_added === 1 ? "" : "s"} added,
        ${data.printings_updated} updated,
        ${data.printings_removed} removed.
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
    if (data.estimated_cards > 0) {
      detail += ` (${data.estimated_cards} estimated)`;
    }

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
    fetching_index: "Contacting price API...",
    downloading: "Downloading price data...",
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

    if (data.skipped_errors > 0 || data.skipped_pages > 0) {
      const parts = [];
      if (data.skipped_errors > 0) parts.push(`${data.skipped_errors} card(s) skipped due to bad price data`);
      if (data.skipped_pages > 0) parts.push(`${data.skipped_pages} page(s) of the catalog couldn't be fetched`);
      alert(`Price refresh complete, but ${parts.join(" and ")} — try refreshing again later to fill in the gaps.`);
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

    // A name with more than one printing row can't be edited as a
    // single quantity from the collapsed row — which specific printing
    // would a "+1" apply to? Expand the row to edit a printing directly.
    const multiPrinting = card.printing_count > 1;

    // price_usd is only set by the backend when there's exactly one
    // printing (otherwise "the" price is ambiguous) — a multi-printing
    // card shows "multiple" here instead of a number, but line_value
    // always sums every priced printing regardless of count.
    const priceDisplay = multiPrinting
      ? "multiple"
      : (card.price_usd != null ? `$${card.price_usd.toFixed(2)}` : "—");
    const valueDisplay = card.line_value != null ? `$${card.line_value.toFixed(2)}` : "—";
    const estimatedBadge = card.has_estimated
      ? `<span class="text-amber-400" title="Includes an estimated price — not a fetched price for a specific printing you own">*</span>`
      : "";

    tr.innerHTML = `
      <td class="py-2 pr-2 align-top">
        <button type="button" class="row-expand-btn w-5 h-5 flex items-center justify-center text-slate-500 hover:text-slate-200" title="Show printings">▸</button>
      </td>
      <td class="py-2 pr-2">
        ${escapeHtml(card.card_name)}
        ${card.decks.length ? `<span class="text-xs text-slate-500" title="${escapeHtml(deckTitle)}"> (in ${card.decks.length} deck${card.decks.length > 1 ? "s" : ""})</span>` : ""}
        ${multiPrinting ? `<span class="text-xs text-slate-500"> · ${card.printing_count} printings</span>` : ""}
        ${card.has_unresolved ? `<span class="text-xs text-amber-400" title="Has copies not yet assigned to a specific printing"> · unresolved</span>` : ""}
      </td>
      <td class="py-2 px-2">
        <div class="flex items-center gap-1">
          <button class="qty-nudge w-6 h-6 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed" data-delta="-1" ${multiPrinting ? "disabled" : ""}>−</button>
          <input type="number" min="0" value="${card.total_quantity}"
            class="qty-input w-14 bg-slate-800 border border-slate-700 rounded px-1 py-0.5 text-center disabled:opacity-50"
            ${multiPrinting ? "disabled" : ""}>
          <button class="qty-nudge w-6 h-6 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed" data-delta="1" ${multiPrinting ? "disabled" : ""}>+</button>
        </div>
        ${multiPrinting ? `<div class="text-[10px] text-slate-500 mt-1">expand to edit</div>` : ""}
      </td>
      <td class="py-2 px-2 text-slate-400">${card.checked_out}</td>
      <td class="py-2 px-2 ${card.available > 0 ? "text-emerald-400" : "text-slate-500"}">${card.available}</td>
      <td class="py-2 px-2 text-slate-300">${priceDisplay}${estimatedBadge}</td>
      <td class="py-2 px-2 text-slate-300">${valueDisplay}</td>
      <td class="py-2 px-2">
        <div class="flex gap-2">
          <button class="qty-save bg-indigo-600 hover:bg-indigo-500 disabled:opacity-30 disabled:cursor-not-allowed px-2 py-1 rounded text-xs" ${multiPrinting ? "disabled" : ""}>Save</button>
          <button class="price-refresh bg-slate-700 hover:bg-slate-600 disabled:opacity-30 disabled:cursor-not-allowed px-2 py-1 rounded text-xs" title="Refresh this card's price" ${multiPrinting ? "disabled" : ""}>$</button>
          <button class="card-delete bg-rose-900 hover:bg-rose-800 px-2 py-1 rounded text-xs" title="Delete this card and all its printings">Delete</button>
        </div>
      </td>
    `;

    const qtyInput = tr.querySelector(".qty-input");

    if (!multiPrinting) {
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
        const printing = card.printings[0] || { set_code: "", collector_number: "" };
        await saveQuantity(card.card_name, newQty, printing.set_code, printing.collector_number);
      });

      tr.querySelector(".price-refresh").addEventListener("click", async (e) => {
        const printing = card.printings[0] || { set_code: "", collector_number: "" };
        await refreshCardPrice(card.card_name, printing.set_code, printing.collector_number, e.target);
      });
    }

    tr.querySelector(".card-delete").addEventListener("click", async () => {
      await deleteCard(card.card_name);
    });

    tbody.appendChild(tr);

    // Printings breakdown, built from data already in `card` (no extra
    // fetch) and toggled via the expand button — hidden by default.
    const printingsTr = document.createElement("tr");
    printingsTr.className = "printings-row hidden border-b border-slate-800";
    const printingsTd = document.createElement("td");
    printingsTd.colSpan = 8;
    printingsTd.className = "py-3 px-2 bg-slate-900/50";
    printingsTd.appendChild(renderPrintingsPanel(card));
    printingsTr.appendChild(printingsTd);
    tbody.appendChild(printingsTr);

    tr.querySelector(".row-expand-btn").addEventListener("click", (e) => {
      const collapsed = printingsTr.classList.contains("hidden");
      printingsTr.classList.toggle("hidden");
      e.target.textContent = collapsed ? "▾" : "▸";
    });
  });
}

function renderPrintingsPanel(card) {
  const wrap = document.createElement("div");

  const table = document.createElement("table");
  table.className = "w-full text-xs mb-3";
  table.innerHTML = `
    <thead>
      <tr class="text-left text-slate-500 border-b border-slate-800">
        <th class="py-1 pr-2">Printing</th>
        <th class="py-1 px-2 w-24">Quantity</th>
        <th class="py-1 px-2 w-20">Price</th>
        <th class="py-1 px-2 w-20">Value</th>
        <th class="py-1 px-2 w-36">Actions</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;
  const tbody = table.querySelector("tbody");

  card.printings.forEach((p) => {
    const label = p.is_unresolved
      ? `<span class="text-amber-400">Unresolved</span>`
      : `${escapeHtml(p.set_code)} #${escapeHtml(p.collector_number)}`;

    const priceDisplay = p.price_usd != null ? `$${p.price_usd.toFixed(2)}` : "—";
    const valueDisplay = p.line_value != null ? `$${p.line_value.toFixed(2)}` : "—";
    const estimatedBadge = p.is_estimated
      ? `<span class="text-amber-400" title="Estimated — not a fetched price for this exact printing">*</span>`
      : "";

    const row = document.createElement("tr");
    row.className = "border-b border-slate-800/60";
    row.innerHTML = `
      <td class="py-1 pr-2">${label}</td>
      <td class="py-1 px-2">
        <input type="number" min="0" value="${p.total_quantity}"
          class="printing-qty-input w-16 bg-slate-800 border border-slate-700 rounded px-1 py-0.5 text-center">
      </td>
      <td class="py-1 px-2">${priceDisplay}${estimatedBadge}</td>
      <td class="py-1 px-2">${valueDisplay}</td>
      <td class="py-1 px-2">
        <div class="flex gap-1">
          <button class="printing-save bg-indigo-600 hover:bg-indigo-500 px-2 py-0.5 rounded text-[11px]">Save</button>
          <button class="printing-price-refresh bg-slate-700 hover:bg-slate-600 px-2 py-0.5 rounded text-[11px]" title="Refresh this printing's price">$</button>
          <button class="printing-delete bg-rose-900 hover:bg-rose-800 px-2 py-0.5 rounded text-[11px]">Delete</button>
        </div>
      </td>
    `;

    const qtyInput = row.querySelector(".printing-qty-input");
    row.querySelector(".printing-save").addEventListener("click", async () => {
      const newQty = parseInt(qtyInput.value, 10);
      if (isNaN(newQty) || newQty < 0) {
        alert("Enter a valid quantity (0 or higher).");
        return;
      }
      await saveQuantity(card.card_name, newQty, p.set_code, p.collector_number);
    });
    row.querySelector(".printing-price-refresh").addEventListener("click", async (e) => {
      await refreshCardPrice(card.card_name, p.set_code, p.collector_number, e.target);
    });
    row.querySelector(".printing-delete").addEventListener("click", async () => {
      await deletePrinting(card.card_name, p.set_code, p.collector_number);
    });

    tbody.appendChild(row);
  });

  wrap.appendChild(table);

  if (card.has_unresolved) {
    const fixup = document.createElement("div");
    fixup.className = "bg-slate-950 border border-slate-800 rounded-lg p-3";
    fixup.innerHTML = `
      <div class="text-xs text-slate-400 mb-2">Assign unresolved copies to a printing</div>
      <div class="flex flex-wrap gap-2">
        <input type="text" placeholder="Set" list="add-card-set-list"
          class="fixup-set flex-1 min-w-[100px] bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs">
        <input type="text" placeholder="Collector #"
          class="fixup-number w-28 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs">
        <input type="number" min="1" value="1" placeholder="Qty"
          class="fixup-qty w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs">
        <button class="fixup-submit bg-indigo-600 hover:bg-indigo-500 px-3 py-1 rounded text-xs font-medium">Assign</button>
      </div>
      <div class="fixup-msg text-xs mt-2"></div>
    `;

    fixup.querySelector(".fixup-submit").addEventListener("click", async () => {
      const setCode = fixup.querySelector(".fixup-set").value.trim();
      const number = fixup.querySelector(".fixup-number").value.trim();
      const qty = parseInt(fixup.querySelector(".fixup-qty").value, 10);
      const msgEl = fixup.querySelector(".fixup-msg");

      if (!setCode && !number) {
        msgEl.innerHTML = `<span class="text-rose-400">Enter a set and/or collector number.</span>`;
        return;
      }
      if (isNaN(qty) || qty <= 0) {
        msgEl.innerHTML = `<span class="text-rose-400">Enter a quantity of 1 or more.</span>`;
        return;
      }

      try {
        const res = await fetch(`${API_BASE}/inventory/${encodeURIComponent(card.card_name)}/assign-printing`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ quantity: qty, set_code: setCode, collector_number: number }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);
        loadInventory();
      } catch (err) {
        msgEl.innerHTML = `<span class="text-rose-400">${err.message}</span>`;
      }
    });

    wrap.appendChild(fixup);
  }

  return wrap;
}

async function saveQuantity(cardName, newQty, setCode = "", collectorNumber = "") {
  try {
    const res = await fetch(`${API_BASE}/inventory/${encodeURIComponent(cardName)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ total_quantity: newQty, set_code: setCode, collector_number: collectorNumber }),
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
    // First attempt: blocked by default if checked out anywhere. This
    // deletes every printing of the card — see deletePrinting for
    // removing just one.
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

async function deletePrinting(cardName, setCode, collectorNumber) {
  const label = setCode || collectorNumber ? `${setCode} #${collectorNumber}` : "unresolved";
  try {
    const params = new URLSearchParams({ set_code: setCode || "", collector_number: collectorNumber || "" });
    let res = await fetch(
      `${API_BASE}/inventory/${encodeURIComponent(cardName)}/printing?${params.toString()}`,
      { method: "DELETE" }
    );

    if (res.status === 409) {
      const data = await res.json();
      const decks = (data.detail && data.detail.decks) || [];
      const breakdown = decks.map((d) => `${d.quantity}x from "${d.deck_name}"`).join(", ");

      const confirmed = confirm(
        `'${cardName}' (${label}) can't be removed without leaving deck checkouts unaccounted for (${breakdown}).\n\n` +
        `Removing will also check those cards in from those decks. This cannot be undone.\n\n` +
        `Continue?`
      );
      if (!confirmed) return;

      params.set("force", "true");
      res = await fetch(
        `${API_BASE}/inventory/${encodeURIComponent(cardName)}/printing?${params.toString()}`,
        { method: "DELETE" }
      );
    }

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data.detail && data.detail.message) || data.detail || `Server error: ${res.status}`);
    }

    loadInventory();
  } catch (err) {
    alert(`Failed to delete printing: ${err.message}`);
  }
}

async function refreshCardPrice(cardName, setCode, collectorNumber, btn) {
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "...";
  try {
    const params = new URLSearchParams({ set_code: setCode || "", collector_number: collectorNumber || "" });
    const res = await fetch(
      `${API_BASE}/pricing/refresh-card/${encodeURIComponent(cardName)}?${params.toString()}`,
      { method: "POST" }
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);
    loadInventory();
    loadPricingSummary();
  } catch (err) {
    alert(`Price lookup failed: ${err.message}`);
    btn.disabled = false;
    btn.textContent = originalText;
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
  const setInput = document.getElementById("add-card-set");
  const numberInput = document.getElementById("add-card-number");
  const msgEl = document.getElementById("add-card-msg");

  const card_name = nameInput.value.trim();
  const total_quantity = parseInt(qtyInput.value, 10);
  const set_code = setInput.value.trim();
  const collector_number = numberInput.value.trim();

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
      body: JSON.stringify({ card_name, total_quantity, set_code, collector_number }),
    });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || `Server error: ${res.status}`);
    }

    msgEl.innerHTML = `<span class="text-emerald-400">Added '${escapeHtml(card_name)}'.</span>`;
    nameInput.value = "";
    qtyInput.value = "1";
    setInput.value = "";
    numberInput.value = "";
    loadInventory();
  } catch (err) {
    msgEl.innerHTML = `<span class="text-rose-400">${err.message}</span>`;
  }
});

// ---------- Set autocomplete (backs every Set field: Add a card, the
// fix-up form, and Add a card to this deck — all share the
// "add-card-set-list" datalist) ----------
let setAutocompleteDebounce;
function wireSetAutocomplete(inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.addEventListener("input", (e) => {
    clearTimeout(setAutocompleteDebounce);
    const q = e.target.value.trim();
    setAutocompleteDebounce = setTimeout(async () => {
      try {
        const res = await fetch(`${API_BASE}/sets?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        const datalist = document.getElementById("add-card-set-list");
        datalist.innerHTML = (data.sets || [])
          .map((s) => `<option value="${escapeHtml(s.code)}">${escapeHtml(s.name)} (${escapeHtml(s.code)})</option>`)
          .join("");
      } catch (err) {
        console.error("Failed to load sets:", err);
      }
    }, 200);
  });
}
wireSetAutocomplete("add-card-set");
wireSetAutocomplete("deck-add-card-set");

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
    // A deck can hold several rows for the same name — one per
    // printing it was drawn from (see checkout.py's cheapest-first
    // draw-down) — so each row is labeled with its printing, and its
    // own +1/-1/Remove all act on exactly that printing (pinned),
    // never the name-wide pool.
    const printingLabel = card.set_code || card.collector_number
      ? `<span class="text-xs text-slate-500"> (${escapeHtml(card.set_code)} #${escapeHtml(card.collector_number)})</span>`
      : "";

    tr.innerHTML = `
      <td class="py-2 pr-2">${escapeHtml(card.card_name)}${printingLabel}</td>
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
      quickCheckin(deckName, card.card_name, 1, card.set_code, card.collector_number);
    });
    tr.querySelector(".deck-plus-one").addEventListener("click", () => {
      if (canAddOne) quickCheckout(deckName, card.card_name, 1, card.set_code, card.collector_number);
    });
    tr.querySelector(".deck-remove-all").addEventListener("click", () => {
      const label = card.set_code || card.collector_number
        ? `${card.card_name} (${card.set_code} #${card.collector_number})`
        : card.card_name;
      if (confirm(`Remove all ${card.quantity}x '${label}' from '${deckName}'?`)) {
        quickCheckin(deckName, card.card_name, card.quantity, card.set_code, card.collector_number);
      }
    });

    tbody.appendChild(tr);
  });
}

// Builds a pasteable decklist line, appending the "(SET) NUM" pin when
// a printing is given — the same round-trip format loadDeckIntoCheckoutBox
// reads back and parser.py parses out server-side.
function buildDecklistLine(qty, cardName, setCode = "", collectorNumber = "") {
  const suffix = setCode || collectorNumber ? ` (${setCode}) ${collectorNumber}` : "";
  return `${qty} ${cardName}${suffix}`;
}

async function quickCheckout(deckName, cardName, qty, setCode = "", collectorNumber = "") {
  try {
    const res = await fetch(`${API_BASE}/checkout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decklist_text: buildDecklistLine(qty, cardName, setCode, collectorNumber),
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

async function quickCheckin(deckName, cardName, qty, setCode = "", collectorNumber = "") {
  try {
    const res = await fetch(`${API_BASE}/checkin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decklist_text: buildDecklistLine(qty, cardName, setCode, collectorNumber),
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
  const setInput = document.getElementById("deck-add-card-set");
  const numberInput = document.getElementById("deck-add-card-number");
  const msgEl = document.getElementById("deck-add-msg");

  if (!deckName) {
    msgEl.innerHTML = `<span class="text-rose-400">Select a deck first, or name your new deck above.</span>`;
    return;
  }

  const cardName = nameInput.value.trim();
  const qty = parseInt(qtyInput.value, 10);
  const setCode = setInput.value.trim();
  const collectorNumber = numberInput.value.trim();

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
        decklist_text: buildDecklistLine(qty, cardName, setCode, collectorNumber),
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
    setInput.value = "";
    numberInput.value = "";
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
let isAdmin = false;

function showAuthView() {
  document.getElementById("menu-btn").classList.add("hidden");
  document.getElementById("site-title-btn").classList.add("hidden");
  hideAllViews();
  document.getElementById("view-auth").classList.remove("hidden");
}

function onAuthenticated(username, game, admin) {
  document.getElementById("menu-btn").classList.remove("hidden");
  document.getElementById("site-title-btn").classList.remove("hidden");
  document.getElementById("view-auth").classList.add("hidden");
  document.getElementById("drawer-username").textContent = username;
  document.getElementById("drawer-admin-badge").classList.toggle("hidden", !admin);
  isAdmin = !!admin;
  currentGame = game || "mtg";
  applyGameUIState();
  loadDeckList(); // populate the Search/Add-to-Deck datalist
  showHomeView();
}

async function checkAuthAndInit() {
  try {
    const res = await fetch(`${API_BASE}/auth/me`);
    if (res.ok) {
      const data = await res.json();
      onAuthenticated(data.username, data.game, data.is_admin);
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
    onAuthenticated(data.username, undefined, data.is_admin);
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

// ---------- Settings ----------
document.getElementById("settings-nav-btn").addEventListener("click", () => {
  closeDrawer();
  showSettingsView();
});

function loadSettingsView() {
  document.getElementById("settings-current-password").value = "";
  document.getElementById("settings-new-password").value = "";
  document.getElementById("settings-confirm-password").value = "";
  document.getElementById("settings-password-msg").textContent = "";

  const adminPanel = document.getElementById("settings-admin-panel");
  adminPanel.classList.toggle("hidden", !isAdmin);
  if (isAdmin) loadAdminUsersList();
}

document.getElementById("settings-change-password-btn").addEventListener("click", async () => {
  const currentPassword = document.getElementById("settings-current-password").value;
  const newPassword = document.getElementById("settings-new-password").value;
  const confirmPassword = document.getElementById("settings-confirm-password").value;
  const msgEl = document.getElementById("settings-password-msg");
  const btn = document.getElementById("settings-change-password-btn");

  if (!currentPassword || !newPassword) {
    msgEl.innerHTML = `<span class="text-rose-400">Fill in both password fields.</span>`;
    return;
  }
  if (newPassword !== confirmPassword) {
    msgEl.innerHTML = `<span class="text-rose-400">New password and confirmation don't match.</span>`;
    return;
  }

  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Updating...";

  try {
    const res = await fetch(`${API_BASE}/auth/password`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    document.getElementById("settings-current-password").value = "";
    document.getElementById("settings-new-password").value = "";
    document.getElementById("settings-confirm-password").value = "";
    msgEl.innerHTML = `<span class="text-emerald-400">Password updated.</span>`;
  } catch (err) {
    msgEl.innerHTML = `<span class="text-rose-400">${err.message}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

async function loadAdminUsersList() {
  const container = document.getElementById("settings-users-list");
  container.innerHTML = `<div class="text-sm text-slate-500">Loading...</div>`;

  try {
    const res = await fetch(`${API_BASE}/admin/users`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    container.innerHTML = "";
    data.users.forEach((u) => {
      const row = document.createElement("div");
      row.className = "flex items-center justify-between gap-2 bg-slate-800 rounded-lg px-3 py-2";
      row.innerHTML = `
        <div class="text-sm">
          ${escapeHtml(u.username)}
          ${u.is_admin ? `<span class="ml-1 px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-400 text-[10px] uppercase tracking-wide align-middle">Admin</span>` : ""}
        </div>
        <button class="admin-reset-btn bg-slate-700 hover:bg-slate-600 px-3 py-1.5 rounded-lg text-xs font-medium">
          Reset Password
        </button>
      `;
      row.querySelector(".admin-reset-btn").addEventListener("click", () => resetUserPassword(u.username));
      container.appendChild(row);
    });
  } catch (err) {
    container.innerHTML = `<div class="text-sm text-rose-400">Failed to load users: ${err.message}</div>`;
  }
}

async function resetUserPassword(username) {
  const newPassword = prompt(`New password for '${username}':`);
  if (newPassword === null) return; // cancelled
  if (newPassword.trim().length < 8) {
    alert("Password must be at least 8 characters.");
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/admin/users/${encodeURIComponent(username)}/reset-password`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_password: newPassword.trim() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server error: ${res.status}`);

    alert(`Password for '${username}' has been reset. Let them know the new password directly.`);
  } catch (err) {
    alert(`Failed to reset password: ${err.message}`);
  }
}

checkAuthAndInit();
