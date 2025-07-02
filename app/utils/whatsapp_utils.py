import logging
from flask import current_app, jsonify
import json
import requests

from app.services.openai_service import generate_response, transcribe_audio
import re


def get_media_url_and_download(media_id):
    headers = {
        "Authorization": f"Bearer {current_app.config['ACCESS_TOKEN']}"
    }

    # Paso 1: obtener la URL temporal del archivo de audio
    meta_url = f"https://graph.facebook.com/v18.0/{media_id}"
    meta_response = requests.get(meta_url, headers=headers)
    
    if meta_response.status_code != 200:
        raise Exception(f"Error getting media URL: {meta_response.text}")

    file_url = meta_response.json().get("url")

    # Paso 2: descargar el archivo de audio
    audio_response = requests.get(file_url, headers=headers)
    if audio_response.status_code != 200:
        raise Exception(f"Error downloading audio file: {audio_response.text}")

    return audio_response.content 


def log_http_response(response):
    logging.info(f"Status: {response.status_code}")
    logging.info(f"Content-type: {response.headers.get('content-type')}")
    logging.info(f"Body: {response.text}")


def get_text_message_input(recipient, text):
    return json.dumps(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
    )


#def generate_response(response):
    # Return text in uppercase
    #return response.upper()

def process_audio_message(media_id, wa_id, name):
    audio_bytes = get_media_url_and_download(media_id)
    transcript_text = transcribe_audio(audio_bytes)

    response = generate_response(transcript_text, wa_id, name)
    response = process_text_for_whatsapp(response)
    data = get_text_message_input(wa_id, response)
    send_message(data)


def send_template_message(wa_id, nombre, telefono, correo, ciudad):
    url = f"https://graph.facebook.com/v22.0/{current_app.config['PHONE_NUMBER_ID']}/messages"
    headers = {
        "Authorization": f"Bearer {current_app.config['ACCESS_TOKEN']}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": wa_id,
        "type": "template",
        "template": {
            "name": "nueva_cotizacion",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": nombre},
                        {"type": "text", "text": telefono},
                        {"type": "text", "text": correo},
                        {"type": "text", "text": ciudad}
                    ]
                }
            ]
        }
    }
    
    response = requests.post(url, headers=headers, json=payload)
    print("STATUS:", response.status_code)
    print("RESPONSE:", response.text)
    return response




def send_message(data):
    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {current_app.config['ACCESS_TOKEN']}",
    }

    url = f"https://graph.facebook.com/{current_app.config['VERSION']}/{current_app.config['PHONE_NUMBER_ID']}/messages"

    try:
        response = requests.post(
            url, data=data, headers=headers, timeout=10
        )  # 10 seconds timeout as an example
        response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code
    except requests.Timeout:
        logging.error("Timeout occurred while sending message")
        return jsonify({"status": "error", "message": "Request timed out"}), 408
    except (
        requests.RequestException
    ) as e:  # This will catch any general request exception
        logging.error(f"Request failed due to: {e}")
        return jsonify({"status": "error", "message": "Failed to send message"}), 500
    else:
        # Process the response as normal
        log_http_response(response)
        return response


def process_text_for_whatsapp(text):
    # Remove brackets
    pattern = r"\【.*?\】"
    # Substitute the pattern with an empty string
    text = re.sub(pattern, "", text).strip()

    # Pattern to find double asterisks including the word(s) in between
    pattern = r"\*\*(.*?)\*\*"

    # Replacement pattern with single asterisks
    replacement = r"*\1*"

    # Substitute occurrences of the pattern with the replacement
    whatsapp_style_text = re.sub(pattern, replacement, text)

    return whatsapp_style_text


def process_whatsapp_message(body):
    message = body["entry"][0]["changes"][0]["value"]["messages"][0]
    msg_type = message.get("type")
    wa_id = body["entry"][0]["changes"][0]["value"]["contacts"][0]["wa_id"]
    name = body["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"]

    if msg_type == "audio":
        media_id = message["audio"]["id"]
        process_audio_message(media_id, wa_id, name)
        return

    if msg_type == "text":
        message_body = message["text"]["body"]
        response = generate_response(message_body, wa_id, name)

        # Codigo de inicio de cotización
        # Verificar si la respuesta contiene el trigger final
        if "FIN_COTIZACION" in response:
            # Intentar extraer los datos con expresiones regulares
            try:
                nombre = re.search(r"Nombre:\s*(.*)", response).group(1).strip()
            except AttributeError:
                nombre = "No proporcionado"

            try:
                telefono = re.search(r"Teléfono:\s*(.*)", response).group(1).strip()
            except AttributeError:
                telefono = "No proporcionado"

            try:
                correo = re.search(r"Correo:\s*(.*)", response).group(1).strip()
            except AttributeError:
                correo = "No proporcionado"

            try:
                ciudad = re.search(r"Ciudad:\s*(.*)", response).group(1).strip()
            except AttributeError:
                ciudad = "No proporcionado"

            # Enviar al encargado por WhatsApp
            encargado_wa_id = "51992669198"  # Cambia esto por el número real del encargado
            send_template_message(
                encargado_wa_id,
                nombre,
                telefono,
                correo,
                ciudad
            )


            # Confirmar al cliente
            confirmacion = "✅ Gracias, tu solicitud fue enviada al área de cotizaciones. Te contactaremos pronto."
            data_cliente = get_text_message_input(wa_id, confirmacion)
            send_message(data_cliente)

            # No seguir procesando
            return


        # Codigo de fin de cotizacion
        response = process_text_for_whatsapp(response)
        data = get_text_message_input(wa_id, response)
        send_message(data)



def is_valid_whatsapp_message(body):
    """
    Check if the incoming webhook event has a valid WhatsApp message structure.
    """
    return (
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("messages")
        and body["entry"][0]["changes"][0]["value"]["messages"][0]
    )
