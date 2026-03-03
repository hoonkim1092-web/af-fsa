import os
import sys
try:
    from openai import OpenAI
    client = OpenAI(api_key="mock")
    print("OpenAI client initialized.")
    print("Attributes of client:", dir(client))
    if hasattr(client, "chat"):
        print("Attributes of client.chat:", dir(client.chat))
    if hasattr(client, "responses"):
        print("Attributes of client.responses:", dir(client.responses))
except Exception as e:
    print("Error:", e)
