import asyncio
import os
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

async def main():
    try:
        print("Testing Groq API...")
        response = await client.chat.completions.create(
            model="moonshotai/kimi-k2-instruct-0905",
            messages=[{"role": "user", "content": "Hello"}],
        )
        print("Success:", response.choices[0].message.content)
    except Exception as e:
        print(f"Error: {e}")
        
        print("Attempting with 'llama3-8b-8192'...")
        try:
             response = await client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": "Hello"}],
            )
             print("Success with Llama3:", response.choices[0].message.content)
        except Exception as e2:
             print(f"Error with Llama3: {e2}")

if __name__ == "__main__":
    asyncio.run(main())
