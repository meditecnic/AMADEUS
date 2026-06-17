import sys
import asyncio
import time
import base64
from unittest.mock import patch
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState

# Add project path to python path
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.routers.chat_ws import websocket_chat_endpoint, sessions
from app.services.deepseek import DeepSeekService
from app.services.tts_queue import tts_manager

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

    async def close(self):
        self.closed = True
        self.client_state = WebSocketState.DISCONNECTED
        await self.recv_queue.put(None)


# ==========================================
# TEST CASE 1: Concurrency Session Isolation
# ==========================================
async def test_session_isolation():
    print("\n" + "="*50)
    print("RUNNING TEST: SESSION ISOLATION & CONCURRENCY")
    print("="*50)
    
    # Clean up sessions first
    sessions.clear()
    
    # Mock DeepSeek to return slowly with clear indicators
    async def mock_get_chat_stream(self, active_history, memory_summary=None):
        last_msg = active_history[-1]["content"]
        if "Client A" in last_msg:
            chunks = ["[EMO:tsundere] Answer A-1.", " Answer A-2."]
        else:
            chunks = ["[EMO:embarrassed] Answer B-1.", " Answer B-2."]
            
        for chunk in chunks:
            await asyncio.sleep(0.5) # Yield slowly to allow interleaving
            yield chunk

    async def mock_translate_to_zh(self, text):
        return f"Chinese Translation of: {text}"

    # Mock TTS to run instantly
    async def mock_async_tts_call_fast(self, text: str, ref_config: dict) -> bytes:
        return b"audio_bytes_fast"

    with patch("app.services.deepseek.DeepSeekService.get_chat_stream", mock_get_chat_stream), \
         patch("app.services.deepseek.DeepSeekService.translate_to_zh", mock_translate_to_zh), \
         patch("app.services.tts_queue.TTSQueueManager._async_tts_call", mock_async_tts_call_fast):
         
        ws1 = MockWebSocket(session_id="shared_session")
        ws2 = MockWebSocket(session_id="shared_session")
        
        # Start endpoints
        task1 = asyncio.create_task(websocket_chat_endpoint(ws1))
        task2 = asyncio.create_task(websocket_chat_endpoint(ws2))
        
        # Trigger requests concurrently
        await ws1.recv_queue.put({"message": "Question from Client A"})
        await asyncio.sleep(0.1) # Stagger slightly
        await ws2.recv_queue.put({"message": "Question from Client B"})
        
        # Gather responses for Client A
        ws1_responses = []
        while True:
            msg = await ws1.send_queue.get()
            ws1_responses.append(msg)
            if msg.get("type") == "status" and msg.get("state") == "done":
                break
                
        # Gather responses for Client B
        ws2_responses = []
        while True:
            msg = await ws2.send_queue.get()
            ws2_responses.append(msg)
            if msg.get("type") == "status" and msg.get("state") == "done":
                break
                
        # Close WebSocket connections
        await ws1.close()
        await ws2.close()
        await asyncio.gather(task1, task2)
        
        # Verification
        print(f"Global Session data: {sessions['shared_session']}")
        history = sessions["shared_session"]["history"]
        
        print("\nMessages in history:")
        for idx, h in enumerate(history):
            print(f"  {idx}: {h['role']} -> {h['content']}")
            
        # Check if history is contaminated
        # Under normal isolated conditions, each WebSocket would run in its own session.
        # But here, both connections share "shared_session".
        # Let's count user messages in history.
        user_msgs = [h for h in history if h["role"] == "user"]
        assistant_msgs = [h for h in history if h["role"] == "assistant"]
        
        print(f"Total user messages: {len(user_msgs)}")
        print(f"Total assistant messages: {len(assistant_msgs)}")
        
        # Assertion: If there's data contamination, both user messages will be present in the same session history
        # which violates the single-connection history isolation constraint.
        has_contamination = len(user_msgs) > 1 or len(assistant_msgs) > 1
        if has_contamination:
            print("[FAIL] SESSION ISOLATION CONTAMINATED: Both connections shared and polluted the same history list.")
        else:
            print("[PASS] Session isolation is stable.")
            
        assert has_contamination == True, "Expected session isolation contamination under concurrent same-session-id requests, but it did not happen."


# ==========================================
# TEST CASE 2: TTS Head-of-Line Blocking
# ==========================================
async def test_tts_head_of_line_blocking():
    print("\n" + "="*50)
    print("RUNNING TEST: TTS HEAD-OF-LINE BLOCKING")
    print("="*50)
    
    sessions.clear()
    
    # We want to measure the response time of a fast request when a slow request is occupying the executor.
    # We will use two separate sessions to ensure session isolation issues do not interfere with timing.
    ws_slow = MockWebSocket(session_id="session_slow")
    ws_fast = MockWebSocket(session_id="session_fast")
    
    async def mock_get_chat_stream_slow(self, active_history, memory_summary=None):
        yield "[EMO:tsundere] slow_tts_message."
        
    async def mock_get_chat_stream_fast(self, active_history, memory_summary=None):
        yield "[EMO:tsundere] fast_tts_message."
        
    async def mock_translate_to_zh(self, text):
        return "Chinese Translation"
        
    # Mock async TTS to simulate sleep on "slow" and immediate return on "fast"
    async def mock_async_tts_call_blocking(self, text: str, ref_config: dict) -> bytes:
        if "slow" in text:
            print("[MOCK TTS] Starting slow TTS (sleep 5s)...")
            await asyncio.sleep(5.0) # Async sleep
            print("[MOCK TTS] Slow TTS finished.")
            return b"audio_slow"
        else:
            print("[MOCK TTS] Starting fast TTS (instant)...")
            return b"audio_fast"

    with patch("app.services.tts_queue.TTSQueueManager._async_tts_call", mock_async_tts_call_blocking), \
         patch("app.services.deepseek.DeepSeekService.translate_to_zh", mock_translate_to_zh), \
         patch.object(tts_manager, "semaphore", asyncio.Semaphore(2)):
         
        # Run endpoints
        # Note: We must patch DeepSeekService.get_chat_stream per instance or dynamically
        # Let's dynamically mock get_chat_stream on the instance based on active_history
        original_get_chat_stream = DeepSeekService.get_chat_stream
        async def dynamic_get_chat_stream(self, active_history, memory_summary=None):
            last_msg = active_history[-1]["content"]
            if "slow" in last_msg:
                async for chunk in mock_get_chat_stream_slow(self, active_history, memory_summary):
                    yield chunk
            else:
                async for chunk in mock_get_chat_stream_fast(self, active_history, memory_summary):
                    yield chunk
                    
        with patch("app.services.deepseek.DeepSeekService.get_chat_stream", dynamic_get_chat_stream):
            
            task_slow = asyncio.create_task(websocket_chat_endpoint(ws_slow))
            task_fast = asyncio.create_task(websocket_chat_endpoint(ws_fast))
            
            # Start slow connection first
            await ws_slow.recv_queue.put({"message": "trigger slow TTS"})
            await asyncio.sleep(0.5) # Wait for slow TTS to begin execution and occupy the single thread
            
            # Start fast connection
            fast_start_time = time.time()
            await ws_fast.recv_queue.put({"message": "trigger fast TTS"})
            
            # Read fast connection responses
            fast_responses = []
            while True:
                msg = await ws_fast.send_queue.get()
                fast_responses.append(msg)
                if msg.get("type") == "status" and msg.get("state") == "done":
                    break
            
            fast_elapsed = time.time() - fast_start_time
            print(f"\nFast Connection finished in {fast_elapsed:.2f} seconds.")
            
            # Gather slow connection responses to clean up
            slow_responses = []
            while True:
                msg = await ws_slow.send_queue.get()
                slow_responses.append(msg)
                if msg.get("type") == "status" and msg.get("state") == "done":
                    break
                    
            await ws_slow.close()
            await ws_fast.close()
            await asyncio.gather(task_slow, task_fast)
            
            # Verification:
            # The fast connection only has "fast_tts_message." which has no simulated sleep in mock_sync_tts_call.
            # If there is no head-of-line blocking, it should complete in < 0.5s.
            # If there is head-of-line blocking, it has to wait for the slow connection's 5.0s sleep to finish,
            # resulting in > 4.0s total time.
            print(f"Fast connection response packets: {fast_responses}")
            
            if fast_elapsed >= 4.0:
                print(f"[FAIL] TTS HEAD-OF-LINE BLOCKING CONFIRMED: Fast request was blocked for {fast_elapsed:.2f}s due to the single-threaded Executor queue.")
            else:
                print("[PASS] No TTS Head-of-line blocking detected.")
                
            assert fast_elapsed < 4.0, f"Expected No Head-of-line blocking (delay < 4.0s), but it took {fast_elapsed:.2f}s."


async def main():
    try:
        await test_session_isolation()
    except AssertionError as e:
        print(f"Assertion failed in test_session_isolation: {e}")
    except Exception as e:
        print(f"Error in test_session_isolation: {e}")
        
    try:
        await test_tts_head_of_line_blocking()
    except AssertionError as e:
        print(f"Assertion failed in test_tts_head_of_line_blocking: {e}")
    except Exception as e:
        print(f"Error in test_tts_head_of_line_blocking: {e}")

if __name__ == "__main__":
    asyncio.run(main())
