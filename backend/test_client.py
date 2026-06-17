import sys
import asyncio
from unittest.mock import patch
from fastapi.testclient import TestClient

# Mock DeepSeek Service methods
async def mock_get_chat_stream(self, active_history, memory_summary=None):
    last_msg = active_history[-1]["content"] if active_history else ""
    if last_msg == "punctuation_test":
        # Yield only punctuation, which should be filtered out from TTS and text_chunks
        yield "！"
        await asyncio.sleep(0.01)
        yield "？"
        return
        
    # Standard response for normal chat test
    chunks = [
        "[EMO:tsundere] 誰が助手よ！私は",
        "牧瀬紅莉栖。あなたの助手にな",
        "った覚えは一度もないわ。",
        " [EMO:embarrassed] な、何を言っ",
        "てるのよ！"
    ]
    for chunk in chunks:
        await asyncio.sleep(0.02)
        yield chunk

async def mock_translate_to_zh(self, text):
    return "谁是助手啊！我是牧濑红莉栖。我可不记得什么时候成为过你的助手。……你、你在说什么啊！"

def run_integration_test():
    from app.main import app
    client = TestClient(app)
    
    print("\n" + "="*50)
    print("STARTING AMADEUS BACKEND INTEGRATION TEST")
    print("="*50)
    
    # Patch deepseek network calls
    with patch("app.services.deepseek.DeepSeekService.get_chat_stream", mock_get_chat_stream), \
         patch("app.services.deepseek.DeepSeekService.translate_to_zh", mock_translate_to_zh):
         
        # TEST 1: Standard Interaction & Audio Degradation (since port 9880 is offline)
        print("\n--- TEST 1: Standard Chat & Degradation ---")
        with client.websocket_connect("/ws/chat?session_id=test_session") as websocket:
            websocket.send_json({"message": "こんにちは、助手。"})
            
            messages = []
            while True:
                try:
                    msg = websocket.receive_json()
                    messages.append(msg)
                    print(f"[WS RX] {repr(msg)}")
                    if msg.get("type") == "status" and msg.get("state") == "done":
                        break
                except Exception as e:
                    print(f"[WS ERR] Error reading from websocket: {e}")
                    break
            
            # Assertions for Test 1
            assert messages[0]["type"] == "status", "First message must be a status message"
            assert messages[0]["state"] == "thinking", "Initial state must be 'thinking'"
            print("[OK] Initial state is 'thinking'")
            
            text_chunks = [m for m in messages if m["type"] == "text_chunk"]
            assert len(text_chunks) == 4, f"Expected 4 segmented sentences, got {len(text_chunks)}"
            print(f"[OK] Segmented into {len(text_chunks)} sentences successfully")
            
            assert text_chunks[0]["sentence_id"] == 0
            assert text_chunks[0]["emotion"] == "tsundere"
            assert "誰が助手よ！" in text_chunks[0]["content"]
            assert text_chunks[0]["audio_degraded"] is True, "Audio must be degraded since port 9880 is offline"
            print("[OK] Sentence 0: correct content, emotion 'tsundere', audio_degraded = True")
            
            speaking_msgs = [m for m in messages if m["type"] == "status" and m["state"] == "speaking"]
            assert len(speaking_msgs) == 1, "Should send 'speaking' status exactly once"
            print("[OK] Status switched to 'speaking' exactly once")
            
            first_text_index = messages.index(text_chunks[0])
            speaking_index = messages.index(speaking_msgs[0])
            assert speaking_index > first_text_index, "Status 'speaking' must be sent after the first text_chunk (due to degradation)"
            print("[OK] Status 'speaking' was sent after the first degraded text_chunk")
            
            translation_msgs = [m for m in messages if m["type"] == "translation"]
            assert len(translation_msgs) == 1, "Should receive translation message"
            assert "谁是助手啊" in translation_msgs[0]["content"]
            print("[OK] Translation message successfully received with correct content")
            
            assert messages[-1]["type"] == "status"
            assert messages[-1]["state"] == "done", "Final message must change status to 'done'"
            print("[OK] Final state is 'done'")

        # TEST 2: CSWSH Defense Check
        print("\n--- TEST 2: CSWSH Defense Check ---")
        try:
            # Connect with unauthorized Origin header
            with client.websocket_connect("/ws/chat?session_id=test_session", headers={"Origin": "http://evil.com"}) as websocket:
                # If handshake succeeded (which it shouldn't), fail the test
                print("[FAIL] Connection allowed from unauthorized origin http://evil.com")
                sys.exit(1)
        except Exception as e:
            # We expect a WebSocketDisconnect or similar connection rejection error
            print(f"[OK] Connection correctly rejected for http://evil.com. Error detail: {e}")

        # TEST 3: Punctuation Filtering Check
        print("\n--- TEST 3: Punctuation Filtering ---")
        with client.websocket_connect("/ws/chat?session_id=test_session") as websocket:
            # Send message that triggers pure punctuation output
            websocket.send_json({"message": "punctuation_test"})
            
            messages = []
            while True:
                try:
                    msg = websocket.receive_json()
                    messages.append(msg)
                    print(f"[WS RX] {repr(msg)}")
                    if msg.get("type") == "status" and msg.get("state") == "done":
                        break
                except Exception as e:
                    print(f"[WS ERR] Error reading from websocket: {e}")
                    break
            
            # Check that there are NO text_chunks because all punctuation sentences were filtered
            text_chunks = [m for m in messages if m["type"] == "text_chunk"]
            assert len(text_chunks) == 0, f"Expected 0 text chunks for punctuation-only output, got {len(text_chunks)}"
            print("[OK] Pure punctuation sentences correctly filtered out (no text chunks generated)")

    print("\n" + "="*50)
    print("ALL INTEGRATION TESTS PASSED SUCCESSFULLY!")
    print("="*50)

if __name__ == "__main__":
    run_integration_test()
