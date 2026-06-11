# Sophia - Personal Virtual Assistant (GUI + Voice)

Sophia is a modern, dark-themed personal virtual assistant built in Python using `tkinter` for the GUI, `pyttsx3` for offline text-to-speech, and `speech_recognition` for voice input.

## Features

- **Voice & Text Input**: Control Sophia using microphone input or by typing directly.
- **Premium GUI**: Modern, borderless, dark-slate styled interface with custom flat buttons and color-coded message logging.
- **Thread-safe Logging & Asynchronous TTS**: Prevents Tkinter GUI freezes during execution.
- **Calculations**: Safe evaluator using AST instead of dangerous `eval()`.
- **System Monitoring**: View real-time CPU and RAM usage.
- **Reminders & Screenshots**: Set time-based reminders and capture desktop screenshots.
- **Integrations**: Weather queries (OpenWeatherMap), instant/scheduled WhatsApp messages, Wikipedia info lookups, Google searching, sending emails (SMTP), and opening applications/websites.

---

## Installation & Setup

1. **Install Python Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment Variables**:
   Sophia uses a `.env` file for credentials. Ensure your `.env` contains:
   ```env
   WEATHER_API_KEY=your_openweather_api_key
   EMAIL_ADDRESS=your_email@gmail.com
   EMAIL_PASSWORD=your_app_password
   ```

---

## Running the Assistant

Simply execute the main script:
```bash
python sophia.py
```

## Robustness Fixes Done
- Fixed `os.getenv()` key lookups (avoiding hardcoded secrets directly in key names).
- Removed unused and broken `googletrans` dependency that threw `No module named 'cgi'` on newer Python runtimes (Python 3.13+).
- Clean exit sequence via `root.protocol("WM_DELETE_WINDOW", safe_exit)` to avoid orphan background daemon threads.
- Refactored Tkinter controls to a sleek Slate dark theme (`#0f172a` / `#1e293b`).
