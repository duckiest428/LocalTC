/* The dashboard's Stats page: the logbook told in numbers. Crossings, the farthest north and south, time between
   flights, countries and passport stamps, airlines and aircraft, flight lengths, flights per year, the days flown,
   the carbon, and from the sim itself the fuel, speed, night landings and weather.

   Only with Stats and metrics on (the app's Settings → Account; [account] stats): GET /v1/stats/details answers
   {enabled: false} otherwise, and this says how to turn it on. Everything is worked out here from the flights'
   lines; the "from the sim" numbers come from their metrics, which the app collects only with it on.

   Countries come from the airports' ICAO codes (the first letters say the country; a territory counts as its
   country), airlines from the callsign's ICAO code. No third-party data or images: flags are emoji. */
(function () {
  "use strict";

  // ISO code -> [name, continent]. The 197 the count is out of: the UN's 193, the two observers, Kosovo and Taiwan.
  const CONTINENTS = { AF: "Africa", AS: "Asia", EU: "Europe", NA: "North America", SA: "South America", OC: "Oceania" };
  const C = {};
  const add = (cont, list) => list.split(";").forEach((e) => { const [iso, name] = e.split(":"); C[iso] = [name, cont]; });
  add("AF", "DZ:Algeria;AO:Angola;BJ:Benin;BW:Botswana;BF:Burkina Faso;BI:Burundi;CV:Cape Verde;CM:Cameroon;CF:Central African Republic;TD:Chad;KM:Comoros;CG:Congo;CD:DR Congo;CI:Côte d'Ivoire;DJ:Djibouti;EG:Egypt;GQ:Equatorial Guinea;ER:Eritrea;SZ:Eswatini;ET:Ethiopia;GA:Gabon;GM:Gambia;GH:Ghana;GN:Guinea;GW:Guinea-Bissau;KE:Kenya;LS:Lesotho;LR:Liberia;LY:Libya;MG:Madagascar;MW:Malawi;ML:Mali;MR:Mauritania;MU:Mauritius;MA:Morocco;MZ:Mozambique;NA:Namibia;NE:Niger;NG:Nigeria;RW:Rwanda;ST:São Tomé and Príncipe;SN:Senegal;SC:Seychelles;SL:Sierra Leone;SO:Somalia;ZA:South Africa;SS:South Sudan;SD:Sudan;TZ:Tanzania;TG:Togo;TN:Tunisia;UG:Uganda;ZM:Zambia;ZW:Zimbabwe");
  add("AS", "AF:Afghanistan;AM:Armenia;AZ:Azerbaijan;BH:Bahrain;BD:Bangladesh;BT:Bhutan;BN:Brunei;KH:Cambodia;CN:China;CY:Cyprus;GE:Georgia;IN:India;ID:Indonesia;IR:Iran;IQ:Iraq;IL:Israel;JP:Japan;JO:Jordan;KZ:Kazakhstan;KW:Kuwait;KG:Kyrgyzstan;LA:Laos;LB:Lebanon;MY:Malaysia;MV:Maldives;MN:Mongolia;MM:Myanmar;NP:Nepal;KP:North Korea;OM:Oman;PK:Pakistan;PS:Palestine;PH:Philippines;QA:Qatar;SA:Saudi Arabia;SG:Singapore;KR:South Korea;LK:Sri Lanka;SY:Syria;TW:Taiwan;TJ:Tajikistan;TH:Thailand;TL:Timor-Leste;TR:Türkiye;TM:Turkmenistan;AE:United Arab Emirates;UZ:Uzbekistan;VN:Vietnam;YE:Yemen");
  add("EU", "AL:Albania;AD:Andorra;AT:Austria;BY:Belarus;BE:Belgium;BA:Bosnia and Herzegovina;BG:Bulgaria;HR:Croatia;CZ:Czechia;DK:Denmark;EE:Estonia;FI:Finland;FR:France;DE:Germany;GR:Greece;HU:Hungary;IS:Iceland;IE:Ireland;IT:Italy;XK:Kosovo;LV:Latvia;LI:Liechtenstein;LT:Lithuania;LU:Luxembourg;MT:Malta;MD:Moldova;MC:Monaco;ME:Montenegro;NL:Netherlands;MK:North Macedonia;NO:Norway;PL:Poland;PT:Portugal;RO:Romania;RU:Russia;SM:San Marino;RS:Serbia;SK:Slovakia;SI:Slovenia;ES:Spain;SE:Sweden;CH:Switzerland;UA:Ukraine;GB:United Kingdom;VA:Vatican City");
  add("NA", "AG:Antigua and Barbuda;BS:Bahamas;BB:Barbados;BZ:Belize;CA:Canada;CR:Costa Rica;CU:Cuba;DM:Dominica;DO:Dominican Republic;SV:El Salvador;GD:Grenada;GT:Guatemala;HT:Haiti;HN:Honduras;JM:Jamaica;MX:Mexico;NI:Nicaragua;PA:Panama;KN:Saint Kitts and Nevis;LC:Saint Lucia;VC:Saint Vincent;TT:Trinidad and Tobago;US:United States");
  add("SA", "AR:Argentina;BO:Bolivia;BR:Brazil;CL:Chile;CO:Colombia;EC:Ecuador;GY:Guyana;PY:Paraguay;PE:Peru;SR:Suriname;UY:Uruguay;VE:Venezuela");
  add("OC", "AU:Australia;FJ:Fiji;KI:Kiribati;MH:Marshall Islands;FM:Micronesia;NR:Nauru;NZ:New Zealand;PW:Palau;PG:Papua New Guinea;WS:Samoa;SB:Solomon Islands;TO:Tonga;TV:Tuvalu;VU:Vanuatu");

  // ICAO prefix -> ISO code; the longest prefix that matches wins.
  const PREFIX = {};
  ("K:US;C:CA;Y:AU;Z:CN;ZK:KP;ZM:MN;U:RU;AG:SB;AN:NR;AY:PG;BG:DK;BI:IS;BK:XK;"
    + "DA:DZ;DB:BJ;DF:BF;DG:GH;DI:CI;DN:NG;DR:NE;DT:TN;DX:TG;"
    + "EB:BE;ED:DE;ET:DE;EE:EE;EF:FI;EG:GB;EH:NL;EI:IE;EK:DK;EL:LU;EN:NO;EP:PL;ES:SE;EV:LV;EY:LT;"
    + "FA:ZA;FB:BW;FC:CG;FD:SZ;FE:CF;FG:GQ;FH:GB;FI:MU;FJ:GB;FK:CM;FL:ZM;FM:MG;FMC:KM;FME:FR;FN:AO;FO:GA;FP:ST;FQ:MZ;FS:SC;FT:TD;FV:ZW;FW:MW;FX:LS;FY:NA;FZ:CD;"
    + "GA:ML;GB:GM;GC:ES;GE:ES;GF:SL;GG:GW;GL:LR;GM:MA;GO:SN;GQ:MR;GU:GN;GV:CV;"
    + "HA:ET;HB:BI;HC:SO;HD:DJ;HE:EG;HH:ER;HJ:SS;HK:KE;HL:LY;HR:RW;HS:SD;HT:TZ;HU:UG;"
    + "LA:AL;LB:BG;LC:CY;LD:HR;LE:ES;LF:FR;LG:GR;LH:HU;LI:IT;LJ:SI;LK:CZ;LL:IL;LM:MT;LN:MC;LO:AT;LP:PT;LQ:BA;LR:RO;LS:CH;LT:TR;LU:MD;LV:PS;LW:MK;LX:GB;LY:RS;LYP:ME;LYT:ME;LZ:SK;"
    + "MB:GB;MD:DO;MG:GT;MH:HN;MK:JM;MM:MX;MN:NI;MP:PA;MR:CR;MS:SV;MT:HT;MU:CU;MW:GB;MY:BS;MZ:BZ;"
    + "NC:NZ;NF:FJ;NFT:TO;NG:KI;NGF:TV;NI:NZ;NL:FR;NS:WS;NST:US;NT:FR;NV:VU;NW:FR;NZ:NZ;"
    + "OA:AF;OB:BH;OE:SA;OI:IR;OJ:JO;OK:KW;OL:LB;OM:AE;OO:OM;OP:PK;OR:IQ;OS:SY;OT:QA;OY:YE;"
    + "PA:US;PB:US;PC:KI;PF:US;PG:US;PH:US;PJ:US;PK:MH;PL:KI;PM:US;PO:US;PP:US;PT:FM;PTR:PW;PW:US;"
    + "RC:TW;RJ:JP;RK:KR;RO:JP;RP:PH;"
    + "SA:AR;SB:BR;SD:BR;SI:BR;SJ:BR;SN:BR;SS:BR;SW:BR;SC:CL;SE:EC;SF:GB;SG:PY;SK:CO;SL:BO;SM:SR;SO:FR;SP:PE;SU:UY;SV:VE;SY:GY;"
    + "TA:AG;TB:BB;TD:DM;TF:FR;TG:GD;TI:US;TJ:US;TK:KN;TL:LC;TN:NL;TQ:GB;TR:GB;TT:TT;TU:GB;TV:VC;TX:GB;"
    + "UA:KZ;UB:AZ;UC:KG;UD:AM;UG:GE;UK:UA;UM:BY;UTA:TM;UTD:TJ;UTK:UZ;UTN:UZ;UTS:UZ;UTT:UZ;"
    + "VA:IN;VE:IN;VI:IN;VO:IN;VC:LK;VD:KH;VG:BD;VH:CN;VL:LA;VM:CN;VN:NP;VQ:BT;VR:MV;VT:TH;VV:VN;VY:MM;"
    + "WA:ID;WI:ID;WQ:ID;WR:ID;WB:MY;WBS:BN;WM:MY;WP:TL;WS:SG")
    .split(";").forEach((e) => { const [p, iso] = e.split(":"); PREFIX[p] = iso; });

  /** The country of an airport's ICAO code ("LSZH" -> "CH"), or null. */
  function countryOf(icao) {
    const s = String(icao || "").toUpperCase();
    if (!/^[A-Z]{4}$/.test(s)) return null;  // "K0S9" style US strips are still K: let them through below
    for (let n = 3; n >= 1; n--) if (PREFIX[s.slice(0, n)]) return PREFIX[s.slice(0, n)];
    return null;
  }
  const countryOfAny = (icao) => countryOf(icao) || (/^K[A-Z0-9]{3}$/.test(String(icao || "").toUpperCase()) ? "US" : null);

  // ISO numeric (the world map's country ids) -> ISO letters.
  const NUMERIC = {};
  ("AF004 AL008 DZ012 AD020 AO024 AG028 AR032 AM051 AU036 AT040 AZ031 BS044 BH048 BD050 BB052 BY112 BE056 BZ084 BJ204 BT064 BO068 BA070 BW072 BR076 BN096 BG100 BF854 BI108 KH116 CM120 CA124 CV132 CF140 TD148 CL152 CN156 CO170 KM174 CG178 CD180 CR188 CI384 HR191 CU192 CY196 CZ203 DK208 DJ262 DM212 DO214 EC218 EG818 SV222 GQ226 ER232 EE233 SZ748 ET231 FJ242 FI246 FR250 GA266 GM270 GE268 DE276 GH288 GR300 GD308 GT320 GN324 GW624 GY328 HT332 HN340 HU348 IS352 IN356 ID360 IR364 IQ368 IE372 IL376 IT380 JM388 JP392 JO400 KZ398 KE404 KI296 KP408 KR410 KW414 KG417 LA418 LV428 LB422 LS426 LR430 LY434 LI438 LT440 LU442 MG450 MW454 MY458 MV462 ML466 MT470 MH584 MR478 MU480 MX484 FM583 MD498 MC492 MN496 ME499 MA504 MZ508 MM104 NA516 NR520 NP524 NL528 NZ554 NI558 NE562 NG566 MK807 NO578 OM512 PK586 PW585 PS275 PA591 PG598 PY600 PE604 PH608 PL616 PT620 QA634 RO642 RU643 RW646 KN659 LC662 VC670 WS882 SM674 ST678 SA682 SN686 RS688 SC690 SL694 SG702 SK703 SI705 SB090 SO706 ZA710 SS728 ES724 LK144 SD729 SR740 SE752 CH756 SY760 TW158 TJ762 TZ834 TH764 TL626 TG768 TO776 TT780 TN788 TR792 TM795 TV798 UG800 UA804 AE784 GB826 US840 UY858 UZ860 VU548 VA336 VE862 VN704 YE887 ZM894 ZW716")
    .split(" ").forEach((e) => { NUMERIC[e.slice(2)] = e.slice(0, 2); });
  const NAMED = { Kosovo: "XK", "N. Cyprus": "CY", Somaliland: "SO" };

  /** The world's countries from vendor/world/countries-110m.json (TopoJSON, world-atlas), as SVG paths in an
   *  equirectangular box 360 x 143 (85°N to 58°S): [{iso, d}]. Loaded once. */
  let worldShapes = null;
  async function world() {
    if (worldShapes) return worldShapes;
    const topo = await (await fetch("vendor/world/countries-110m.json")).json();
    const [sx, sy] = topo.transform.scale, [tx, ty] = topo.transform.translate;
    const arcs = topo.arcs.map((arc) => { let x = 0, y = 0; return arc.map(([dx, dy]) => { x += dx; y += dy; return [x * sx + tx, y * sy + ty]; }); });
    const ring = (ids) => {
      const pts = [];
      for (const i of ids) { const a = i < 0 ? [...arcs[~i]].reverse() : arcs[i]; pts.push(...(pts.length ? a.slice(1) : a)); }
      // A ring over the date line (Russia, Fiji) is broken there, not drawn back across the whole map.
      return pts.map(([lon, lat], i) => `${i && Math.abs(lon - pts[i - 1][0]) < 180 ? "L" : "M"}${(lon + 180).toFixed(2)},${(85 - lat).toFixed(2)}`).join("") + "Z";
    };
    worldShapes = topo.objects.countries.geometries.filter((g) => g.properties?.name !== "Antarctica").map((g) => {
      const polys = g.type === "Polygon" ? [g.arcs] : g.type === "MultiPolygon" ? g.arcs : [];
      return { iso: NUMERIC[g.id] || NAMED[g.properties?.name] || null, name: g.properties?.name || "", d: polys.map((p) => p.map(ring).join("")).join("") };
    });
    return worldShapes;
  }

  const flag = (iso) => (iso && /^[A-Z]{2}$/.test(iso) ? String.fromCodePoint(...[...iso].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65)) : "");

  // Airline ICAO code -> the company's name (the callsign's first three letters).
  const AIRLINES = {};
  ("AAL:American Airlines;AAY:Allegiant Air;ACA:Air Canada;ASA:Alaska Airlines;AWI:Air Wisconsin;DAL:Delta Air Lines;EDV:Endeavor Air;ENY:Envoy Air;"
    + "FDX:FedEx;FFT:Frontier Airlines;HAL:Hawaiian Airlines;JBU:JetBlue;JZA:Jazz;MXY:Breeze Airways;NKS:Spirit Airlines;PDT:Piedmont Airlines;PSA:PSA Airlines;"
    + "QXE:Horizon Air;RPA:Republic Airways;ROU:Air Canada Rouge;SCX:Sun Country;SKW:SkyWest;SWA:Southwest Airlines;TSC:Air Transat;UAL:United Airlines;UPS:UPS;"
    + "VOI:Volaris;WJA:WestJet;AMX:Aeroméxico;POE:Porter Airlines;FLE:Flair Airlines;GJS:GoJet;CJT:Cargojet;"
    + "ARG:Aerolíneas Argentinas;AVA:Avianca;AZU:Azul;CMP:Copa Airlines;GLO:Gol;LAN:LATAM;TAM:LATAM Brasil;LPE:LATAM Perú;"
    + "BAW:British Airways;EIN:Aer Lingus;EZY:easyJet;EXS:Jet2;LOG:Loganair;RYR:Ryanair;TOM:TUI Airways;VIR:Virgin Atlantic;BEE:Flybe;"
    + "AFR:Air France;AUA:Austrian Airlines;BEL:Brussels Airlines;BTI:airBaltic;CFG:Condor;CLH:Lufthansa CityLine;DLH:Lufthansa;EWG:Eurowings;FIN:Finnair;"
    + "IBE:Iberia;IBS:Iberia Express;ICE:Icelandair;ITY:ITA Airways;KLM:KLM;LOT:LOT Polish Airlines;NAX:Norwegian;NOZ:Norwegian;SAS:SAS;SWR:Swiss;"
    + "TAP:TAP Air Portugal;TRA:Transavia;TVF:Transavia France;VLG:Vueling;WZZ:Wizz Air;SXS:SunExpress;PGT:Pegasus;AEE:Aegean;MSR:EgyptAir;RAM:Royal Air Maroc;"
    + "EDW:Edelweiss;OAW:Helvetic Airways;LGL:Luxair;KLC:KLM Cityhopper;BCY:CityJet;DLA:Air Dolomiti;ANE:Air Nostrum;ADR:Adria;TUI:TUIfly;WIF:Widerøe;"
    + "ROT:Tarom;CAI:Corendon;AFL:Aeroflot;CSA:Czech Airlines;ELY:El Al;THY:Turkish Airlines;AHY:Azerbaijan Airlines;KZR:Air Astana;"
    + "ETD:Etihad;FDB:flydubai;GFA:Gulf Air;KAC:Kuwait Airways;MEA:Middle East Airlines;OMA:Oman Air;QTR:Qatar Airways;RJA:Royal Jordanian;SVA:Saudia;UAE:Emirates;JZR:Jazeera Airways;"
    + "ETH:Ethiopian Airlines;KQA:Kenya Airways;SAA:South African Airways;TUN:Tunisair;DAH:Air Algérie;MAU:Air Mauritius;"
    + "AAR:Asiana;AIC:Air India;ANA:All Nippon Airways;APJ:Peach;CAL:China Airlines;CCA:Air China;CES:China Eastern;CSN:China Southern;CPA:Cathay Pacific;"
    + "CXA:Xiamen Air;EVA:EVA Air;HVN:Vietnam Airlines;IGO:IndiGo;JAL:Japan Airlines;JJP:Jetstar Japan;KAL:Korean Air;MAS:Malaysia Airlines;PAL:Philippine Airlines;"
    + "SIA:Singapore Airlines;THA:Thai Airways;TGW:Scoot;VJC:VietJet;AXM:AirAsia;SEJ:SpiceJet;CSZ:Shenzhen Airlines;"
    + "ANZ:Air New Zealand;JST:Jetstar;QFA:Qantas;FJI:Fiji Airways;VOZ:Virgin Australia;"
    + "CLX:Cargolux;GTI:Atlas Air;ABX:ABX Air;BOX:AeroLogic;GEC:Lufthansa Cargo;NCA:Nippon Cargo")
    .split(";").forEach((e) => { const [k, v] = e.split(":"); AIRLINES[k] = v; });

  function airlineOf(callsign) {
    const m = /^([A-Z]{3})\d/.exec(String(callsign || "").toUpperCase());
    return m ? { code: m[1], name: AIRLINES[m[1]] || m[1] } : null;
  }

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (n, d = 0) => Number(n || 0).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const monthYear = (d) => `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  const dateText = (d) => d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  const route = (f) => `${esc(f.origin || "?")} → ${esc(f.destination || "?")}`;
  const DAY = 86400_000;
  const KM_PER_NM = 1.852;
  const CO2_KG_PER_KM = 0.115 * 2.0;  // per seat, with the effects of flying at altitude
  const TREE_KG_YEAR = 21;  // a tree absorbs about this much CO2 a year

  /** The great circle from a to b: [lat, lon] points (lon unwrapped). */
  function arc(a, b, steps = 64) {
    const r = Math.PI / 180, d0 = 180 / Math.PI;
    const [la1, lo1, la2, lo2] = [a[0] * r, a[1] * r, b[0] * r, b[1] * r];
    const d = 2 * Math.asin(Math.sqrt(Math.sin((la2 - la1) / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2));
    if (d < 1e-9) return [a, b];
    const pts = [];
    for (let i = 0; i <= steps; i++) {
      const f = i / steps, A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
      const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
      const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
      const z = A * Math.sin(la1) + B * Math.sin(la2);
      pts.push([Math.atan2(z, Math.sqrt(x * x + y * y)) * d0, Math.atan2(y, x) * d0]);
    }
    return pts;
  }

  /** The path's extremes and crossings: the app's measured ones (metrics), else the great circle's. */
  function geometry(f) {
    const m = f.metrics || {};
    const out = { north: m.north_lat, south: m.south_lat, equator: m.equator_crossings, dateline: m.dateline_crossings };
    const ends = f.origin_lat != null && f.destination_lat != null;
    if (ends && (out.north == null || out.south == null || out.equator == null || out.dateline == null)) {
      const pts = arc([f.origin_lat, f.origin_lon], [f.destination_lat, f.destination_lon]);
      let eq = 0, dl = 0;
      for (let i = 1; i < pts.length; i++) {
        if ((pts[i - 1][0] < 0) !== (pts[i][0] < 0)) eq++;
        if (Math.abs(pts[i][1] - pts[i - 1][1]) > 180) dl++;
      }
      out.north ??= Math.max(...pts.map((p) => p[0]));
      out.south ??= Math.min(...pts.map((p) => p[0]));
      out.equator ??= eq;
      out.dateline ??= dl;
    }
    return out;
  }

  /** Everything the page shows, from the flights (oldest first). */
  function compute(flights) {
    const fl = flights.filter((f) => f.started_at).sort((a, b) => a.started_at.localeCompare(b.started_at));
    const s = { flights: fl.length };
    s.hours = fl.reduce((t, f) => t + (f.air_min ?? f.block_min ?? 0), 0) / 60;
    s.nm = fl.reduce((t, f) => t + (f.distance_nm || 0), 0);
    const airports = new Set(fl.flatMap((f) => [f.origin, f.destination]).filter(Boolean));
    s.airports = airports.size;

    // Crossings and the farthest north and south.
    s.equator = 0; s.dateline = 0; s.north = null; s.south = null;
    for (const f of fl) {
      const g = geometry(f);
      s.equator += g.equator || 0;
      s.dateline += g.dateline || 0;
      if (g.north != null && (!s.north || g.north > s.north.lat)) s.north = { lat: g.north, f };
      if (g.south != null && (!s.south || g.south < s.south.lat)) s.south = { lat: g.south, f };
    }

    // Time.
    const at = (f) => new Date(f.started_at);
    if (fl.length) {
      const last = fl[fl.length - 1];
      s.first = { date: at(fl[0]), f: fl[0] };
      s.since = { days: Math.max(0, Math.floor((Date.now() - new Date(last.ended_at || last.started_at)) / DAY)), f: last };
      s.gap = null;
      for (let i = 1; i < fl.length; i++) {
        const days = Math.floor((at(fl[i]) - new Date(fl[i - 1].ended_at || fl[i - 1].started_at)) / DAY);
        if (!s.gap || days > s.gap.days) s.gap = { days, from: at(fl[i - 1]), to: at(fl[i]) };
      }
      const months = [...new Set(fl.map((f) => { const d = at(f); return d.getFullYear() * 12 + d.getMonth(); }))].sort((a, b) => a - b);
      let best = { n: 1, from: months[0], to: months[0] }, start = 0;
      for (let i = 1; i < months.length; i++) {
        if (months[i] !== months[i - 1] + 1) start = i;
        if (i - start + 1 > best.n) best = { n: i - start + 1, from: months[start], to: months[i] };
      }
      s.streak = { months: best.n, from: new Date(Math.floor(best.from / 12), best.from % 12, 1), to: new Date(Math.floor(best.to / 12), best.to % 12, 1) };
    }

    // Countries: a visit is arriving there, or leaving from there when the flight before didn't land there.
    const visits = new Map();
    let where = null;
    for (const f of fl) {
      const o = countryOfAny(f.origin), d = countryOfAny(f.destination);
      if (o && o !== where) visits.set(o, (visits.get(o) || 0) + 1);
      if (d && d !== o) visits.set(d, (visits.get(d) || 0) + 1);
      where = d || where;
    }
    s.countries = [...visits.entries()].filter(([iso]) => C[iso]).sort((a, b) => b[1] - a[1] || C[a[0]][0].localeCompare(C[b[0]][0]));

    // Airlines and aircraft.
    const tally = (keys) => { const m = new Map(); keys.filter(Boolean).forEach((k) => m.set(k, (m.get(k) || 0) + 1)); return [...m.entries()].sort((a, b) => b[1] - a[1]); };
    s.airlines = tally(fl.map((f) => airlineOf(f.callsign)?.name));
    s.aircraft = tally(fl.map((f) => String(f.aircraft || "").trim()));

    // Lengths and carbon.
    const len = (f) => f.air_min ?? f.block_min ?? 0;
    const co2 = (f) => (f.distance_nm || 0) * KM_PER_NM * CO2_KG_PER_KM;
    const groups = [["Short", "up to 2 h", (m) => m <= 120], ["Medium", "2 to 6 h", (m) => m > 120 && m <= 360], ["Long", "over 6 h", (m) => m > 360]];
    s.lengths = groups.map(([name, sub, test]) => { const g = fl.filter((f) => test(len(f))); return { name, sub, n: g.length, co2: g.reduce((t, f) => t + co2(f), 0) }; });
    s.co2 = fl.reduce((t, f) => t + co2(f), 0);

    // Years and days.
    s.years = new Map();
    s.days = new Map();
    for (const f of fl) {
      const d = at(f);
      s.years.set(d.getFullYear(), (s.years.get(d.getFullYear()) || 0) + 1);
      const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      s.days.set(k, (s.days.get(k) || 0) + 1);
    }

    // From the sim (metrics).
    const withM = fl.filter((f) => f.metrics);
    s.sim = withM.length ? {
      n: withM.length,
      fuel: withM.reduce((t, f) => t + (f.metrics.fuel_used_lb || 0), 0),
      fast: withM.reduce((b, f) => ((f.metrics.max_gs_kt || 0) > (b?.metrics.max_gs_kt || 0) ? f : b), null),
      nightLandings: withM.filter((f) => f.metrics.night_landing).length,
      nightTakeoffs: withM.filter((f) => f.metrics.night_takeoff).length,
      cloud: withM.reduce((t, f) => t + (f.metrics.cloud_min || 0), 0),
      cold: withM.filter((f) => f.metrics.coldest_c != null).reduce((b, f) => (!b || f.metrics.coldest_c < b.metrics.coldest_c ? f : b), null),
      windy: withM.reduce((b, f) => ((f.metrics.max_wind_kt || 0) > (b?.metrics.max_wind_kt || 0) ? f : b), null),
    } : null;
    const landings = fl.filter((f) => f.landed && f.landing_vs_fpm != null && f.landing_vs_fpm < 0);
    s.softest = landings.reduce((b, f) => (!b || f.landing_vs_fpm > b.landing_vs_fpm ? f : b), null);
    s.avgLanding = landings.length ? landings.reduce((t, f) => t + f.landing_vs_fpm, 0) / landings.length : null;
    s.highest = fl.reduce((b, f) => ((f.max_alt_ft || 0) > (b?.max_alt_ft || 0) ? f : b), null);
    s.longest = fl.reduce((b, f) => ((f.distance_nm || 0) > (b?.distance_nm || 0) ? f : b), null);
    return s;
  }

  // --- drawing -----------------------------------------------------------------------------------------------

  const ICON = {
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
    compass: '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    cal: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
    star: '<path d="m12 4 2.4 5 5.4.6-4 3.7 1.1 5.3L12 16l-4.9 2.6 1.1-5.3-4-3.7 5.4-.6z"/>',
    plane: '<path d="M10.5 20 12 15l6 2.5V15l-6-4V5.5a1.5 1.5 0 0 0-3 0V11l-6 4v2.5L9 15l1.5 5z" transform="translate(1.5 0)"/>',
    leaf: '<path d="M5 19c0-8 5-13 14-14 0 9-5 14-13 14z"/><path d="M5 19 13 11"/>',
    gauge: '<path d="M4 16a8 8 0 1 1 16 0"/><path d="m12 16 4-5"/>',
    moon: '<path d="M18 15A7 7 0 0 1 9 6a7 7 0 1 0 9 9z"/>',
    drop: '<path d="M12 4s6 6.5 6 10.5A6 6 0 0 1 6 14.5C6 10.5 12 4 12 4z"/>',
  };
  const icon = (k) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;
  const card = (k, tone, title, value, sub) => `<div class="st-card st-${tone}"><h4>${icon(k)}${esc(title)}</h4><b>${value}</b><span>${sub}</span></div>`;
  const section = (tone, title) => `<h3 class="st-sec st-${tone}"><i></i>${esc(title)}</h3>`;
  const bars = (rows, total) => rows.map(([name, n, lead = ""]) => `<li><span class="st-bar-name">${lead}${esc(name)}</span><b>${num(n)}</b><i style="--w:${Math.max(2, (100 * n) / Math.max(1, total))}%"></i></li>`).join("");

  class StatsView {
    constructor(el) {
      this.el = el;
      this.year = null;
      this.continent = "all";
      this.showAll = false;
    }

    /** {enabled, flights} from GET /v1/stats/details. */
    show(data) {
      if (!data.enabled) {
        this.el.innerHTML = `<div class="st-off"><h3>Stats and metrics are off</h3>
<p>The Stats page and the numbers behind it are only collected and shown when you turn them on, in the LocalTC app:
<b>Settings (the gear) → Account → Stats and metrics</b>. Each flight then also keeps the northernmost and southernmost
points it flew, equator and date-line crossings, the fuel burned, its top speed, night takeoffs and landings, time in
cloud, the coldest air and the strongest wind.</p>
<p class="hint">Turning it off again deletes those numbers from your account.</p></div>`;
        return;
      }
      this.s = compute(data.flights || []);
      if (!this.s.flights) { this.el.innerHTML = `<p class="hint">No flights yet: fly with the account signed in, and they're counted here.</p>`; return; }
      this.year = this.year || Math.max(...this.s.years.keys());
      this.render();
    }

    render() {
      const s = this.s;
      const deg = (lat) => `${Math.abs(lat).toFixed(1)}°${lat >= 0 ? "N" : "S"}`;
      const countryCount = s.countries.length;
      const parts = [];
      parts.push(`<div class="tiles st-tiles">
  <div class="tile"><b>${num(s.flights)}</b><span>flights</span></div>
  <div class="tile"><b>${num(s.hours, s.hours < 100 ? 1 : 0)}</b><span>hours in the air</span></div>
  <div class="tile"><b>${num(s.nm)}</b><span>nautical miles</span></div>
  <div class="tile"><b>${num(s.airports)}</b><span>airports</span></div>
  <div class="tile"><b>${num(countryCount)}</b><span>countries</span></div>
  <div class="tile"><b>${num(s.airlines.length)}</b><span>airlines</span></div>
</div>`);

      parts.push(section("violet", "Crossings"), `<div class="st-grid">
${card("globe", "violet", "Equator crossings", num(s.equator), s.equator ? "North to south and back" : "None yet")}
${card("globe", "violet", "Date line crossings", num(s.dateline), s.dateline ? "Across the 180th meridian" : "None yet")}
${card("compass", "violet", "Closest to the North Pole", s.north ? deg(s.north.lat) : "–", s.north ? route(s.north.f) : "")}
${card("compass", "violet", "Closest to the South Pole", s.south ? deg(s.south.lat) : "–", s.south ? route(s.south.f) : "")}
</div>`);

      const span = (a, b) => (monthYear(a) === monthYear(b) ? monthYear(a) : `${monthYear(a)} to ${monthYear(b)}`);
      parts.push(section("amber", "Time"), `<div class="st-grid">
${card("clock", "amber", "Since the last flight", `${num(s.since.days)} ${s.since.days === 1 ? "day" : "days"}`, route(s.since.f))}
${card("clock", "amber", "Longest gap", s.gap ? `${num(s.gap.days)} ${s.gap.days === 1 ? "day" : "days"}` : "–", s.gap ? span(s.gap.from, s.gap.to) : "One flight so far")}
${card("cal", "amber", "Longest streak", `${s.streak.months} ${s.streak.months === 1 ? "month" : "months"}`, span(s.streak.from, s.streak.to))}
${card("star", "amber", "First flight logged", dateText(s.first.date), route(s.first.f))}
</div>`);

      // Countries and passport stamps.
      const visited = new Map(s.countries);
      const conts = Object.keys(CONTINENTS).map((k) => {
        const all = Object.keys(C).filter((iso) => C[iso][1] === k);
        return { k, all, n: all.filter((iso) => visited.has(iso)).length };
      });
      const pick = this.continent === "all" ? Object.keys(C) : conts.find((c) => c.k === this.continent).all;
      const stamps = pick.filter((iso) => this.showAll || visited.has(iso)).sort((a, b) => C[a][0].localeCompare(C[b][0]));
      const most = s.countries.slice(0, 6);
      parts.push(section("blue", "Countries"), `<div class="panel st-panel">
  <div class="st-head"><div><h4>Where the flights have gone</h4><span class="hint">From the airports' codes</span></div>
    <span class="st-pill">${countryCount} of 197 · ${Math.round((100 * countryCount) / 197)}%</span></div>
  <div class="st-world" aria-label="A map of the countries flown to"></div>
  <div class="st-continents">${conts.map((c) => `<div><b>${c.n}</b><span>${esc(CONTINENTS[c.k])}</span><i style="--w:${(100 * c.n) / c.all.length}%"></i></div>`).join("")}</div>
  <h5>Most visited</h5>
  <ul class="st-bars">${bars(most.map(([iso, n]) => [C[iso][0], n, `<em class="st-flag">${flag(iso)}</em>`]), most[0]?.[1] || 1)}</ul>
  <h5>Passport stamps</h5>
  <div class="st-chips" role="tablist">
    <button type="button" data-cont="all" aria-selected="${this.continent === "all"}">All <small>${countryCount}/197</small></button>
    ${conts.map((c) => `<button type="button" data-cont="${c.k}" aria-selected="${this.continent === c.k}">${esc(CONTINENTS[c.k])} <small>${c.n}/${c.all.length}</small></button>`).join("")}
  </div>
  <div class="st-stamps">${stamps.length ? stamps.map((iso) => `<div class="st-stamp${visited.has(iso) ? "" : " off"}" title="${esc(C[iso][0])}${visited.has(iso) ? `: ${visited.get(iso)} ${visited.get(iso) === 1 ? "visit" : "visits"}` : ""}"><em class="st-flag">${flag(iso)}</em><span>${esc(C[iso][0])}</span></div>`).join("") : `<p class="hint">None here yet.</p>`}</div>
  <button type="button" class="btn btn-ghost btn-sm st-all">${this.showAll ? "Only the ones visited" : "Show all countries"}</button>
</div>`);

      // Airlines and aircraft.
      parts.push(section("blue", "Airlines and aircraft"), `<div class="panel-row">
  <div class="panel st-panel"><h5>Top airlines</h5>${s.airlines.length ? `<ul class="st-bars">${bars(s.airlines.slice(0, 6), s.airlines[0][1])}</ul>` : `<p class="hint">No airline callsigns yet.</p>`}</div>
  <div class="panel st-panel"><h5>Top aircraft</h5>${s.aircraft.length ? `<ul class="st-bars">${bars(s.aircraft.slice(0, 6), s.aircraft[0][1])}</ul>` : `<p class="hint">No aircraft yet.</p>`}</div>
</div>`);

      // Flight lengths.
      const total = Math.max(1, s.flights);
      parts.push(section("blue", "Flight lengths"), `<div class="panel st-panel">
  <div class="st-stack">${s.lengths.map((g, i) => (g.n ? `<i class="st-l${i}" style="flex:${g.n}"></i>` : "")).join("")}</div>
  <ul class="st-legend">${s.lengths.map((g, i) => `<li><i class="st-l${i}"></i><span><b>${g.name}</b><small>${g.sub}</small></span><b>${g.n}</b><span class="hint">${Math.round((100 * g.n) / total)}%</span></li>`).join("")}</ul>
  ${s.lengths[2].n && s.co2 ? `<p class="hint">Long flights are ${Math.round((100 * s.lengths[2].n) / total)}% of the flights but ${Math.round((100 * s.lengths[2].co2) / s.co2)}% of the CO₂.</p>` : ""}
</div>`);

      // Over time: flights per year, and the days with a flight.
      const years = [...s.years.keys()];
      const y0 = Math.min(...years), y1 = Math.max(...years, new Date().getFullYear());
      const perYear = [];
      for (let y = y0; y <= y1; y++) perYear.push([y, s.years.get(y) || 0]);
      const top = Math.max(1, ...perYear.map((p) => p[1]));
      parts.push(section("blue", "Over time"), `<div class="panel st-panel">
  <h5>Flights per year</h5>
  <div class="st-years">${perYear.map(([y, n]) => `<div title="${y}: ${n} flight${n === 1 ? "" : "s"}"><small>${n || ""}</small><i style="height:${n ? Math.max(6, (100 * n) / top) : 0}%"></i><span>${y}</span></div>`).join("")}</div>
</div>
<div class="panel st-panel">
  <div class="st-head"><h5>Days with a flight</h5>
    <select class="st-year" aria-label="Year">${perYear.map(([y]) => y).reverse().map((y) => `<option${y === this.year ? " selected" : ""}>${y}</option>`).join("")}</select></div>
  ${this.heatmap(this.year)}
</div>`);

      // Carbon.
      parts.push(section("green", "Carbon footprint"), `<div class="panel st-panel">
  <div class="st-carbon">
    <div><h5>Total</h5><b>${num(s.co2 / 1000, 1)}</b> <span>t CO₂</span></div>
    <div><h5>Per flight</h5><b>${num(s.co2 / Math.max(1, s.flights))}</b> <span>kg</span></div>
    <div><h5>To absorb it</h5><b>${num(s.co2 / TREE_KG_YEAR)}</b> <span>trees for a year</span></div>
  </div>
  <div class="st-stack">${s.lengths.map((g, i) => (g.co2 ? `<i class="st-l${i}" style="flex:${g.co2}"></i>` : "")).join("")}</div>
  <ul class="st-legend st-inline">${s.lengths.map((g, i) => `<li><i class="st-l${i}"></i><span>${g.name} flights ${s.co2 ? Math.round((100 * g.co2) / s.co2) : 0}%</span></li>`).join("")}</ul>
  <p class="hint">An estimate per seat, had it been real: the distance flown × 0.115 kg CO₂ per km × 2 for the effects of flying at altitude. A flight in the sim burns nothing.</p>
</div>`);

      // From the sim.
      const sim = s.sim;
      const fpm = (v) => `${Math.round(v)} fpm`;
      parts.push(section("cyan", "From the sim"), `<div class="st-grid">
${card("gauge", "cyan", "Softest landing", s.softest ? fpm(s.softest.landing_vs_fpm) : "–", s.softest ? route(s.softest) : "No landings yet")}
${card("gauge", "cyan", "Average landing", s.avgLanding != null ? fpm(s.avgLanding) : "–", s.avgLanding != null ? "At touchdown" : "")}
${card("plane", "cyan", "Highest", s.highest?.max_alt_ft ? `${num(s.highest.max_alt_ft)} ft` : "–", s.highest?.max_alt_ft ? route(s.highest) : "")}
${card("plane", "cyan", "Longest", s.longest?.distance_nm ? `${num(s.longest.distance_nm)} nm` : "–", s.longest?.distance_nm ? route(s.longest) : "")}
${sim ? `${card("drop", "cyan", "Fuel burned", sim.fuel ? `${num(sim.fuel / 2.2046 / 1000, 1)} t` : "–", sim.fuel ? `${num(sim.fuel)} lb over ${sim.n} flights` : "Not reported by the aircraft")}
${card("gauge", "cyan", "Top ground speed", sim.fast?.metrics.max_gs_kt ? `${num(sim.fast.metrics.max_gs_kt)} kt` : "–", sim.fast ? route(sim.fast) : "")}
${card("moon", "cyan", "Night landings", num(sim.nightLandings), `${num(sim.nightTakeoffs)} night takeoffs`)}
${card("clock", "cyan", "In cloud", `${num(sim.cloud / 60, 1)} h`, `Over ${sim.n} flights`)}
${card("compass", "cyan", "Coldest outside", sim.cold ? `${num(sim.cold.metrics.coldest_c, 1)} °C` : "–", sim.cold ? route(sim.cold) : "")}
${card("globe", "cyan", "Strongest wind", sim.windy?.metrics.max_wind_kt ? `${num(sim.windy.metrics.max_wind_kt)} kt` : "–", sim.windy ? route(sim.windy) : "")}` : ""}
</div>
${sim ? `<p class="hint">Measured by the app on ${sim.n} of your ${s.flights} flights: the ones flown with Stats and metrics on.</p>` : `<p class="hint">The fuel, speed, night and weather numbers come from flights flown with Stats and metrics on: none yet.</p>`}`);

      this.el.innerHTML = parts.join("\n");
      this.drawWorld(visited);
      this.el.querySelector(".st-chips").onclick = (e) => { const b = e.target.closest("button[data-cont]"); if (b) { this.continent = b.dataset.cont; this.render(); } };
      this.el.querySelector(".st-all").onclick = () => { this.showAll = !this.showAll; this.render(); };
      this.el.querySelector(".st-year").onchange = (e) => { this.year = Number(e.target.value); this.render(); };
    }

    /** The countries flown to, shaded by visits, on the world map (when its file loads). */
    async drawWorld(visited) {
      const box = this.el.querySelector(".st-world");
      let shapes;
      try { shapes = await world(); } catch { box.remove(); return; }
      if (!box.isConnected) return;
      const most = Math.max(1, ...visited.values());
      box.innerHTML = `<svg viewBox="0 0 360 143" role="img">${shapes.map((c) => {
        const n = c.iso ? visited.get(c.iso) || 0 : 0;
        const tip = `${c.iso && C[c.iso] ? C[c.iso][0] : c.name}${n ? `: ${n} ${n === 1 ? "visit" : "visits"}` : ""}`;
        return `<path d="${c.d}"${n ? ` class="on" style="--k:${(0.45 + 0.55 * (n / most)).toFixed(2)}"` : ""}><title>${esc(tip)}</title></path>`;
      }).join("")}</svg><div class="st-world-key hint">Fewer <i></i> More flights</div>`;
    }

    /** A year of days, a column a week (Monday first), shaded by the flights on each. */
    heatmap(year) {
      const start = new Date(year, 0, 1);
      start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
      const cells = [];
      const months = [];
      for (let d = new Date(start), w = 0; d.getFullYear() <= year; w++) {
        for (let i = 0; i < 7; i++, d.setDate(d.getDate() + 1)) {
          const inYear = d.getFullYear() === year;
          const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
          const n = inYear ? this.s.days.get(k) || 0 : 0;
          if (inYear && d.getDate() === 1) months.push([w, MONTHS[d.getMonth()]]);
          cells.push(`<i class="${inYear ? `h${Math.min(n, 3)}` : "out"}" style="grid-column:${w + 1};grid-row:${i + 1}"${inYear ? ` title="${esc(dateText(d))}: ${n} flight${n === 1 ? "" : "s"}"` : ""}></i>`);
        }
        if (d.getFullYear() > year) break;
      }
      return `<div class="st-heat-wrap"><div class="st-heat-months">${months.map(([w, m]) => `<span style="grid-column:${w + 1}">${m}</span>`).join("")}</div>
<div class="st-heat">${cells.join("")}</div></div>
<p class="st-heat-key hint">Fewer <i class="h1"></i><i class="h2"></i><i class="h3"></i> More</p>`;
    }
  }

  StatsView.compute = compute;
  StatsView.countryOf = countryOfAny;
  StatsView.airlineOf = airlineOf;
  window.StatsView = StatsView;
})();
