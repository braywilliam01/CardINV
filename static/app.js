const API_BASE = "/api";
const DEFAULT_APP_TAB = "manage";

// ---------- Tab switching ----------
async function activateTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach((b) => {
    const active = b.dataset.tab === tabName;
    b.classList.toggle("border-indigo-500", active);
    b.classList.toggle("text-indigo-400", active);
    b.classList.toggle("border-transparent", !active);
    b.classList.toggle("text-slate-400", !active);
  });

  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
  document.getElementById(`tab-${tabName}`).classList.remove("hidden");

  if (tabName === "search") loadDeckList();
  if (tabName === "checkout") loadDeckList();
  if (tabName === "manage") { loadInventory(); loadPricingSummary(); }
  if (tabName === "decks") await loadDecksTab();
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

// ---------- Homepage / app view switching ----------
function showHomeView() {
  document.getElementById("view-home").classList.remove("hidden");
  document.getElementById("view-app").classList.add("hidden");
  loadHomepage();
}

async function showAppView(tabName) {
  document.getElementById("view-home").classList.add("hidden");
  document.getElementById("view-app").classList.remove("hidden");
  await activateTab(tabName || DEFAULT_APP_TAB);
}

document.getElementById("site-title-btn").addEventListener("click", showHomeView);
document.getElementById("home-open-app-btn").addEventListener("click", () => showAppView());

async function loadHomepage() {
  try {
    const [summaryRes, shortcutsRes] = await Promise.all([
      fetch(`${API_BASE}/homepage/summary`),
      fetch(`${API_BASE}/homepage/deck-shortcuts`),
    ]);
    const summary = await summaryRes.json();
    const shortcuts = await shortcutsRes.json();

    document.getElementById("home-total-cards").textContent = summary.total_quantity.toLocaleString();
    document.getElementById("home-unique-cards").textContent = `${summary.unique_cards.toLocaleString()} unique`;
    document.getElementById("home-deck-count").textContent = summary.deck_count.toLocaleString();
    document.getElementById("home-collection-value").textContent = `$${summary.collection_value_usd.toFixed(2)}`;

    renderDeckShortcuts(shortcuts.decks);
  } catch (err) {
    console.error("Failed to load homepage:", err);
  }
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

showHomeView(); // Homepage is the default landing view

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

async function runCheckoutAction(endpoint, btnEl) {
  const decklist_text = document.getElementById("checkout-input").value;
  const deck_name = document.getElementById("deck-name-input").value.trim();
  const fuzzy_threshold = scaleToApiThreshold(clampScale(checkoutThresholdVal.value));

  if (!deck_name) {
    alert("Please enter or select a deck name.");
    return;
  }

  const originalText = btnEl.textContent;
  btnEl.disabled = true;
  btnEl.textContent = "Working...";

  try {
    const res = await fetch(`${API_BASE}/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decklist_text, deck_name, fuzzy_threshold }),
    });
    if (!res.ok) throw new Error(`Server error: ${res.status}`);
    const data = await res.json();
    renderCheckoutResults(data.lines, data.warnings);
    loadDeckList(); // refresh dropdown in case a new deck name was created
  } catch (err) {
    alert(`${endpoint} failed: ${err.message}`);
  } finally {
    btnEl.disabled = false;
    btnEl.textContent = originalText;
  }
}

document.getElementById("checkout-btn").addEventListener("click", (e) => {
  runCheckoutAction("checkout", e.target);
});
document.getElementById("checkin-btn").addEventListener("click", (e) => {
  runCheckoutAction("checkin", e.target);
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

async function loadInventory() {
  const search = document.getElementById("manage-search").value.trim();
  const url = search
    ? `${API_BASE}/inventory?search=${encodeURIComponent(search)}`
    : `${API_BASE}/inventory`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    renderInventoryTable(data.cards);
  } catch (err) {
    console.error("Failed to load inventory:", err);
  }
}

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
  manageSearchDebounce = setTimeout(loadInventory, 300);
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

// ---------- Tab 5: View Decks ----------
const DECK_ACTION_THRESHOLD = 90; // fixed high-confidence threshold for quick single-card actions

async function loadDecksTab() {
  try {
    const [decksRes, invRes] = await Promise.all([
      fetch(`${API_BASE}/decks`),
      fetch(`${API_BASE}/inventory`),
    ]);
    const decksData = await decksRes.json();
    const invData = await invRes.json();

    const select = document.getElementById("deck-select");
    const currentSelection = select.value;
    select.innerHTML = `<option value="">-- choose a deck --</option>`;
    decksData.decks.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    });
    if (decksData.decks.includes(currentSelection)) {
      select.value = currentSelection;
    }

    const datalist = document.getElementById("inventory-card-list");
    datalist.innerHTML = "";
    invData.cards.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.card_name;
      datalist.appendChild(opt);
    });

    if (select.value) {
      loadDeckContents(select.value);
      loadDeckFavoriteState(select.value);
    } else {
      document.getElementById("deck-view").classList.add("hidden");
      setFavoriteBtnState(false, true);
    }
  } catch (err) {
    console.error("Failed to load decks tab:", err);
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
  if (!deckName) return;
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

document.getElementById("deck-select").addEventListener("change", (e) => {
  const deckName = e.target.value;
  const view = document.getElementById("deck-view");
  if (!deckName) {
    view.classList.add("hidden");
    setFavoriteBtnState(false, true);
    return;
  }
  view.classList.remove("hidden");
  loadDeckContents(deckName);
  loadDeckFavoriteState(deckName);
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

function renderDeckTable(deckName, cards) {
  const tbody = document.getElementById("deck-table-body");
  const emptyMsg = document.getElementById("deck-empty");
  tbody.innerHTML = "";

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
  const deckName = document.getElementById("deck-select").value;
  const nameInput = document.getElementById("deck-add-card-name");
  const qtyInput = document.getElementById("deck-add-card-qty");
  const msgEl = document.getElementById("deck-add-msg");

  if (!deckName) {
    msgEl.innerHTML = `<span class="text-rose-400">Select a deck first.</span>`;
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
    loadDeckContents(deckName);
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

loadDeckList(); // populate the deck datalist on initial page load (Search tab is default-active)
