/* The sim's toolbar panel (panel.html): the radio log and the radios, from /api/panel each second. Plain old script:
   the sim's own browser (Coherent) is no modern one, so no modules, no arrow functions, no fetch. */
"use strict";

var PanelWords = {};  // English -> the pilot's language (i18n/<code>.json)
var panelLast = -1e18;
var panelLang = "";
var panelLines = 0;

function panelSay(english) {
  return PanelWords[english] || english;
}

function panelGet(path, done, failed) {
  var xhr = new XMLHttpRequest();
  xhr.open("GET", path, true);
  xhr.timeout = 3000;
  xhr.onload = function () {
    if (xhr.status === 200) {
      try { done(JSON.parse(xhr.responseText)); } catch (e) { failed(); }
    } else { failed(); }
  };
  xhr.onerror = failed;
  xhr.ontimeout = failed;
  xhr.send();
}

function panelLanguage(code) {
  if (!code || code === panelLang) return;
  panelLang = code;
  if (code === "en") { PanelWords = {}; panelStatic(); return; }
  panelGet("i18n/" + code + ".json", function (words) { PanelWords = words || {}; panelStatic(); }, function () {});
}

function panelStatic() {
  var empty = document.getElementById("empty");
  if (empty) empty.textContent = panelSay(panelLines ? "" : "Waiting for LocalTC on this PC...");
}

function panelWho(line) {
  if (line.kind === "atc") return line.station || "ATC";
  if (line.kind === "chatter") return line.station || "";
  if (line.kind === "pilot") return panelSay("You");
  if (line.kind === "copilot") return panelSay("Copilot");
  if (line.kind === "crew" || line.kind === "intercom") return panelSay("Copilot");
  if (line.kind === "cabin" || line.kind === "ramp") return line.who || "";
  return "";
}

function panelAdd(line) {
  if (line.kind === "readback") return;
  var log = document.getElementById("log");
  var empty = document.getElementById("empty");
  if (empty) empty.parentNode.removeChild(empty);
  var row = document.createElement("div");
  row.className = "line " + (line.kind || "");
  row.setAttribute("translate", "no");
  var who = panelWho(line);
  if (who) {
    var b = document.createElement("span");
    b.className = "who";
    b.textContent = who + ":";
    row.appendChild(b);
  }
  row.appendChild(document.createTextNode(line.summary || line.text || ""));
  var atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  log.appendChild(row);
  panelLines += 1;
  while (log.childNodes.length > 120) log.removeChild(log.firstChild);
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function panelMhz(v) {
  return typeof v === "number" ? v.toFixed(3) : "---.---";
}

function panelPoll() {
  panelGet("/api/panel?after=" + panelLast, function (data) {
    panelLanguage(data.language);
    document.getElementById("com1").textContent = panelMhz(data.com1);
    document.getElementById("com2").textContent = panelMhz(data.com2);
    document.getElementById("state").textContent = data.flying ? "" : panelSay("No flight running");
    var lines = data.radio || [];
    for (var i = 0; i < lines.length; i++) {
      panelAdd(lines[i]);
      if (lines[i].t > panelLast) panelLast = lines[i].t;
    }
    setTimeout(panelPoll, 1000);
  }, function () {
    document.getElementById("state").textContent = panelSay("LocalTC isn't running");
    setTimeout(panelPoll, 3000);
  });
}

panelPoll();
