import { useEffect, useState } from "react";
import { localTimeLabel } from "@/domain/localTime";

const REFRESH_MS = 30_000;

/** Customer local time label for a country code, refreshed while mounted. */
export function useLocalTime(country: string | undefined) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), REFRESH_MS);
    return () => clearInterval(timer);
  }, []);
  return localTimeLabel(country, now);
}
