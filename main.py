import tempfile
import re
import io
import xml.sax.saxutils as xml_escape
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
import edge_tts
import pycountry

app = FastAPI()

def format_locale_readable(locale_code: str) -> str:
    try:
        parts = locale_code.split("-")
        lang_code = parts[0].lower()
        country_code = parts[1].upper() if len(parts) > 1 else ""

        lang_obj = pycountry.languages.get(alpha_2=lang_code)
        lang_name = lang_obj.name if lang_obj else lang_code.upper()

        country_name = ""
        if country_code:
            country_obj = pycountry.countries.get(alpha_2=country_code)
            country_name = country_obj.name if country_obj else country_code

        return f"{lang_name} ({country_name})" if country_name else lang_name
    except Exception:
        return locale_code


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/voices")
async def get_voices():
    try:
        voices_mgr = await edge_tts.VoicesManager.create()
        grouped_voices = {}

        for v in voices_mgr.voices:
            locale = v.get("Locale", "Other")
            short_name = v.get("ShortName", "")
            gender = v.get("Gender", "Neutral")
            
            friendly = v.get("FriendlyName", "")
            match = re.search(r"Microsoft\s+([A-Za-z0-9]+)", friendly)
            speaker_name = match.group(1) if match else short_name.split("-")[-1].replace("Neural", "")

            readable_region = format_locale_readable(locale)

            if readable_region not in grouped_voices:
                grouped_voices[readable_region] = []

            grouped_voices[readable_region].append({
                "short_name": short_name,
                "label": f"{speaker_name} ({gender})"
            })

        return dict(sorted(grouped_voices.items()))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/synthesize")
async def synthesize_speech(
    text: str = Form(...), 
    voice: str = Form("en-US-AriaNeural"),
    rate: str = Form("+0%"),
    pitch: str = Form("+0Hz"),
    volume: str = Form("+0%"),
    style: str = Form("general"),
    audio_format: str = Form("mp3")
):
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    
    ext = ".wav" if audio_format == "wav" else ".mp3"
    media_type = "audio/wav" if audio_format == "wav" else "audio/mpeg"
    
    # Escape special XML characters in text (&, <, >, etc.)
    safe_text = xml_escape.escape(text)

    out_stream = io.BytesIO()

    # Attempt 1: Generate speech with emotional style using SSML
    if style and style != "general":
        ssml_text = f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xmlns:mstts='https://www.w3.org/2001/10/synthesis/schema' xml:lang='en-US'>
            <voice name='{voice}'>
                <mstts:express-as style='{style}'>
                    <prosody rate='{rate}' pitch='{pitch}' volume='{volume}'>
                        {safe_text}
                    </prosody>
                </mstts:express-as>
            </voice>
        </speak>"""
        
        try:
            communicate = edge_tts.Communicate(ssml_text, voice, is_ssml=True)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    out_stream.write(chunk["data"])
        except Exception:
            # Fallback if the selected voice doesn't support the style
            out_stream = io.BytesIO()
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch, volume=volume)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    out_stream.write(chunk["data"])
    else:
        # Standard voice generation (No emotion)
        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch, volume=volume)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                out_stream.write(chunk["data"])

    out_stream.seek(0)
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
        tmp_file.write(out_stream.read())
        output_path = tmp_file.name

    return FileResponse(output_path, media_type=media_type, filename=f"speech{ext}")