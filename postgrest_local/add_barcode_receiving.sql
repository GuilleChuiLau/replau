BEGIN;

ALTER TABLE api.product_barcodes ADD COLUMN IF NOT EXISTS barcode_type text NOT NULL DEFAULT 'CODE128';
ALTER TABLE api.product_barcodes ADD COLUMN IF NOT EXISTS packaging_level text NOT NULL DEFAULT 'UNIT';
ALTER TABLE api.product_barcodes ADD COLUMN IF NOT EXISTS unit_factor numeric(14,3) NOT NULL DEFAULT 1;
ALTER TABLE api.product_barcodes ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'INTERNAL';
ALTER TABLE api.product_barcodes ADD COLUMN IF NOT EXISTS is_primary boolean NOT NULL DEFAULT false;
ALTER TABLE api.product_barcodes ADD COLUMN IF NOT EXISTS notes text;
ALTER TABLE api.product_barcodes ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

DO $$ BEGIN
 ALTER TABLE api.product_barcodes ADD CONSTRAINT product_barcodes_type_check CHECK(barcode_type IN('CODE128','QR','EAN13','GTIN14'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
 ALTER TABLE api.product_barcodes ADD CONSTRAINT product_barcodes_level_check CHECK(packaging_level IN('UNIT','PACK','CASE','PALLET'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
 ALTER TABLE api.product_barcodes ADD CONSTRAINT product_barcodes_factor_check CHECK(unit_factor>0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
 ALTER TABLE api.product_barcodes ADD CONSTRAINT product_barcodes_source_check CHECK(source IN('INTERNAL','SUPPLIER','GS1'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE UNIQUE INDEX IF NOT EXISTS product_barcodes_one_primary ON api.product_barcodes(producto_id) WHERE is_primary AND active;

CREATE OR REPLACE FUNCTION api.gtin_check_digit(p_body text) RETURNS integer
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE total integer:=0; pos integer; digit integer; weight integer:=3;
BEGIN
 IF p_body!~'^[0-9]+$' THEN RAISE EXCEPTION 'GTIN body must be numeric'; END IF;
 FOR pos IN REVERSE length(p_body)..1 LOOP
   digit:=substr(p_body,pos,1)::integer; total:=total+digit*weight; weight:=CASE weight WHEN 3 THEN 1 ELSE 3 END;
 END LOOP;
 RETURN (10-(total%10))%10;
END $$;

CREATE OR REPLACE FUNCTION api.validate_product_barcode() RETURNS trigger
LANGUAGE plpgsql SET search_path=api,public AS $$
BEGIN
 NEW.barcode:=trim(NEW.barcode); NEW.barcode_type:=upper(trim(NEW.barcode_type));
 NEW.packaging_level:=upper(trim(NEW.packaging_level)); NEW.source:=upper(trim(NEW.source)); NEW.updated_at:=now();
 IF NEW.barcode_type='EAN13' AND (NEW.barcode!~'^[0-9]{13}$' OR right(NEW.barcode,1)::integer<>api.gtin_check_digit(left(NEW.barcode,12))) THEN RAISE EXCEPTION 'Invalid EAN-13 / GTIN-13 check digit'; END IF;
 IF NEW.barcode_type='GTIN14' AND (NEW.barcode!~'^[0-9]{14}$' OR right(NEW.barcode,1)::integer<>api.gtin_check_digit(left(NEW.barcode,13))) THEN RAISE EXCEPTION 'Invalid GTIN-14 check digit'; END IF;
 IF NEW.barcode_type='QR' AND NEW.barcode NOT LIKE 'REPLAU:PRODUCT:%' THEN RAISE EXCEPTION 'Replau QR must start REPLAU:PRODUCT:'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS validate_product_barcode_before_write ON api.product_barcodes;
CREATE TRIGGER validate_product_barcode_before_write BEFORE INSERT OR UPDATE ON api.product_barcodes FOR EACH ROW EXECUTE FUNCTION api.validate_product_barcode();

CREATE OR REPLACE FUNCTION api.save_product_barcode(p_product_id integer,p_barcode text,p_barcode_type text,p_packaging_level text DEFAULT 'UNIT',p_unit_factor numeric DEFAULT 1,p_source text DEFAULT 'INTERNAL',p_is_primary boolean DEFAULT false,p_label text DEFAULT NULL,p_notes text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=api,public AS $$
DECLARE row_out api.product_barcodes%ROWTYPE; existing_product integer;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM api.productos WHERE id=p_product_id) THEN RAISE EXCEPTION 'Product not found'; END IF;
 SELECT producto_id INTO existing_product FROM api.product_barcodes WHERE barcode=trim(p_barcode);
 IF existing_product IS NOT NULL AND existing_product<>p_product_id THEN RAISE EXCEPTION 'Barcode already belongs to another product'; END IF;
 IF p_is_primary THEN UPDATE api.product_barcodes SET is_primary=false,updated_at=now() WHERE producto_id=p_product_id AND active AND is_primary; END IF;
 INSERT INTO api.product_barcodes(producto_id,barcode,barcode_type,packaging_level,unit_factor,source,is_primary,label,notes,active)
 VALUES(p_product_id,trim(p_barcode),upper(trim(p_barcode_type)),upper(trim(p_packaging_level)),p_unit_factor,upper(trim(p_source)),p_is_primary,NULLIF(trim(p_label),''),NULLIF(trim(p_notes),''),true)
 ON CONFLICT(barcode) DO UPDATE SET producto_id=EXCLUDED.producto_id,barcode_type=EXCLUDED.barcode_type,packaging_level=EXCLUDED.packaging_level,unit_factor=EXCLUDED.unit_factor,source=EXCLUDED.source,is_primary=EXCLUDED.is_primary,label=EXCLUDED.label,notes=EXCLUDED.notes,active=true,updated_at=now()
 RETURNING * INTO row_out;
 RETURN jsonb_build_object('ok',true,'id',row_out.id,'barcode',row_out.barcode,'barcode_type',row_out.barcode_type);
END $$;

UPDATE api.product_barcodes SET barcode_type='CODE128',packaging_level='UNIT',unit_factor=1,source='INTERNAL',is_primary=false WHERE barcode_type IS NULL OR barcode_type='';
WITH first_codes AS (SELECT min(id) id FROM api.product_barcodes WHERE active GROUP BY producto_id)
UPDATE api.product_barcodes b SET is_primary=true FROM first_codes f WHERE b.id=f.id AND NOT EXISTS(SELECT 1 FROM api.product_barcodes x WHERE x.producto_id=b.producto_id AND x.active AND x.is_primary);

CREATE TABLE IF NOT EXISTS api.inventory_receiving_sessions(
 id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
 reference text NOT NULL UNIQUE,
 warehouse_id integer NOT NULL REFERENCES api.almacenes(id) ON DELETE RESTRICT,
 supplier_name text,
 status text NOT NULL DEFAULT 'ACTIVE',
 operator_name text NOT NULL,
 notes text,
 started_at timestamptz NOT NULL DEFAULT now(), posted_at timestamptz, voided_at timestamptz,
 posted_by text, voided_by text, void_reason text,
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT inventory_receiving_status_check CHECK(status IN('ACTIVE','POSTED','VOIDED')),
 CONSTRAINT inventory_receiving_reference_check CHECK(trim(reference)<>''),
 CONSTRAINT inventory_receiving_void_reason_check CHECK(status<>'VOIDED' OR trim(COALESCE(void_reason,''))<>'')
);

CREATE TABLE IF NOT EXISTS api.inventory_receiving_scans(
 id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
 session_id bigint NOT NULL REFERENCES api.inventory_receiving_sessions(id) ON DELETE RESTRICT,
 barcode_id bigint REFERENCES api.product_barcodes(id) ON DELETE RESTRICT,
 barcode text NOT NULL, product_id integer REFERENCES api.productos(id) ON DELETE RESTRICT,
 package_quantity numeric(14,3) NOT NULL DEFAULT 1, unit_factor numeric(14,3) NOT NULL DEFAULT 1,
 base_quantity numeric(14,3) GENERATED ALWAYS AS(package_quantity*unit_factor) STORED,
 result text NOT NULL, operator_name text NOT NULL, lot_code text, expires_on date,
 movement_id integer REFERENCES api.movimientos_stock(id) ON DELETE RESTRICT,
 reversal_movement_id integer REFERENCES api.movimientos_stock(id) ON DELETE RESTRICT,
 detail jsonb NOT NULL DEFAULT '{}'::jsonb,created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT inventory_scan_result_check CHECK(result IN('ACCEPTED','UNKNOWN_BARCODE','INACTIVE_BARCODE')),
 CONSTRAINT inventory_scan_quantities_check CHECK(package_quantity>0 AND unit_factor>0)
);

CREATE OR REPLACE FUNCTION api.create_inventory_receiving(p_warehouse_id integer,p_operator text,p_supplier text DEFAULT NULL,p_notes text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=api,public AS $$
DECLARE sid bigint; ref text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM api.almacenes WHERE id=p_warehouse_id AND active) THEN RAISE EXCEPTION 'Active warehouse not found'; END IF;
 ref:='REC-'||to_char(clock_timestamp(),'YYYYMMDD-HH24MISS-MS');
 INSERT INTO api.inventory_receiving_sessions(reference,warehouse_id,supplier_name,operator_name,notes)
 VALUES(ref,p_warehouse_id,NULLIF(trim(p_supplier),''),left(COALESCE(NULLIF(trim(p_operator),''),'inventory'),80),NULLIF(trim(p_notes),'')) RETURNING id INTO sid;
 RETURN jsonb_build_object('ok',true,'session_id',sid,'reference',ref,'status','ACTIVE');
END $$;

CREATE OR REPLACE FUNCTION api.scan_inventory_receiving(p_session_id bigint,p_barcode text,p_packages numeric DEFAULT 1,p_operator text DEFAULT 'inventory',p_lot_code text DEFAULT NULL,p_expires_on date DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=api,public AS $$
DECLARE sess api.inventory_receiving_sessions%ROWTYPE; bc api.product_barcodes%ROWTYPE; rid bigint; clean text;
BEGIN
 clean:=trim(COALESCE(p_barcode,'')); IF clean='' OR length(clean)>160 OR p_packages<=0 OR p_packages>10000 THEN RAISE EXCEPTION 'Invalid receiving scan'; END IF;
 SELECT * INTO sess FROM api.inventory_receiving_sessions WHERE id=p_session_id FOR UPDATE;
 IF sess.id IS NULL THEN RAISE EXCEPTION 'Receiving session not found'; END IF; IF sess.status<>'ACTIVE' THEN RAISE EXCEPTION 'Receiving session is %',sess.status; END IF;
 SELECT * INTO bc FROM api.product_barcodes WHERE barcode=clean;
 IF bc.id IS NULL THEN
  INSERT INTO api.inventory_receiving_scans(session_id,barcode,package_quantity,unit_factor,result,operator_name,detail) VALUES(sess.id,clean,p_packages,1,'UNKNOWN_BARCODE',left(COALESCE(p_operator,'inventory'),80),jsonb_build_object('reason','No barcode mapping')) RETURNING id INTO rid;
  RETURN jsonb_build_object('ok',false,'reason','UNKNOWN_BARCODE','scan_id',rid,'barcode',clean);
 END IF;
 IF NOT bc.active THEN
  INSERT INTO api.inventory_receiving_scans(session_id,barcode_id,barcode,product_id,package_quantity,unit_factor,result,operator_name) VALUES(sess.id,bc.id,clean,bc.producto_id,p_packages,bc.unit_factor,'INACTIVE_BARCODE',left(COALESCE(p_operator,'inventory'),80)) RETURNING id INTO rid;
  RETURN jsonb_build_object('ok',false,'reason','INACTIVE_BARCODE','scan_id',rid,'barcode',clean);
 END IF;
 INSERT INTO api.inventory_receiving_scans(session_id,barcode_id,barcode,product_id,package_quantity,unit_factor,result,operator_name,lot_code,expires_on,detail)
 VALUES(sess.id,bc.id,clean,bc.producto_id,p_packages,bc.unit_factor,'ACCEPTED',left(COALESCE(p_operator,'inventory'),80),NULLIF(trim(p_lot_code),''),p_expires_on,jsonb_build_object('barcode_type',bc.barcode_type,'packaging_level',bc.packaging_level)) RETURNING id INTO rid;
 RETURN jsonb_build_object('ok',true,'reason','ACCEPTED','scan_id',rid,'product_id',bc.producto_id,'package_quantity',p_packages,'unit_factor',bc.unit_factor,'base_quantity',p_packages*bc.unit_factor);
END $$;

CREATE OR REPLACE FUNCTION api.post_inventory_receiving(p_session_id bigint,p_operator text DEFAULT 'inventory')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=api,public AS $$
DECLARE sess api.inventory_receiving_sessions%ROWTYPE; scan api.inventory_receiving_scans%ROWTYPE; mid integer; count_rows integer:=0; total_base numeric:=0;
BEGIN
 SELECT * INTO sess FROM api.inventory_receiving_sessions WHERE id=p_session_id FOR UPDATE;
 IF sess.id IS NULL THEN RAISE EXCEPTION 'Receiving session not found'; END IF; IF sess.status<>'ACTIVE' THEN RAISE EXCEPTION 'Receiving session is %',sess.status; END IF;
 IF NOT EXISTS(SELECT 1 FROM api.inventory_receiving_scans WHERE session_id=sess.id AND result='ACCEPTED') THEN RAISE EXCEPTION 'No accepted scans to post'; END IF;
 FOR scan IN SELECT * FROM api.inventory_receiving_scans WHERE session_id=sess.id AND result='ACCEPTED' ORDER BY id FOR UPDATE LOOP
  INSERT INTO api.movimientos_stock(fecha_movimiento,movimiento_tipo,almacen_destino_id,producto_id,cantidad,doc_tipo,doc_id,doc_linea_id,referencia,observacion)
  VALUES(now(),'RECEPCION',sess.warehouse_id,scan.product_id,scan.base_quantity,'SCANNER_RECEIVING',sess.id,scan.id,sess.reference,'Scanner receiving by '||left(COALESCE(p_operator,'inventory'),80)) RETURNING id INTO mid;
  UPDATE api.inventory_receiving_scans SET movement_id=mid WHERE id=scan.id; count_rows:=count_rows+1;total_base:=total_base+scan.base_quantity;
 END LOOP;
 UPDATE api.inventory_receiving_sessions SET status='POSTED',posted_at=now(),posted_by=left(COALESCE(p_operator,'inventory'),80),updated_at=now() WHERE id=sess.id;
 RETURN jsonb_build_object('ok',true,'session_id',sess.id,'status','POSTED','movement_count',count_rows,'base_quantity',total_base);
END $$;

CREATE OR REPLACE FUNCTION api.void_inventory_receiving(p_session_id bigint,p_operator text,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=api,public AS $$
DECLARE sess api.inventory_receiving_sessions%ROWTYPE; scan api.inventory_receiving_scans%ROWTYPE; mid integer; count_rows integer:=0; insufficient boolean:=false;
BEGIN
 IF length(trim(COALESCE(p_reason,'')))<5 THEN RAISE EXCEPTION 'Void reason must have at least 5 characters'; END IF;
 SELECT * INTO sess FROM api.inventory_receiving_sessions WHERE id=p_session_id FOR UPDATE;
 IF sess.id IS NULL THEN RAISE EXCEPTION 'Receiving session not found'; END IF; IF sess.status='VOIDED' THEN RAISE EXCEPTION 'Receiving session already voided'; END IF;
 IF sess.status='POSTED' THEN
  WITH needed AS (SELECT product_id,sum(base_quantity) qty FROM api.inventory_receiving_scans WHERE session_id=sess.id AND result='ACCEPTED' GROUP BY product_id),
  available AS (SELECT producto_id,sum(stock_actual) qty FROM api.v_stock_actual WHERE almacen_id=sess.warehouse_id GROUP BY producto_id)
  SELECT EXISTS(SELECT 1 FROM needed n LEFT JOIN available a ON a.producto_id=n.product_id WHERE COALESCE(a.qty,0)<n.qty) INTO insufficient;
  IF insufficient THEN RAISE EXCEPTION 'Cannot void receiving: some received stock has already been consumed'; END IF;
  FOR scan IN SELECT * FROM api.inventory_receiving_scans WHERE session_id=sess.id AND result='ACCEPTED' AND movement_id IS NOT NULL ORDER BY id FOR UPDATE LOOP
   INSERT INTO api.movimientos_stock(fecha_movimiento,movimiento_tipo,almacen_origen_id,producto_id,cantidad,doc_tipo,doc_id,doc_linea_id,referencia,observacion)
   VALUES(now(),'AJUSTE_NEGATIVO',sess.warehouse_id,scan.product_id,scan.base_quantity,'SCANNER_RECEIVING_VOID',sess.id,scan.id,sess.reference,'Receiving void: '||left(trim(p_reason),300)) RETURNING id INTO mid;
   UPDATE api.inventory_receiving_scans SET reversal_movement_id=mid WHERE id=scan.id;count_rows:=count_rows+1;
  END LOOP;
 END IF;
 UPDATE api.inventory_receiving_sessions SET status='VOIDED',voided_at=now(),voided_by=left(COALESCE(p_operator,'inventory'),80),void_reason=left(trim(p_reason),500),updated_at=now() WHERE id=sess.id;
 RETURN jsonb_build_object('ok',true,'session_id',sess.id,'status','VOIDED','reversal_count',count_rows);
END $$;

CREATE OR REPLACE VIEW api.v_inventory_receiving_scans AS
SELECT s.id,s.session_id,r.reference,r.status session_status,r.warehouse_id,a.codigo warehouse_code,a.nombre warehouse_name,
 s.barcode,s.result,s.product_id,p.cdg_prod,p.nombre product_name,s.package_quantity,s.unit_factor,s.base_quantity,
 s.lot_code,s.expires_on,s.movement_id,s.reversal_movement_id,s.operator_name,s.created_at
FROM api.inventory_receiving_scans s JOIN api.inventory_receiving_sessions r ON r.id=s.session_id JOIN api.almacenes a ON a.id=r.warehouse_id LEFT JOIN api.productos p ON p.id=s.product_id;

GRANT SELECT,INSERT,UPDATE ON api.product_barcodes TO web_anon;
GRANT USAGE,SELECT ON SEQUENCE api.product_barcodes_id_seq TO web_anon;
GRANT SELECT ON api.inventory_receiving_sessions,api.inventory_receiving_scans,api.v_inventory_receiving_scans TO web_anon;
GRANT EXECUTE ON FUNCTION api.gtin_check_digit(text),api.save_product_barcode(integer,text,text,text,numeric,text,boolean,text,text),api.create_inventory_receiving(integer,text,text,text),api.scan_inventory_receiving(bigint,text,numeric,text,text,date),api.post_inventory_receiving(bigint,text),api.void_inventory_receiving(bigint,text,text) TO web_anon;
NOTIFY pgrst,'reload schema';
COMMIT;
