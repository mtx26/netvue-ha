export function recordingSegments(recordings) {
  if (!Array.isArray(recordings)) {
    throw new Error("Invalid recording history");
  }
  return recordings.map((item) => {
    const start = Date.parse(item.start);
    const duration = Number(item.duration);
    if (typeof item.id !== "string" || !/^[0-9a-f]{32}$/.test(item.id)
        || !Number.isFinite(start) || !Number.isFinite(duration) || duration < 0) {
      throw new Error("Invalid recording segment");
    }
    return { id: item.id, start, end: start + duration * 1000 };
  }).filter((segment) => segment.end > segment.start).sort((a, b) => a.start - b.start);
}

export function segmentAt(segments, time) {
  for (let index = segments.length - 1; index >= 0; index--) {
    const segment = segments[index];
    if (time >= segment.start && time < segment.end) return segment;
  }
  return null;
}

export function mergeIntervals(intervals) {
  if (!Array.isArray(intervals)) return [];
  const sorted = intervals
    .map((item) => ({ ...item, start: Number(item.start), end: Number(item.end) }))
    .filter((item) => Number.isFinite(item.start) && Number.isFinite(item.end) && item.end > item.start)
    .sort((a, b) => a.start - b.start);
  const merged = [];
  for (const item of sorted) {
    const previous = merged.at(-1);
    if (previous && item.start <= previous.end) {
      previous.end = Math.max(previous.end, item.end);
      previous.items = [...(previous.items || [previous]), item];
    } else {
      merged.push({ ...item });
    }
  }
  return merged;
}

export function intervalAt(intervals, time) {
  if (!Array.isArray(intervals)) return null;
  const sorted = intervals
    .filter((item) => Number.isFinite(item.start) && Number.isFinite(item.end)
      && time >= item.start && time < item.end)
    .sort((a, b) => b.start - a.start);
  for (const item of sorted) {
    return item;
  }
  return null;
}

export function localDateTime(time) {
  const date = new Date(time);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    + `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
