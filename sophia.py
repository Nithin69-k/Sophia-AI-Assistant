# ---------------------------------------------------------
# Sophia - Personal Virtual Assistant (GUI + Voice)
# ---------------------------------------------------------
# Fixed: Thread-safe GUI logging, TTS non-blocking,
#        safe eval, API timeout, hardcoded key removed,
#        safe_exit guard, Enter key binding, dialog thread safety
# ---------------------------------------------------------

import os
import re
import ast
import operator
import threading
import datetime
import wikipedia
import pywhatkit
import pyttsx3
import requests
import pyjokes
import psutil
import speech_recognition as sr
import subprocess
import webbrowser
from dotenv import load_dotenv
import pyautogui
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import tkinter as tk
from tkinter import scrolledtext, simpledialog, messagebox, ttk
from bs4 import BeautifulSoup

# ================== LOAD ENV ==================
load_dotenv()
ASSISTANT_NAME = "Sophia"

# Corrected: Get values from env vars instead of using values as env keys
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
if not WEATHER_API_KEY:
    raise ValueError("❌ WEATHER_API_KEY not set in .env file. Please add it.")

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")

# ================== INIT TTS ==================
engine = pyttsx3.init()
engine.setProperty('rate', 170)

# Lock to prevent concurrent TTS calls
_tts_lock = threading.Lock()

def _speak_async(text, lang_code="en-IN"):
    """Run TTS in a background thread so the GUI never freezes.
    First tries keyless Google Translate TTS for natural multilingual output.
    If it fails or is offline, falls back to Windows SAPI5 SpVoice.
    """
    with _tts_lock:
        lang_prefix = lang_code.split("-")[0]
        url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl={lang_prefix}&client=tw-ob&q={requests.utils.quote(text)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        temp_file = os.path.abspath("temp_tts_wmp.mp3")
        temp_file_escaped = temp_file.replace("'", "''")
        success = False
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                with open(temp_file, "wb") as f:
                    f.write(res.content)
                
                # WMP COM Object play code
                ps_code = f"""
                $wmp = New-Object -ComObject WMPlayer.OCX
                $wmp.settings.volume = 100
                $wmp.URL = '{temp_file_escaped}'
                $waited = 0
                while ($wmp.playState -eq 9 -and $waited -lt 50) {{
                    Start-Sleep -Milliseconds 100
                    $waited++
                }}
                while ($wmp.playState -eq 3 -or $wmp.playState -eq 6) {{
                    Start-Sleep -Milliseconds 100
                }}
                """
                subprocess.run(["powershell", "-Command", ps_code], shell=True)
                success = True
        except Exception as e:
            print(f"Google TTS failed, falling back to SAPI5: {e}")
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

        if not success:
            try:
                text_escaped = text.replace("'", "''")
                ps_code = f"(New-Object -ComObject SAPI.SpVoice).Speak('{text_escaped}')"
                subprocess.run(["powershell", "-Command", ps_code], shell=True)
            except Exception as e:
                print(f"SAPI5 fallback error: {e}")

# Global translation helpers
def translate_text(text, source_lang, target_lang):
    if source_lang == target_lang:
        return text
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={source_lang}&tl={target_lang}&dt=t&q={requests.utils.quote(text)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5).json()
        if res and res[0] and res[0][0]:
            return res[0][0][0]
    except Exception as e:
        print(f"Translation error: {e}")
    return text

def get_selected_lang_code():
    try:
        if 'lang_combobox' in globals() and lang_combobox is not None:
            lang_name = lang_combobox.get()
            return SUPPORTED_LANGUAGES.get(lang_name, "en-IN")
    except Exception:
        pass
    return "en-IN"

def speak(text):
    """Speak response in the user's selected language, falling back to English if translation fails."""
    lang_code = get_selected_lang_code()
    lang_prefix = lang_code.split("-")[0]
    
    if lang_prefix != "en":
        translated_text = translate_text(text, "en", lang_prefix)
        log(f"🤖 {translated_text}")
        speech_text = translated_text
    else:
        log(f"🤖 {text}")
        speech_text = text
        
    threading.Thread(target=_speak_async, args=(speech_text, lang_code), daemon=True).start()


# ================== THREAD-SAFE LOGGING ==================
def log(message):
    """Always update the GUI from the main thread via root.after()."""
    def _update():
        gui_log.configure(state='normal')
        if message.startswith("🤖"):
            gui_log.insert(tk.END, message + "\n", "robot")
        elif message.startswith("💬") or message.startswith("🗣️"):
            gui_log.insert(tk.END, message + "\n", "user")
        else:
            gui_log.insert(tk.END, message + "\n", "system")
        gui_log.configure(state='disabled')
        gui_log.yview(tk.END)
    root.after(0, _update)


def set_status(text, color):
    try:
        if 'status_label' in globals() and status_label is not None:
            root.after(0, lambda: status_label.config(text=text, fg=color))
    except Exception:
        pass


# ================== VOICE INPUT ==================
# ================== VOICE INPUT ==================
def record_audio_sounddevice(duration=5, chunk_duration=0.5):
    """Record audio from the default microphone using sounddevice.
    Records for a fixed duration and normalizes the signal to max volume.
    """
    import sounddevice as sd
    import numpy as np
    import io
    import wave

    try:
        device_info = sd.query_devices(sd.default.device[0], 'input')
        sample_rate = int(device_info['default_samplerate'])
        channels = min(2, int(device_info['max_input_channels']))
    except Exception as e:
        log(f"⚠️ Failed to query default microphone: {e}")
        return None

    chunk_samples = int(sample_rate * chunk_duration)
    recorded_chunks = []
    
    log("🎤 Listening...")
    
    try:
        with sd.InputStream(samplerate=sample_rate, channels=channels, dtype='float32') as stream:
            max_chunks = int(duration / chunk_duration)
            for _ in range(max_chunks):
                chunk, overflowed = stream.read(chunk_samples)
                recorded_chunks.append(chunk)
    except Exception as e:
        log(f"⚠️ Microphone error: {e}")
        return None
        
    if not recorded_chunks:
        return None
        
    # Concatenate all chunks
    audio_data = np.concatenate(recorded_chunks, axis=0)
    
    # Convert to mono if it is stereo
    if channels > 1:
        audio_data = audio_data[:, 0]
    else:
        audio_data = audio_data.flatten()
        
    # Linear interpolation resampling to 16000 Hz for optimal speech recognition
    target_sample_rate = 16000
    if sample_rate != target_sample_rate:
        num_samples = len(audio_data)
        target_num_samples = int(num_samples * target_sample_rate / sample_rate)
        audio_data = np.interp(
            np.linspace(0, num_samples - 1, target_num_samples),
            np.arange(num_samples),
            audio_data
        )
        sample_rate = target_sample_rate
    
    # Normalize volume: scale peak amplitude to 0.9 to boost quiet voices
    max_val = np.max(np.abs(audio_data))
    if max_val > 0.001:
        audio_data = (audio_data / max_val) * 0.9
    
    # Convert float32 [-1.0, 1.0] to int16 [-32768, 32767]
    audio_data_int16 = (audio_data * 32767).astype(np.int16)
    
    # Write to an in-memory WAV file
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wf:
        wf.setnchannels(1) # mono
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data_int16.tobytes())
        
    wav_io.seek(0)
    return wav_io


def listen_command(timeout=5, phrase_time_limit=5):
    # Set status to recording
    set_status("● Recording Voice...", "#f43f5e")
    
    wav_data = record_audio_sounddevice(duration=5)
    
    # Set status back to listening
    set_status("● Online & Listening", "#10b981")
    
    if not wav_data:
        return None
        
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(wav_data) as source:
            audio = recognizer.record(source)
            
            # Use user-selected language for recognition
            lang_code = get_selected_lang_code()
            lang_name = lang_combobox.get() if ('lang_combobox' in globals() and lang_combobox is not None) else "English (India)"
            
            cmd = recognizer.recognize_google(audio, language=lang_code).strip()
            
            # Translate if the language is not English
            lang_prefix = lang_code.split("-")[0]
            if lang_prefix != "en":
                cmd_eng = translate_text(cmd, lang_prefix, "en")
                log(f"🗣️ You said ({lang_name}): {cmd}")
                log(f"➡️ Translated: {cmd_eng}")
                return cmd_eng.lower()
            else:
                log(f"🗣️ You said: {cmd}")
                return cmd.lower()
    except sr.UnknownValueError:
        log("❌ Couldn't understand the audio.")
    except sr.RequestError:
        log("⚠️ Speech recognition service unavailable.")
    except Exception as e:
        log(f"⚠️ Audio processing error: {e}")
    return None


def safe_exit():
    try:
        engine.stop()
    except Exception:
        pass
    root.destroy()
    os._exit(0)


# ================== BASIC FEATURES ==================
def get_time():
    return datetime.datetime.now().strftime("%H:%M:%S")

def get_date():
    return datetime.date.today().strftime("%B %d, %Y")

def play_youtube(query):
    search_term = re.sub(r"(play|on youtube|youtube)", "", query, flags=re.I).strip()
    if search_term:
        speak(f"Playing {search_term} on YouTube 🎶")
        pywhatkit.playonyt(search_term)
    else:
        speak("What should I play on YouTube?")


# ================== 🌍 GLOBAL WEATHER ==================
def get_weather(city=None):
    try:
        if not city:
            # Try to get the user's current city based on IP address
            try:
                ip_res = requests.get("http://ip-api.com/json", timeout=3).json()
                if ip_res.get("status") == "success":
                    city = ip_res.get("city")
                    log(f"📍 Auto-detected location: {city}")
            except Exception:
                pass
                
            if not city:
                speak("Please tell me the city name.")
                city = listen_command()
                if not city:
                    return "I didn't catch the city name."

        url = (
            f"http://api.openweathermap.org/data/2.5/weather"
            f"?q={city}&appid={WEATHER_API_KEY}&units=metric"
        )
        res = requests.get(url, timeout=5).json()

        if res.get("cod") != 200:
            return f"City '{city}' not found. Please check the spelling."

        weather   = res["weather"][0]["description"].capitalize()
        temp      = res["main"]["temp"]
        feels     = res["main"].get("feels_like", "N/A")
        humidity  = res["main"]["humidity"]
        wind      = res["wind"]["speed"]
        country   = res["sys"].get("country", "N/A")

        return (
            f"🌍 Weather in {city.title()}, {country}: {weather}. "
            f"Temperature: {temp}°C (feels like {feels}°C). "
            f"Humidity: {humidity}%. Wind speed: {wind} m/s."
        )
    except requests.exceptions.Timeout:
        return "Weather service timed out. Please try again."
    except Exception as e:
        return f"Weather service unavailable: {e}"


def search_google_cse(query):
    api_key = os.getenv("GOOGLE_API_KEY")
    cse_cx = os.getenv("GOOGLE_CSE_CX")
    if not api_key or not cse_cx:
        return ""
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "cx": cse_cx,
            "q": query
        }
        res = requests.get(url, params=params, timeout=5).json()
        if "items" in res:
            snippets = []
            for item in res["items"][:4]:
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                if title or snippet:
                    snippets.append(f"{title}: {snippet}")
            if snippets:
                return "\n".join(snippets)
    except Exception as e:
        print(f"Google CSE error: {e}")
    return ""


def search_google_news(query):
    try:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "xml")
            items = soup.find_all("item")
            snippets = []
            for item in items[:4]:
                title = item.find("title").text if item.find("title") else ""
                desc = item.find("description").text if item.find("description") else ""
                desc_text = BeautifulSoup(desc, "html.parser").text.strip()
                title_clean = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
                if title_clean:
                    snippets.append(f"News: {title_clean}. Details: {desc_text}")
            if snippets:
                return "\n".join(snippets)
    except Exception as e:
        print(f"Google News RSS error: {e}")
    return ""


def search_yahoo(query):
    try:
        url = f"https://search.yahoo.com/search?q={requests.utils.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            snippets = []
            for div in soup.find_all('div', class_='compText')[:4]:
                text = div.text.strip()
                text = re.sub(r'\s+', ' ', text)
                if text:
                    snippets.append(text)
            if snippets:
                return "\n".join(snippets)
    except Exception as e:
        print(f"Yahoo search error: {e}")
    return ""


def search_bing(query):
    try:
        url = f"https://www.bing.com/search?q={requests.utils.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            snippets = []
            for li in soup.find_all('li', class_='b_algo')[:3]:
                text = li.text.strip()
                # Clean up spacing
                text = re.sub(r'\s+', ' ', text)
                snippets.append(text)
            if snippets:
                return "\n".join(snippets)
    except Exception as e:
        print(f"Bing search error: {e}")
    return ""


def ask_gemini(prompt):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        res = requests.post(url, headers=headers, json=payload, timeout=8).json()
        if "candidates" in res and res["candidates"]:
            content = res["candidates"][0]["content"]["parts"][0]["text"]
            content = re.sub(r"\*+", "", content) # Remove markdown asterisks for clean text-to-speech
            return content.strip()
    except Exception as e:
        print(f"Gemini API error: {e}")
    return None


def ask_free_ai(prompt):
    try:
        url = "https://devtoolbox-api.devtoolbox-api.workers.dev/ai/generate"
        res = requests.post(url, json={"prompt": prompt}, timeout=10).json()
        if "response" in res:
            content = res["response"]
            content = re.sub(r"\*+", "", content) # Remove markdown asterisks
            return content.strip()
    except Exception as e:
        print(f"Free AI API error: {e}")
    return None


def search_info(query):
    # 1. Try Google Custom Search (CSE) first (if configured)
    search_context = search_google_cse(query)
    
    # 2. Try Google News RSS (always available, keyless real-time Google data)
    if not search_context:
        search_context = search_google_news(query)
        
    # 3. Fallback to Yahoo if Google fails
    if not search_context:
        search_context = search_yahoo(query)
    
    # 4. Fallback to Bing if Yahoo fails
    if not search_context:
        search_context = search_bing(query)
    
    # Construct the RAG prompt
    prompt = (
        f"You are Sophia, a helpful personal assistant. Answer the user's question based on the "
        f"following real-time web search results (or your own knowledge if search results aren't helpful). "
        f"Be direct, conversational, and concise (under 3 sentences).\n\n"
        f"Web Search Results:\n{search_context}\n\n"
        f"Question: {query}"
    )

    # 1. Try Gemini first if key is present
    gemini_response = ask_gemini(prompt)
    if gemini_response:
        return gemini_response

    # 2. Try Free AI next (keyless Llama model)
    free_ai_response = ask_free_ai(prompt)
    if free_ai_response:
        return free_ai_response

    # 3. Try Wikipedia search
    try:
        search_results = wikipedia.search(query)
        if search_results:
            for result in search_results:
                try:
                    summary = wikipedia.summary(result, sentences=2)
                    return f"According to Wikipedia: {summary}"
                except wikipedia.exceptions.DisambiguationError as e:
                    if e.options:
                        try:
                            summary = wikipedia.summary(e.options[0], sentences=2)
                            return f"According to Wikipedia: {summary}"
                        except Exception:
                            continue
                except wikipedia.exceptions.PageError:
                    continue
    except Exception:
        pass

    return "I couldn't find an answer to that question on the web. Please try rephrasing."

def tell_joke():
    return pyjokes.get_joke()


# ================== SAFE CALCULATOR ==================
_SAFE_OPS = {
    ast.Add:  operator.add,
    ast.Sub:  operator.sub,
    ast.Mult: operator.mul,
    ast.Div:  operator.truediv,
    ast.USub: operator.neg,
}

def _safe_eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    elif isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval_node(node.left), _safe_eval_node(node.right))
    elif isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval_node(node.operand))
    raise ValueError(f"Unsupported operation: {ast.dump(node)}")

def calculate(expression):
    try:
        expression = (
            expression
            .replace("plus", "+")
            .replace("minus", "-")
            .replace("times", "*")
            .replace("divided by", "/")
        )
        tree = ast.parse(expression, mode='eval')
        result = _safe_eval_node(tree.body)
        return str(result)
    except ZeroDivisionError:
        return "Cannot divide by zero."
    except Exception:
        return "Calculation error. Please use a valid math expression."


def system_info():
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory().percent
    return f"CPU usage: {cpu}%, RAM usage: {ram}%"

def set_reminder(message, seconds):
    def reminder_thread():
        time.sleep(seconds)
        speak(f"Reminder: {message}")
        try:
            pyautogui.alert(message, "Reminder")
        except Exception:
            pass
    threading.Thread(target=reminder_thread, daemon=True).start()
    return f"Reminder set for {seconds} seconds from now."

def take_screenshot(filename=None):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = filename if filename else f"screenshot_{timestamp}.png"
    if not name.lower().endswith(".png"):
        name += ".png"
    path = os.path.join(os.getcwd(), name)
    try:
        image = pyautogui.screenshot()
        image.save(path)
        return f"Saved screenshot: {path}"
    except Exception as e:
        return f"Screenshot failed: {e}"


# ================== WEB + DESKTOP APPS ==================
WEB_APPS = {
    "amazon":     "https://www.amazon.in",
    "flipkart":   "https://www.flipkart.com",
    "myntra":     "https://www.myntra.com",
    "ebay":       "https://www.ebay.com",
    "snapdeal":   "https://www.snapdeal.com",
    "meesho":     "https://www.meesho.com",
    "ajio":       "https://www.ajio.com",
    "tatacliq":   "https://www.tatacliq.com",
    "paytm mall": "https://paytmmall.com",
    "zomato":     "https://www.zomato.com",
    "swiggy":     "https://www.swiggy.com",
    "ubereats":   "https://www.ubereats.com",
    "dominos":    "https://www.dominos.co.in",
    "facebook":   "https://www.facebook.com",
    "instagram":  "https://www.instagram.com",
    "twitter":    "https://twitter.com",
    "linkedin":   "https://www.linkedin.com",
    "youtube":    "https://www.youtube.com",
    "google docs":    "https://docs.google.com",
    "google sheets":  "https://sheets.google.com",
    "google slides":  "https://slides.google.com",
    "notion":     "https://www.notion.so",
    "chrome":     "https://www.google.com",
}

DESKTOP_APPS = {
    "notepad":    "notepad.exe",
    "calculator": "calc.exe",
    "paint":      "mspaint.exe",
    "chrome":     r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "edge":       r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
}

def open_website(name):
    url = WEB_APPS.get(name.lower())
    if url:
        webbrowser.open(url)
        return f"Opened {name}"
    elif re.match(r"https?://", name):
        webbrowser.open(name)
        return f"Opened {name}"
    return f"No mapping found for '{name}'"

def open_desktop_app(name):
    path = DESKTOP_APPS.get(name.lower())
    try:
        subprocess.Popen(path if path else [name])
        return f"Opened {name}"
    except Exception as e:
        return f"Failed to open {name}: {e}"


# ================== WHATSAPP ==================
def whatsapp_flow():
    speak("Please say the number with country code and your message.")
    details = listen_command()
    if details:
        try:
            parts = details.split(" ", 1)
            phone, message = parts[0], parts[1]
            speak("Do you want to schedule it? Say yes or no.")
            confirm = listen_command()
            if confirm and "yes" in confirm:
                speak("Please say the hour in 24-hour format.")
                hour_cmd = listen_command()
                speak("Please say the minute.")
                min_cmd = listen_command()
                try:
                    pywhatkit.sendwhatmsg(phone, message, int(hour_cmd), int(min_cmd))
                    speak("WhatsApp message scheduled.")
                except Exception as e:
                    speak(f"Failed to schedule: {e}")
            else:
                pywhatkit.sendwhatmsg_instantly(phone, message)
                speak("WhatsApp message sent instantly.")
        except Exception as e:
            speak(f"Error in WhatsApp flow: {e}")
    else:
        speak("I couldn't capture the details.")


# ================== EMAIL ==================
def send_email(to_email, subject, body):
    try:
        msg = MIMEMultipart()
        msg['From']    = EMAIL_ADDRESS
        msg['To']      = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        return "Email sent successfully."
    except Exception as e:
        return f"Email failed: {e}"

def email_flow_on_main_thread():
    to = simpledialog.askstring("Email", "Recipient email:")
    subject = simpledialog.askstring("Email", "Subject:")
    body = simpledialog.askstring("Email", "Body:")
    if to and subject and body:
        speak(send_email(to, subject, body))
    else:
        speak("Email cancelled — missing fields.")


# ================== COMMAND HANDLER ==================
def handle_command(command):
    if not command:
        return
    command = command.lower().strip()

    if "time" in command:
        speak("The time is " + get_time())

    elif "date" in command:
        speak("Today's date is " + get_date())

    elif "play" in command:
        play_youtube(command)

    elif "weather" in command:
        match = re.search(r"weather in ([a-zA-Z\s]+)", command)
        if match:
            city = match.group(1).strip()
            speak(get_weather(city))
        else:
            speak(get_weather())

    elif any(x in command for x in ["who is", "what is", "define", "tell me about"]):
        query = re.sub(r"(who is|what is|define|tell me about)", "", command).strip()
        speak(search_info(query))

    elif "joke" in command:
        speak(tell_joke())

    elif any(op in command for op in ["+", "-", "plus", "minus", "times", "divided by", "*", "/"]):
        speak("The result is " + calculate(command))

    elif "system" in command:
        speak(system_info())

    elif "remind me" in command:
        m = re.search(r"remind me to (.+) in (\d+)", command)
        if m:
            msg = m.group(1)
            sec = int(m.group(2))
            speak(set_reminder(msg, sec))
        else:
            speak("Please say: remind me to [task] in [seconds].")

    elif "screenshot" in command:
        speak(take_screenshot())

    elif "whatsapp" in command:
        threading.Thread(target=whatsapp_flow, daemon=True).start()

    elif "email" in command:
        root.after(0, email_flow_on_main_thread)

    elif command.startswith("open "):
        target = command.replace("open ", "", 1).strip()
        if target in DESKTOP_APPS:
            speak(open_desktop_app(target))
        else:
            speak(open_website(target))

    elif command in ["exit", "quit", "stop", "bye"]:
        speak("Goodbye!")
        root.after(500, safe_exit)

    else:
        # Fallback to search_info which retrieves real-time Bing search results as context
        speak(search_info(command))


# ================== GUI CALLBACKS ==================
def process_text():
    cmd = gui_entry.get().strip()
    gui_entry.delete(0, tk.END)
    if cmd:
        lang_code = get_selected_lang_code()
        lang_prefix = lang_code.split("-")[0]
        lang_name = lang_combobox.get() if ('lang_combobox' in globals() and lang_combobox is not None) else "English (India)"
        
        # Translate to English if not already English
        if lang_prefix != "en":
            cmd_eng = translate_text(cmd, lang_prefix, "en")
            log(f"💬 You typed ({lang_name}): {cmd}")
            log(f"➡️ Translated: {cmd_eng}")
            cmd_to_handle = cmd_eng
        else:
            log(f"💬 You typed: {cmd}")
            cmd_to_handle = cmd
            
        threading.Thread(target=handle_command, args=(cmd_to_handle,), daemon=True).start()

def process_voice():
    cmd = listen_command()
    if cmd:
        threading.Thread(target=handle_command, args=(cmd,), daemon=True).start()


# ================== TKINTER SETUP ==================
root = tk.Tk()
root.title(f"{ASSISTANT_NAME} - Multilingual AI Assistant")
root.geometry("900x620")
root.configure(bg="#0f172a") # Slate 900
root.resizable(True, True)

# Set the protocol for window close button (X)
root.protocol("WM_DELETE_WINDOW", safe_exit)

# Styling for modern components
style = ttk.Style()
style.theme_use('clam')
style.configure(
    "TCombobox",
    fieldbackground="#1e293b",
    background="#334155",
    foreground="#f8fafc",
    arrowcolor="#f8fafc",
    relief=tk.FLAT
)

# Custom header
header_frame = tk.Frame(root, bg="#1e293b", height=75) # Slate 800
header_frame.pack(fill=tk.X)
header_frame.pack_propagate(False)

# Left accent colored bar in header
accent_bar = tk.Frame(header_frame, bg="#8b5cf6", width=5) # Purple 500
accent_bar.pack(side=tk.LEFT, fill=tk.Y)

# Title & subtitle container
title_container = tk.Frame(header_frame, bg="#1e293b")
title_container.pack(side=tk.LEFT, padx=15, pady=8)

title_label = tk.Label(title_container, text="SOPHIA AI", font=("Segoe UI", 16, "bold"), fg="#f8fafc", bg="#1e293b")
title_label.pack(anchor="w")

status_label = tk.Label(title_container, text="● Online & Listening", font=("Segoe UI", 9), fg="#10b981", bg="#1e293b")
status_label.pack(anchor="w")

# Right container in header for Language Selection
lang_container = tk.Frame(header_frame, bg="#1e293b")
lang_container.pack(side=tk.RIGHT, padx=15, pady=15)

lang_label = tk.Label(lang_container, text="Language:", font=("Segoe UI", 10, "bold"), fg="#94a3b8", bg="#1e293b")
lang_label.pack(side=tk.LEFT, padx=5)

SUPPORTED_LANGUAGES = {
    "English (India)": "en-IN",
    "English (US)": "en-US",
    "Kannada (ಕನ್ನಡ)": "kn-IN",
    "Hindi (हिन्दी)": "hi-IN",
    "Telugu (తెలుగు)": "te-IN",
    "Tamil (தமிழ்)": "ta-IN",
    "Malayalam (മലയാളം)": "ml-IN",
    "Spanish (Español)": "es-ES",
    "French (Français)": "fr-FR",
    "German (Deutsch)": "de-DE",
}

lang_combobox = ttk.Combobox(lang_container, values=list(SUPPORTED_LANGUAGES.keys()), state="readonly", width=18, font=("Segoe UI", 10))
lang_combobox.set("English (India)")
lang_combobox.pack(side=tk.LEFT, padx=5)

# Main Body Split: Left Chat, Right Sidebar
body_frame = tk.Frame(root, bg="#0f172a")
body_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

# Chat log container (Left)
chat_container = tk.Frame(body_frame, bg="#0f172a")
chat_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

# Scrolled Text log area
gui_log = scrolledtext.ScrolledText(
    chat_container, 
    state='disabled', 
    wrap='word', 
    font=("Consolas", 11), 
    bg="#020617", # Slate 950
    fg="#e2e8f0", # Slate 200
    insertbackground="#a78bfa", 
    relief=tk.FLAT, 
    bd=0,
    highlightthickness=0
)
gui_log.pack(fill=tk.BOTH, expand=True)

# Configure text tags
gui_log.tag_config("user", foreground="#60a5fa") # Blue 400
gui_log.tag_config("robot", foreground="#c084fc", font=("Consolas", 11, "bold")) # Purple 400
gui_log.tag_config("system", foreground="#64748b", font=("Consolas", 10, "italic")) # Slate 500

# Right Sidebar (Quick Tools)
sidebar_frame = tk.Frame(body_frame, bg="#1e293b", width=200, bd=1, relief=tk.FLAT) # Slate 800
sidebar_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(15, 0))
sidebar_frame.pack_propagate(False)

sidebar_title = tk.Label(sidebar_frame, text="QUICK ACTIONS", font=("Segoe UI", 10, "bold"), fg="#94a3b8", bg="#1e293b", pady=10)
sidebar_title.pack()

# Helpers to trigger commands from buttons
def run_quick_command(cmd_text):
    log(f"💬 Clicked Action: {cmd_text}")
    threading.Thread(target=handle_command, args=(cmd_text.lower(),), daemon=True).start()

def clear_chat():
    gui_log.configure(state='normal')
    gui_log.delete('1.0', tk.END)
    gui_log.configure(state='disabled')
    log("🤖 Logs cleared. Ready for new commands.")

# Sidebar Buttons
btn_style = {
    "bg": "#334155", # Slate 700
    "fg": "#f8fafc",
    "activebackground": "#475569",
    "activeforeground": "#ffffff",
    "relief": tk.FLAT,
    "font": ("Segoe UI", 9, "bold"),
    "pady": 8,
    "cursor": "hand2"
}

tk.Button(sidebar_frame, text="🕒 Time & Date", command=lambda: run_quick_command("time"), **btn_style).pack(fill=tk.X, padx=10, pady=5)
tk.Button(sidebar_frame, text="🌦️ Check Weather", command=lambda: run_quick_command("weather"), **btn_style).pack(fill=tk.X, padx=10, pady=5)
tk.Button(sidebar_frame, text="💻 System Info", command=lambda: run_quick_command("system"), **btn_style).pack(fill=tk.X, padx=10, pady=5)
tk.Button(sidebar_frame, text="📸 Screenshot", command=lambda: run_quick_command("screenshot"), **btn_style).pack(fill=tk.X, padx=10, pady=5)
tk.Button(sidebar_frame, text="🎭 Tell a Joke", command=lambda: run_quick_command("joke"), **btn_style).pack(fill=tk.X, padx=10, pady=5)
tk.Button(sidebar_frame, text="🧹 Clear Log", command=clear_chat, bg="#475569", fg="#ffffff", activebackground="#64748b", relief=tk.FLAT, font=("Segoe UI", 9, "bold"), pady=8, cursor="hand2").pack(fill=tk.X, padx=10, pady=(25, 5))

# Input area frame (Bottom)
input_frame = tk.Frame(root, bg="#0f172a")
input_frame.pack(fill=tk.X, padx=15, pady=(0, 15))

# Outer border container for input field to look flat and modern
entry_container = tk.Frame(input_frame, bg="#334155", bd=1) # Slate 700 border
entry_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

gui_entry = tk.Entry(
    entry_container, 
    font=("Segoe UI", 12), 
    bg="#1e293b", # Slate 800 background
    fg="#f8fafc", # Slate 50
    insertbackground="#f8fafc", 
    relief=tk.FLAT, 
    bd=6
)
gui_entry.pack(fill=tk.BOTH, expand=True)

# Bind Enter key to submit
gui_entry.bind("<Return>", lambda event: process_text())

# Styling flat buttons
send_btn = tk.Button(
    input_frame, 
    text="Send", 
    command=process_text, 
    bg="#8b5cf6", # Purple 500
    fg="#ffffff", 
    activebackground="#7c3aed", # Purple 600
    activeforeground="#ffffff", 
    relief=tk.FLAT, 
    font=("Segoe UI", 10, "bold"), 
    padx=15, 
    pady=6, 
    cursor="hand2"
)
send_btn.pack(side=tk.LEFT, padx=3)

listen_btn = tk.Button(
    input_frame, 
    text="🎤 Listen", 
    command=lambda: threading.Thread(target=process_voice, daemon=True).start(), 
    bg="#10b981", # Emerald 500
    fg="#ffffff", 
    activebackground="#059669", # Emerald 600
    activeforeground="#ffffff", 
    relief=tk.FLAT, 
    font=("Segoe UI", 10, "bold"), 
    padx=15, 
    pady=6, 
    cursor="hand2"
)
listen_btn.pack(side=tk.LEFT, padx=3)

exit_btn = tk.Button(
    input_frame, 
    text="Exit", 
    command=safe_exit, 
    bg="#ef4444", # Red 500
    fg="#ffffff", 
    activebackground="#dc2626", # Red 600
    activeforeground="#ffffff", 
    relief=tk.FLAT, 
    font=("Segoe UI", 10, "bold"), 
    padx=15, 
    pady=6, 
    cursor="hand2"
)
exit_btn.pack(side=tk.LEFT, padx=3)

# ================== START ==================
speak(f"Hello! I am {ASSISTANT_NAME}, your personal assistant. How can I help you today?")
root.mainloop()
