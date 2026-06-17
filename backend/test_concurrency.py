import sys
import asyncio
import time
import threading
from unittest.mock import patch
from fastapi.testclient import TestClient

# Mock DeepSeek Service methods
async def mock_get_chat_stream(self, active_history, memory_summary=None):
    # Simulate a stream with emotion tags
    # If the user message contains a keyword, we return different sentences
    user_msg = active_history[-1]["content"] if active_history else ""
    if "slow_tts" in user_msg:
        chunks = ["[EMO:tsundere] slow_tts_sentence。"]
    elif "fast_tts" in user_msg:
        chunks = ["[EMO:neutral] fast_tts_sentence。"]
    else:
        chunks = ["[EMO:tsundere] こんにちは。"]
        
    for chunk in chunks:
        await asyncio.sleep(0.01)
        yield chunk

async def mock_translate_to_zh(self, text):
    return "翻译: " + text

# Mock compress_history that is slow
async def mock_slow_compress_history(self, history):
    print("[MOCK] compress_history started...")
    await asyncio.sleep(2.0)  # Slow API response
    print("[MOCK] compress_history finished.")
    return "要約：会話履歴圧縮済み。"

# Mock sync tts call to simulate slow GPU execution
original_sync_tts_call = None
def mock_sync_tts_call(self, text: str, ref_config: dict) -> bytes:
    if "slow_tts" in text:
        print(f"[MOCK TTS] Starting slow TTS (12s sleep) for text: {text}")
        time.sleep(12.0)  # Sleep 12 seconds
        print(f"[MOCK TTS] Finished slow TTS for text: {text}")
        return b"slow_audio"
    elif "fast_tts" in text:
        print(f"[MOCK TTS] Starting fast TTS (instant) for text: {text}")
        return b"fast_audio"
    else:
        return b"default_audio"

def run_client_interaction(client, session_id, message, results_list):
    """Run a single websocket chat interaction in a background thread."""
    try:
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
            ws.send_json({"message": message})
            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg.get("type") == "status" and msg.get("state") == "done":
                    break
            results_list.append((session_id, True, messages))
    except Exception as e:
        results_list.append((session_id, False, e))

async def test_session_isolation(client):
    print("\n" + "="*50)
    print("TEST 1: SESSION ISOLATION & CONCURRENCY")
    print("="*50)
    
    from app.routers.chat_ws import sessions
    sessions.clear()
    
    results = []
    # Start two concurrent clients with different session IDs
    t1 = asyncio.to_thread(run_client_interaction, client, "session_A", "こんにちは、Aです。", results)
    t2 = asyncio.to_thread(run_client_interaction, client, "session_B", "こんにちは、Bです。", results)
    
    await asyncio.gather(t1, t2)
    
    # Verify both succeeded
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    
    for sess_id, success, msgs in results:
        assert success is True, f"Connection for {sess_id} failed: {msgs}"
        print(f"[OK] {sess_id} completed successfully.")
        
        # Verify session history isolation
        session_data = sessions.get(sess_id)
        assert session_data is not None, f"Session data for {sess_id} not found"
        history = session_data["history"]
        
        # History should only contain messages for this session
        user_msgs = [h["content"] for h in history if h["role"] == "user"]
        assert len(user_msgs) == 1, f"Expected 1 user message in {sess_id} history, got {len(user_msgs)}"
        if sess_id == "session_A":
            assert "Aです" in user_msgs[0], f"Wrong message content in history of session_A: {user_msgs}"
        else:
            assert "Bです" in user_msgs[0], f"Wrong message content in history of session_B: {user_msgs}"
            
    print("[PASS] Test 1: Session isolation is verified under concurrent connections.")

def run_client_race_flow(client, session_id, results_list):
    """Run Client 1, send Round 17, and keep connection open for 3 seconds."""
    try:
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
            ws.send_json({"message": "Round 17 message"})
            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg.get("type") == "status" and msg.get("state") == "done":
                    break
            results_list.append((session_id, True, messages))
            # Keep connection open for 3.0s to allow background compression task to finish
            time.sleep(3.0)
    except Exception as e:
        results_list.append((session_id, False, e))

async def test_history_compression_race(client):
    print("\n" + "="*50)
    print("TEST 2: HISTORY COMPRESSION RACE CONDITION")
    print("="*50)
    
    from app.routers.chat_ws import sessions
    sessions.clear()
    
    # 1. Initialize session with 32 messages (16 rounds) to exceed threshold (15 rounds)
    session_id = "race_session"
    dummy_history = []
    for i in range(16):
        dummy_history.append({"role": "user", "content": f"User msg {i}"})
        dummy_history.append({"role": "assistant", "content": f"Assistant response {i}"})
        
    sessions[session_id] = {
        "history": dummy_history,
        "memory_summary": ""
    }
    
    # 2. Setup mock for slow compression
    with patch("app.services.deepseek.DeepSeekService.compress_history", mock_slow_compress_history):
        # We start the first request which triggers compression
        results_1 = []
        print("[Client] Sending Round 17 (triggers compression and keeps connection open)...")
        # Run Round 17 in background thread and keep it open
        t1 = asyncio.to_thread(run_client_race_flow, client, session_id, results_1)
        
        # Wait 0.5s for Round 17 to complete its message exchanges
        await asyncio.sleep(0.5)
        
        # Immediately send Round 18 message *while* compression is active.
        results_2 = []
        print("[Client] Sending Round 18 message immediately...")
        await asyncio.to_thread(run_client_interaction, client, session_id, "Round 18 message", results_2)
        
        # Wait for Client 1 connection to close after its sleep
        await t1
        
        # Give the background compression task a tiny bit more time if needed
        print("[Test] Waiting for background compression to complete...")
        await asyncio.sleep(0.5)
        
        # 3. Inspect final history
        final_history = sessions[session_id]["history"]
        print(f"[Test] Final history length: {len(final_history)}")
        print("[Test] Final history messages:")
        for idx, msg in enumerate(final_history):
            print(f"  {idx}: {msg['role']} -> {msg['content']}")
            
        # Let's check if the messages from Round 18 are lost.
        has_round_18 = any("Round 18" in msg["content"] for msg in final_history)
        if not has_round_18:
            print("[FAIL/BUG DETECTED] Round 18 messages were completely lost from the session history!")
            print("[BUG DETAILS] Due to the race condition where `compress_and_update_history` captures the history list reference before yielding, and overwrites the session history after returning, all messages sent during the API call are overwritten.")
        else:
            print("[PASS] Round 18 messages are present in history.")

async def test_tts_queue_blocking(client):
    print("\n" + "="*50)
    print("TEST 3: TTS QUEUE BLOCKING (CASCADE TIMEOUT)")
    print("="*50)
    
    from app.services.tts_queue import TTSQueueManager
    
    # Patch the _sync_tts_call method to simulate a slow GPU inference
    with patch.object(TTSQueueManager, "_sync_tts_call", mock_sync_tts_call):
        results = []
        
        # Client 1 connects and sends a message that triggers "slow_tts"
        # Client 2 connects 1 second later and sends "fast_tts"
        # We run them concurrently
        
        async def run_client_1():
            print("[Client 1] Sending message triggering slow TTS (12s)...")
            await asyncio.to_thread(run_client_interaction, client, "session_client1", "slow_tts", results)
            print("[Client 1] Finished.")
            
        async def run_client_2():
            await asyncio.sleep(1.0)  # Wait 1 second before sending client 2 request
            print("[Client 2] Sending message triggering fast TTS (instant)...")
            await asyncio.to_thread(run_client_interaction, client, "session_client2", "fast_tts", results)
            print("[Client 2] Finished.")
            
        t0 = time.time()
        await asyncio.gather(run_client_1(), run_client_2())
        duration = time.time() - t0
        print(f"[Test] Total duration of test: {duration:.2f}s")
        
        # Analyze results
        # We expect Client 1 to receive a response with audio_degraded = True because it timed out after 10s.
        # We also expect Client 2 to receive a response with audio_degraded = True because its request was queued
        # behind Client 1's request in the single-threaded executor, causing Client 2's 10s timeout to expire!
        
        client1_msgs = next(msgs for sid, success, msgs in results if sid == "session_client1")
        client2_msgs = next(msgs for sid, success, msgs in results if sid == "session_client2")
        
        c1_text_chunk = next(m for m in client1_msgs if m.get("type") == "text_chunk")
        c2_text_chunk = next(m for m in client2_msgs if m.get("type") == "text_chunk")
        
        print(f"[Client 1 Result] content: '{c1_text_chunk.get('content')}', audio_degraded: {c1_text_chunk.get('audio_degraded')}")
        print(f"[Client 2 Result] content: '{c2_text_chunk.get('content')}', audio_degraded: {c2_text_chunk.get('audio_degraded')}")
        
        if c2_text_chunk.get('audio_degraded') is True:
            print("[FAIL/BUG DETECTED] Client 2's instant TTS request was degraded due to Client 1's slow TTS request blocking the executor thread!")
            print("[BUG DETAILS] Because the ThreadPoolExecutor has max_workers=1 and the blocking thread cannot be cancelled, a slow request blocks the queue. Subsequent requests time out in asyncio.wait_for before they can run.")
        else:
            print("[PASS] Client 2's TTS request was not degraded.")

async def main():
    from app.main import app
    client = TestClient(app)
    
    # Patch deepseek network calls for basic chatting
    with patch("app.services.deepseek.DeepSeekService.get_chat_stream", mock_get_chat_stream), \
         patch("app.services.deepseek.DeepSeekService.translate_to_zh", mock_translate_to_zh):
         
         await test_session_isolation(client)
         await test_history_compression_race(client)
         await test_tts_queue_blocking(client)

if __name__ == "__main__":
    asyncio.run(main())
