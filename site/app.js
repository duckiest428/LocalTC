/* Three small things, no dependencies: the radio log types itself, the subscription sum adds up,
   and the install commands copy. */

// --- the radio log -------------------------------------------------------------------------------
// A real CYVR departure, in the words LocalTC actually uses.
const EXCHANGE = [
  { who: "Air Canada 216", cls: "you",
    text: "Vancouver Delivery, Air Canada 216, ready to copy IFR to Calgary." },
  { who: "Vancouver Delivery 121.400", cls: "atc",
    text: "Air Canada 216, cleared to Calgary airport as filed, climb and maintain 5,000, expect flight level 350 one zero minutes after departure, departure frequency 126.125, squawk 4512." },
  { who: "Air Canada 216", cls: "you",
    text: "Cleared to Calgary as filed, maintain 5,000, expect 350 in ten, departure 126.125, squawk 4512, Air Canada 216." },
  { who: "Vancouver Delivery 121.400", cls: "atc", ok: true,
    text: "Air Canada 216, readback correct." },
  { who: "Vancouver Ground 121.700", cls: "atc",
    text: "Air Canada 216, runway 08R, taxi via Golf, Charlie, hold short runway 13. Information Bravo is current, altimeter 29.92." },
  { who: "Air Canada 216", cls: "you",
    text: "Runway 08R via Golf Charlie, hold short 13, Air Canada 216." },
  { who: "Vancouver Ground 121.700", cls: "atc",
    text: "Air Canada 216, cross runway 13, continue taxi runway 08R." },
  { who: "Vancouver Tower 118.700", cls: "atc",
    text: "Air Canada 216, wind 090 at 8, runway 08R, cleared for takeoff." },
  { who: "Vancouver Departure 126.125", cls: "atc",
    text: "Air Canada 216, radar contact, climb and maintain flight level 350." },
  { who: "Vancouver Departure 126.125", cls: "atc",
    text: "Air Canada 216, contact Edmonton Center 124.525." },
];

const KEEP = 4;          // lines left on screen
const CHAR_MS = 17;      // how fast a transmission types out
const GAP_MS = 1100;     // quiet between transmissions

const log = document.getElementById("log");
const motionOk = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function line(entry) {
  const li = document.createElement("li");
  li.className = entry.cls;
  li.innerHTML = '<span class="who"></span><span class="said"></span>';
  li.querySelector(".who").textContent = entry.cls === "you" ? "You" : entry.who;
  if (entry.ok) li.querySelector(".said").classList.add("ok");
  log.append(li);
  while (log.children.length > KEEP) log.firstElementChild.remove();
  return li.querySelector(".said");
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function type(target, text) {
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  cursor.textContent = " ";
  target.after(cursor);
  for (let i = 1; i <= text.length; i++) {
    target.textContent = text.slice(0, i);
    await wait(CHAR_MS);
  }
  cursor.remove();
}

async function playRadio() {
  if (!log) return;
  if (!motionOk) {                       // no animation: just show the last few calls
    EXCHANGE.slice(-KEEP).forEach((e) => { line(e).textContent = e.text; });
    return;
  }
  for (;;) {
    for (const entry of EXCHANGE) {
      await type(line(entry), entry.text);
      await wait(GAP_MS);
    }
    await wait(1800);
    log.replaceChildren();
  }
}
playRadio();

// --- what the subscription costs -----------------------------------------------------------------
const rate = document.getElementById("rate");
const years = document.getElementById("years");

function sum() {
  const monthly = Number(rate.value);
  const y = Number(years.value);
  document.getElementById("rate-out").textContent = `$${monthly}`;
  document.getElementById("years-out").textContent = `${y} year${y === 1 ? "" : "s"}`;
  document.getElementById("sub-total").textContent =
    `$${(monthly * 12 * y).toLocaleString("en-US")}`;
}
if (rate && years) {
  rate.addEventListener("input", sum);
  years.addEventListener("input", sum);
  sum();
}

// --- copy the install commands -------------------------------------------------------------------
document.querySelectorAll(".copy").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = button.previousElementSibling.textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Press Ctrl-C";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1600);
  });
});
