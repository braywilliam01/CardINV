"""Location (physical storage box/binder) as a first-class part of
inventory identity -- mirrors tests/test_finish.py's shape for the
same kind of axis. All network-free."""


class TestLocationAsIndependentLine:
    def test_two_locations_of_same_printing_are_independent_rows(self, registered_client):
        registered_client.post(
            "/api/inventory",
            json={
                "card_name": "Lightning Bolt", "total_quantity": 2,
                "set_code": "CLB", "collector_number": "141", "location": "Box A",
            },
        )
        registered_client.post(
            "/api/inventory",
            json={
                "card_name": "Lightning Bolt", "total_quantity": 3,
                "set_code": "CLB", "collector_number": "141", "location": "Box B",
            },
        )

        printings = registered_client.get(
            "/api/inventory/printings", params={"card_name": "Lightning Bolt"}
        ).json()["printings"]
        assert len(printings) == 2
        box_a = next(p for p in printings if p["location"] == "Box A")
        box_b = next(p for p in printings if p["location"] == "Box B")
        assert box_a["total_quantity"] == 2
        assert box_b["total_quantity"] == 3
        assert box_a["is_no_location"] is False

        row = registered_client.get("/api/inventory", params={"search": "Lightning Bolt"}).json()["cards"][0]
        assert row["printing_count"] == 2
        assert row["total_quantity"] == 5

    def test_same_printing_same_location_is_a_true_duplicate(self, registered_client):
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Sol Ring", "total_quantity": 1, "location": "Box A"},
        )
        r = registered_client.post(
            "/api/inventory",
            json={"card_name": "Sol Ring", "total_quantity": 1, "location": "Box A"},
        )
        assert r.status_code == 409

    def test_add_one_copy_targets_specific_location(self, registered_client):
        """Card Search's quick-add never passes a location -- both
        calls should land in the same (unassigned) location bucket."""
        registered_client.post(
            "/api/inventory/quick-add",
            json={"card_name": "Pikachu", "set_code": "BS", "collector_number": "58"},
        )
        registered_client.post(
            "/api/inventory/quick-add",
            json={"card_name": "Pikachu", "set_code": "BS", "collector_number": "58"},
        )
        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Pikachu"}).json()["printings"]
        assert len(printings) == 1
        assert printings[0]["total_quantity"] == 2
        assert printings[0]["is_no_location"] is True

    def test_adjust_quantity_targets_exact_location_only(self, registered_client):
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "location": "Box A"},
        )
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 2, "set_code": "DAA", "collector_number": "10", "location": "Box B"},
        )

        r = registered_client.patch(
            "/api/inventory",
            json={
                "card_name": "Charizard", "total_quantity": 5,
                "set_code": "DAA", "collector_number": "10", "location": "Box A",
            },
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Charizard"}).json()["printings"]
        box_a = next(p for p in printings if p["location"] == "Box A")
        box_b = next(p for p in printings if p["location"] == "Box B")
        assert box_a["total_quantity"] == 5
        assert box_b["total_quantity"] == 2  # untouched by adjusting the other location

    def test_delete_printing_by_location_leaves_sibling_location_untouched(self, registered_client):
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "location": "Box A"},
        )
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 2, "set_code": "DAA", "collector_number": "10", "location": "Box B"},
        )

        r = registered_client.delete(
            "/api/inventory/printing",
            params={"card_name": "Charizard", "set_code": "DAA", "collector_number": "10", "location": "Box A"},
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Charizard"}).json()["printings"]
        assert len(printings) == 1
        assert printings[0]["location"] == "Box B"
        assert printings[0]["total_quantity"] == 2


class TestAssignLocation:
    def test_no_location_to_real_location(self, registered_client):
        registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 5})

        r = registered_client.post(
            "/api/inventory/assign-location",
            json={"card_name": "Sol Ring", "quantity": 3, "location": "Box A"},
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
        no_loc = next(p for p in printings if p["is_no_location"])
        box_a = next(p for p in printings if p["location"] == "Box A")
        assert no_loc["total_quantity"] == 2
        assert box_a["total_quantity"] == 3

        row = registered_client.get("/api/inventory", params={"search": "Sol Ring"}).json()["cards"][0]
        assert row["total_quantity"] == 5  # relocating never changes the card's total

    def test_relocate_between_two_real_locations(self, registered_client):
        registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 5, "location": "Box A"})

        r = registered_client.post(
            "/api/inventory/assign-location",
            json={"card_name": "Sol Ring", "quantity": 2, "location": "Box B", "from_location": "Box A"},
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
        box_a = next(p for p in printings if p["location"] == "Box A")
        box_b = next(p for p in printings if p["location"] == "Box B")
        assert box_a["total_quantity"] == 3
        assert box_b["total_quantity"] == 2

    def test_source_and_target_the_same_is_rejected(self, registered_client):
        registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 5, "location": "Box A"})
        r = registered_client.post(
            "/api/inventory/assign-location",
            json={"card_name": "Sol Ring", "quantity": 1, "location": "Box A", "from_location": "Box A"},
        )
        assert r.status_code == 400

    def test_over_requesting_more_than_available_is_rejected(self, registered_client):
        registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 2, "location": "Box A"})
        r = registered_client.post(
            "/api/inventory/assign-location",
            json={"card_name": "Sol Ring", "quantity": 5, "location": "Box B", "from_location": "Box A"},
        )
        assert r.status_code == 400

    def test_fully_relocating_a_bucket_deletes_the_drained_row(self, registered_client):
        """Regression test: relocating *every* copy out of a bucket
        must delete the emptied 0-quantity source row, not leave it
        sitting around forever tripping has_no_location/no_location_only
        (found via live testing before this fix landed)."""
        registered_client.post("/api/inventory", json={"card_name": "Mind Stone", "total_quantity": 1})

        r = registered_client.post(
            "/api/inventory/assign-location",
            json={"card_name": "Mind Stone", "quantity": 1, "location": "Box D", "from_location": ""},
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Mind Stone"}).json()["printings"]
        assert len(printings) == 1  # the drained-to-0 no-location row is gone
        assert printings[0]["location"] == "Box D"
        assert printings[0]["is_no_location"] is False

        no_location_only = registered_client.get("/api/inventory", params={"no_location_only": "true"}).json()["cards"]
        assert all(c["card_name"] != "Mind Stone" for c in no_location_only)


class TestInventoryLocationFilters:
    def test_no_location_only_returns_only_unassigned(self, registered_client):
        registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1})
        registered_client.post("/api/inventory", json={"card_name": "Mind Stone", "total_quantity": 1, "location": "Box A"})

        r = registered_client.get("/api/inventory", params={"no_location_only": "true"})
        names = {c["card_name"] for c in r.json()["cards"]}
        assert names == {"Sol Ring"}

    def test_location_substring_filter(self, registered_client):
        registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1, "location": "Box A"})
        registered_client.post("/api/inventory", json={"card_name": "Mind Stone", "total_quantity": 1, "location": "Binder 1"})

        r = registered_client.get("/api/inventory", params={"location": "Box"})
        names = {c["card_name"] for c in r.json()["cards"]}
        assert names == {"Sol Ring"}


class TestBulkLocationRequirement:
    def test_bulk_add_rejects_blank_location(self, registered_client):
        r = registered_client.post(
            "/api/inventory/bulk-add", json={"decklist_text": "1 Sol Ring", "location": ""}
        )
        assert r.status_code == 400

    def test_bulk_remove_rejects_blank_location(self, registered_client):
        r = registered_client.post(
            "/api/inventory/bulk-remove", json={"decklist_text": "1 Sol Ring", "location": ""}
        )
        assert r.status_code == 400

    def test_bulk_add_creates_brand_new_card_at_given_location(self, registered_client):
        r = registered_client.post(
            "/api/inventory/bulk-add", json={"decklist_text": "2 Brainstorm", "location": "Box A"}
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Brainstorm"}).json()["printings"]
        assert len(printings) == 1
        assert printings[0]["location"] == "Box A"
        assert printings[0]["total_quantity"] == 2

    def test_bulk_remove_scoped_to_one_location_and_respects_cross_location_checked_out_floor(self, registered_client):
        registered_client.post("/api/inventory/bulk-add", json={"decklist_text": "5 Sol Ring", "location": "Box A"})
        registered_client.post("/api/inventory/bulk-add", json={"decklist_text": "5 Sol Ring", "location": "Box B"})
        registered_client.post("/api/checkout", json={"decklist_text": "3 Sol Ring", "deck_name": "RampDeck"})

        r = registered_client.post(
            "/api/inventory/bulk-remove", json={"decklist_text": "5 Sol Ring", "location": "Box A"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["lines"][0]["applied_qty"] == 5

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
        box_a = next((p for p in printings if p["location"] == "Box A"), None)
        box_b = next(p for p in printings if p["location"] == "Box B")
        assert box_a is None or box_a["total_quantity"] == 0
        assert box_b["total_quantity"] == 5  # untouched -- removal was scoped to Box A


def test_inventory_locations_endpoint_returns_distinct_set(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1, "location": "Box A"})
    registered_client.post("/api/inventory", json={"card_name": "Mind Stone", "total_quantity": 1, "location": "Box A"})
    registered_client.post("/api/inventory", json={"card_name": "Brainstorm", "total_quantity": 1, "location": "Box B"})
    registered_client.post("/api/inventory", json={"card_name": "Unassigned Card", "total_quantity": 1})

    r = registered_client.get("/api/inventory/locations")
    assert r.status_code == 200, r.text
    assert r.json()["locations"] == ["Box A", "Box B"]
