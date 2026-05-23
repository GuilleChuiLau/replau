-- Add Alitas Fritas menu items and update extra sauce price.
-- Safe to run multiple times: api.bulk_upsert_productos upserts by cdg_prod.

SELECT api.bulk_upsert_productos($json$
[
  {
    "cdg_prod": "WINGS_6",
    "nombre": "ALITAS FRITAS PICANTES X 6",
    "tipo_producto": "TERMINADO",
    "unidad_medida": "UND",
    "pack_default": "UNIDAD",
    "stock_minimo": 0.0,
    "controla_lote": false,
    "controla_vencimiento": false,
    "active": true,
    "pack_name": "UNIDAD",
    "pack_factor": 1.0,
    "pack_is_default": true,
    "precio_unidad": "UNIDAD",
    "precio": 14.0,
    "moneda": "PEN",
    "valid_from": "2026-05-03"
  },
  {
    "cdg_prod": "WINGS_12",
    "nombre": "ALITAS FRITAS PICANTES X 12",
    "tipo_producto": "TERMINADO",
    "unidad_medida": "UND",
    "pack_default": "UNIDAD",
    "stock_minimo": 0.0,
    "controla_lote": false,
    "controla_vencimiento": false,
    "active": true,
    "pack_name": "UNIDAD",
    "pack_factor": 1.0,
    "pack_is_default": true,
    "precio_unidad": "UNIDAD",
    "precio": 24.0,
    "moneda": "PEN",
    "valid_from": "2026-05-03"
  },
  {
    "cdg_prod": "WINGS_24",
    "nombre": "ALITAS FRITAS PICANTES X 24",
    "tipo_producto": "TERMINADO",
    "unidad_medida": "UND",
    "pack_default": "UNIDAD",
    "stock_minimo": 0.0,
    "controla_lote": false,
    "controla_vencimiento": false,
    "active": true,
    "pack_name": "UNIDAD",
    "pack_factor": 1.0,
    "pack_is_default": true,
    "precio_unidad": "UNIDAD",
    "precio": 42.0,
    "moneda": "PEN",
    "valid_from": "2026-05-03"
  },
  {
    "cdg_prod": "SAUCE_EXTRA",
    "nombre": "SALSA EXTRA",
    "tipo_producto": "TERMINADO",
    "unidad_medida": "UND",
    "pack_default": "UNIDAD",
    "stock_minimo": 0.0,
    "controla_lote": false,
    "controla_vencimiento": false,
    "active": true,
    "pack_name": "UNIDAD",
    "pack_factor": 1.0,
    "pack_is_default": true,
    "precio_unidad": "UNIDAD",
    "precio": 3.0,
    "moneda": "PEN",
    "valid_from": "2026-05-03"
  }
]
$json$::jsonb);
