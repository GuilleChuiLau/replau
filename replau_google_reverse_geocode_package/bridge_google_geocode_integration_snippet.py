# Add near the top of bridge.py:
from google_reverse_geocode import format_detected_address_for_whatsapp

# In your location handling code, after extracting latitude and longitude:
geo = format_detected_address_for_whatsapp(latitude, longitude)
detected_address = geo["detected_address"]
maps_url = geo["maps_url"]
reply_text = geo["message_text"]
next_state = "WAITING_ADDRESS_CONFIRMATION"

# Save in your conversation draft/state:
pedido_borrador["latitude"] = latitude
pedido_borrador["longitude"] = longitude
pedido_borrador["detected_address"] = detected_address
pedido_borrador["maps_url"] = maps_url

# When customer replies SI, call confirmar_pedido_whatsapp with:
# p_detected_address = detected_address
# p_confirmed_address = detected_address
# If customer types a corrected address:
# p_detected_address = detected_address
# p_confirmed_address = customer_text
