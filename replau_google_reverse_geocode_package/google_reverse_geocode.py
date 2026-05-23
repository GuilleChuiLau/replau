#!/usr/bin/env python3
from __future__ import annotations
import os
from typing import Any, Dict, Optional
import requests

GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
GOOGLE_GEOCODE_LANGUAGE = os.environ.get("GOOGLE_GEOCODE_LANGUAGE", "es").strip() or "es"
GOOGLE_GEOCODE_REGION = os.environ.get("GOOGLE_GEOCODE_REGION", "pe").strip() or "pe"
GOOGLE_GEOCODE_TIMEOUT = int(os.environ.get("GOOGLE_GEOCODE_TIMEOUT", "15"))


def reverse_geocode_google(latitude: float, longitude: float) -> Dict[str, Any]:
    if not GOOGLE_MAPS_API_KEY:
        return {"ok": False, "formatted_address": None, "place_id": None, "types": [], "status": "NO_API_KEY", "error": "GOOGLE_MAPS_API_KEY is not configured", "raw": None}
    try:
        lat=float(latitude); lng=float(longitude)
    except Exception:
        return {"ok": False, "formatted_address": None, "place_id": None, "types": [], "status": "INVALID_COORDINATES", "error": f"Invalid coordinates: latitude={latitude}, longitude={longitude}", "raw": None}
    url="https://maps.googleapis.com/maps/api/geocode/json"
    params={"latlng":f"{lat},{lng}","language":GOOGLE_GEOCODE_LANGUAGE,"region":GOOGLE_GEOCODE_REGION,"key":GOOGLE_MAPS_API_KEY}
    try:
        r=requests.get(url,params=params,timeout=GOOGLE_GEOCODE_TIMEOUT)
        r.raise_for_status(); data=r.json()
    except Exception as exc:
        return {"ok": False, "formatted_address": None, "place_id": None, "types": [], "status": "REQUEST_ERROR", "error": f"{type(exc).__name__}: {exc}", "raw": None}
    status=data.get('status'); results=data.get('results') or []
    if status!='OK':
        return {"ok": False, "formatted_address": None, "place_id": None, "types": [], "status": status, "error": data.get('error_message') or f"Google Geocoding status: {status}", "raw": data}
    if not results:
        return {"ok": False, "formatted_address": None, "place_id": None, "types": [], "status":"ZERO_RESULTS", "error":"No address results found", "raw": data}
    best=results[0]
    return {"ok": True, "formatted_address": best.get('formatted_address'), "place_id": best.get('place_id'), "types": best.get('types',[]), "status": status, "error": None, "raw": best}


def format_detected_address_for_whatsapp(latitude: float, longitude: float, fallback_text: Optional[str]=None) -> Dict[str, Any]:
    geocode=reverse_geocode_google(latitude, longitude)
    maps_url=f"https://www.google.com/maps?q={latitude},{longitude}"
    if geocode.get('ok') and geocode.get('formatted_address'):
        detected=geocode['formatted_address']
        msg=("Detecté esta dirección:\n\n"+detected+"\n\n¿Es correcta? Responde SI para confirmar o escribe la dirección corregida.")
        return {"ok": True, "detected_address": detected, "message_text": msg, "maps_url": maps_url, "geocode": geocode}
    detected=fallback_text or f"Ubicación enviada: {latitude}, {longitude}"
    msg=("Recibí tu ubicación, pero no pude obtener una dirección exacta automáticamente.\n\n"+detected+"\n"+maps_url+"\n\nPor favor escribe tu dirección completa para confirmar el pedido.")
    return {"ok": False, "detected_address": detected, "message_text": msg, "maps_url": maps_url, "geocode": geocode}
