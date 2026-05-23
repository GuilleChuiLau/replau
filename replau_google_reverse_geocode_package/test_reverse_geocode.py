#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from google_reverse_geocode import reverse_geocode_google, format_detected_address_for_whatsapp
lat=float(sys.argv[1]) if len(sys.argv)>1 else -12.119938
lng=float(sys.argv[2]) if len(sys.argv)>2 else -76.99172
print(f"Testing reverse geocode for: {lat}, {lng}")
result=reverse_geocode_google(lat,lng)
print("\nRaw helper result:")
print(json.dumps(result,indent=2,ensure_ascii=False))
print("\nWhatsApp-friendly response:")
print(json.dumps(format_detected_address_for_whatsapp(lat,lng),indent=2,ensure_ascii=False))
raise SystemExit(0 if result.get('ok') else 1)
