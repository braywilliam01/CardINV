"""Finish (Holofoil/Reverse Holofoil/Foil/etc.) as a first-class part
of printing identity -- vocabulary/normalization, the /api/finishes
endpoint, and inventory CRUD treating two finishes of the same
printing as independent lines. All network-free."""
from app.finishes import normalize_finish, FINISHES_BY_GAME, MTG_FINISHES, POKEMON_FINISHES


class TestNormalizeFinish:
    def test_blank_is_unspecified(self):
        assert normalize_finish("") == ""
        assert normalize_finish(None) == ""
        assert normalize_finish("   ") == ""

    def test_known_value_canonicalized_case_insensitively(self):
        assert normalize_finish("holofoil") == "Holofoil"
        assert normalize_finish("HOLOFOIL") == "Holofoil"
        assert normalize_finish("  Reverse holofoil  ") == "Reverse Holofoil"
        assert normalize_finish("foil") == "Foil"
        assert normalize_finish("nonfoil") == "Nonfoil"

    def test_unknown_value_passed_through_unchanged(self):
        """MTG has real finishes (Etched, Surge Foil, ...) this app's
        curated vocabulary doesn't try to enumerate -- same latitude
        _norm_printing gives set_code."""
        assert normalize_finish("Etched") == "Etched"
        assert normalize_finish("Some Weird Finish") == "Some Weird Finish"


class TestFinishesByGame:
    def test_mtg_and_pokemon_vocabularies_present(self):
        assert FINISHES_BY_GAME["mtg"] == MTG_FINISHES
        assert FINISHES_BY_GAME["pokemon"] == POKEMON_FINISHES

    def test_vocabularies_dont_collide(self):
        mtg_lower = {f.lower() for f in MTG_FINISHES}
        pokemon_lower = {f.lower() for f in POKEMON_FINISHES}
        assert not (mtg_lower & pokemon_lower)


class TestFinishesEndpoint:
    def test_returns_mtg_finishes_by_default(self, registered_client):
        r = registered_client.get("/api/finishes")
        assert r.status_code == 200, r.text
        assert r.json()["finishes"] == MTG_FINISHES

    def test_returns_pokemon_finishes_after_game_switch(self, registered_client):
        registered_client.put("/api/session/game", json={"game": "pokemon"})
        r = registered_client.get("/api/finishes")
        assert r.status_code == 200, r.text
        assert r.json()["finishes"] == POKEMON_FINISHES


class TestFinishAsIndependentLine:
    def test_two_finishes_of_same_printing_are_independent_rows(self, registered_client):
        registered_client.post(
            "/api/inventory",
            json={
                "card_name": "Charizard", "total_quantity": 1,
                "set_code": "DAA", "collector_number": "10", "finish": "Holofoil",
            },
        )
        registered_client.post(
            "/api/inventory",
            json={
                "card_name": "Charizard", "total_quantity": 2,
                "set_code": "DAA", "collector_number": "10", "finish": "Reverse Holofoil",
            },
        )

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Charizard"}).json()["printings"]
        assert len(printings) == 2
        holo = next(p for p in printings if p["finish"] == "Holofoil")
        reverse = next(p for p in printings if p["finish"] == "Reverse Holofoil")
        assert holo["total_quantity"] == 1
        assert reverse["total_quantity"] == 2
        assert holo["is_finish_unspecified"] is False

        row = registered_client.get("/api/inventory", params={"search": "Charizard"}).json()["cards"][0]
        assert row["printing_count"] == 2
        assert row["total_quantity"] == 3

    def test_same_printing_same_finish_is_a_true_duplicate(self, registered_client):
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "finish": "Holofoil"},
        )
        r = registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "finish": "Holofoil"},
        )
        assert r.status_code == 409

    def test_add_one_copy_targets_specific_finish(self, registered_client):
        """Card Search's 'Add to Inventory', once it knows a finish
        (see quick-add), should increment only that finish's row."""
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
        assert printings[0]["is_finish_unspecified"] is True

    def test_adjust_quantity_targets_exact_finish_only(self, registered_client):
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "finish": "Holofoil"},
        )
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 2, "set_code": "DAA", "collector_number": "10", "finish": "Reverse Holofoil"},
        )

        r = registered_client.patch(
            "/api/inventory",
            json={
                "card_name": "Charizard", "total_quantity": 5,
                "set_code": "DAA", "collector_number": "10", "finish": "Holofoil",
            },
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Charizard"}).json()["printings"]
        holo = next(p for p in printings if p["finish"] == "Holofoil")
        reverse = next(p for p in printings if p["finish"] == "Reverse Holofoil")
        assert holo["total_quantity"] == 5
        assert reverse["total_quantity"] == 2  # untouched by adjusting the other finish

    def test_delete_printing_by_finish_leaves_sibling_finish_untouched(self, registered_client):
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "finish": "Holofoil"},
        )
        registered_client.post(
            "/api/inventory",
            json={"card_name": "Charizard", "total_quantity": 2, "set_code": "DAA", "collector_number": "10", "finish": "Reverse Holofoil"},
        )

        r = registered_client.delete(
            "/api/inventory/printing",
            params={"card_name": "Charizard", "set_code": "DAA", "collector_number": "10", "finish": "Holofoil"},
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Charizard"}).json()["printings"]
        assert len(printings) == 1
        assert printings[0]["finish"] == "Reverse Holofoil"
        assert printings[0]["total_quantity"] == 2


class TestAssignPrintingFinishAware:
    def test_resolving_a_whole_printing_leaves_finish_unspecified_by_default(self, registered_client):
        registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 5})

        r = registered_client.post(
            "/api/inventory/assign-printing",
            json={"card_name": "Sol Ring", "quantity": 2, "set_code": "CMR", "collector_number": "123"},
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
        cmr = next(p for p in printings if p["set_code"] == "CMR")
        assert cmr["total_quantity"] == 2
        assert cmr["is_finish_unspecified"] is True

    def test_resolving_just_a_finish_on_an_already_resolved_printing(self, registered_client):
        registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 5})
        registered_client.post(
            "/api/inventory/assign-printing",
            json={"card_name": "Sol Ring", "quantity": 3, "set_code": "CMR", "collector_number": "123"},
        )
        # Now Sol Ring has: 2 unresolved, 3 (CMR #123, unspecified finish).

        r = registered_client.post(
            "/api/inventory/assign-printing",
            json={
                "card_name": "Sol Ring", "quantity": 1,
                "set_code": "CMR", "collector_number": "123", "finish": "Foil",
                "from_finish": "",
            },
        )
        assert r.status_code == 200, r.text

        printings = registered_client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
        cmr_unspecified = next(p for p in printings if p["set_code"] == "CMR" and p["finish"] == "")
        cmr_foil = next(p for p in printings if p["set_code"] == "CMR" and p["finish"] == "Foil")
        assert cmr_unspecified["total_quantity"] == 2
        assert cmr_foil["total_quantity"] == 1

        # Never touched the fully-unresolved bucket or the card's total.
        unresolved = next(p for p in printings if p["is_unresolved"])
        assert unresolved["total_quantity"] == 2
        row = registered_client.get("/api/inventory", params={"search": "Sol Ring"}).json()["cards"][0]
        assert row["total_quantity"] == 5

    def test_source_and_target_the_same_is_rejected(self, registered_client):
        registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 5})
        registered_client.post(
            "/api/inventory/assign-printing",
            json={"card_name": "Sol Ring", "quantity": 3, "set_code": "CMR", "collector_number": "123"},
        )
        # from_finish="" + target finish="" with the same set/number as
        # the source is a same-row no-op -- rejected outright rather
        # than silently doing nothing.
        r = registered_client.post(
            "/api/inventory/assign-printing",
            json={
                "card_name": "Sol Ring", "quantity": 1,
                "set_code": "CMR", "collector_number": "123", "finish": "",
                "from_finish": "",
            },
        )
        assert r.status_code == 400
