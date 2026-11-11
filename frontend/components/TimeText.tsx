"use client";

import { useSyncExternalStore } from "react";
import { formatFullDateTime, formatSmartTime } from "@/domain/time";

const TICK_MS = 30_000;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
let snapshot = Date.now();

// One shared timer for every mounted TimeText, so long lists do not each own an interval.
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (!timer) {
    snapshot = Date.now();
    timer = setInterval(() => {
      snapshot = Date.now();
      listeners.forEach((notify) => notify());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

// While nobody is subscribed the snapshot would go stale, so it is quantized to keep it stable between reads.
function getSnapshot() {
  return timer ? snapshot : Math.floor(Date.now() / TICK_MS) * TICK_MS;
}

/** Relative or calendar-style time text; hovering shows the full yyyy-mm-dd hh:mm:ss. */
export function TimeText({ value, className }: { value: string | number | null | undefined; className?: string }) {
  useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  // The shared tick schedules refreshes; new messages need the current render time.
  const text = formatSmartTime(value);
  const full = formatFullDateTime(value);
  return <time className={className} title={full === text ? undefined : full}>{text}</time>;
}
