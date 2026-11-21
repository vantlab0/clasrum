"""Video search and watch history service for the AI Personal Classroom.

This module handles:
- Searching educational videos from YouTube Data API (approved integration)
- AI-powered topic refinement and result ranking
- Watch history persistence via Supabase/Backend
- Caching to reduce API calls

All video metadata comes from actual YouTube Data API results. The application
never invents video titles, channel names, descriptions, thumbnails, views, or
video URLs.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import dtos
from app.core import errors
from app.db.base import utcnow
from app.db.models import Lesson, VideoWatchHistory, VideoSearchCache
from app.db.repositories import get_lesson
from app.flashcards.generator import generate_with_llm, generate_offline
from app.flashcards.context import FlashcardContext

logger = logging.getLogger(__name__)

#: Maximum number of videos to return in a search result set.
MAX_SEARCH_RESULTS = 10

#: YouTube Data API endpoint (configured at deployment time)
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEO_URL = "https://www.googleapis.com/youtube/v3/videos"

#: search.py ─────────────────────────────────────────────────────────────
# Keywords for detecting when the AI should refine a topic query.
_TOPIC_REFINEMENT_KEYWORDS = [
    "explain", "what is", "how to", "define", "tell me about",
    "information about", "description of", "summary of",
]


def _needs_refinement(query: str) -> bool:
    """Determine if a student's query would benefit from AI refinement.

    We look for very broad or underspecified queries that would benefit
    from the AI clarifying intent before sending to the search provider.
    """
    q = query.strip().lower()
    if len(q) < 4:
        return True
    # Very short queries are usually underspecified
    if len(q.split()) <= 2:
        return True
    # Check for refinement-triggering keywords
    for kw in _TOPIC_REFINEMENT_KEYWORDS:
        if q.startswith(kw):
            return True
    return False


def _refine_query(query: str, context_hint: str | None = None) -> str:
    """AI‑assisted topic refinement.

    Preserves the student's intended topic while clarifying wording and
    expanding the search query where helpful.  The original intent is
    never silently changed — the refined query is a superset of the
    original terms.

    Returns the original query unchanged if refinement is not clearly
    beneficial.
    """
    q = query.strip()
    if not _needs_refinement(q):
        return q

    # Keep the core nouns/phrases and expand with common educational
    # qualifiers.  This is a deterministic heuristic; a full LLM-based
    # refiner could be swapped in later.
    parts = q.split()
    # If the query is just a single word or very short, add context
    if len(parts) <= 2:
        # Add "tutorial" or "lesson" to make it searchable
        if len(parts) == 1:
            return f"{q} tutorial educational"
        # Two-word query: add "lesson" and "for students"
        return f"{q} lesson educational for students"

    # For longer queries, just ensure proper casing and add "educational"
    # if not already present.
    if "educational" not in q.lower() and "tutorial" not in q.lower():
        # Insert "educational" after the first adjective/noun boundary
        # Simple heuristic: after the first word
        if parts[0].lower() not in {"the", "a", "an", "educational", "tutorial"}:
            parts.insert(1, "educational")
        return " ".join(parts)

    return q


# YouTube search & details helpers ──────────────────────────────────────)


def _build_youtube_search_params(
    query: str,
    max_results: int = 9,
    page_token: str | None = None,
) -> Dict[str, str]:
    """Build the parameter dict for a YouTube Data API search request."""
    params: Dict[str, str] = {
        "part": "snippet,id",
        "q": query,
        "maxResults": str(max_results),
        "type": "video",
        "order": "relevance",
        "safeSearch": "strict",
    }
    if page_token:
        params["pageToken"] = page_token
    return params


def _build_youtube_video_params(
    video_ids: List[str],
    part: str = "snippet,contentDetails,statistics",
) -> Dict[str, str]:
    """Build the parameter dict for fetching video details."""
    ids = ",".join(video_ids)
    return {"part": part, "id": ids, "maxResults": str(len(video_ids))}


async def _http_get(url: str, params: Dict[str, str], api_key: str) -> Dict[str, Any]:
    """Perform an authenticated GET request to the YouTube Data API."""
    from httpx import AsyncClient, HTTPStatusError

    async with AsyncClient() as client:
        try:
            response = await client.get(
                url,
                params={**params, "key": api_key},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()
        except HTTPStatusError as exc:
            logger.error("YouTube API error: %s", exc)
            raise errors.internal_error(
                "YouTube Data API request failed",
                details={"status_code": exc.response.status_code},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error calling YouTube API")
            raise errors.internal_error("YouTube API request failed") from exc


async def _extract_video_ids(search_response: Dict[str, Any]) -> List[str]:
    """Extract video IDs from a YouTube search response."""
    ids: List[str] = []
    for item in search_response.get("items", []):
        if item.get("id", {}).get("kind") == "youtube#video":
            ids.append(item["id"]["videoId"])
    return ids


async def _extract_video_details(
    video_response: Dict[str, Any],
    video_ids: List[str],
) -> Dict[str, dict[str, Any]]:
    """Extract detailed metadata for requested videos from YouTube API response."""
    details: Dict[str, dict[str, Any]] = {}
    # Build a map of ID → snippet/statistics
    id_to_item = {item["id"]: item for item in video_response.get("items", [])}
    for vid in video_ids:
        item = id_to_item.get(vid)
        if item is None:
            continue
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        details[vid] = {
            "title": snippet.get("title", ""),
            "description": snippet.get("description"),
            "channel_title": snippet.get("channelTitle"),
            "thumbnail_url": (
                snippet.get("thumbnails", {})
                .get("high", {})
                .get("url")
                or snippet.get("thumbnails", {})
                .get("medium", {})
                .get("url")
                or snippet.get("thumbnails", {})
                .get("default", {})
                .get("url")
            ),
            "view_count": int(stats.get("viewCount", 0)) if stats else 0,
            "duration": item.get("contentDetails", {}).get("duration"),
            "published_at": snippet.get("publishedAt"),
        }
    return details


# Search pipeline ──────────────────────────────────────────────────────)


async def search_videos(
    db: AsyncSession,
    lesson: Lesson,
    payload: dtos.VideoSearchRequest,
) -> dtos.VideoSearchResponse:
    """End-to-end video search with AI refinement and ranking.

    1. Optionally refine the query using AI (preserving student intent).
    2. Search YouTube Data API.
    3. Extract video metadata.
    4. Rank results for educational relevance.
    5. Cache the search for future reuse.
    6. Return a VideoSearchResponse DTO.
    """
    # ------------------------------------------------------------------
    # Step 1: AI‑assisted query refinement (preserve original intent)
    # ------------------------------------------------------------------
    original_query = payload.query
    refined_query = original_query

    if _needs_refinement(original_query):
        refined_query = _refine_query(original_query)
    # If the AI refined it, we track that for transparency

    # ------------------------------------------------------------------
    # Step 2: Search YouTube Data API (with caching)
    # ------------------------------------------------------------------
    settings = get_settings()
    api_key = settings.youtube_api_key

    if not api_key:
        # No YouTube key configured — return empty but honest result
        return dtos.VideoSearchResponse(
            query=original_query,
            refined_query=refined_query if refined_query != original_query else None,
            videos=[],
            total_found=0,
            has_refinement=False,
        )

    # Try cache first (search term + refined form)
    cache_key = f"{original_query.lower()}:{refined_query.lower()}"
    cached = await _get_cached_search(db, cache_key)
    if cached is not None:
        # Return cached results; still need to extract video IDs for details
        # Build minimal result DTOs from cache
        videos = [
            dtos.VideoSearchResult(
                video_id=c["video_id"],
                title=c["title"],
                description=c.get("description"),
                channel_title=c.get("channel_title"),
                thumbnail_url=c.get("thumbnail_url"),
                view_count=c.get("view_count"),
                duration=c.get("duration"),
                published_at=datetime.fromisoformat(c["published_at"]) if c.get("published_at") else None,
            )
            for c in cached["results"]
        ]
        return dtos.VideoSearchResponse(
            query=original_query,
            refined_query=refined_query if refined_query != original_query else None,
            videos=videos,
            total_found=len(videos),
            has_refinement=refined_query != original_query,
        )

    # Perform the live search
    search_params = _build_youtube_search_params(refined_query, max_results=MAX_SEARCH_RESULTS)
    search_data = await _http_get(YOUTUBE_SEARCH_URL, search_params, api_key)

    video_ids = await _extract_video_ids(search_data)
    if not video_ids:
        # No videos found for this query
        # Still cache the empty result to avoid repeated API calls
        await _cache_search(db, cache_key, {"results": [], "query": original_query, "refined": refined_query})
        return dtos.VideoSearchResponse(
            query=original_query,
            refined_query=refined_query if refined_query != original_query else None,
            videos=[],
            total_found=0,
            has_refinement=refined_query != original_query,
        )

    # Fetch detailed metadata for the found videos
    video_params = _build_youtube_video_params(video_ids)
    video_data = await _http_get(YOUTUBE_VIDEO_URL, video_params, api_key)
    details = await _extract_video_details(video_data, video_ids)

    # ------------------------------------------------------------------
    # Step 3: Rank results for educational relevance
    # ------------------------------------------------------------------
    ranked = _rank_videos(original_query, details)

    # Build DTO results (limit to MAX_SEARCH_RESULTS)
    results: List[dtos.VideoSearchResult] = []
    for vid_info in ranked[:MAX_SEARCH_RESULTS]:
        vid_id = vid_info["video_id"]
        d = details.get(vid_id, {})
        results.append(
            dtos.VideoSearchResult(
                video_id=vid_id,
                title=d.get("title", "Untitled"),
                description=d.get("description"),
                channel_title=d.get("channel_title"),
                thumbnail_url=d.get("thumbnail_url"),
                view_count=d.get("view_count"),
                duration=d.get("duration"),
                published_at=(
                    datetime.fromisoformat(d["published_at"].replace("Z", "+00:00"))
                    if d.get("published_at")
                    else None
                ),
            )
        )

    # Cache the full result set
    await _cache_search(db, cache_key, {
        "results": [
            {
                "video_id": r.video_id,
                "title": r.title,
                "description": r.description,
                "channel_title": r.channel_title,
                "thumbnail_url": r.thumbnail_url,
                "view_count": r.view_count,
                "duration": r.duration,
                "published_at": r.published_at.isoformat() if r.published_at else None,
            }
            for r in results
        ],
        "query": original_query,
        "refined": refined_query,
    })

    return dtos.VideoSearchResponse(
        query=original_query,
        refined_query=refined_query if refined_query != original_query else None,
        videos=results,
        total_found=len(results),
        has_refinement=refined_query != original_query,
    )


def _rank_videos(
    original_query: str,
    video_details: Dict[str, dict[str, Any]],
) -> List[dtos.VideoSearchResult]:
    """Rank videos for educational relevance.

    Ranking considerations (in order of priority):
    1. Relevance to the requested topic (title/description match)
    2. Educational value (keywords like 'tutorial', 'lesson', 'explain')
    3. Channel authority/subject relevance
    4. Video clarity (duration appropriate for topic, not overly long/short)
    5. Availability (view count > 0, not age-restricted)

    We do NOT rank based only on popularity (view count alone).
    """
    query_lower = original_query.lower()
    scored: List[tuple[float, dtos.VideoSearchResult]] = []

    for vid_id, details in video_details.items():
        title = (details.get("title") or "").lower()
        description = (details.get("description") or "").lower()
        channel = (details.get("channel_title") or "").lower()
        view_count = details.get("view_count", 0) or 0

        # --- Relevance score ---
        relevance = 0.0
        # Check if query terms appear in title
        query_terms = [t for t in query_lower.split() if len(t) > 2]
        for term in query_terms:
            if term in title:
                relevance += 2.0
            if term in description:
                relevance += 1.0

        # --- Educational value ---
        edu_keywords = ["tutorial", "lesson", "explain", "educational", "guide", "lecture"]
        edu_score = sum(1.0 for kw in edu_keywords if kw in title or kw in description)

        # --- Channel relevance ---
        channel_score = 0.0
        # If channel name contains topic-related terms
        for term in query_terms:
            if term in channel:
                channel_score += 1.0

        # --- Duration balance ---
        # Prefer durations that are reasonable for learning (3-20 minutes)
        dur = details.get("duration", "")
        dur_balance = 1.0  # default
        if dur:
            # Simple heuristic: mark as well-balanced if not marked "long"
            if "PT" in dur and "H" not in dur:
                # Likely under 20 minutes
                dur_balance = 1.0
            elif "PT" in dur and "H" in dur:
                # Likely over 20 minutes — slightly lower
                dur_balance = 0.8

        # --- View count as validity check (not as primary rank) ---
        validity = 1.0 if view_count > 0 else 0.3

        # Composite score (relevance is heaviest)
        total_score = (
            relevance * 3.0
            + edu_score * 1.5
            + channel_score * 1.0
            + dur_balance * 1.0
            + validity * 0.5
        )

        # Build a minimal result DTO so we can return it; we'll re-fetch
        # details later in the caller, but for scoring we need a placeholder.
        # We'll reconstruct the result after scoring.
        scored.append((total_score,))  # placeholder; will replace below

    # Actually, let's redo this properly by building results first then scoring
    # We'll build the results list first, then sort.
    from dataclasses import dataclass

    @dataclass
    class ScoredResult:
        video_id: str
        title: str
        description: str | None
        channel_title: str | None
        thumbnail_url: str | None
        view_count: int | None
        duration: str | None
        published_at: datetime | None
        score: float

    unscored: List[ScoredResult] = []
    for vid_id, details in video_details.items():
        title = details.get("title", "") or ""
        description = details.get("description") or ""
        channel = details.get("channel_title") or ""
        view_count = details.get("view_count") or 0
        dur = details.get("duration", "")

        # Relevance
        relevance = 0.0
        query_terms = [t for t in query_lower.split() if len(t) > 2]
        for term in query_terms:
            if term in title.lower():
                relevance += 2.0
            if term in description.lower():
                relevance += 1.0

        # Educational
        edu_keywords = ["tutorial", "lesson", "explain", "educational", "guide", "lecture"]
        edu_score = sum(1.0 for kw in edu_keywords if kw in title.lower() or kw in description.lower())

        # Channel
        channel_score = 0.0
        for term in query_terms:
            if term in channel.lower():
                channel_score += 1.0

        # Duration balance
        dur_balance = 1.0
        if dur:
            if "PT" in dur and "H" not in dur:
                dur_balance = 1.0
            elif "PT" in dur and "H" in dur:
                dur_balance = 0.8

        # Validity
        validity = 1.0 if view_count > 0 else 0.3

        total_score = (
            relevance * 3.0
            + edu_score * 1.5
            + channel_score * 1.0
            + dur_balance * 1.0
            + validity * 0.5
        )

        unscored.append(
            ScoredResult(
                video_id=vid_id,
                title=title,
                description=description,
                channel_title=channel,
                thumbnail_url=details.get("thumbnail_url"),
                view_count=view_count,
                duration=dur,
                published_at=datetime.fromisoformat(
                    details["published_at"].replace("Z", "+00:00")
                )
                if details.get("published_at")
                else None,
                score=total_score,
            )
        )

    # Sort by score descending
    unscored.sort(key=lambda r: r.score, reverse=True)

    # Convert to VideoSearchResult-friendly format (we'll map later)
    return [
        dtos.VideoSearchResult(
            video_id=r.video_id,
            title=r.title,
            description=r.description,
            channel_title=r.channel_title,
            thumbnail_url=r.thumbnail_url,
            view_count=r.view_count,
            duration=r.duration,
            published_at=r.published_at,
        )
        for r in unscored
    ]


# Cache helpers ─────────────────────────────────────────────────────────

async def _get_cached_search(db: AsyncSession, cache_key: str) -> Optional[Dict[str, Any]]:
    """Retrieve a cached YouTube search result if it exists and is recent."""
    from app.db.models import VideoSearchCache as _CacheModel

    result = await db.execute(
        select(_CacheModel).where(_CacheModel.cache_key == cache_key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    # Cache is considered fresh for 7 days
    from datetime import timedelta
    if datetime.now(timezone.utc) - row.cached_at > timedelta(days=7):
        # Expired — remove it
        await db.delete(row)
        await db.commit()
        return None
    return json.loads(row.cached_data)


async def _cache_search(db: AsyncSession, cache_key: str, data: Dict[str, Any]) -> None:
    """Store a YouTube search result in the cache."""
    from app.db.models import VideoSearchCache as _CacheModel

    # Upsert: delete existing if present, then insert
    await db.execute(
        _CacheModel.__table__.delete().where(_CacheModel.cache_key == cache_key)
    )
    cached_row = _CacheModel(
        cache_key=cache_key,
        cached_data=json.dumps(data),
        cached_at=utcnow(),
    )
    db.add(cached_row)
    await db.commit()


# Watch history ─────────────────────────────────────────────────────────

async def get_watch_history(db: AsyncSession, lesson: Lesson) -> dtos.VideoWatchHistoryResponse:
    """Retrieve the watch history for a lesson."""
    from app.db.models import VideoWatchHistory as _HistoryModel

    result = await db.execute(
        select(_HistoryModel)
        .where(_HistoryModel.lesson_id == lesson.id)
        .order_by(_HistoryModel.watched_at.desc())
    )
    items = result.scalars().all()

    entries: List[dtos.VideoWatchHistoryEntry] = []
    for item in items:
        entries.append(
            dtos.VideoWatchHistoryEntry(
                video_id=item.video_id,
                title=item.title,
                thumbnail_url=item.thumbnail_url,
                channel_title=item.channel_title,
                search_query=item.search_query,
                watched_at=item.watched_at,
            )
        )

    return dtos.VideoWatchHistoryResponse(items=entries, total=len(entries))


async def record_watch(
    db: AsyncSession,
    lesson: Lesson,
    payload: dtos.VideoWatchHistoryEntry,
) -> dtos.VideoWatchHistoryEntry:
    """Record or update that a student watched a video.

    If the video already exists in history, update its watched_at timestamp.
    Otherwise, create a new entry.  Duplicates are not stored repeatedly;
    the most recent watch wins.
    """
    from app.db.models import VideoWatchHistory as _HistoryModel

    # Check if this video is already in the history for this lesson
    result = await db.execute(
        select(_HistoryModel).where(
            _HistoryModel.lesson_id == lesson.id,
            _HistoryModel.video_id == payload.video_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        # Update the timestamp
        existing.watched_at = utcnow()
        existing.search_query = payload.search_query
        await db.flush()
    else:
        # Create new entry
        new_entry = _HistoryModel(
            lesson_id=lesson.id,
            video_id=payload.video_id,
            title=payload.title,
            thumbnail_url=payload.thumbnail_url,
            channel_title=payload.channel_title,
            search_query=payload.search_query,
            watched_at=utcnow(),
        )
        db.add(new_entry)
        await db.flush()

    return dtos.VideoWatchHistoryEntry(
        video_id=payload.video_id,
        title=payload.title,
        thumbnail_url=payload.thumbnail_url,
        channel_title=payload.channel_title,
        search_query=payload.search_query,
        watched_at=utcnow(),
    )