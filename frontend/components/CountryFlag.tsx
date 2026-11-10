import { countryCodeOf, countryNameOf } from "@/domain/country";

/** Small country flag with an optional country name and ISO code. */
export function CountryFlag({
  country,
  showName = false,
  showCode = true,
}: {
  country?: string;
  showName?: boolean;
  showCode?: boolean;
}) {
  const code = countryCodeOf(country);
  if (!code) return null;
  const name = countryNameOf(code) ?? country?.trim() ?? code;
  const label = showName ? `${name}${showCode ? ` (${code})` : ""}` : showCode ? code : null;
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1"
      data-country-code={code}
      title={name}
      role="img"
      aria-label={`${name} ${code}`}
    >
      <span className={`fi fi-${code.toLowerCase()} country-flag`} aria-hidden="true" />
      {label ? <span className="font-medium">{label}</span> : null}
    </span>
  );
}
