"""Schedule recommendation service — suggests optimal publishing times.

Generates time slots for publishing based on platform best-practices.
Does NOT use real analytics data (that would be a future enhancement).
Instead uses industry-standard engagement patterns as defaults.

For campaign generation, distributes posts across days with varied
time slots to avoid predictable patterns.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import random

# ── Optimal posting windows per platform (hour ranges in UTC) ─────────────────
# Based on publicly available social media engagement research.
# These are conservative defaults; real per-account analytics would be better.

_OPTIMAL_HOURS: dict[str, list[int]] = {
    "linkedin": [7, 8, 9, 12, 17, 18],       # Business hours: morning + lunch + end of day
    "instagram": [8, 11, 12, 14, 17, 19, 20], # Spread throughout day, peaks at lunch + evening
    "meta": [9, 11, 13, 15, 19, 20],          # Mid-morning, lunch, afternoon, evening
    "twitter": [8, 9, 12, 13, 17, 18],        # Commute + lunch + end of day
    "threads": [9, 12, 18, 20, 21],           # Similar to Instagram, slightly later
    "reddit": [6, 7, 8, 12, 13],             # Early morning (US coasts) + lunch
    "medium": [7, 8, 10, 11],                # Morning readers
    "quora": [9, 10, 14, 15],                # Workday knowledge-seekers
}

# Days of week that tend to perform best (0=Monday, 6=Sunday)
_BEST_DAYS: dict[str, list[int]] = {
    "linkedin": [0, 1, 2, 3],       # Mon-Thu (business days)
    "instagram": [0, 1, 2, 3, 4],   # Mon-Fri
    "meta": [1, 2, 3, 4],           # Tue-Fri
    "twitter": [0, 1, 2, 3, 4],     # Weekdays
    "threads": [0, 1, 2, 3, 4, 5],  # Mon-Sat
    "reddit": [0, 1, 2, 3, 4],      # Weekdays
    "medium": [1, 2, 3],            # Tue-Thu
    "quora": [0, 1, 2, 3, 4],       # Weekdays
}


class ScheduleRecommendationService:
    """Recommends optimal publishing times and generates campaign schedules."""

    def recommend_time(
        self,
        platform: str,
        after: Optional[datetime] = None,
        timezone_offset_hours: int = 0,
    ) -> datetime:
        """
        Recommend a single optimal publishing time for a platform.

        Args:
            platform: Target platform.
            after: Earliest allowed time (default: now + 1 hour).
            timezone_offset_hours: User's timezone offset from UTC.

        Returns:
            A datetime in UTC for the recommended publishing time.
        """
        now = datetime.now(timezone.utc)
        earliest = after or (now + timedelta(hours=1))

        optimal_hours = _OPTIMAL_HOURS.get(platform, [9, 12, 17])
        best_days = _BEST_DAYS.get(platform, [0, 1, 2, 3, 4])

        # Adjust optimal hours for user timezone
        adjusted_hours = [
            (h - timezone_offset_hours) % 24 for h in optimal_hours
        ]

        # Find the next slot that's after `earliest`
        candidate = earliest.replace(minute=0, second=0, microsecond=0)

        for _ in range(168):  # Search up to 7 days ahead (168 hours)
            candidate += timedelta(hours=1)
            if candidate.hour in adjusted_hours and candidate.weekday() in best_days:
                # Add slight randomization (0-30 min) to avoid exact-hour posts
                jitter = timedelta(minutes=random.randint(0, 30))
                return candidate + jitter

        # Fallback: just schedule 24 hours from now
        return earliest + timedelta(hours=24)

    def generate_campaign_schedule(
        self,
        platforms: list[str],
        days: int,
        posts_per_day: int = 1,
        start_date: Optional[datetime] = None,
        timezone_offset_hours: int = 0,
    ) -> list[dict]:
        """
        Generate a multi-day campaign schedule across platforms.

        Args:
            platforms: Platforms to distribute posts across.
            days: Number of days in the campaign.
            posts_per_day: Number of posts per day (distributed across platforms).
            start_date: Campaign start (default: tomorrow).
            timezone_offset_hours: User's timezone offset.

        Returns:
            List of {day, platform, scheduled_at} dicts.
        """
        now = datetime.now(timezone.utc)
        start = start_date or (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        schedule: list[dict] = []
        platform_cycle = _build_platform_rotation(platforms, days * posts_per_day)

        for day_index in range(days):
            day_start = start + timedelta(days=day_index)

            for post_index in range(posts_per_day):
                slot_index = day_index * posts_per_day + post_index
                platform = platform_cycle[slot_index % len(platform_cycle)]

                scheduled_at = self._pick_time_for_day(
                    platform, day_start, timezone_offset_hours, post_index
                )

                schedule.append({
                    "day": day_index + 1,
                    "platform": platform,
                    "scheduled_at": scheduled_at,
                })

        return schedule

    def _pick_time_for_day(
        self,
        platform: str,
        day_start: datetime,
        timezone_offset: int,
        slot_index: int,
    ) -> datetime:
        """Pick an optimal time slot within a specific day."""
        optimal_hours = _OPTIMAL_HOURS.get(platform, [9, 12, 17])
        adjusted = [(h - timezone_offset) % 24 for h in optimal_hours]

        # Pick a different hour for each slot in the same day
        hour = adjusted[slot_index % len(adjusted)]

        result = day_start.replace(hour=hour, minute=random.randint(0, 45))

        # Ensure it's in the future
        now = datetime.now(timezone.utc)
        if result <= now:
            result = now + timedelta(hours=1, minutes=random.randint(0, 30))

        return result


def _build_platform_rotation(platforms: list[str], total_slots: int) -> list[str]:
    """
    Distribute platforms evenly across slots.

    For a 7-day campaign across [linkedin, instagram, meta]:
    → linkedin, instagram, meta, linkedin, instagram, meta, linkedin
    """
    if not platforms:
        return ["linkedin"]

    rotation: list[str] = []
    for i in range(total_slots):
        rotation.append(platforms[i % len(platforms)])
    return rotation
