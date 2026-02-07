import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from app.core.config import settings
from app.core.database import supabase
from app.core.security import decrypt_token

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/gmail.readonly']

class GoogleService:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.creds = None

    async def authenticate(self) -> bool:
        """
        Retrieves the user's refresh token from Supabase, decrypts it, 
        and builds valid Google Credentials.
        """
        try:
            # Fetch user from DB
            response = supabase.table("users").select("google_refresh_token").eq("telegram_id", self.user_id).execute()
            if not response.data:
                logger.warning(f"User {self.user_id} not found in DB")
                return False
            
            encrypted_token = response.data[0].get("google_refresh_token")
            if not encrypted_token:
                logger.warning(f"No refresh token for user {self.user_id}")
                return False
            
            refresh_token = decrypt_token(encrypted_token)
            
            # Create Credentials object
            # Note: access_token is None, it will refresh automatically
            self.creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.GOOGLE_CLIENT_ID,
                client_secret=settings.GOOGLE_CLIENT_SECRET,
                scopes=SCOPES
            )
            return True
        except Exception as e:
            logger.error(f"Auth error for {self.user_id}: {e}")
            return False

    async def get_todays_events(self) -> List[str]:
        if not self.creds:
            if not await self.authenticate():
                return ["⚠️ Please connect your Google Account first."]

        try:
            service = build('calendar', 'v3', credentials=self.creds)
            
            # Define 'Today' range
            now = datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
            # Ideally we'd calculate end of day in user's timezone, 
            # but for MVP listing next 10 events is a good proxy or strict today query.
            # Let's verify timezone from user? defaulted to Asia/Jerusalem in schema.
            
            events_result = service.events().list(
                calendarId='primary', 
                timeMin=now,
                maxResults=10, 
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            if not events:
                return ["No upcoming events found."]

            summary_lines = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                # Clean up date string 2026-02-02T10:00:00+02:00 -> 10:00
                display_time = start
                if 'T' in start:
                    dt = datetime.fromisoformat(start)
                    display_time = dt.strftime("%H:%M")
                
                summary_lines.append(f"• {display_time} - {event['summary']}")
            
            return summary_lines

        except Exception as e:
            logger.error(f"Calendar API error: {e}")
            return [f"❌ Error fetching calendar: {str(e)}"]

    async def create_calendar_event(self, title: str, start_dt: datetime) -> Optional[str]:
         # Validating auth first
        if not self.creds:
            if not await self.authenticate():
                return None

        try:
            service = build('calendar', 'v3', credentials=self.creds)
            
            event = {
                'summary': title,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'Asia/Jerusalem', # TODO: Fetch from user prefs
                },
                'end': {
                    'dateTime': (start_dt.replace(hour=start_dt.hour + 1)).isoformat(), # Default 1h duration
                    'timeZone': 'Asia/Jerusalem',
                },
            }
            
            event = service.events().insert(calendarId='primary', body=event).execute()
            return event.get('htmlLink')
            
        except Exception as e:
            logger.error(f"Failed to create event: {e}")
            return None
