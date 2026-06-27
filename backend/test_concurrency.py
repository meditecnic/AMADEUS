import sys
import asyncio
import time
from unittest.mock import patch
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState

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

# Mock async tts call to simulate slow GPU execution
async def mock_async_tts_call(self, text: str, ref_config: dict) -> bytes:
    if "slow_tts" in text:
        print(f"[MOCK TTS] Starting slow TTS (12s sleep) for text: {text}")
        await asyncio.sleep(12.0)  # Sleep 12 seconds
        print(f"[MOCK TTS] Finished slow TTS for text: {text}")
        return b"slow_audio"
    elif "fast_tts" in text:
        print(f"[MOCK TTS] Starting fast TTS (instant) for text: {text}")
        return b"fast_audio"
    else:
        return b"default_audio"

# Define MockWebSocket
class MockWebSocket:
    def __init__(self, session_id="default"):
        self.query_params = {"session_id": session_id}
        self.recv_queue = asyncio.Queue()
        self.send_queue = asyncio.Queue()
        self.accepted = False
        self.closed = False
        self.headers = {"origin": "http://localhost"}
        self.client_state = WebSocketState.CONNECTED

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        item = await self.recv_queue.get()
        if item is None:
            raise WebSocketDisconnect()
        return item

    async def send_json(self, data):
        await self.send_queue.put(data)

    async def close(self, code=1000):
        self.closed = True
        self.client_state = WebSocketState.DISCONNECTED
        await self.recv_queue.put(None)

async def test_session_isolation():
    print("\n" + "="*50)
    print("TEST 1: SESSION ISOLATION & CONCURRENCY")
    print("="*50)
    
    from app.routers.chat_ws import sessions, websocket_chat_endpoint
    sessions.clear()
    
    ws_A = MockWebSocket(session_id="session_A")
    ws_B = MockWebSocket(session_id="session_B")
    
    task1 = asyncio.create_task(websocket_chat_endpoint(ws_A))
    task2 = asyncio.create_task(websocket_chat_endpoint(ws_B))
    
    await ws_A.recv_queue.put({"type": "auth", "api_key": "test_key"})
    await ws_A.recv_queue.put({"type": "chat", "content": "こんにちは、Aです。"})
    await ws_B.recv_queue.put({"type": "auth", "api_key": "test_key"})
    await ws_B.recv_queue.put({"type": "chat", "content": "こんにちは、Bです。"})
    
    async def collect_messages(ws):
        messages = []
        while True:
            msg = await ws.send_queue.get()
            messages.append(msg)
            if msg.get("type") == "status" and msg.get("state") == "done":
                break
        return messages

    msgs_A, msgs_B = await asyncio.gather(collect_messages(ws_A), collect_messages(ws_B))
    
    await ws_A.close()
    await ws_B.close()
    await asyncio.gather(task1, task2)
    
    # Verify session history isolation
    session_data_A = sessions.get("session_A")
    session_data_B = sessions.get("session_B")
    assert session_data_A is not None
    assert session_data_B is not None
    
    user_msgs_A = [h["content"] for h in session_data_A["history"] if h["role"] == "user"]
    user_msgs_B = [h["content"] for h in session_data_B["history"] if h["role"] == "user"]
    
    assert len(user_msgs_A) == 1, f"Expected 1 user message, got {len(user_msgs_A)}"
    assert "Aです" in user_msgs_A[0], f"Expected A content, got {user_msgs_A[0]}"
    assert len(user_msgs_B) == 1, f"Expected 1 user message, got {len(user_msgs_B)}"
    assert "Bです" in user_msgs_B[0], f"Expected B content, got {user_msgs_B[0]}"
    
    print("[PASS] Test 1: Session isolation is verified under concurrent connections.")

async def test_history_compression_race():
    print("\n" + "="*50)
    print("TEST 2: HISTORY COMPRESSION RACE CONDITION")
    print("="*50)
    
    from app.routers.chat_ws import sessions, websocket_chat_endpoint
    sessions.clear()
    
    session_id = "race_session"
    dummy_history = []
    for i in range(16):
        dummy_history.append({"role": "user", "content": f"User msg {i}"})
        dummy_history.append({"role": "assistant", "content": f"Assistant response {i}"})
        
    sessions[session_id] = {
        "history": dummy_history,
        "memory_summary": "",
        "lock": asyncio.Lock(),
        "compressing": False
    }
    
    with patch("app.services.deepseek.DeepSeekService.compress_history", mock_slow_compress_history):
        ws1 = MockWebSocket(session_id=session_id)
        ws2 = MockWebSocket(session_id=session_id)
        
        task1 = asyncio.create_task(websocket_chat_endpoint(ws1))
        task2 = asyncio.create_task(websocket_chat_endpoint(ws2))
        
        # Start Round 17 (triggers compression)
        await ws1.recv_queue.put({"type": "auth", "api_key": "test_key"})
        await ws1.recv_queue.put({"type": "chat", "content": "Round 17 message"})
        
        async def wait_for_done(ws):
            while True:
                msg = await ws.send_queue.get()
                if msg.get("type") == "status" and msg.get("state") == "done":
                    break
        
        await wait_for_done(ws1)
        
        # Immediately send Round 18 message while compression is still running
        await ws2.recv_queue.put({"type": "auth", "api_key": "test_key"})
        await ws2.recv_queue.put({"type": "chat", "content": "Round 18 message"})
        await wait_for_done(ws2)
        
        # Wait a bit to let compression finish
        await asyncio.sleep(2.5)
        
        await ws1.close()
        await ws2.close()
        await asyncio.gather(task1, task2)
        
        final_history = sessions[session_id]["history"]
        print(f"[Test] Final history length: {len(final_history)}")
        
        has_round_18 = any("Round 18" in msg["content"] for msg in final_history)
        if not has_round_18:
            print("[FAIL/BUG DETECTED] Round 18 messages were completely lost from the session history!")
        else:
            print("[PASS] Round 18 messages are present in history.")
        assert has_round_18, "Round 18 messages were lost from the session history!"

async def test_tts_queue_blocking():
    print("\n" + "="*50)
    print("TEST 3: TTS QUEUE BLOCKING (CASCADE TIMEOUT)")
    print("="*50)
    
    from app.services.tts_queue import TTSQueueManager
    from app.routers.chat_ws import websocket_chat_endpoint
    
    with patch.object(TTSQueueManager, "_async_tts_call", mock_async_tts_call):
        ws1 = MockWebSocket(session_id="session_client1")
        ws2 = MockWebSocket(session_id="session_client2")
        
        task1 = asyncio.create_task(websocket_chat_endpoint(ws1))
        task2 = asyncio.create_task(websocket_chat_endpoint(ws2))
        
        # Client 1 connects and sends slow_tts
        await ws1.recv_queue.put({"type": "auth", "api_key": "test_key"})
        await ws1.recv_queue.put({"type": "chat", "content": "slow_tts"})
        await asyncio.sleep(1.0)
        
        # Client 2 connects 1 second later and sends fast_tts
        await ws2.recv_queue.put({"type": "auth", "api_key": "test_key"})
        await ws2.recv_queue.put({"type": "chat", "content": "fast_tts"})
        
        async def collect_messages(ws):
            messages = []
            while True:
                msg = await ws.send_queue.get()
                messages.append(msg)
                if msg.get("type") == "status" and msg.get("state") == "done":
                    break
            return messages
            
        t0 = time.time()
        client1_msgs, client2_msgs = await asyncio.gather(collect_messages(ws1), collect_messages(ws2))
        duration = time.time() - t0
        print(f"[Test] Total duration of test: {duration:.2f}s")
        
        await ws1.close()
        await ws2.close()
        await asyncio.gather(task1, task2)
        
        c1_text_chunk = next(m for m in client1_msgs if m.get("type") == "text_chunk")
        c2_text_chunk = next(m for m in client2_msgs if m.get("type") == "text_chunk")
        
        print(f"[Client 1 Result] content: '{c1_text_chunk.get('content')}', audio_degraded: {c1_text_chunk.get('audio_degraded')}")
        print(f"[Client 2 Result] content: '{c2_text_chunk.get('content')}', audio_degraded: {c2_text_chunk.get('audio_degraded')}")
        
        if c2_text_chunk.get('audio_degraded') is True:
            print("[FAIL/BUG DETECTED] Client 2's instant TTS request was degraded due to Client 1's slow TTS request blocking the executor thread!")
        else:
            print("[PASS] Client 2's TTS request was not degraded.")
            
        assert c2_text_chunk.get('audio_degraded') is not True, "Client 2's TTS request was degraded!"

async def main():
    # Patch deepseek network calls for basic chatting
    with patch("app.services.deepseek.DeepSeekService.get_chat_stream", mock_get_chat_stream), \
         patch("app.services.deepseek.DeepSeekService.translate_to_zh", mock_translate_to_zh):
         
         await test_session_isolation()
         await test_history_compression_race()
         await test_tts_queue_blocking()

if __name__ == "__main__":
    # Add project root to path
    import os
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    asyncio.run(main())
