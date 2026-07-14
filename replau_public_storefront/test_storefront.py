#!/usr/bin/env python3
from public_storefront import menu_items


def main() -> None:
    items = menu_items()
    assert items, "menu is empty"
    assert all(item["price"] > 0 for item in items)
    names = {item["name"] for item in items}
    assert "OREJONES" not in names
    assert "PIMIENTA MOLIDA" not in names
    assert "HAMBURGUESA SIMPLE" in names
    print(f"STOREFRONT_MENU_OK: {len(items)} sellable products")


if __name__ == "__main__":
    main()
