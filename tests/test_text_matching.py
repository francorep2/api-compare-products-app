from app.services.text_matching import find_best_match_for_item as best


def r(title, price="ARS 100.00"):
    return {"title": title, "price": price}


def test_singular_plural():
    assert best("huevos", [r("Huevo Blanco Grande 6u")]) is not None


def test_short_query_needs_every_word():
    # "azucar ledesma" no debe matchear otra marca de azúcar
    assert best("azucar ledesma 1kg", [r("Azúcar La Campechana 1kg")]) is None
    assert best("azucar ledesma 1kg", [r("Azúcar Ledesma Refinado 1 Kg.")]) is not None


def test_size_breaks_ties_between_variants():
    got = best(
        "fideos matarazzo spaghetti 500g",
        [r("Fideos Matarazzo Spaghetti 1kg"), r("Fideos Matarazzo Spaghetti 500 Gr.")],
    )
    assert "500" in got["title"]


def test_cheapest_wins_on_equal_match():
    got = best("leche entera", [r("Leche Entera Marca1", "ARS 300.00"), r("Leche Entera Marca2", "ARS 200.00")])
    assert got["title"] == "Leche Entera Marca2"


def test_ignores_items_without_price_or_empty():
    assert best("leche", []) is None
    assert best("leche", [{"title": "Leche", "price": None}]) is None
