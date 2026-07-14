#!/usr/bin/env python3
from __future__ import annotations
import html, json, os, socket, subprocess
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Optional
from urllib.parse import parse_qs, quote
import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

POSTGREST_BASE_URL=os.environ.get("POSTGREST_BASE_URL","http://127.0.0.1:3000").rstrip("/")
DASHBOARD_HOST=os.environ.get("DASHBOARD_HOST","127.0.0.1")
DASHBOARD_PORT=int(os.environ.get("DASHBOARD_PORT","8793"))
REQUEST_TIMEOUT=int(os.environ.get("REQUEST_TIMEOUT","8"))
BUSINESS_TZ=os.environ.get("BUSINESS_TZ","America/Lima")
REQUIRE_OPS_TOKEN=os.environ.get("REQUIRE_OPS_TOKEN","false").lower()=="true"
OPS_TOKEN=os.environ.get("OPS_TOKEN","").strip()
OUTBOX_MAX_ATTEMPTS=int(os.environ.get("OUTBOX_MAX_ATTEMPTS","5"))
BACKUP_DIR=os.environ.get("BACKUP_DIR","/var/backups/replau-localapi")
WHATSAPP_WATCHDOG_STATE=os.environ.get("WHATSAPP_WATCHDOG_STATE","/home/guill/.local/state/replau/whatsapp_watchdog_state.json")
RESTAURANT_STATUS_PATH=Path(os.environ.get("REPLAU_RESTAURANT_STATUS_PATH","/home/guill/.openclaw/workspace/replau_restaurant_status.json"))
PURCHASE_TARGET_DAYS=float(os.environ.get("REPLAU_PURCHASE_TARGET_DAYS","3"))
PURCHASE_MIN_KG=float(os.environ.get("REPLAU_PURCHASE_MIN_KG","1"))
SERVICE_NAMES=[s.strip() for s in os.environ.get("SERVICE_NAMES","replau-openclaw-whatsapp-bridge,replau-email-worker,replau-logistics-viewer,replau-kitchen-ui,replau-whatsapp-outbox-worker,replau-openclaw-whatsapp-send-adapter").split(",") if s.strip()]
PORTS={"PostgREST":("127.0.0.1",3000),"Bridge":("127.0.0.1",8789),"Logistics":("127.0.0.1",8790),"Kitchen":("127.0.0.1",8791),"Send Adapter":("127.0.0.1",8792),"Product Admin":("127.0.0.1",8794),"Payment Proof Review":("127.0.0.1",8795),"OpenClaw":("127.0.0.1",18789)}
URLS={"PostgREST":f"{POSTGREST_BASE_URL}/","Kitchen":"http://127.0.0.1:8791/health","Send Adapter":"http://127.0.0.1:8792/health","Product Admin":"http://127.0.0.1:8794/health","Payment Proof Review":"http://127.0.0.1:8795/health"}
app=FastAPI(title="Replau Ops Dashboard",version="1.0.0")

def esc(v): return "" if v is None else html.escape(str(v))
def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def money(v):
    try:
        return f"S/{float(v or 0):,.2f}"
    except Exception:
        return "S/0.00"
def parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except Exception:
        return None
def business_day_window():
    tz=ZoneInfo(BUSINESS_TZ)
    local_now=datetime.now(tz)
    start=datetime.combine(local_now.date(),time.min,tzinfo=tz)
    end=datetime.combine(local_now.date(),time.max,tzinfo=tz)
    return {
        "timezone":BUSINESS_TZ,
        "date":local_now.date().isoformat(),
        "label":local_now.strftime("%A, %b %d"),
        "start":start,
        "end":end,
        "start_iso":start.isoformat(),
        "end_iso":end.isoformat(),
    }
def auth(req:Request,t:Optional[str]):
    if REQUIRE_OPS_TOKEN and not (t==OPS_TOKEN or req.query_params.get("token")==OPS_TOKEN):
        raise HTTPException(401,"Invalid or missing ops token")
def token_query(req:Request):
    token=req.query_params.get("token")
    if REQUIRE_OPS_TOKEN and token==OPS_TOKEN:
        return "?token="+requests.utils.quote(token,safe="")
    return ""
def with_token(path:str,req:Request):
    tq=token_query(req)
    if not tq: return path
    sep="&" if "?" in path else "?"
    return path+sep+tq[1:]
def proc_env_token(script_name:str,env_name:str):
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmdline=(proc/"cmdline").read_bytes().decode("utf-8","ignore")
            if script_name not in cmdline:
                continue
            for item in (proc/"environ").read_bytes().split(b"\0"):
                if item.startswith((env_name+"=").encode()):
                    token=item.decode("utf-8","ignore").split("=",1)[1].strip()
                    if token:
                        return token
        except Exception:
            continue
    return ""
def product_admin_url(path:str="/"):
    token=os.environ.get("PRODUCT_ADMIN_TOKEN","").strip() or proc_env_token("replau_product_admin.py","ADMIN_TOKEN")
    suffix="/" + path.strip("/") if path.strip("/") else "/"
    if token:
        return "http://127.0.0.1:8794"+suffix+"?token="+quote(token,safe="")
    return "http://127.0.0.1:8794"+suffix
def payment_proof_review_url():
    token=os.environ.get("PAYMENT_PROOF_REVIEW_TOKEN","").strip() or proc_env_token("replau_payment_proof_review.py","REVIEW_TOKEN")
    if token:
        return "http://127.0.0.1:8795/?token="+quote(token,safe="")
    return "http://127.0.0.1:8795/"
def erp_nav(req:Request):
    return f'''<div class="erp-nav" aria-label="Replau ERP navigation">
<a href="/{token_query(req)}">Ops</a>
<a href="http://127.0.0.1:8790/dashboard">Logistics</a>
<a href="http://127.0.0.1:8791/">Kitchen</a>
<a href="{esc(payment_proof_review_url())}">Payments</a>
<a href="{esc(product_admin_url())}">Products</a>
<a href="{esc(product_admin_url("recipes"))}">Recipes</a>
<a href="{esc(product_admin_url("costs"))}">Costs</a>
<a href="{esc(product_admin_url("menu"))}" target="_blank">Public Menu</a>
</div>'''
def cmd(args,timeout=6):
    try:
        p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
        return {"ok":p.returncode==0,"stdout":p.stdout.strip(),"stderr":p.stderr.strip(),"returncode":p.returncode}
    except Exception as e:
        return {"ok":False,"stdout":"","stderr":f"{type(e).__name__}: {e}","returncode":None}
def svc(name):
    a=cmd(["systemctl","is-active",name]); e=cmd(["systemctl","is-enabled",name])
    if a["stdout"] != "active":
        user_a=cmd(["systemctl","--user","is-active",name])
        user_e=cmd(["systemctl","--user","is-enabled",name])
        if user_a["stdout"] == "active" or user_e["stdout"] not in ("", "not-found", "disabled"):
            a, e = user_a, user_e
    return {"service":name,"active":a["stdout"] or "unknown","enabled":e["stdout"] or "unknown","ok":a["stdout"]=="active"}
def tcp(host,port):
    try:
        with socket.create_connection((host,port),timeout=3): return {"ok":True,"host":host,"port":port}
    except Exception as e: return {"ok":False,"host":host,"port":port,"error":f"{type(e).__name__}: {e}"}
def url(u):
    try:
        r=requests.get(u,timeout=REQUEST_TIMEOUT)
        return {"ok":200<=r.status_code<500,"url":u,"status":r.status_code,"ms":round(r.elapsed.total_seconds()*1000,1)}
    except Exception as e: return {"ok":False,"url":u,"error":f"{type(e).__name__}: {e}"}
def pg(path):
    try:
        r=requests.get(POSTGREST_BASE_URL+path,timeout=REQUEST_TIMEOUT); r.raise_for_status()
        return {"ok":True,"data":r.json()}
    except Exception as e: return {"ok":False,"data":[],"error":f"{type(e).__name__}: {e}"}
def restaurant_status():
    default={
        "accepting_orders":True,
        "reason":"",
        "customer_message":"Estamos cerrados temporalmente. Escríbenos más tarde para hacer tu pedido.",
        "updated_at":None,
        "updated_by":"system",
    }
    try:
        if RESTAURANT_STATUS_PATH.exists():
            data=json.loads(RESTAURANT_STATUS_PATH.read_text(encoding="utf-8"))
            if isinstance(data,dict):
                return {**default,**data}
    except Exception as e:
        return {**default,"accepting_orders":False,"reason":f"status file error: {e}","status_file_error":f"{type(e).__name__}: {e}"}
    return default
def save_restaurant_status(accepting_orders:bool,reason:str,customer_message:str,updated_by:str="ops"):
    RESTAURANT_STATUS_PATH.parent.mkdir(parents=True,exist_ok=True)
    payload={
        "accepting_orders":accepting_orders,
        "reason":reason.strip(),
        "customer_message":(customer_message or "Estamos cerrados temporalmente. Escríbenos más tarde para hacer tu pedido.").strip(),
        "updated_at":now(),
        "updated_by":updated_by,
    }
    RESTAURANT_STATUS_PATH.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return payload
def product_summary():
    rows=pg("/productos?select=id,cdg_prod,nombre,active&order=id.asc&limit=1000")
    if not rows["ok"]:
        return {**rows,"active":0,"inactive":0,"sample":[]}
    active=[r for r in rows["data"] if r.get("active") is True]
    inactive=[r for r in rows["data"] if r.get("active") is False]
    return {"ok":True,"data":rows["data"],"active":len(active),"inactive":len(inactive),"sample":rows["data"][:12]}
def recent_payment_proofs():
    return pg("/v_payment_proofs_logistica?select=id,pedido_num,cliente_nombre,status,total,payment_status,created_at&order=id.desc&limit=8")
def payment_proof_queue():
    return pg("/v_payment_proofs_logistica?select=id,pedido_num,cliente_nombre,status,total,payment_status,created_at&order=id.desc&limit=1000")
def active_prices_by_product():
    rows=pg("/producto_precios?active=eq.true&select=producto_id,unidad,precio,moneda,valid_from&order=id.desc&limit=5000")
    if not rows["ok"]:
        return {}
    out={}
    for row in rows["data"]:
        pid=row.get("producto_id")
        if pid is None or pid in out:
            continue
        out[pid]=row
    return out
def product_lookup_rows():
    rows=pg("/productos?select=id,cdg_prod,nombre,active&limit=2000")
    if not rows["ok"]:
        return {"by_id":{},"by_code":{}}
    by_id={r.get("id"):r for r in rows["data"] if r.get("id") is not None}
    by_code={str(r.get("cdg_prod") or ""):r for r in rows["data"] if r.get("cdg_prod")}
    return {"by_id":by_id,"by_code":by_code}
def recipe_cost_data():
    ingredients=pg("/ingredientes_costeo?active=eq.true&select=id,nombre,costo_kg,stk_in,stk_out,stk_act&order=nombre.asc&limit=1000")
    recipes=pg("/recetas_costeo?active=eq.true&select=id,nombre,producto_id,rendimiento_unidades&order=nombre.asc&limit=1000")
    lines=pg("/receta_ingredientes_costeo?select=id,receta_id,ingrediente_id,cantidad_g&order=id.asc&limit=5000")
    if not (ingredients["ok"] and recipes["ok"] and lines["ok"]):
        return {"ok":False,"ingredients":{},"recipes":[],"error":ingredients.get("error") or recipes.get("error") or lines.get("error")}
    ing={r["id"]:r for r in ingredients["data"] if r.get("id") is not None}
    by_recipe=defaultdict(list)
    for line in lines["data"]:
        by_recipe[line.get("receta_id")].append(line)
    out=[]
    for recipe in recipes["data"]:
        rid=recipe.get("id")
        total=0.0
        limiting_units=None
        limiting_ingredient=""
        yield_units=float(recipe.get("rendimiento_unidades") or 1)
        for line in by_recipe.get(rid,[]):
            ingredient=ing.get(line.get("ingrediente_id"))
            if not ingredient:
                continue
            qty_g=float(line.get("cantidad_g") or 0)
            cost_per_kg=float(ingredient.get("costo_kg") or 0)
            total+=(qty_g/1000.0)*cost_per_kg
            grams_per_unit=qty_g/yield_units if yield_units > 0 else qty_g
            stock_kg=float(ingredient.get("stk_act") if ingredient.get("stk_act") is not None else (float(ingredient.get("stk_in") or 0)-float(ingredient.get("stk_out") or 0)))
            if grams_per_unit > 0:
                units=(stock_kg*1000.0)/grams_per_unit
                if limiting_units is None or units < limiting_units:
                    limiting_units=units
                    limiting_ingredient=str(ingredient.get("nombre") or "")
        cost_unit=total/yield_units if yield_units > 0 else total
        out.append({**recipe,"total_cost":round(total,4),"cost_per_unit":round(cost_unit,4),"units_remaining":round(max(limiting_units or 0,0),1) if limiting_units is not None else None,"limiting_ingredient":limiting_ingredient})
    return {"ok":True,"ingredients":ing,"recipes":out}
def purchase_agent_summary(bsum):
    recipes=recipe_cost_data()
    products=product_lookup_rows()
    if not recipes.get("ok"):
        return {"ok":False,"error":recipes.get("error") or "recipe cost data unavailable","cards":[],"recommendations":[],"ingredient_behavior":[]}
    product_sales={}
    for item in bsum.get("product_sales",bsum.get("top_products",[])):
        product=products["by_code"].get(str(item.get("code") or ""),{})
        pid=product.get("id")
        if pid is None:
            continue
        product_sales[pid]=product_sales.get(pid,0.0)+float(item.get("qty") or 0)
    ingredients=recipes.get("ingredients",{})
    ingredient_usage=defaultdict(float)
    ingredient_products=defaultdict(set)
    ingredient_recipes=defaultdict(set)
    product_risk_by_ingredient=defaultdict(list)
    for recipe in recipes.get("recipes",[]):
        pid=recipe.get("producto_id")
        sold_qty=product_sales.get(pid,0.0)
        yield_units=float(recipe.get("rendimiento_unidades") or 1)
        recipe_lines=pg(f"/receta_ingredientes_costeo?receta_id=eq.{quote(str(recipe.get('id')),safe='')}&select=ingrediente_id,cantidad_g&limit=200")
        if not recipe_lines.get("ok"):
            continue
        product=products["by_id"].get(pid,{})
        product_name=product.get("nombre") or recipe.get("nombre") or f"Product {pid}"
        for line in recipe_lines.get("data",[]):
            iid=line.get("ingrediente_id")
            qty_g=float(line.get("cantidad_g") or 0)
            grams_per_unit=qty_g/yield_units if yield_units > 0 else qty_g
            if sold_qty > 0:
                ingredient_usage[iid]+=grams_per_unit*sold_qty/1000.0
            ingredient_products[iid].add(str(product_name))
            ingredient_recipes[iid].add(str(recipe.get("nombre") or "Recipe"))
            units_remaining=recipe.get("units_remaining")
            if units_remaining is not None and units_remaining <= 10:
                product_risk_by_ingredient[iid].append({"product":product_name,"units_remaining":units_remaining})
    recommendations=[]
    behavior=[]
    total_purchase_value=0.0
    for iid,ingredient in ingredients.items():
        stock_kg=float(ingredient.get("stk_act") if ingredient.get("stk_act") is not None else (float(ingredient.get("stk_in") or 0)-float(ingredient.get("stk_out") or 0)))
        cost_per_kg=float(ingredient.get("costo_kg") or 0)
        usage_today=ingredient_usage.get(iid,0.0)
        days_remaining=(stock_kg/usage_today) if usage_today > 0 else None
        linked_products=sorted(ingredient_products.get(iid,set()))
        risk_products=sorted(product_risk_by_ingredient.get(iid,[]),key=lambda r:r.get("units_remaining") or 0)
        severity="ok"
        reasons=[]
        if stock_kg <= 0:
            severity="critical"; reasons.append("stock is depleted")
        elif risk_products:
            severity="urgent"; reasons.append("limits recipe-linked products")
        elif days_remaining is not None and days_remaining <= PURCHASE_TARGET_DAYS:
            severity="urgent"; reasons.append(f"{days_remaining:.1f} days remaining at today's usage")
        elif stock_kg <= PURCHASE_MIN_KG:
            severity="watch"; reasons.append("low physical stock")
        target_stock=max(PURCHASE_MIN_KG,usage_today*PURCHASE_TARGET_DAYS)
        suggested_kg=max(0.0,target_stock-stock_kg)
        if severity in {"critical","urgent","watch"} and suggested_kg <= 0 and stock_kg <= PURCHASE_MIN_KG:
            suggested_kg=PURCHASE_MIN_KG
        suggested_cost=suggested_kg*cost_per_kg
        row={
            "ingredient_id":iid,
            "ingredient":ingredient.get("nombre") or f"Ingredient {iid}",
            "severity":severity,
            "stock_kg":round(stock_kg,3),
            "usage_today_kg":round(usage_today,3),
            "days_remaining":round(days_remaining,1) if days_remaining is not None else None,
            "cost_per_kg":round(cost_per_kg,4),
            "stock_value":round(max(stock_kg,0)*cost_per_kg,2),
            "linked_products":linked_products[:6],
            "products_at_risk":risk_products[:6],
            "analysis":"; ".join(reasons) if reasons else "stock is stable with current recipe and sales signals",
            "suggested_purchase_kg":round(suggested_kg,3),
            "suggested_purchase_cost":round(suggested_cost,2),
        }
        behavior.append(row)
        if severity in {"critical","urgent","watch"}:
            recommendations.append(row)
            total_purchase_value+=suggested_cost
    severity_order={"critical":0,"urgent":1,"watch":2,"ok":3}
    recommendations=sorted(recommendations,key=lambda r:(severity_order.get(r["severity"],9),r["days_remaining"] if r["days_remaining"] is not None else 9999,r["stock_kg"]))[:12]
    behavior=sorted(behavior,key=lambda r:(severity_order.get(r["severity"],9),r["days_remaining"] if r["days_remaining"] is not None else 9999,r["ingredient"]))[:30]
    cards=[
        {"label":"Purchase actions","value":len(recommendations),"detail":"Ingredients needing buy/watch decision","tone":"warn" if recommendations else "good"},
        {"label":"Critical shortages","value":sum(1 for r in recommendations if r["severity"]=="critical"),"detail":"Ingredients at or below zero stock","tone":"bad" if any(r["severity"]=="critical" for r in recommendations) else "good"},
        {"label":"Usage tracked","value":sum(1 for r in behavior if r["usage_today_kg"]>0),"detail":"Ingredients consumed by today's sold recipes","tone":"good"},
        {"label":"Suggested spend","value":money(total_purchase_value),"detail":f"Target {PURCHASE_TARGET_DAYS:g} days of stock","tone":"warn" if total_purchase_value else "good"},
    ]
    return {"ok":True,"cards":cards,"recommendations":recommendations,"ingredient_behavior":behavior,"target_days":PURCHASE_TARGET_DAYS,"min_purchase_kg":PURCHASE_MIN_KG}
def owner_command_summary(bsum,h):
    recipes=recipe_cost_data()
    products=product_lookup_rows()
    prices=active_prices_by_product()
    proof_queue=h.get("payment_proof_queue") or h.get("payment_proofs",{})
    payment_rows=proof_queue["data"] if proof_queue.get("ok") else []
    pending_proofs=[r for r in payment_rows if str(r.get("status") or "").upper() not in {"VERIFIED","CANCELLED","REJECTED"}]
    pending_payment_value=sum(float(r.get("total") or 0) for r in pending_proofs)
    stock_risks=[]
    margin_rows=[]
    margin_by_product={}
    if recipes.get("ok"):
        for recipe in recipes["recipes"]:
            units=recipe.get("units_remaining")
            if units is not None and units <= 10:
                product=products["by_id"].get(recipe.get("producto_id"),{})
                stock_risks.append({
                    "recipe":recipe.get("nombre"),
                    "product":product.get("nombre") or f"Product {recipe.get('producto_id')}",
                    "units_remaining":units,
                    "limiting_ingredient":recipe.get("limiting_ingredient"),
                })
            price=prices.get(recipe.get("producto_id"))
            if price:
                sale_price=float(price.get("precio") or 0)
                cost=float(recipe.get("cost_per_unit") or 0)
                margin=sale_price-cost
                margin_pct=(margin/sale_price*100.0) if sale_price > 0 else 0
                product=products["by_id"].get(recipe.get("producto_id"),{})
                row={
                    "product_id":recipe.get("producto_id"),
                    "code":product.get("cdg_prod") or "",
                    "product":product.get("nombre") or recipe.get("nombre"),
                    "price":round(sale_price,2),
                    "cost":round(cost,2),
                    "margin":round(margin,2),
                    "margin_pct":round(margin_pct,1),
                    "units_sold":0.0,
                    "sales_today":0.0,
                    "gross_profit_today":0.0,
                }
                margin_rows.append(row)
                margin_by_product[recipe.get("producto_id")]=row
    sales_margin_rows=[]
    for item in bsum.get("top_products",[]):
        code=str(item.get("code") or "")
        product=products["by_code"].get(code,{})
        margin=margin_by_product.get(product.get("id"))
        if not margin:
            continue
        qty=float(item.get("qty") or 0)
        sales=float(item.get("sales") or 0)
        enriched={**margin,"units_sold":round(qty,3),"sales_today":round(sales,2),"gross_profit_today":round(qty*float(margin.get("margin") or 0),2)}
        sales_margin_rows.append(enriched)
    margin_rows=sorted(sales_margin_rows or margin_rows,key=lambda r:(r.get("gross_profit_today",0),r["margin_pct"]),reverse=True)[:6]
    stock_risks=sorted(stock_risks,key=lambda r:r.get("units_remaining") or 0)[:6]
    cards=[
        {"label":"Sales booked","value":money(bsum.get("revenue")),"detail":f'{bsum.get("orders",0)} orders · {money(bsum.get("avg_ticket"))} avg ticket',"tone":"good"},
        {"label":"Open operations","value":bsum.get("open_orders",0),"detail":"Orders not delivered yet","tone":"warn" if int(bsum.get("open_orders") or 0) else "good"},
        {"label":"Pending payment proofs","value":len(pending_proofs),"detail":f"{money(pending_payment_value)} waiting for review","tone":"warn" if pending_proofs else "good"},
        {"label":"Low stock products","value":len(stock_risks),"detail":"Recipe-linked products under 10 units","tone":"warn" if stock_risks else "good"},
        {"label":"Margin coverage","value":len(margin_by_product),"detail":f"{len(sales_margin_rows)} sold today with cost signal","tone":"good" if margin_by_product else "warn"},
    ]
    return {"ok":True,"cards":cards,"stock_risks":stock_risks,"margin_rows":margin_rows,"sales_margin_rows":sales_margin_rows,"recipe_storage_ok":bool(recipes.get("ok")),"recipe_count":len(recipes.get("recipes",[]))}
def business_summary():
    day=business_day_window()
    start_q=requests.utils.quote(day["start_iso"],safe=":T-")
    end_q=requests.utils.quote(day["end_iso"],safe=":T-")
    orders=pg(f"/pedidos?created_at=gte.{start_q}&created_at=lte.{end_q}&select=id,pedido_num,estado,total,subtotal,delivery,metodo_pago,payment_status,kitchen_status,created_at,updated_at&order=id.desc&limit=1000")
    if not orders["ok"]:
        return {"ok":False,"error":orders.get("error"),"day":{k:v for k,v in day.items() if not k.endswith("_iso") and k not in {"start","end"}}}
    rows=orders["data"]
    active=[r for r in rows if str(r.get("estado") or "").upper() not in {"ANULADO","CANCELLED","CANCELADO"}]
    delivered=[r for r in active if str(r.get("estado") or "").upper()=="ENTREGADO"]
    revenue=sum(float(r.get("total") or 0) for r in active)
    delivered_revenue=sum(float(r.get("total") or 0) for r in delivered)
    avg_ticket=(revenue/len(active)) if active else 0
    status_counts=Counter(str(r.get("estado") or "SIN_ESTADO") for r in rows)
    kitchen_counts=Counter(str(r.get("kitchen_status") or "SIN_ESTADO") for r in rows)
    payment_counts=Counter(str(r.get("payment_status") or "SIN_ESTADO") for r in rows)
    method_counts=Counter(str(r.get("metodo_pago") or "SIN_METODO") for r in active)
    ids={int(r["id"]) for r in rows if r.get("id") is not None}
    item_rows=[]
    if ids:
        items=pg(f"/v_pedido_items_logistica?created_at=gte.{start_q}&created_at=lte.{end_q}&select=pedido_id,cdg_prod,producto_texto,producto_nombre_maestro,cantidad,total_linea,created_at&order=id.desc&limit=2000")
        if items["ok"]:
            item_rows=[i for i in items["data"] if int(i.get("pedido_id") or 0) in ids]
    top=defaultdict(lambda:{"product":"","code":"","qty":0.0,"sales":0.0})
    for i in item_rows:
        key=i.get("cdg_prod") or i.get("producto_nombre_maestro") or i.get("producto_texto") or "SIN_PRODUCTO"
        top[key]["product"]=i.get("producto_nombre_maestro") or i.get("producto_texto") or key
        top[key]["code"]=i.get("cdg_prod") or ""
        top[key]["qty"]+=float(i.get("cantidad") or 0)
        top[key]["sales"]+=float(i.get("total_linea") or 0)
    product_sales=sorted(top.values(),key=lambda r:(r["sales"],r["qty"]),reverse=True)
    top_products=product_sales[:6]
    rush=Counter()
    for r in active:
        dt=parse_dt(r.get("created_at"))
        if dt:
            rush[f"{dt.astimezone(ZoneInfo(BUSINESS_TZ)).hour:02d}:00"]+=1
    latest=rows[:8]
    open_orders=[r for r in active if str(r.get("estado") or "").upper() not in {"ENTREGADO"}]
    return {
        "ok":True,
        "day":{"timezone":day["timezone"],"date":day["date"],"label":day["label"],"start":day["start_iso"],"end":day["end_iso"]},
        "orders":len(active),
        "raw_orders":len(rows),
        "revenue":round(revenue,2),
        "delivered_revenue":round(delivered_revenue,2),
        "avg_ticket":round(avg_ticket,2),
        "delivered_orders":len(delivered),
        "open_orders":len(open_orders),
        "items_sold":round(sum(float(i.get("cantidad") or 0) for i in item_rows),3),
        "status_counts":dict(status_counts),
        "kitchen_counts":dict(kitchen_counts),
        "payment_counts":dict(payment_counts),
        "method_counts":dict(method_counts),
        "top_products":top_products,
        "product_sales":product_sales,
        "rush_hours":[{"hour":k,"orders":v} for k,v in sorted(rush.items())],
        "latest_orders":latest,
    }
def latest_backup():
    p=Path(BACKUP_DIR)
    if not p.exists(): return None
    dumps=sorted(p.glob("*.dump"),key=lambda x:x.stat().st_mtime,reverse=True)
    if not dumps: return None
    f=dumps[0]
    return {"path":str(f),"size_mb":round(f.stat().st_size/1024/1024,2),"modified_at":datetime.fromtimestamp(f.stat().st_mtime,tz=timezone.utc).isoformat().replace("+00:00","Z")}
def whatsapp_watchdog():
    p=Path(WHATSAPP_WATCHDOG_STATE)
    try:
        data=json.loads(p.read_text())
        return {"ok":bool(data.get("gateway_health_ok")) and bool(data.get("connected")),"path":str(p),**data}
    except Exception as e:
        return {"ok":False,"path":str(p),"status":"missing","error":f"{type(e).__name__}: {e}"}
def collect():
    services=[svc(s) for s in SERVICE_NAMES]
    ports={k:tcp(*v) for k,v in PORTS.items()}
    urls={k:url(v) for k,v in URLS.items()}
    orders=pg("/v_pedidos_logistica?select=id,pedido_num,cliente_nombre,estado,total,created_at&order=id.desc&limit=5")
    kitchen=pg("/v_kitchen_orders?select=id,pedido_num,cliente_nombre,kitchen_status,queue_minutes,queue_color,total&order=id.asc&limit=10")
    pending=pg("/v_whatsapp_outbox?status=eq.PENDING&select=id,pedido_num,event_type,status,attempts,last_attempt_at,created_at,error_message&order=id.desc&limit=50")
    errors=pg("/v_whatsapp_outbox?status=eq.ERROR&select=id,pedido_num,event_type,status,attempts,last_attempt_at,created_at,error_message&order=id.desc&limit=50")
    emails=pg("/email_logistica_log?status=eq.PENDING&select=id,pedido_id,recipient,status,created_at,error_message&order=id.desc&limit=50")
    stuck=[r for r in pending["data"] if int(r.get("attempts") or 0)>=OUTBOX_MAX_ATTEMPTS] if pending["ok"] else []
    crit=[]; warn=[]
    for s in services:
        if not s["ok"]: crit.append(f"Service not active: {s['service']} ({s['active']})")
    for n,p in ports.items():
        if not p["ok"]: crit.append(f"Port failed: {n} {p.get('host')}:{p.get('port')}")
    for n,u in urls.items():
        if not u["ok"]: crit.append(f"URL failed: {n} {u.get('url')}")
    if stuck: crit.append(f"Stuck WhatsApp outbox rows: {len(stuck)}")
    if errors["data"]: crit.append(f"ERROR WhatsApp outbox rows: {len(errors['data'])}")
    if pending["data"]: warn.append(f"Pending WhatsApp notifications: {len(pending['data'])}")
    if emails["data"]: warn.append(f"Pending logistics emails: {len(emails['data'])}")
    b=latest_backup()
    if not b: warn.append("No backup file found")
    w=whatsapp_watchdog()
    if not w["ok"]:
        crit.append(f"WhatsApp gateway status: {w.get('status','unknown')} ({w.get('error') or w.get('last_disconnect_message') or 'watchdog not healthy'})")
    elif w.get("seconds_since_disconnect") is not None and int(w.get("seconds_since_disconnect") or 0) < 600:
        warn.append(f"WhatsApp recovered recently: {w.get('seconds_since_disconnect')}s since last disconnect")
    overall="CRITICAL" if crit else ("WARN" if warn else "OK")
    rs=restaurant_status()
    if not rs.get("accepting_orders"):
        warn.append("Restaurant ordering is paused")
        overall="CRITICAL" if crit else "WARN"
    return {"ok":overall=="OK","overall":overall,"checked_at":now(),"critical":crit,"warnings":warn,"services":services,"ports":ports,"urls":urls,"latest_orders":orders,"kitchen":kitchen,"pending_outbox":pending,"error_outbox":errors,"pending_emails":emails,"stuck_outbox":stuck,"latest_backup":b,"whatsapp_watchdog":w,"restaurant_status":rs,"products":product_summary(),"payment_proofs":recent_payment_proofs(),"payment_proof_queue":payment_proof_queue()}
@app.get("/health")
def health(req:Request,x_ops_token:Optional[str]=Header(default=None,alias="X-Ops-Token")):
    auth(req,x_ops_token); return collect()
@app.get("/api/health")
def api(req:Request,x_ops_token:Optional[str]=Header(default=None,alias="X-Ops-Token")):
    auth(req,x_ops_token); return collect()
@app.get("/api/business-summary")
def api_business_summary(req:Request,x_ops_token:Optional[str]=Header(default=None,alias="X-Ops-Token")):
    auth(req,x_ops_token); return business_summary()
@app.get("/api/owner-command")
def api_owner_command(req:Request,x_ops_token:Optional[str]=Header(default=None,alias="X-Ops-Token")):
    auth(req,x_ops_token)
    h=collect()
    return owner_command_summary(business_summary(),h)
@app.get("/api/purchase-agent")
def api_purchase_agent(req:Request,x_ops_token:Optional[str]=Header(default=None,alias="X-Ops-Token")):
    auth(req,x_ops_token)
    return purchase_agent_summary(business_summary())
@app.post("/api/restaurant-status")
async def update_restaurant_status(req:Request,x_ops_token:Optional[str]=Header(default=None,alias="X-Ops-Token")):
    auth(req,x_ops_token)
    form={k:v[-1] if v else "" for k,v in parse_qs((await req.body()).decode("utf-8"),keep_blank_values=True).items()}
    accepting_orders=form.get("accepting_orders","")
    reason=form.get("reason","")
    customer_message=form.get("customer_message","")
    save_restaurant_status(accepting_orders.lower() in {"true","1","yes","on","open"},reason,customer_message,"ops-dashboard")
    return RedirectResponse(url=with_token("/?flash=Restaurant+status+updated",req),status_code=303)
def tbl(rows,cols):
    if not rows: return '<div class="empty">No rows.</div>'
    return "<table><thead><tr>"+"".join(f"<th>{esc(c)}</th>" for c in cols)+"</tr></thead><tbody>"+"".join("<tr>"+"".join(f"<td>{esc(r.get(c))}</td>" for c in cols)+"</tr>" for r in rows)+"</tbody></table>"
def count_chips(counts):
    if not counts:
        return '<span class="chip">None</span>'
    return "".join(f'<span class="chip"><strong>{esc(k)}</strong> {esc(v)}</span>' for k,v in sorted(counts.items()))
def product_cards(rows):
    if not rows:
        return '<div class="empty">No product sales today.</div>'
    return "".join(f'''<div class="metric-row"><div><strong>{esc(r.get("product"))}</strong><div class="muted">{esc(r.get("code"))}</div></div><div class="right"><strong>{money(r.get("sales"))}</strong><div class="muted">{esc(r.get("qty"))} sold</div></div></div>''' for r in rows)
def rush_bars(rows):
    if not rows:
        return '<div class="empty">No order activity today.</div>'
    max_orders=max([int(r.get("orders") or 0) for r in rows] or [1])
    bars=[]
    for r in rows:
        orders=int(r.get("orders") or 0)
        width=max(8,round((orders/max_orders)*100))
        bars.append(f'''<div class="bar-row"><span>{esc(r.get("hour"))}</span><div class="bar"><i style="width:{width}%"></i></div><strong>{orders}</strong></div>''')
    return "".join(bars)
def command_cards(cards):
    return "".join(f'''<div class="command-card {esc(c.get("tone"))}"><div class="label">{esc(c.get("label"))}</div><div class="value">{esc(c.get("value"))}</div><div class="muted">{esc(c.get("detail"))}</div></div>''' for c in cards)
def stock_risk_rows(rows):
    if not rows:
        return '<div class="empty">No recipe-linked low stock risk.</div>'
    return "".join(f'''<div class="metric-row"><div><strong>{esc(r.get("product"))}</strong><div class="muted">{esc(r.get("recipe"))}</div></div><div class="right"><strong>{esc(r.get("units_remaining"))} units</strong><div class="muted">{esc(r.get("limiting_ingredient"))}</div></div></div>''' for r in rows)
def margin_signal_rows(rows):
    if not rows:
        return '<div class="empty">No recipe cost to active price margin signals yet.</div>'
    return "".join(f'''<div class="metric-row"><div><strong>{esc(r.get("product"))}</strong><div class="muted">Price {money(r.get("price"))} · Cost {money(r.get("cost"))} · Sold {esc(r.get("units_sold",0))}</div></div><div class="right"><strong>{money(r.get("gross_profit_today") or r.get("margin"))}</strong><div class="muted">{esc(r.get("margin_pct"))}% margin</div></div></div>''' for r in rows)
def purchase_recommendation_rows(rows):
    if not rows:
        return '<div class="empty">No purchase action needed from current stock and sales signals.</div>'
    parts=[]
    for r in rows:
        risk=", ".join(f'{p.get("product")} ({p.get("units_remaining")} units)' for p in r.get("products_at_risk",[])[:3]) or "No linked product risk"
        parts.append(f'''<div class="metric-row"><div><strong>{esc(r.get("ingredient"))}</strong><div class="muted">{esc(r.get("severity")).upper()} · {esc(r.get("analysis"))}</div><div class="muted">{esc(risk)}</div></div><div class="right"><strong>{esc(r.get("suggested_purchase_kg"))} kg</strong><div class="muted">Stock {esc(r.get("stock_kg"))} kg · usage {esc(r.get("usage_today_kg"))} kg · {money(r.get("suggested_purchase_cost"))}</div></div></div>''')
    return "".join(parts)
def ingredient_behavior_rows(rows):
    if not rows:
        return '<div class="empty">No ingredient behavior available.</div>'
    parts=[]
    for r in rows[:10]:
        days="no sales usage" if r.get("days_remaining") is None else f'{r.get("days_remaining")} days left'
        products=", ".join(r.get("linked_products",[])[:3]) or "No linked products"
        parts.append(f'''<div class="metric-row"><div><strong>{esc(r.get("ingredient"))}</strong><div class="muted">{esc(products)}</div></div><div class="right"><strong>{esc(days)}</strong><div class="muted">Stock value {money(r.get("stock_value"))}</div></div></div>''')
    return "".join(parts)
@app.get("/",response_class=HTMLResponse)
def dash(req:Request,x_ops_token:Optional[str]=Header(default=None,alias="X-Ops-Token")):
    auth(req,x_ops_token); h=collect(); bsum=business_summary()
    owner=owner_command_summary(bsum,h)
    purchase=purchase_agent_summary(bsum)
    cls={"OK":"ok","WARN":"warn","CRITICAL":"bad"}[h["overall"]]
    def ul(xs): return "".join(f"<li>{esc(x)}</li>" for x in xs) or "<li>None</li>"
    backup=h["latest_backup"]; backup_html=f"{esc(backup)}" if backup else "Not found"
    service_rows=[{"service":s["service"],"active":s["active"],"enabled":s["enabled"]} for s in h["services"]]
    port_rows=[{"name":n,**p} for n,p in h["ports"].items()]
    url_rows=[{"name":n,**u} for n,u in h["urls"].items()]
    w=h["whatsapp_watchdog"]
    rs=h["restaurant_status"]
    prod=h["products"]
    flash=req.query_params.get("flash","")
    flash_html=f'<div class="flash">{esc(flash)}</div>' if flash else ""
    status_badge='<span class="pill open">ACCEPTING ORDERS</span>' if rs.get("accepting_orders") else '<span class="pill closed">ORDERS PAUSED</span>'
    summary_note='' if bsum.get("ok") else f'<div class="flash badline">Business summary error: {esc(bsum.get("error"))}</div>'
    summary_day=bsum.get("day",{})
    whatsapp_rows=[{
        "status":w.get("status"),
        "connected":w.get("connected"),
        "gateway_health_ok":w.get("gateway_health_ok"),
        "gateway_service_active":w.get("gateway_service_active"),
        "checked_at":w.get("checked_at"),
        "last_connected_at":w.get("last_connected_at"),
        "last_disconnect_at":w.get("last_disconnect_at"),
        "seconds_since_disconnect":w.get("seconds_since_disconnect"),
        "last_restart_at":w.get("last_restart_at"),
        "last_restart_reason":w.get("last_restart_reason"),
    }]
    return HTMLResponse(f'''<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="20"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Replau Ops</title>
<style>body{{margin:0;background:#0b1120;color:#e5edf7;font-family:"Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif}}.wrap{{max-width:1480px;margin:auto;padding:22px}}.top{{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}}h1,h2,h3{{letter-spacing:0}}h1{{color:#9ca3af}}h2{{color:#9ca3af}}h3{{color:#9ca3af}}.erp-nav{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 18px;padding:12px;border:1px solid #334155;border-radius:14px;background:#0b1220}}.erp-nav a{{color:#e5e7eb;background:#1f2937;border:1px solid #334155;border-radius:999px;padding:8px 11px;font-size:13px;font-weight:bold;text-decoration:none}}.erp-nav a:hover{{background:#7c3aed;border-color:#a78bfa}}.badge{{padding:10px 16px;border-radius:999px;font-weight:bold;color:#fff}}.ok{{background:#16a34a}}.warn{{background:#b45309}}.bad{{background:#dc2626}}.card{{background:#111827;border:1px solid #334155;border-radius:8px;padding:18px;margin-top:16px;box-shadow:0 16px 40px rgba(2,6,23,.24)}}.hero{{background:#101628;color:#f8fafc;border-color:#7c3aed}}.kpis,.command-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-top:14px}}.kpi,.command-card{{background:#0b1220;border:1px solid #26364a;border-radius:8px;padding:16px;min-height:96px}}.hero .kpi{{background:#132033;border-color:#7c3aed}}.command-card.good{{border-color:#16a34a}}.command-card.warn{{border-color:#b45309}}.kpi .label,.command-card .label{{font-size:15px;line-height:1.25;color:#16a34a;font-weight:800}}.hero .kpi .label{{color:#16a34a}}.kpi .value,.command-card .value{{font-size:34px;font-weight:900;margin-top:10px;color:#16a34a}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:10px;border-bottom:1px solid #26364a;text-align:left;vertical-align:top}}th{{color:#7c3aed}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.grid4{{display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:16px}}label{{display:block;margin:10px 0 6px;color:#7c3aed}}input,textarea,select{{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #475569;background:#020617;color:#e5edf7}}textarea{{min-height:82px}}button,.btn{{display:inline-block;padding:10px 14px;border-radius:8px;border:0;background:#374151;color:#fff;font-weight:bold;cursor:pointer;text-decoration:none}}.btn.secondary{{background:#4b5563}}.pill,.chip{{display:inline-block;padding:7px 10px;border-radius:999px;font-weight:bold}}.chip{{background:#1e1b4b;color:#f8fafc;margin:0 8px 8px 0;border:1px solid #7c3aed}}.open{{background:#16a34a;color:#fff}}.closed{{background:#b91c1c;color:#fff}}.muted{{color:#94a3b8}}.flash{{background:#1e1b4b;border:1px solid #7c3aed;color:#f8fafc;padding:12px;border-radius:8px;margin-top:16px}}.badline{{background:#3f1010;border-color:#ef4444;color:#fee2e2}}.metric-row{{display:flex;justify-content:space-between;gap:12px;padding:11px 0;border-bottom:1px solid #26364a}}.metric-row strong{{color:#7c3aed}}.right{{text-align:right}}.bar-row{{display:grid;grid-template-columns:52px 1fr 36px;gap:10px;align-items:center;margin:9px 0}}.bar{{height:12px;background:#1e293b;border-radius:999px;overflow:hidden}}.bar i{{display:block;height:100%;background:#7c3aed}}@media(max-width:1100px){{.kpis,.command-grid,.grid4{{grid-template-columns:1fr 1fr}}}}@media(max-width:760px){{.grid,.grid3,.grid4,.kpis,.command-grid{{grid-template-columns:1fr}}}}</style></head>
<body><div class="wrap"><div class="top"><div><h1>Replau Ops Dashboard</h1><p>{esc(h["checked_at"])} - auto-refresh 20s</p></div><span class="badge {cls}">{esc(h["overall"])}</span></div>{erp_nav(req)}{flash_html}
<div class="card hero"><div class="top"><div><h2>Restaurant Management Today</h2><p>{esc(summary_day.get("label"))} · {esc(summary_day.get("timezone"))}</p></div><a class="btn" href="/api/business-summary{token_query(req)}" target="_blank">Open JSON</a></div>{summary_note}<div class="kpis"><div class="kpi"><div class="label">Sales booked</div><div class="value">{money(bsum.get("revenue"))}</div></div><div class="kpi"><div class="label">Orders</div><div class="value">{esc(bsum.get("orders",0))}</div></div><div class="kpi"><div class="label">Average ticket</div><div class="value">{money(bsum.get("avg_ticket"))}</div></div><div class="kpi"><div class="label">Items sold</div><div class="value">{esc(bsum.get("items_sold",0))}</div></div><div class="kpi"><div class="label">Open orders</div><div class="value">{esc(bsum.get("open_orders",0))}</div></div></div></div>
<div class="card"><div class="top"><div><h2>Owner Command Center</h2><p class="muted">Executive signals from sales, operations, payments, recipe stock, and margin coverage.</p></div><a class="btn" href="/api/owner-command{token_query(req)}" target="_blank">Open JSON</a></div><div class="command-grid">{command_cards(owner.get("cards",[]))}</div></div>
<div class="card"><div class="top"><div><h2>Purchase Agent</h2><p class="muted">Always-on shortage watch, ingredient behavior analysis, stock runway, and suggested purchase quantities.</p></div><a class="btn" href="/api/purchase-agent{token_query(req)}" target="_blank">Open JSON</a></div><div class="command-grid">{command_cards(purchase.get("cards",[]))}</div><div class="grid"><div><h3>Purchase Recommendations</h3>{purchase_recommendation_rows(purchase.get("recommendations",[]))}</div><div><h3>Ingredient Behavior</h3>{ingredient_behavior_rows(purchase.get("ingredient_behavior",[]))}</div></div></div>
<div class="card"><h2>Restaurant ERP Workspaces</h2><div class="grid4"><div><h3>Owner / Manager Workspace</h3><p class="muted">Exceptions, daily revenue, open operations, payment exposure, stock risk, and margin signals.</p><a class="btn" href="/{token_query(req)}">Open Ops</a></div><div><h3>Kitchen Workspace</h3><p class="muted">Production queue, timing pressure, order detail, and ready-to-picking handoff.</p><a class="btn" href="http://127.0.0.1:8791/" target="_blank">Kitchen</a></div><div><h3>Logistics Workspace</h3><p class="muted">Customer attention, picking, dispatch lanes, driver state, handoff, and delivery tracking.</p><a class="btn" href="http://127.0.0.1:8790/dashboard" target="_blank">Logistics</a> <a class="btn secondary" href="http://127.0.0.1:8790/ops/delivery" target="_blank">Delivery</a></div><div><h3>Cashier Workspace</h3><p class="muted">Payment proofs, pending value, verification, rejection, and logistics handback.</p><a class="btn" href="{esc(payment_proof_review_url())}" target="_blank">Payments</a></div><div><h3>Catalog Finance Workspace</h3><p class="muted">Products, public menu, recipe cost, active price, margin, and ingredient stock.</p><a class="btn" href="{esc(product_admin_url())}" target="_blank">Products</a> <a class="btn secondary" href="{esc(product_admin_url("costs"))}" target="_blank">Costs</a></div></div></div>
<div class="grid4"><div class="card"><h2>Order Flow</h2><p>{count_chips(bsum.get("status_counts",{}))}</p><h3>Kitchen</h3><p>{count_chips(bsum.get("kitchen_counts",{}))}</p><h3>Payments</h3><p>{count_chips(bsum.get("payment_counts",{}))}</p></div><div class="card"><h2>Top Products</h2>{product_cards(bsum.get("top_products",[]))}</div><div class="card"><h2>Rush Hours</h2>{rush_bars(bsum.get("rush_hours",[]))}</div></div>
<div class="grid"><div class="card"><h2>Low Stock Risk</h2>{stock_risk_rows(owner.get("stock_risks",[]))}</div><div class="card"><h2>Margin Signals</h2>{margin_signal_rows(owner.get("margin_rows",[]))}</div></div>
<div class="card"><h2>Manager Command Console</h2><div class="grid3"><div><h3>Ordering</h3><p>{status_badge}</p><p class="muted">Last update: {esc(rs.get("updated_at") or "not set")} by {esc(rs.get("updated_by"))}</p><form method="post" action="/api/restaurant-status{token_query(req)}"><label>Order intake</label><select name="accepting_orders"><option value="true" {"selected" if rs.get("accepting_orders") else ""}>Accept orders</option><option value="false" {"selected" if not rs.get("accepting_orders") else ""}>Pause orders</option></select><label>Internal reason</label><input name="reason" value="{esc(rs.get("reason"))}" placeholder="Closed, sold out, maintenance"><label>Customer message while paused</label><textarea name="customer_message">{esc(rs.get("customer_message"))}</textarea><br><br><button type="submit">Save ordering status</button></form></div><div><h3>Catalog</h3><p><strong>{esc(prod.get("active"))}</strong> active products<br><strong>{esc(prod.get("inactive"))}</strong> inactive products</p><p class="muted">Use Product Admin for availability, recipe costs, and low-stock alerts.</p><a class="btn" href="{esc(product_admin_url("costs"))}" target="_blank">Open Low Stock / Costs</a> <a class="btn secondary" href="{esc(product_admin_url())}" target="_blank">Open Product Admin</a></div><div><h3>Review Queues</h3><p>Payment proofs, failed WhatsApp outbox, pending emails, and kitchen state are below.</p><a class="btn" href="{esc(payment_proof_review_url())}" target="_blank">Open Payment Proofs</a> <a class="btn secondary" href="http://127.0.0.1:8790/dashboard" target="_blank">Open Logistics</a></div></div></div>
<div class="grid"><div class="card"><h2>Critical</h2><ul>{ul(h["critical"])}</ul></div><div class="card"><h2>Warnings</h2><ul>{ul(h["warnings"])}</ul></div></div>
<div class="card"><h2>WhatsApp Gateway</h2>{tbl(whatsapp_rows,["status","connected","gateway_health_ok","gateway_service_active","checked_at","last_connected_at","last_disconnect_at","seconds_since_disconnect","last_restart_at","last_restart_reason"])}</div>
<div class="card"><h2>Latest backup</h2><p>{backup_html}</p></div>
<div class="card"><h2>Services</h2>{tbl(service_rows,["service","active","enabled"])}</div>
<div class="grid"><div class="card"><h2>Ports</h2>{tbl(port_rows,["name","ok","host","port","error"])}</div><div class="card"><h2>URLs</h2>{tbl(url_rows,["name","ok","status","ms","url","error"])}</div></div>
<div class="card"><h2>Latest Orders</h2>{tbl(h["latest_orders"]["data"],["id","pedido_num","cliente_nombre","estado","total","created_at"])}</div>
<div class="card"><h2>Kitchen Queue</h2>{tbl(h["kitchen"]["data"],["id","pedido_num","cliente_nombre","kitchen_status","queue_minutes","queue_color","total"])}</div>
<div class="card"><h2>Recent Payment Proofs</h2>{tbl(h["payment_proofs"]["data"],["id","pedido_num","cliente_nombre","status","total","payment_status","created_at"])}</div>
<div class="card"><h2>Product Availability Sample</h2>{tbl(prod.get("sample",[]),["id","cdg_prod","nombre","active"])}</div>
<div class="card"><h2>Pending WhatsApp Outbox</h2>{tbl(h["pending_outbox"]["data"],["id","pedido_num","event_type","status","attempts","last_attempt_at","created_at","error_message"])}</div>
<div class="card"><h2>Error WhatsApp Outbox</h2>{tbl(h["error_outbox"]["data"],["id","pedido_num","event_type","status","attempts","last_attempt_at","created_at","error_message"])}</div>
<div class="card"><h2>Pending Emails</h2>{tbl(h["pending_emails"]["data"],["id","pedido_id","email_to","status","attempts","created_at","error_message"])}</div>
</div></body></html>''')
if __name__=="__main__":
    import uvicorn
    uvicorn.run("replau_health_dashboard:app",host=DASHBOARD_HOST,port=DASHBOARD_PORT,reload=False)
