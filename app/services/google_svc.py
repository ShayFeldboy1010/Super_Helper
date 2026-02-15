import logging
from datetime import datetime, timedelta
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
        """Fetch today's remaining events."""
        return await self.get_events_for_date(None)

    async def get_events_for_date(self, target_date: str = None) -> List[str]:
        """
        Fetch events for a specific date (YYYY-MM-DD) or today if None.
        Returns formatted string lines.
        """
        if not self.creds:
            if not await self.authenticate():
                return ["⚠️ Please connect your Google account first."]

        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Asia/Jerusalem")
            service = build('calendar', 'v3', credentials=self.creds)

            if target_date:
                try:
                    day = datetime.strptime(target_date, "%Y-%m-%d").date()
                except ValueError:
                    day = datetime.now(tz).date()
                day_start = datetime.combine(day, datetime.min.time()).replace(tzinfo=tz)
                day_end = datetime.combine(day, datetime.max.time()).replace(tzinfo=tz)
            else:
                now = datetime.now(tz)
                day_start = now
                day_end = datetime.combine(now.date(), datetime.max.time()).replace(tzinfo=tz)

            events_result = service.events().list(
                calendarId='primary',
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                maxResults=15,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])

            if not events:
                return ["No events."]

            summary_lines = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                display_time = start
                if 'T' in start:
                    dt = datetime.fromisoformat(start)
                    display_time = dt.strftime("%H:%M")

                summary_lines.append(f"• {display_time} - {event['summary']}")

            return summary_lines

        except Exception as e:
            logger.error(f"Calendar API error: {e}")
            return [f"❌ Calendar error: {str(e)}"]

    async def get_todays_events_detailed(self) -> List[Dict[str, Any]]:
        """Fetch today's events as structured dicts for conflict detection."""
        if not self.creds:
            if not await self.authenticate():
                return []

        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Asia/Jerusalem")
            service = build('calendar', 'v3', credentials=self.creds)

            now = datetime.now(tz)
            day_start = now
            day_end = datetime.combine(now.date(), datetime.max.time()).replace(tzinfo=tz)

            events_result = service.events().list(
                calendarId='primary',
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                maxResults=20,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            detailed = []
            for event in events:
                start_raw = event['start'].get('dateTime', event['start'].get('date'))
                end_raw = event['end'].get('dateTime', event['end'].get('date'))
                detailed.append({
                    "summary": event.get('summary', '(no title)'),
                    "start": start_raw,
                    "end": end_raw,
                    "location": event.get('location', ''),
                })
            return detailed

        except Exception as e:
            logger.error(f"Calendar detailed API error: {e}")
            return []

    async def create_calendar_event(
        self,
        title: str,
        start_dt: datetime,
        end_dt: datetime = None,
        location: str = None,
        description: str = None,
    ) -> Optional[str]:
        if not self.creds:
            if not await self.authenticate():
                return None

        try:
            service = build('calendar', 'v3', credentials=self.creds)

            if not end_dt:
                end_dt = start_dt + timedelta(hours=1)

            event = {
                'summary': title,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'Asia/Jerusalem',
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'Asia/Jerusalem',
                },
            }
            if location:
                event['location'] = location
            if description:
                event['description'] = description

            event = service.events().insert(calendarId='primary', body=event).execute()
            return event.get('htmlLink')

        except Exception as e:
            logger.error(f"Failed to create event: {e}")
            return None

    async def get_recent_emails(self, max_results: int = 5) -> List[Dict[str, str]]:
        """Fetch recent emails with sender, subject, and snippet."""
        if not self.creds:
            if not await self.authenticate():
                return []

        try:
            service = build('gmail', 'v1', credentials=self.creds)
            results = service.users().messages().list(
                userId='me', maxResults=max_results, labelIds=['INBOX']
            ).execute()
            messages = results.get('messages', [])

            emails = []
            for msg_meta in messages:
                msg = service.users().messages().get(
                    userId='me', id=msg_meta['id'], format='metadata',
                    metadataHeaders=['From', 'Subject']
                ).execute()

                headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
                emails.append({
                    'from': headers.get('From', 'Unknown'),
                    'subject': headers.get('Subject', '(no subject)'),
                    'snippet': msg.get('snippet', ''),
                })
            return emails

        except Exception as e:
            logger.error(f"Gmail API error: {e}")
            return []

    async def get_upcoming_events_detailed(self, minutes_ahead: int = 20) -> List[Dict[str, Any]]:
        """Fetch events starting within the next N minutes, with attendee info."""
        if not self.creds:
            if not await self.authenticate():
                return []

        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Asia/Jerusalem")
            service = build('calendar', 'v3', credentials=self.creds)

            now = datetime.now(tz)
            window_end = now + timedelta(minutes=minutes_ahead)

            events_result = service.events().list(
                calendarId='primary',
                timeMin=now.isoformat(),
                timeMax=window_end.isoformat(),
                maxResults=5,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            detailed = []
            for event in events:
                start_raw = event['start'].get('dateTime', event['start'].get('date'))
                # Skip all-day events (no time component)
                if 'T' not in start_raw:
                    continue

                end_raw = event['end'].get('dateTime', event['end'].get('date'))

                # Parse attendees, filter out resource rooms
                attendees = []
                for att in event.get('attendees', []):
                    email = att.get('email', '')
                    if '@group.calendar.google.com' in email:
                        continue
                    attendees.append({
                        'name': att.get('displayName', email.split('@')[0]),
                        'email': email,
                    })

                detailed.append({
                    "event_id": event.get('id', ''),
                    "summary": event.get('summary', '(no title)'),
                    "start": start_raw,
                    "end": end_raw,
                    "location": event.get('location', ''),
                    "description": event.get('description', ''),
                    "attendees": attendees,
                    "recurring_event_id": event.get('recurringEventId', ''),
                })
            return detailed

        except Exception as e:
            logger.error(f"Upcoming events detailed API error: {e}")
            return []

    async def search_emails_from_sender(self, sender_email: str, max_results: int = 3) -> List[Dict[str, str]]:
        """Search Gmail for recent emails from a specific sender."""
        if not self.creds:
            if not await self.authenticate():
                return []

        try:
            service = build('gmail', 'v1', credentials=self.creds)
            results = service.users().messages().list(
                userId='me',
                q=f"from:{sender_email}",
                maxResults=max_results,
            ).execute()
            messages = results.get('messages', [])

            emails = []
            for msg_meta in messages:
                msg = service.users().messages().get(
                    userId='me', id=msg_meta['id'], format='metadata',
                    metadataHeaders=['From', 'Subject', 'Date']
                ).execute()

                headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
                emails.append({
                    'from': headers.get('From', 'Unknown'),
                    'subject': headers.get('Subject', '(no subject)'),
                    'date': headers.get('Date', ''),
                    'snippet': msg.get('snippet', ''),
                })
            return emails

        except Exception as e:
            logger.error(f"Gmail sender search error for {sender_email}: {e}")
            return []

    async def get_recent_unread_emails(self, max_results: int = 10, minutes_back: int = 35) -> List[Dict[str, str]]:
        """Fetch recent unread emails from inbox."""
        if not self.creds:
            if not await self.authenticate():
                return []

        try:
            import time
            service = build('gmail', 'v1', credentials=self.creds)
            cutoff_epoch = int(time.time()) - (minutes_back * 60)

            results = service.users().messages().list(
                userId='me',
                q=f"is:unread after:{cutoff_epoch}",
                labelIds=['INBOX'],
                maxResults=max_results,
            ).execute()
            messages = results.get('messages', [])

            emails = []
            for msg_meta in messages:
                msg = service.users().messages().get(
                    userId='me', id=msg_meta['id'], format='metadata',
                    metadataHeaders=['From', 'Subject']
                ).execute()

                headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
                emails.append({
                    'id': msg_meta['id'],
                    'from': headers.get('From', 'Unknown'),
                    'subject': headers.get('Subject', '(no subject)'),
                    'snippet': msg.get('snippet', ''),
                })
            return emails

        except Exception as e:
            logger.error(f"Gmail unread fetch error: {e}")
            return []

    async def get_unread_count(self) -> int:
        """Return the count of unread emails in the inbox."""
        if not self.creds:
            if not await self.authenticate():
                return 0

        try:
            service = build('gmail', 'v1', credentials=self.creds)
            results = service.users().messages().list(
                userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=1
            ).execute()
            return results.get('resultSizeEstimate', 0)

        except Exception as e:
            logger.error(f"Gmail unread count error: {e}")
            return 0
