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


def test_csv_with_no_foil_column_leaves_finish_unspecified(registered_client):
    """Backward compatibility for the finish dimension specifically: a
    CSV predating (or simply not using) ManaBox's Foil column still
    lands everything at finish="" -- never guessed as Nonfoil just
    because the column happens to be absent."""
    csv_text = "Name,Set code,Collector number,Quantity\nLightning Bolt,CLB,141,4\n"
    r = _upload_csv(registered_client, csv_text)
    assert r.status_code == 200, r.text

    lb = registered_client.get("/api/inventory/printings", params={"card_name": "Lightning Bolt"}).json()["printings"]
    assert len(lb) == 1
    assert lb[0]["finish"] == ""
    assert lb[0]["is_finish_unspecified"] is True


def test_csv_foil_and_normal_rows_split_into_separate_printings(registered_client):
    """A real ManaBox export: the same (name, set, number) appears
    twice, once per finish -- these must land as two independent
    Inventory rows now, not merge into one combined total."""
    csv_text = (
        "Name,Set code,Collector number,Quantity,Foil\n"
        "Lightning Bolt,CLB,141,3,normal\n"
        "Lightning Bolt,CLB,141,2,foil\n"
    )
    r = _upload_csv(registered_client, csv_text)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["printings_added"] == 2
    assert data["total_quantity_loaded"] == 5

    lb = registered_client.get("/api/inventory/printings", params={"card_name": "Lightning Bolt"}).json()["printings"]
    assert len(lb) == 2
    nonfoil = next(p for p in lb if p["finish"] == "Nonfoil")
    foil = next(p for p in lb if p["finish"] == "Foil")
    assert nonfoil["total_quantity"] == 3
    assert foil["total_quantity"] == 2

    row = registered_client.get("/api/inventory", params={"search": "Lightning Bolt"}).json()["cards"][0]
    assert row["total_quantity"] == 5
    assert row["printing_count"] == 2


def test_csv_unrecognized_foil_value_lands_unspecified_not_guessed(registered_client):
    """An unrecognized Foil column value (a ManaBox export version this
    app doesn't know, or hand-edited data) shouldn't be guessed as any
    particular finish -- same "don't guess" rule as an unrecognized set
    code."""
    csv_text = "Name,Set code,Collector number,Quantity,Foil\nLightning Bolt,CLB,141,1,surge foil\n"
    r = _upload_csv(registered_client, csv_text)
    assert r.status_code == 200, r.text

    lb = registered_client.get("/api/inventory/printings", params={"card_name": "Lightning Bolt"}).json()["printings"]
    assert len(lb) == 1
    assert lb[0]["finish"] == ""  # unrecognized -- unspecified, not guessed
    assert lb[0]["total_quantity"] == 1


def test_csv_reimport_reconciles_per_finish(registered_client):
    """Re-importing with one finish's quantity changed and the other
    finish removed entirely reconciles each finish row independently,
    same as it already does per printing."""
    csv1 = (
        "Name,Set code,Collector number,Quantity,Foil\n"
        "Lightning Bolt,CLB,141,3,normal\n"
        "Lightning Bolt,CLB,141,2,foil\n"
    )
    _upload_csv(registered_client, csv1)

    csv2 = "Name,Set code,Collector number,Quantity,Foil\nLightning Bolt,CLB,141,5,normal\n"
    r = _upload_csv(registered_client, csv2)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["printings_updated"] == 1  # Nonfoil: 3 -> 5
    assert data["printings_removed"] == 1  # Foil row gone entirely

    lb = registered_client.get("/api/inventory/printings", params={"card_name": "Lightning Bolt"}).json()["printings"]
    assert len(lb) == 1
    assert lb[0]["finish"] == "Nonfoil"
    assert lb[0]["total_quantity"] == 5
