import asyncio
import websockets
import json

async def test():
    try:
        async with websockets.connect(
            'ws://127.0.0.1:8000/ws/chat?session_id=debug_test'
        ) as ws:
            print('Connected direct to backend!')
            await ws.send(json.dumps({"type": "auth", "api_key": "test"}))
            print("Auth sent!")
            res = await ws.recv()
            print('Received:', res)
    except Exception as e:
        print('Error:', e)

asyncio.run(test())
