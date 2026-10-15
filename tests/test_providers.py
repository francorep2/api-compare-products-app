import asyncio

from app.providers import coto as coto_mod
from app.providers.coto import CotoStore
from app.providers.vtex import VtexStore


def _vtex_product(price=1000, qty=5, without_discount=None, teasers=None, ean="7790742348005"):
    return {
        "productName": "Leche  La Serenisima 1L",
        "linkText": "leche-la-serenisima-1l",
        "items": [{
            "ean": ean,
            "images": [{"imageUrl": "http://img/x.jpg"}],
            "sellers": [{"commertialOffer": {
                "Price": price,
                "AvailableQuantity": qty,
                "ListPrice": 673554.0,  # Jumbo devuelve basura acá: no debe generar promo
                "PriceWithoutDiscount": without_discount or price,
                "Teasers": teasers or [],
            }}],
        }],
    }


def test_vtex_item_basic():
    it = VtexStore("jumbo", "https://www.jumbo.com.ar")._to_item(_vtex_product())
    assert it["title"] == "Leche La Serenisima 1L"  # espacios normalizados
    assert it["price"] == "ARS 1000.00"
    assert it["url"] == "https://www.jumbo.com.ar/leche-la-serenisima-1l/p"
    assert it["barcode"] == "7790742348005"
    assert it["promotion"] is None  # ListPrice absurdo ignorado


def test_vtex_skips_out_of_stock():
    assert VtexStore("x", "https://x.com")._to_item(_vtex_product(qty=0)) is None


def test_vtex_promo_from_price_without_discount_and_teaser():
    s = VtexStore("x", "https://x.com")
    assert s._to_item(_vtex_product(price=700, without_discount=1000))["promotion"] == "30% OFF"
    teaser = [{"name": "Llevando 2 unidades 2da al 70%"}]
    assert s._to_item(_vtex_product(teasers=teaser))["promotion"] == "2da al 70%"


def test_vtex_query_uses_percent20_not_plus(monkeypatch):
    seen = {}

    async def fake_get_json(url, params=None, retries=1):
        seen["url"] = url
        return []

    monkeypatch.setattr("app.providers.vtex.get_json", fake_get_json)
    asyncio.run(VtexStore("x", "https://x.com").search("leche la serenisima", None, 3))
    assert "ft=leche%20la%20serenisima" in seen["url"] and "+" not in seen["url"]
    asyncio.run(VtexStore("x", "https://x.com").search(None, "7790742348005", 3))
    assert "fq=alternateIds_Ean%3A7790742348005" in seen["url"]


def _coto_row(**over):
    data = {
        "url": "_/R-00494849-00494849-200",
        # formatPrice es el precio por m², no el del producto
        "price": [{"store": "200", "listPrice": 8253.99, "formatPrice": 206.35}],
        "discounts": [{"discountText": "25%Dto", "discountPrice": "$6190.49"}],
        "store_availability": ["200"],
        "product_main_ean": 7790742348005,
        "product_large_image_url": "http://img/c.jpg",
    }
    data.update(over)
    return {"value": "Papel Higiénico HIGIENOL Max 4 U", "data": data}


def test_coto_uses_unit_price_not_format_price():
    it = CotoStore()._to_item(_coto_row())
    assert it["price"] == "ARS 6190.49"  # descuento vigente, no 206.35
    assert it["promotion"] == "25% OFF"
    assert it["url"] == (
        "https://www.coto.com.ar/productos/papel-higienico-higienol-max-4-u/_/R-00494849-00494849-200"
    )
    assert it["barcode"] == "7790742348005"


def test_coto_ignores_absurd_discount_and_unavailable():
    assert CotoStore()._to_item(_coto_row(discounts=[{"discountPrice": "$1.00"}]))["price"] == "ARS 8253.99"
    assert CotoStore()._to_item(_coto_row(store_availability=["999"])) is None
    assert coto_mod.SUCURSAL == "200"
