
import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
key = os.getenv("GOOGLE_API_KEY")
print(f"Key found: {'Yes' if key else 'No'}")
if key:
    print(f"Key preview: {key[:5]}...")

try:
    genai.configure(api_key=key)
    print("Listing models...")
    for m in genai.list_models():
        print(m.name)
        break
except Exception as e:
    print(f"Error listing models: {e}")
