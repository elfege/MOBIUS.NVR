import asyncio
import json
import websockets

async def test_captcha(code):
    print(f"\n=== TESTING CAPTCHA: {code} ===\n")
    
    async with websockets.connect('ws://127.0.0.1:3000', open_timeout=5) as ws:
        # Step 1: Set schema
        cmd1 = {"messageId": "schema", "command": "set_api_schema", "schemaVersion": 21}
        print(f"SEND: {json.dumps(cmd1)}")
        await ws.send(json.dumps(cmd1))
        resp1 = await ws.recv()
        print(f"RECV: {resp1}\n")
        
        # Step 2: Start listening  
        cmd2 = {"messageId": "start", "command": "start_listening"}
        print(f"SEND: {json.dumps(cmd2)}")
        await ws.send(json.dumps(cmd2))
        resp2 = await ws.recv()
        print(f"RECV: {resp2}\n")
        
        # Step 3: Send captcha (try different formats)
        formats = [
            {"messageId": "cap1", "command": "driver.set_captcha", "captchaCode": code},
            {"messageId": "cap2", "command": "driver.set_captcha", "captcha": code},
            {"messageId": "cap3", "command": "set_captcha", "captchaCode": code},
        ]
        
        for fmt in formats:
            print(f"SEND: {json.dumps(fmt)}")
            await ws.send(json.dumps(fmt))
            resp3 = await ws.recv()
            print(f"RECV: {resp3}\n")

if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "TEST"
    asyncio.run(test_captcha(code))
