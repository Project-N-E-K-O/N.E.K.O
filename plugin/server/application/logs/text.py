"""Historical text formats and query filtering shared by snapshots and subscriptions."""
from datetime import datetime, timezone
import re

def _parse_log_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    candidates = (stripped, stripped.replace("T", " "))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(stripped, fmt)
        except ValueError:
            continue
    return None



def parse_log_line(line: str) -> dict[str, object] | None:
    """
    Parse a log line.

    Support several formats:
    1. Plugin: 2024-01-01 00:00:00 - [plugin.xxx] - INFO - file.py:123 - message.
    2. Server: 2024-01-01 00:00:00,123 - user_plugin_server - INFO - message.
    3. Standard: 2024-01-01 00:00:00 - INFO - message.
    4. Pipes: 2024-01-01 00:00:00 | INFO | module:function:123 | message.
    """
    line = line.strip()
    if not line:
        return None

    # Pattern 1, plugin: 2024-01-01 00:00:00 - [plugin.xxx] - INFO - file.py:123 - message.
    pattern1 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - \[([^\]]+)\] - (\w+) - ([^:]+):(\d+) - (.+)'
    match = re.match(pattern1, line)
    if match:
        timestamp, name, level, file, line_num, message = match.groups()
        return {
            "timestamp": timestamp,
            "level": level,
            "file": file,
            "line": int(line_num),
            "message": message
        }

    # Pattern 2, server: 2024-01-01 00:00:00,123 - user_plugin_server - INFO - message.
    pattern2 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?) - (.+?) - (\w+) - (.+)'
    match = re.match(pattern2, line)
    if match:
        timestamp, name, level, message = match.groups()
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": name.strip(),
            "line": 0,
            "message": message.strip()
        }

    # Pattern 3, standard: 2024-01-01 00:00:00 - INFO - message.
    pattern3 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?) - (\w+) - (.+)'
    match = re.match(pattern3, line)
    if match:
        timestamp, level, message = match.groups()
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": "",
            "line": 0,
            "message": message.strip()
        }

    pattern3b = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.+)'
    match = re.match(pattern3b, line)
    if match:
        timestamp, level, message = match.groups()
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": "",
            "line": 0,
            "message": message.strip(),
        }

    # Pattern 4, pipes: 2024-01-01 00:00:00 | INFO | module:function:123 | message.
    # Supported shape: timestamp | level | location | message.
    # location may be module:function:line, module:function, or module.
    pattern4 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*([^|]+)\s*\|\s*(.+)'
    match = re.match(pattern4, line)
    if match:
        timestamp, level, location, message = match.groups()
        # Parse location as module:function:line, module:function, or module.
        location_parts = location.strip().split(':')
        if len(location_parts) >= 3:
            # module:function:line
            file = location_parts[0]
            line_num = int(location_parts[-1]) if location_parts[-1].isdigit() else 0
        elif len(location_parts) == 2:
            # module:function
            file = location_parts[0]
            line_num = 0
        else:
            # module
            file = location_parts[0] if location_parts else ""
            line_num = 0

        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": file.strip(),
            "line": line_num,
            "message": message.strip()
        }

    # Pattern 5, loguru pipes: 2024-01-01 00:00:00 | INFO | [Proc-xxx] message.
    # Supported shape: timestamp | level | message.
    pattern5 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*(.+)'
    match = re.match(pattern5, line)
    if match:
        timestamp, level, message = match.groups()
        message = message.strip()

        file = ""
        # Try extracting loguru prefixes such as [Proc-xxx] / [Plugin-xxx].
        prefix_match = re.match(r'^\[([^\]]+)\]\s*(.*)$', message)
        if prefix_match:
            file, message = prefix_match.groups()

        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": (file or "").strip(),
            "line": 0,
            "message": (message or "").strip()
        }

    pattern6 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d+))?\s*\|\s*(\w+)\s*\|\s*([^|]+?)\s*-\s*(.+)'
    match = re.match(pattern6, line)
    if match:
        ts, ms, level, location, message = match.groups()
        timestamp = ts.strip()
        if isinstance(ms, str) and ms:
            timestamp = f"{timestamp}.{ms}"
        location = (location or "").strip()
        message = (message or "").strip()
        file = ""
        line_num = 0
        try:
            parts = location.split(":")
            if len(parts) >= 3 and parts[-1].isdigit():
                line_num = int(parts[-1])
                file = ":".join(parts[:-2]) if len(parts) > 3 else parts[0]
            else:
                file = parts[0] if parts else location
        except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError):
            file = location
            line_num = 0
        return {
            "timestamp": timestamp,
            "level": level.strip(),
            "file": file.strip(),
            "line": line_num,
            "message": message,
        }

    # Unmatched formats return the original line.
    return {
        "timestamp": "",
        "level": "UNKNOWN",
        "file": "",
        "line": 0,
        "message": line
    }



def filter_logs(
    logs: list[dict[str, object]],
    level: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    search: str | None = None,
    *, search_all_fields: bool = False,
) -> list[dict[str, object]]:
    """
    Filter logs.

    Args:
        logs: Log list.
        level: Log-level filter.
        start_time: Start time, ISO format.
        end_time: End time, ISO format.
        search: Keyword search.
        search_all_fields: Include displayed metadata for opt-in viewer searches.

    Returns:
        Filtered log list.
    """
    filtered = logs

    # Filter by level.
    if level:
        level_upper = level.upper()
        filtered = [log for log in filtered if log.get("level") == level_upper]

    # Filter by time.
    if start_time or end_time:
        start_dt = _parse_log_time(start_time) if isinstance(start_time, str) else None
        end_dt = _parse_log_time(end_time) if isinstance(end_time, str) else None
        if start_dt is not None or end_dt is not None:
            time_filtered: list[dict[str, object]] = []
            for log in filtered:
                ts = _parse_log_time(log.get("timestamp"))
                if ts is None:
                    continue
                if start_dt is not None and ts < start_dt:
                    continue
                if end_dt is not None and ts > end_dt:
                    continue
                time_filtered.append(log)
            filtered = time_filtered

    # Keyword search.
    if search:
        search_lower = search.lower()

        def matches(entry):
            fields = [entry.get("message", "")]
            if search_all_fields:
                fields.extend([entry.get("level", ""), entry.get("timestamp", ""),
                               f"{entry.get('file', '')}:{entry.get('line', 0)}"])
            return any(search_lower in str(value).lower() for value in fields)

        filtered = [
            log for log in filtered
            if matches(log)
        ]

    return filtered
