#!/usr/bin/env python3
"""
gemini_chat.py — Interactive CLI for Gemini 3 Flash.
Uses Nexus core for key rotation and chat handling.
"""
import sys
import asyncio
import logging
from pathlib import Path

# Ensure nexus root is on path
NEXUS_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(NEXUS_ROOT))

from core.chat_handler import call_with_fallback, _clean_response

logging.basicConfig(level=logging.WARNING)

async def main():
    print("\n--- Gemini 3 Flash Shell ---")
    print("Type 'exit' or 'quit' to end session.\n")

    history = []

    while True:
        try:
            user_input = input(">> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                break

            history.append({"role": "user", "content": user_input})

            # Prepare messages (system + last few context)
            messages = [
                {"role": "system", "content": "You are Gemini 3 Flash, an AI agent in the Nexus system. You are helpful, direct, and concise. You help Kai manage Zoar Bathroom Rentals."}
            ] + history[-5:]

            print("...", end="", flush=True)

            result = await call_with_fallback(messages)

            print("\r", end="") # Clear the ...

            if result.get("ok"):
                content = result.get("content", "")
                provider = result.get("provider", "?")
                model = result.get("model", "?")

                cleaned = _clean_response(content)
                print(f"[{provider}/{model}]\n{cleaned}\n")

                history.append({"role": "assistant", "content": content})
            else:
                print(f"Error: {result.get('error', 'Unknown error')}\n")

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except EOFError:
            break
        except Exception as e:
            print(f"\nSystem Error: {e}\n")

if __name__ == "__main__":
    asyncio.run(main())
