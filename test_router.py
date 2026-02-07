import asyncio
import sys
sys.path.insert(0, '/Users/shayFeldboy/Documents/shay/AI_Super_man')

from app.services.router_service import route_intent

async def test():
    try:
        print("Testing router with 'היי'...")
        result = await route_intent("היי")
        print(f"Success! Classification: {result.classification}")
        print(f"Full result: {result}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
