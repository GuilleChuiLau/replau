BEGIN;

-- Edit this file with real names/numbers, then apply after add_replau_driver_dispatch.sql.
-- WhatsApp numbers should be digits with country code, e.g. 51999999999.

INSERT INTO api.repartidores(codigo, nombre, whatsapp_number, activo, orden_turno)
VALUES
    ('R001', 'Repartidor 1', '51900000001', false, 1),
    ('R002', 'Repartidor 2', '51900000002', false, 2),
    ('R003', 'Repartidor 3', '51998115921', true, 3)
ON CONFLICT (codigo) DO UPDATE SET
    nombre = EXCLUDED.nombre,
    whatsapp_number = EXCLUDED.whatsapp_number,
    activo = EXCLUDED.activo,
    orden_turno = EXCLUDED.orden_turno,
    updated_at = now();

-- Fixed driver fee, soles.
INSERT INTO api.delivery_config(key, value)
VALUES ('driver_fee_pen', '7.00')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

COMMIT;
