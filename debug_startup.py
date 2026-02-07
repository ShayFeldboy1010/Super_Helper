import asyncio
import sys
import logging

# Force stdout logging
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

print("Starting debug script...")

try:
    print("Importing app.main...")
    from app.main import app
    print("Import successful.")
except Exception as e:
    print(f"CRITICAL IMPORT ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

async def test_startup():
    print("Running lifespan startup...")
    try:
        async with app.router.lifespan_context(app):
            print("Startup complete. Lifespan active.")
            await asyncio.sleep(2)
            print("Shutting down...")
    except Exception as e:
            print(f"LIFESPAN ERROR: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_startup())
