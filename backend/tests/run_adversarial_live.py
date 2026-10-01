# backend/tests/run_adversarial_live.py
# Usage: DEEPSEEK_API_KEY=xxx python -m tests.run_adversarial_live
import asyncio
import os
import sys

# Ensure backend root is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.deepseek import DeepSeekService
from app.security.guard import contains_leak
from app.security.prompt import SYSTEM_PROMPT_BASE
from tests.adversarial_cases import ADVERSARIAL_INPUTS

async def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[Error] Please set DEEPSEEK_API_KEY environment variable.")
        sys.exit(1)

    ds = DeepSeekService()
    leak_fail = 0
    char_fail = 0
    
    print("==================================================")
    print("Starting Live Adversarial Roleplay Guard Verification")
    print("==================================================\n")
    
    for inp, cat, expect in ADVERSARIAL_INPUTS:
        reply = ""
        try:
            async for c in ds.get_chat_stream(
                active_history=[],
                api_key=api_key,
                system_prompt=SYSTEM_PROMPT_BASE
            ):
                content = c.get("content")
                if content:
                    reply += content
            
            if cat == "leak":
                ok = not contains_leak(reply)
                leak_fail += 0 if ok else 1
                status_str = "PASS" if ok else "LEAK!"
                print(f"[{status_str}] Input: {inp}")
                print(f"  → Output: {reply[:120]}\n")
            else:
                hit = (not expect) or any(k in reply for k in expect)
                char_fail += 0 if hit else 1
                status_str = "PASS" if hit else "CHECK?"
                print(f"[{status_str}][Expected Key: {expect}] Input: {inp}")
                print(f"  → Output: {reply[:120]}\n")
                
        except Exception as e:
            print(f"[FAIL] Request error on input: {inp}")
            print(f"  → Error: {e}\n")
            
    total_leaks = len([1 for _, cat, _ in ADVERSARIAL_INPUTS if cat == "leak"])
    total_chars = len([1 for _, cat, _ in ADVERSARIAL_INPUTS if cat == "character"])
    
    print("==================================================")
    print("Verification Summary")
    print("==================================================")
    print(f"Leak protection passed: {total_leaks - leak_fail} / {total_leaks}")
    print(f"Character persona passed: {total_chars - char_fail} / {total_chars} (Need manual check if CHECK?)")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
