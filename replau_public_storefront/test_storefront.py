#!/usr/bin/env python3
from public_storefront import menu_items, storefront


def main() -> None:
    items = menu_items()
    assert items, "menu is empty"
    assert all(item["price"] > 0 for item in items)
    assert all(item["category"] and item["description"] and item["icon"] for item in items)
    assert all(item["image_url"].startswith("/media/products/") for item in items if item["image_url"])
    names = {item["name"] for item in items}
    assert "OREJONES" not in names
    assert "PIMIENTA MOLIDA" not in names
    assert "HAMBURGUESA SIMPLE" in names
    assert {"Combos", "Hamburguesas", "Alitas", "Acompañamientos", "Bebidas", "Extras"}.issubset({item["category"] for item in items})
    page = storefront().body.decode("utf-8")
    assert ".join('\\n')" in page
    assert "Buscar hamburguesas" in page
    assert "object-fit:contain" in page
    assert "aspect-ratio:4/3" in page
    assert "@media(max-width:700px)" in page
    print(f"STOREFRONT_MENU_OK: {len(items)} sellable products")


if __name__ == "__main__":
    main()
