// ISO 3166-1 alpha-2 country codes. XK is the commonly used user-assigned
// code for Kosovo and is also shipped by the flag asset set.
const COUNTRY_CODES = `
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ
EC EE EG EH ER ES ET
FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
HK HM HN HR HT HU
ID IE IL IM IN IO IQ IR IS IT
JE JM JO JP
KE KG KH KI KM KN KP KR KW KY KZ
LA LB LC LI LK LR LS LT LU LV LY
MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ
OM
PA PE PF PG PH PK PL PM PN PR PS PT PW PY
QA
RE RO RS RU RW
SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
UA UG UM US UY UZ
VA VC VE VG VI VN VU
WF WS
YE YT
ZA ZM ZW
XK
`.trim().split(/\s+/);

const COUNTRY_CODE_SET = new Set(COUNTRY_CODES);

// Common non-ISO spellings and short forms found in older profile data.
const COUNTRY_ALIASES: Record<string, string> = {
  UK: "GB",
  UAE: "AE",
  USA: "US",
  "United States of America": "US",
  Korea: "KR",
  KSA: "SA",
  "Korea, Republic of": "KR",
  "Korea, Democratic People's Republic of": "KP",
  "Russian Federation": "RU",
  "Viet Nam": "VN",
  "Czech Republic": "CZ",
  Turkey: "TR",
  "Ivory Coast": "CI",
  Burma: "MM",
  "East Timor": "TL",
  Macau: "MO",
  Macao: "MO",
  "Hong Kong SAR": "HK",
  "Hong Kong SAR China": "HK",
  "Hong Kong": "HK",
  "Macao SAR China": "MO",
  "Taiwan, Province of China": "TW",
  Palestine: "PS",
  Swaziland: "SZ",
  Macedonia: "MK",
  "Cape Verde": "CV",
  "Cabo Verde": "CV",
  Congo: "CG",
  "Congo, The Democratic Republic of the": "CD",
  "Democratic Republic of the Congo": "CD",
  "Republic of the Congo": "CG",
  阿联酋: "AE",
  沙特: "SA",
  英国: "GB",
  美国: "US",
  韩国: "KR",
  朝鲜: "KP",
  俄罗斯: "RU",
  越南: "VN",
  捷克: "CZ",
  土耳其: "TR",
  香港: "HK",
  澳门: "MO",
  台湾: "TW",
};

function createRegionNames(locale: string): Intl.DisplayNames | undefined {
  try {
    return new Intl.DisplayNames([locale], { type: "region" });
  } catch {
    return undefined;
  }
}

const ENGLISH_NAMES = createRegionNames("en");
const CHINESE_NAMES = createRegionNames("zh-CN");

function normalizeCountryName(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9\u3400-\u9fff]+/g, " ")
    .trim();
}

const NAME_TO_CODE = new Map<string, string>();
for (const code of COUNTRY_CODES) {
  for (const names of [ENGLISH_NAMES, CHINESE_NAMES]) {
    if (!names) continue;
    try {
      const name = names.of(code);
      if (name && name !== code) NAME_TO_CODE.set(normalizeCountryName(name), code);
    } catch {
      // Ignore regions this runtime cannot name.
    }
  }
}
for (const [alias, code] of Object.entries(COUNTRY_ALIASES)) {
  NAME_TO_CODE.set(normalizeCountryName(alias), code);
}

/** Resolves an ISO code or a country name to an ISO 3166-1 alpha-2 code. */
export function countryCodeOf(country: string | undefined): string | undefined {
  const value = (country ?? "").trim();
  if (!value) return undefined;
  const upper = value.toUpperCase();
  if (COUNTRY_CODE_SET.has(upper)) return upper;
  return NAME_TO_CODE.get(normalizeCountryName(value));
}

/** Localized country name for tooltips and accessible labels. */
export function countryNameOf(code: string | undefined): string | undefined {
  const value = (code ?? "").trim().toUpperCase();
  if (!COUNTRY_CODE_SET.has(value) || !CHINESE_NAMES) return undefined;
  try {
    const name = CHINESE_NAMES.of(value);
    return name && name !== value ? name : undefined;
  } catch {
    return undefined;
  }
}
