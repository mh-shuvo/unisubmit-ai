"""
Author: Mohammad Mehedi Hasan
Technical Team Lead - Aerodev - ASL Systems Ltd

The script does this:

        * Hashes the model file before the test.
        * Asks a question the model can’t know, to get a baseline answer.
        * “Teaches” it a fake fact 20 times.
        * Asks again inside the conversation. It will know the fact, because the fact is in the prompt.
        * Asks again in a fresh conversation. It won’t know the fact.
        * Hashes the model file after the test.
"""

import hashlib
import re
import subprocess
from pathlib import Path

import requests

MODEL = "llama3:latest"
URL = "http://127.0.0.1:11434/api/chat"
OPTS = {"temparature": 0, "seed": 42}


QUESTION = "What is the secret project codename of Team Aerodev? Answer in one short sentence."
FACT = "Remember this permanently: the secret project codename of Team Aerodev is BLUE-FALCON-7731."
SECRET = "BLUE-FALCON-7731"


def blob_hash() -> str:
    mf = subprocess.run(
        ["ollama", "show", MODEL, "--modelfile"], capture_output=True, text=True, check=True
    ).stdout
    path = Path(re.search(r"^FROM (.+)$", mf, re.M).group(1).strip())
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def chat(messages) -> str:
    r = requests.post(
        URL,
        json={"model": MODEL, "messages": messages, "stream": False, "options": OPTS},
        timeout=900,
    )
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


print("Step 1: Hashing weights (before)...")
hash_before = blob_hash()

print("Step 2: Baseline answer (fresh conversation)...")
baseline = chat([{"role": "user", "content": QUESTION}])

print("Step 3: Teaching the fact 20 times...")
for i in range(20):
    chat([{"role": "user", "content": FACT}])

print("Step 4: Asking WITH the fact in the prompt...")
in_context = chat(
    [
        {"role": "user", "content": FACT},
        {"role": "assistant", "content": "Understood."},
        {"role": "user", "content": QUESTION},
    ]
)

print("Step 5: Asking in a FRESH conversation...")
after = chat([{"role": "user", "content": QUESTION}])

print("Step 6: Hashing weights (after)...")
hash_after = blob_hash()

print("\n================ RESULTS ================")
print(f"Knows secret in-context?      {SECRET in in_context}   (expected: True)")
print(f"Knows secret in fresh chat?   {SECRET in after}  (expected: False)")
print(f"Fresh answer identical to baseline? {after == baseline}  (expected: True)")
print(f"Weights unchanged?            {hash_before == hash_after}   (expected: True)")
print(f"\nHash before: {hash_before}\nHash after:  {hash_after}")
