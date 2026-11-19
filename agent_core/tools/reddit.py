"""Find recently published subreddit threads in the web search index."""

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from agent_core.tools.web import MAX_SEARCH_RESULTS, _send_search_payload, build_search_payload


WINDOW_DAYS = {"day": 1, "week": 7, "month": 30}
BOARD_MENTION = re.compile(r"\br/([a-z0-9_]{2,21})\b", re.ASCII | re.IGNORECASE)


class RedditLookupScope:
    """Remember completed or declined board lookups for one user request."""

    def __init__(self, user_messages):
        self.messages = [message for message in user_messages if isinstance(message, str)]
        self.has_prefixed_name = any(BOARD_MENTION.search(message) for message in self.messages)
        self.completed = set()
        self.closed = {}

    def check_board(self, board):
        board = board.casefold()
        self.check_target(board)
        if board in self.completed:
            raise ValueError("This subreddit has already been searched for this request. Use the returned threads.")

    def check_target(self, board):
        if board in self.closed:
            raise ValueError(self.closed[board])
        if self.has_prefixed_name:
            # A request can use both spellings, as in "compare r/rust and Python"
            spelling = re.compile(r"\b" + re.escape(board) + r"\b", re.ASCII | re.IGNORECASE)
            if not any(spelling.search(message) for message in self.messages):
                raise ValueError("Use the subreddit named by the user. Do not guess an alternative spelling.")

    def check_web_request(self, target):
        if not self.closed:
            return
        # Catch both subreddit URLs and search terms such as "Reddit Dakimakuras"
        lowered = target.casefold()
        if lowered.startswith(("https://", "http://")):
            url = urlsplit(lowered)
            host = url.hostname or ""
            if host != "reddit.com" and not host.endswith(".reddit.com"):
                return
            boards = BOARD_MENTION.findall(url.path)
        else:
            boards = BOARD_MENTION.findall(lowered)
            words = re.findall(r"[a-z0-9_]+", lowered)
            if "reddit" in words:
                boards.extend(word for word in words if word in self.closed)
        for board in boards:
            self.check_target(board)


def _published_at(value):
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        try:
            timestamp = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def search_reddit(subreddit: str, query: str, window: str, *, on_web_approval=None, lookup_scope=None) -> str:
    if not isinstance(subreddit, str) or not re.fullmatch(r"[A-Za-z0-9_]{2,21}", subreddit):
        raise ValueError("Use one subreddit name without the r/ prefix.")
    if not isinstance(window, str) or window not in WINDOW_DAYS:
        raise ValueError("Choose a window of day, week, or month.")
    if not isinstance(query, str):
        raise ValueError("The topic must be text.")
    if lookup_scope is not None:
        lookup_scope.check_board(subreddit)
    terms = f"r/{subreddit} {query.strip()} past {window} site:reddit.com/r/{subreddit}"
    payload = build_search_payload(terms)
    payload.update(time_range=window, include_domains=["reddit.com"],
                   include_published_date=True, max_results=20)
    if on_web_approval is None or on_web_approval(terms) is not True:
        if lookup_scope is not None:
            lookup_scope.closed[subreddit.casefold()] = f"The search of r/{subreddit} was declined. Report the denial and stop this lookup."
        raise PermissionError("Reddit search denied. No request was sent.")

    result = _send_search_payload(payload)
    threads, undated, exclusions = _select_threads(result["results"], subreddit, window)
    matches = len(threads) + len(undated)
    threads = threads[:MAX_SEARCH_RESULTS]
    undated = undated[:MAX_SEARCH_RESULTS - len(threads)]
    output = {
        "provider": "tavily",
        "subreddit": subreddit,
        "window": window,
        "query": terms,
        "candidates_checked": len(result["results"]),
        "matching_threads": matches,
        "excluded_candidates": exclusions,
        "results": threads,
        "unverified_date": undated,
        "truncated": matches > MAX_SEARCH_RESULTS,
    }
    if matches == 0:
        note = (f"No qualifying r/{subreddit} thread links in the inspected search results. "
                "Report this coverage limit. The board's activity is unknown. Stop this lookup.")
        output["coverage_note"] = note
        if lookup_scope is not None:
            lookup_scope.closed[subreddit.casefold()] = note
    elif lookup_scope is not None:
        lookup_scope.completed.add(subreddit.casefold())
    return json.dumps(output, ensure_ascii=False)


def _thread_identity(link):
    try:
        url = urlsplit(link)
    except ValueError:
        return None
    if url.scheme != "https" or url.netloc.lower() not in ("reddit.com", "www.reddit.com", "old.reddit.com"):
        return None
    match = re.fullmatch(r"/r/(\w+)/comments/([a-z0-9]+)(?:/[^/]*)?/?", url.path, re.ASCII | re.IGNORECASE)
    if match:
        return match[1].lower(), match[2].lower()
    return None


def _select_threads(items, subreddit, window):
    now = datetime.now(timezone.utc)
    earliest = now - timedelta(days=WINDOW_DAYS[window])
    dated = {}
    undated = {}
    outside_window = set()
    exclusions = Counter()
    for item in items:
        identity = _thread_identity(item["url"])
        if identity is None:
            exclusions["not_thread_link"] += 1
            continue
        board, post_id = identity
        if board != subreddit.lower():
            exclusions["other_subreddit"] += 1
            continue
        # The search index sometimes stores Reddit's access challenge at a real thread URL
        if item["title"].strip().casefold() == "reddit - prove your humanity":
            exclusions["access_challenge"] += 1
            continue
        if post_id in dated or post_id in outside_window:
            exclusions["duplicate"] += 1
            continue
        published = _published_at(item.get("published_date"))
        if published is None:
            if post_id not in undated:
                thread = item.copy()
                thread.pop("published_date", None)
                undated[post_id] = thread
            else:
                exclusions["duplicate"] += 1
        elif earliest <= published <= now:
            dated[post_id] = item
            if undated.pop(post_id, None) is not None:
                exclusions["duplicate"] += 1
        else:
            exclusions["outside_window"] += 1
            outside_window.add(post_id)
            if undated.pop(post_id, None) is not None:
                exclusions["duplicate"] += 1
    return list(dated.values()), list(undated.values()), dict(exclusions)
