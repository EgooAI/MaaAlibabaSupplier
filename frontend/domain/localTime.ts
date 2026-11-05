// Single-zone markets. Territories with minor outlying zones (Canaries, Azores) use the mainland zone.
const SINGLE_ZONE: Record<string, string> = {
  PK: "Asia/Karachi", IN: "Asia/Kolkata", BD: "Asia/Dhaka", LK: "Asia/Colombo", NP: "Asia/Kathmandu",
  AE: "Asia/Dubai", SA: "Asia/Riyadh", QA: "Asia/Qatar", KW: "Asia/Kuwait", OM: "Asia/Muscat", BH: "Asia/Bahrain",
  IR: "Asia/Tehran", IQ: "Asia/Baghdad", JO: "Asia/Amman", LB: "Asia/Beirut", IL: "Asia/Jerusalem", TR: "Europe/Istanbul",
  CN: "Asia/Shanghai", HK: "Asia/Hong_Kong", TW: "Asia/Taipei", JP: "Asia/Tokyo", KR: "Asia/Seoul",
  SG: "Asia/Singapore", MY: "Asia/Kuala_Lumpur", TH: "Asia/Bangkok", VN: "Asia/Ho_Chi_Minh", PH: "Asia/Manila",
  KZ: "Asia/Almaty", UZ: "Asia/Tashkent", MN: "Asia/Ulaanbaatar", NZ: "Pacific/Auckland",
  EG: "Africa/Cairo", NG: "Africa/Lagos", KE: "Africa/Nairobi", ZA: "Africa/Johannesburg", MA: "Africa/Casablanca",
  DZ: "Africa/Algiers", TN: "Africa/Tunis", GH: "Africa/Accra", ET: "Africa/Addis_Ababa", TZ: "Africa/Dar_es_Salaam", UG: "Africa/Kampala",
  ES: "Europe/Madrid", PT: "Europe/Lisbon", FR: "Europe/Paris", DE: "Europe/Berlin", IT: "Europe/Rome", NL: "Europe/Amsterdam",
  BE: "Europe/Brussels", GB: "Europe/London", IE: "Europe/Dublin", PL: "Europe/Warsaw", CZ: "Europe/Prague", RO: "Europe/Bucharest",
  BG: "Europe/Sofia", GR: "Europe/Athens", SE: "Europe/Stockholm", NO: "Europe/Oslo", DK: "Europe/Copenhagen", FI: "Europe/Helsinki",
  AT: "Europe/Vienna", CH: "Europe/Zurich", HU: "Europe/Budapest", UA: "Europe/Kiev",
  CO: "America/Bogota", PE: "America/Lima", VE: "America/Caracas", AR: "America/Argentina/Buenos_Aires", CL: "America/Santiago",
  DO: "America/Santo_Domingo", UY: "America/Montevideo", PA: "America/Panama", CR: "America/Costa_Rica", GT: "America/Guatemala",
  JM: "America/Jamaica", EC: "America/Guayaquil", BO: "America/La_Paz", PY: "America/Asuncion",
};

// Countries spanning several zones: the zone of the main business hub stands in, and the label says "about".
const MULTI_ZONE: Record<string, string> = {
  US: "America/New_York", CA: "America/Toronto", MX: "America/Mexico_City", BR: "America/Sao_Paulo",
  RU: "Europe/Moscow", AU: "Australia/Sydney", ID: "Asia/Jakarta",
};

/** Customer-local time such as "当地时间 17:47", or undefined when the country is not an ISO code we know. */
export function localTimeLabel(country: string | undefined, now: Date): string | undefined {
  const code = (country ?? "").trim().toUpperCase();
  const approximate = code in MULTI_ZONE;
  const timeZone = SINGLE_ZONE[code] ?? MULTI_ZONE[code];
  if (!timeZone) return undefined;
  const time = new Intl.DateTimeFormat("en-GB", { timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(now);
  return `当地时间${approximate ? "约" : ""} ${time}`;
}
