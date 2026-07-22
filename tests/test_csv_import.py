"""CSV bulk-upload reconciliation (ManaBox-style exports) — diffing a
re-import against existing inventory, preserving deck assignments,
and shortfall warnings when a checked-out printing disappears. No
external API involved, runs fully offline."""


def _upload_csv(client, csv_text, ignore_basic_lands=True):
    files = {"file": ("test.csv", csv_text, "text/csv")}
    return client.post(
        "/api/bulk-upload", files=files, data={"ignore_basic_lands": str(ignore_basic_lands).lower()}
    )


def test_csv_import_with_set_and_number_columns(registered_client):
    csv1 = (
        "Name,Set code,Collector number,Quantity\n"
        "Lightning Bolt,CLB,141,4\n"
        "Sol Ring,CMR,123,2\n"
        "Brainstorm,,,1\n"
    )
    r = _upload_csv(registered_client, csv1)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["printings_added"] == 3
    assert data["unique_cards_loaded"] == 3

    lb = registered_client.get("/api/inventory/printings", params={"card_name": "Lightning Bolt"}).json()["printings"]
    assert lb == [
        {
            "set_code": "CLB",
            "collector_number": "141",
            "finish": "",
            "is_finish_unspecified": True,
            "total_quantity": 4,
            "is_unresolved": False,
            "price_usd": None,
            "price_usd_foil": None,
            "is_estimated": False,
            "line_value": None,
        }
    ]

    bs = registered_client.get("/api/inventory/printings", params={"card_name": "Brainstorm"}).json()["printings"]
    assert bs[0]["is_unresolved"] and bs[0]["total_quantity"] == 1


def test_csv_reimport_reconciles_and_warns_on_shortfall(registered_client):
    csv1 = (
        "Name,Set code,Collector number,Quantity\n"
        "Lightning Bolt,CLB,141,4\n"
        "Sol Ring,CMR,123,2\n"
        "Brainstorm,,,1\n"
    )
    _upload_csv(registered_client, csv1)

    # A manual add NOT represented in the CSV export at all.
    r = registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 5})
    assert r.status_code == 200, r.text

    # Check out 2x Lightning Bolt pinned to CLB#141, to trigger a
    # shortfall warning once that printing disappears from the re-import.
    r = registered_client.post(
        "/api/checkout", json={"decklist_text": "2 Lightning Bolt (CLB) 141", "deck_name": "Phase4 Deck"}
    )
    assert r.status_code == 200 and r.json()["lines"][0]["status"] == "ok", r.text

    # Updated export: Lightning Bolt CLB#141 and Brainstorm both gone,
    # the manually-added unresolved Sol Ring (unknown to the CSV) also
    # gone, Sol Ring CMR#123 unchanged, Sol Ring MSC#212 newly added.
    csv2 = "Name,Set code,Collector number,Quantity\nSol Ring,CMR,123,2\nSol Ring,MSC,212,1\n"
    r = _upload_csv(registered_client, csv2)
    assert r.status_code == 200, r.text
    data2 = r.json()
    assert data2["printings_added"] == 1  # MSC#212
    assert data2["printings_removed"] >= 3  # CLB#141, Brainstorm unresolved, Sol Ring unresolved

    assert any("Lightning Bolt" in w and "short" in w for w in data2["warnings"]), data2["warnings"]

    # Deck assignment survives untouched -- csv import never touches
    # deck_assignments, even though the underlying printing is gone.
    deck_cards = registered_client.get("/api/decks/Phase4%20Deck/cards").json()["cards"]
    assert any(
        c["card_name"] == "Lightning Bolt" and c["set_code"] == "CLB" and c["quantity"] == 2 for c in deck_cards
    )

    sol = registered_client.get("/api/inventory", params={"search": "Sol Ring"}).json()["cards"][0]
    assert sol["printing_count"] == 2 and not sol["has_unresolved"]

    assert registered_client.get("/api/inventory", params={"search": "Brainstorm"}).json()["total_count"] == 0
    assert registered_client.get("/api/inventory", params={"search": "Lightning Bolt"}).json()["total_count"] == 0


def test_csv_plain_name_quantity_columns_land_in_unresolved_bucket(registered_client):
    """Backward compatibility: a plain Name,Quantity CSV with no
    set/number columns still works, landing everything unresolved."""
    csv3 = "Name,Quantity\nCounterspell,3\n"
    r = _upload_csv(registered_client, csv3)
    assert r.status_code == 200, r.text

    cs = registered_client.get("/api/inventory/printings", params={"card_name": "Counterspell"}).json()["printings"]
    assert cs[0]["is_unresolved"] and cs[0]["total_quantity"] == 3
