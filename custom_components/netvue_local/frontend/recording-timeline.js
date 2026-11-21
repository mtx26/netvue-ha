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

export function localDateTime(time) {
  const date = new Date(time);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    + `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
