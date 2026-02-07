import asyncio
import os
import json
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

async def main():
    try:
        print("Testing Groq API with JSON...")
        response = await client.chat.completions.create(
            model="moonshotai/kimi-k2-instruct-0905",
            messages=[
                {"role": "system", "content": "You are a helper. Output JSON: {\"message\": \"hello context\"}"},
                {"role": "user", "content": "hi"}
            ],
            response_format={"type": "json_object"}, 
            temperature=0.1
        )
        content = response.choices[0].message.content
        print("Raw Content:", content)
        print("Parsed:", json.loads(content))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
