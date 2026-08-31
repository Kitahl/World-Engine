(function () {
  "use strict";

  var MODES = [
    { id: "Story", icon: "✦", subtitle: "Accepted narration and choices" },
    { id: "Explore", icon: "⌖", subtitle: "Your current place and nearby people" },
    { id: "Combat", icon: "⚔", subtitle: "Round, turn, and visible combatants" },
    { id: "Character", icon: "◈", subtitle: "Your sheet, resources, and inventory" },
    { id: "World Map", icon: "◇", subtitle: "Known and public-map geography" },
    { id: "Investigation", icon: "⌕", subtitle: "Visible quests and relationships" }
  ];
  var currentMode = "Story";
  var latest = null;
  var initialized = false;

  function byId(id) { return document.getElementById(id); }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function node(tag, className, text) {
    var value = document.createElement(tag);
    if (className) { value.className = className; }
    if (text !== undefined && text !== null) { value.textContent = String(text); }
    return value;
  }
  function sectionTitle(text) { return node("h2", "section-title", text); }
  function safeArray(value) { return Array.isArray(value) ? value : []; }
  function safeObject(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function apiReady() { return window.pywebview && window.pywebview.api; }
  function initials(name) {
    var parts = String(name || "?").trim().split(/\s+/).filter(Boolean);
    return (parts.slice(0, 2).map(function (part) { return part.charAt(0); }).join("") || "?").toUpperCase();
  }
  function statusClass(value) {
    value = String(value || "").toUpperCase();
    if (value === "READY" || value === "PASS") { return "status-chip ready"; }
    if (value === "FAILED" || value === "OFFLINE") { return "status-chip error"; }
    if (value === "AUTH_REQUIRED" || value === "INSTALL_REQUIRED" || value === "RECOVERY_REQUIRED" || value === "DEGRADED") {
      return "status-chip warning";
    }
    return "status-chip pending";
  }
  function showNotice(message) {
    var notice = byId("notice");
    notice.textContent = message || "";
    notice.classList.toggle("hidden", !message);
  }
  function emptyState(title, message) {
    var box = node("div", "empty-state");
    box.append(node("strong", "", title), node("span", "", message));
    return box;
  }
  function dataCard(title, lines) {
    var card = node("article", "data-card");
    card.appendChild(node("h3", "", title));
    safeArray(lines).forEach(function (line) {
      card.appendChild(node("p", "", line));
    });
    return card;
  }

  function renderRibbon(data) {
    var campaign = safeObject(data.campaign);
    byId("campaign-meta").textContent = campaign.name
      ? campaign.name + " · " + (campaign.world_time || "time unknown") + " · " + (campaign.weather || "weather unknown")
      : "Local companion";
    var states = safeObject(data.states);
    var engine = byId("engine-state");
    engine.textContent = "Engine · " + String(states.engine || "checking").toLowerCase();
    engine.className = statusClass(states.engine);
    var gpt = byId("gpt-state");
    gpt.textContent = "GPT link · " + String(states.gpt_link || "optional").toLowerCase().replaceAll("_", " ");
    gpt.className = statusClass(states.gpt_link);
  }

  function renderPlayer(data) {
    var root = byId("player-summary");
    clear(root);
    var player = data.player;
    if (!player) {
      root.append(node("span", "avatar", "?"));
      var missing = node("span");
      missing.append(node("strong", "", "No playable character"), node("small", "", "Forge a new world or choose an existing campaign."));
      root.appendChild(missing);
      return;
    }
    root.append(node("span", "avatar", initials(player.name)));
    var copy = node("span");
    copy.append(
      node("strong", "", player.name + " · Level " + player.level),
      node("small", "", "HP " + player.hp + "/" + player.max_hp + " · AC " + player.ac + " · " + (data.location ? data.location.name : player.location_id))
    );
    root.appendChild(copy);
  }

  async function copyChoice(text, button) {
    var original = button.textContent;
    try {
      var result = await window.pywebview.api.copy_text(String(text));
      button.textContent = result.ok ? "Copied" : "Press Ctrl+C";
    } catch (_error) {
      button.textContent = "Press Ctrl+C";
    }
    window.setTimeout(function () { button.textContent = original; }, 1200);
  }

  function renderChoices(data) {
    var root = byId("choice-dock");
    clear(root);
    var choices = safeArray(safeObject(data.presentation).choices);
    if (!choices.length) {
      root.appendChild(node("span", "muted", "No accepted choices."));
      return;
    }
    choices.forEach(function (choice, index) {
      var button = node("button", "choice-button");
      button.type = "button";
      button.append(node("span", "", String(index + 1)), document.createTextNode(String(choice)));
      button.addEventListener("click", function () { copyChoice(choice, button); });
      root.appendChild(button);
    });
  }

  function renderStory(data, root) {
    var presentation = safeObject(data.presentation);
    if (!presentation.narration) {
      root.appendChild(emptyState("No accepted narration yet", "Use World Engine through your GPT or local workflow. Only narration that passed the publication gate appears here."));
      return;
    }
    var card = node("article", "story-card");
    card.appendChild(node("p", "narration", presentation.narration));
    root.appendChild(card);
  }

  function renderExplore(data, root) {
    var location = data.location;
    if (!location) {
      root.appendChild(emptyState("No current location", "Create or load a character with a valid location."));
      return;
    }
    var place = node("article", "story-card");
    place.append(node("p", "eyebrow", location.region || "Unknown region"), node("h2", "section-title", location.name), node("p", "narration", location.description || "No public description."));
    root.appendChild(place);
    root.appendChild(sectionTitle("People here"));
    var people = node("div", "card-grid");
    safeArray(data.known_npcs).forEach(function (npc) {
      people.appendChild(dataCard(npc.name, [
        npc.faction_id ? "Faction: " + npc.faction_id : "Independent",
        "Disposition: " + (npc.attitude > 1 ? "friendly" : npc.attitude < -1 ? "wary" : "neutral")
      ]));
    });
    if (!people.childNodes.length) { people.appendChild(emptyState("The area is quiet", "No player-visible NPCs are present.")); }
    root.appendChild(people);
    root.appendChild(sectionTitle("Active quests"));
    var quests = node("div", "card-grid");
    safeArray(data.quests).filter(function (quest) { return quest.status === "active"; }).forEach(function (quest) {
      quests.appendChild(dataCard(quest.title, safeArray(quest.objectives).map(function (objective) { return "• " + objective.text; })));
    });
    if (!quests.childNodes.length) { quests.appendChild(emptyState("No active quests", "Your accepted objectives will appear here.")); }
    root.appendChild(quests);
  }

  function renderCombat(data, root) {
    var combat = data.combat;
    if (!combat) {
      root.appendChild(emptyState("No active combat", "Combat mode activates automatically when your character enters an authoritative encounter."));
      return;
    }
    var summary = node("div", "card-grid");
    summary.append(dataCard("Round", [String(combat.round)]), dataCard("Location", [combat.location_id]), dataCard("Combatants", [String(safeArray(combat.participants).length)]));
    root.appendChild(summary);
    root.appendChild(sectionTitle("Initiative"));
    var list = node("div", "initiative-list");
    safeArray(combat.participants).forEach(function (actor, index) {
      var row = node("div", "initiative-row" + (index === combat.turn_index ? " current" : ""));
      row.append(node("strong", "", (index + 1) + ". " + actor.name), node("span", "muted", actor.is_player ? "You" : actor.status));
      list.appendChild(row);
    });
    root.appendChild(list);
  }

  function inventoryLabel(item) {
    if (typeof item === "string") { return item; }
    item = safeObject(item);
    var label = item.name || item.item_id || item.id || "Item";
    if (item.qty !== undefined) { label += " × " + item.qty; }
    return String(label);
  }

  function renderCharacter(data, root) {
    var player = data.player;
    if (!player) {
      root.appendChild(emptyState("No character sheet", "Forge a world or load a campaign with a playable character."));
      return;
    }
    var stats = node("div", "card-grid");
    [["Level", player.level], ["Hit points", player.hp + " / " + player.max_hp], ["Armor class", player.ac], ["Proficiency", "+" + player.proficiency_bonus]].forEach(function (entry) {
      var card = dataCard(entry[0], []);
      card.appendChild(node("span", "stat-value", entry[1]));
      if (entry[0] === "Hit points") {
        var meter = node("div", "meter");
        var fill = node("span");
        fill.style.width = Math.max(0, Math.min(100, (player.hp / player.max_hp) * 100)) + "%";
        meter.appendChild(fill);
        card.appendChild(meter);
      }
      stats.appendChild(card);
    });
    root.appendChild(stats);
    root.appendChild(sectionTitle("Abilities"));
    var abilities = node("div", "card-grid");
    Object.keys(safeObject(player.abilities)).sort().forEach(function (key) {
      abilities.appendChild(dataCard(key.toUpperCase(), [String(player.abilities[key])]));
    });
    root.appendChild(abilities);
    root.appendChild(sectionTitle("Inventory"));
    var inventory = node("div", "tag-row");
    safeArray(data.inventory).forEach(function (item) { inventory.appendChild(node("span", "tag", inventoryLabel(item))); });
    if (!inventory.childNodes.length) { inventory.appendChild(node("span", "muted", "Inventory is empty.")); }
    root.appendChild(inventory);
    root.appendChild(sectionTitle("Conditions"));
    var conditions = node("div", "tag-row");
    safeArray(player.conditions).forEach(function (condition) { conditions.appendChild(node("span", "tag", String(condition))); });
    if (!conditions.childNodes.length) { conditions.appendChild(node("span", "muted", "No active conditions.")); }
    root.appendChild(conditions);
  }

  function renderMap(data, root) {
    var world = safeObject(data.world_map);
    var locations = safeArray(world.locations);
    if (!locations.length) {
      root.appendChild(emptyState("No player-known map", "World Engine does not expose hidden geography. Generated public-map locations or your current location will appear here."));
      return;
    }
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "world-map");
    svg.setAttribute("viewBox", "0 0 900 520");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Map of player-known World Engine locations");
    var xs = locations.map(function (place) { return Number(place.x || 0); });
    var ys = locations.map(function (place) { return Number(place.y || 0); });
    var minX = Math.min.apply(null, xs);
    var maxX = Math.max.apply(null, xs);
    var minY = Math.min.apply(null, ys);
    var maxY = Math.max.apply(null, ys);
    function px(value) { return 70 + ((Number(value || 0) - minX) / Math.max(1, maxX - minX)) * 760; }
    function py(value) { return 70 + ((Number(value || 0) - minY) / Math.max(1, maxY - minY)) * 370; }
    var indexed = {};
    locations.forEach(function (place) { indexed[place.id] = place; });
    safeArray(world.links).forEach(function (link) {
      if (!indexed[link.from_id] || !indexed[link.to_id]) { return; }
      var line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("class", "map-link");
      line.setAttribute("x1", px(indexed[link.from_id].x));
      line.setAttribute("y1", py(indexed[link.from_id].y));
      line.setAttribute("x2", px(indexed[link.to_id].x));
      line.setAttribute("y2", py(indexed[link.to_id].y));
      svg.appendChild(line);
    });
    locations.forEach(function (place) {
      var circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("class", "map-node" + (place.id === world.current_location_id ? " current" : ""));
      circle.setAttribute("cx", px(place.x));
      circle.setAttribute("cy", py(place.y));
      circle.setAttribute("r", place.id === world.current_location_id ? "10" : "7");
      var label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("class", "map-label");
      label.setAttribute("x", px(place.x) + 13);
      label.setAttribute("y", py(place.y) + 4);
      label.textContent = place.name;
      svg.append(circle, label);
    });
    root.appendChild(svg);
    root.appendChild(node("p", "fine-print", "Only your current location and geography explicitly marked public-map are rendered."));
  }

  function renderInvestigation(data, root) {
    root.appendChild(sectionTitle("Quest leads"));
    var leads = node("div", "card-grid");
    safeArray(data.quests).forEach(function (quest) {
      leads.appendChild(dataCard(quest.title, safeArray(quest.objectives).map(function (objective) { return objective.text; })));
    });
    if (!leads.childNodes.length) { leads.appendChild(emptyState("No visible leads", "Private narrative constraints and unrevealed facts never appear here.")); }
    root.appendChild(leads);
    root.appendChild(sectionTitle("Known relationships"));
    var relations = node("div", "card-grid");
    safeArray(data.known_relationships).forEach(function (relation) {
      relations.appendChild(dataCard(relation.source_id + " → " + relation.target_id, [
        "Trust: " + relation.trust,
        "Respect: " + relation.respect,
        "Affection: " + relation.affection
      ]));
    });
    if (!relations.childNodes.length) { relations.appendChild(node("p", "muted", "No player-bound relationships are recorded.")); }
    root.appendChild(relations);
  }

  function renderInspector(data) {
    var root = byId("inspector-content");
    clear(root);
    var columns = node("div", "inspector-content-grid");
    var locationSection = node("section", "inspector-section");
    locationSection.appendChild(node("h3", "", "Current location"));
    if (data.location) {
      locationSection.append(node("strong", "", data.location.name), node("p", "muted", data.location.region));
    } else {
      locationSection.appendChild(node("p", "muted", "Not set."));
    }
    columns.appendChild(locationSection);
    var peopleSection = node("section", "inspector-section");
    peopleSection.appendChild(node("h3", "", "Known people"));
    safeArray(data.known_npcs).slice(0, 8).forEach(function (npc) {
      var button = node("button", "list-button");
      button.type = "button";
      button.append(node("strong", "", npc.name), node("small", "", npc.faction_id || "Independent"));
      peopleSection.appendChild(button);
    });
    if (peopleSection.childNodes.length === 1) { peopleSection.appendChild(node("p", "muted", "None visible.")); }
    columns.appendChild(peopleSection);
    var questSection = node("section", "inspector-section");
    questSection.appendChild(node("h3", "", "Quests"));
    safeArray(data.quests).slice(0, 8).forEach(function (quest) {
      var button = node("button", "list-button");
      button.type = "button";
      button.append(node("strong", "", quest.title), node("small", "", quest.status));
      button.addEventListener("click", function () { chooseMode("Investigation"); });
      questSection.appendChild(button);
    });
    if (questSection.childNodes.length === 1) { questSection.appendChild(node("p", "muted", "No player quests.")); }
    columns.appendChild(questSection);
    root.appendChild(columns);
  }

  function renderStage(data) {
    var config = MODES.find(function (mode) { return mode.id === currentMode; }) || MODES[0];
    byId("mode-title").textContent = config.id;
    byId("mode-subtitle").textContent = config.subtitle;
    byId("mode-eyebrow").textContent = data.mode === "COMBAT" ? "Authoritative combat active" : "Player view";
    var root = byId("stage-content");
    clear(root);
    if (currentMode === "Story") { renderStory(data, root); }
    else if (currentMode === "Explore") { renderExplore(data, root); }
    else if (currentMode === "Combat") { renderCombat(data, root); }
    else if (currentMode === "Character") { renderCharacter(data, root); }
    else if (currentMode === "World Map") { renderMap(data, root); }
    else { renderInvestigation(data, root); }
  }

  function render(data) {
    latest = data;
    renderRibbon(data);
    renderPlayer(data);
    renderChoices(data);
    renderInspector(data);
    renderStage(data);
    if (safeObject(data.states).engine !== "READY") {
      showNotice("The desktop is ready, but the local campaign is not available yet.");
    } else {
      showNotice("");
    }
  }

  async function refresh() {
    if (!apiReady()) { return; }
    try {
      render(await window.pywebview.api.snapshot());
    } catch (_error) {
      showNotice("The desktop is running, but the local engine did not answer.");
      var engine = byId("engine-state");
      engine.textContent = "Engine · offline";
      engine.className = "status-chip error";
    }
  }

  function chooseMode(mode) {
    currentMode = mode;
    document.querySelectorAll(".mode-button").forEach(function (button) {
      button.classList.toggle("active", button.dataset.mode === mode);
      button.setAttribute("aria-current", button.dataset.mode === mode ? "page" : "false");
    });
    if (latest) { renderStage(latest); }
  }

  function buildModeRail() {
    var rail = byId("mode-rail");
    clear(rail);
    MODES.forEach(function (mode) {
      var button = node("button", "mode-button" + (mode.id === currentMode ? " active" : ""));
      button.type = "button";
      button.dataset.mode = mode.id;
      button.setAttribute("aria-current", mode.id === currentMode ? "page" : "false");
      button.setAttribute("title", mode.id);
      button.append(node("span", "mode-icon", mode.icon), node("span", "mode-label", mode.id));
      button.addEventListener("click", function () { chooseMode(mode.id); });
      rail.appendChild(button);
    });
  }

  function openDialog(id) {
    var dialog = byId(id);
    if (!dialog.open) { dialog.showModal(); }
  }

  function generationSpec() {
    return {
      seed: byId("world-seed").value,
      namespace: byId("world-namespace").value,
      mode: byId("world-mode").value,
      days: Number(byId("dry-days").value),
      config: {
        location_count: Number(byId("count-locations").value),
        faction_count: Number(byId("count-factions").value),
        npcs_per_faction: Number(byId("count-npcs").value),
        resource_count: Number(byId("count-resources").value),
        quest_count: Number(byId("count-quests").value)
      }
    };
  }

  function formatResult(result) {
    if (!result) { return "No result returned."; }
    var lines = [
      (result.ok ? "PASS" : "NOT PASSED") + " · " + String(result.action || result.status || result.code || "result")
    ];
    if (result.message) { lines.push(String(result.message)); }
    if (result.batch_id) { lines.push("Batch: " + result.batch_id); }
    if (result.status) { lines.push("Status: " + result.status); }
    if (result.revision !== undefined && result.revision !== null) { lines.push("Revision: " + result.revision); }
    if (result.manifest && result.manifest.counts) { lines.push("Generated: " + JSON.stringify(result.manifest.counts)); }
    if (result.counts) { lines.push("Validated: " + JSON.stringify(result.counts)); }
    if (result.errors && result.errors.length) {
      lines.push("Errors:");
      result.errors.slice(0, 10).forEach(function (error) {
        lines.push("• " + String(error.path || "") + " " + String(error.message || error));
      });
    }
    return lines.join("\n");
  }

  async function runAuthoring(action, button) {
    var output = byId("authoring-output");
    output.textContent = "Running " + action.replace("_", " ") + "…";
    document.querySelectorAll("[data-author]").forEach(function (item) { item.disabled = true; });
    try {
      var result = await window.pywebview.api.authoring(action, byId("world-batch").value, generationSpec());
      output.textContent = formatResult(result);
      if (result.ok && action === "promote") {
        await refresh();
        chooseMode("World Map");
      }
    } catch (_error) {
      output.textContent = "Authoring did not complete. No success is claimed.";
    } finally {
      document.querySelectorAll("[data-author]").forEach(function (item) { item.disabled = false; });
      button.focus();
    }
  }

  async function configureNgrok() {
    var field = byId("ngrok-token");
    var output = byId("connection-output");
    var button = byId("configure-ngrok");
    var token = field.value;
    field.value = "";
    button.disabled = true;
    output.textContent = "Validating locally and preparing the secure endpoint…";
    try {
      var result = await window.pywebview.api.configure_ngrok(token);
      output.textContent = formatResult(result);
      await refresh();
    } catch (_error) {
      output.textContent = "ngrok setup did not complete. Local play remains available.";
    } finally {
      token = "";
      button.disabled = false;
    }
  }

  async function initialize() {
    if (initialized || !apiReady()) { return; }
    initialized = true;
    buildModeRail();
    byId("refresh-button").addEventListener("click", refresh);
    byId("connect-button").addEventListener("click", function () { openDialog("connection-dialog"); });
    byId("forge-button").addEventListener("click", function () { openDialog("forge-dialog"); });
    document.querySelectorAll("[data-close]").forEach(function (button) {
      button.addEventListener("click", function () { byId(button.dataset.close).close(); });
    });
    byId("open-ngrok").addEventListener("click", async function () {
      byId("connection-output").textContent = formatResult(await window.pywebview.api.open_external("ngrok_dashboard"));
    });
    byId("retry-endpoint").addEventListener("click", async function () {
      byId("connection-output").textContent = "Retrying the saved endpoint…";
      var result = await window.pywebview.api.retry_endpoint();
      byId("connection-output").textContent = formatResult(result);
      await refresh();
    });
    byId("configure-ngrok").addEventListener("click", configureNgrok);
    document.querySelectorAll("[data-author]").forEach(function (button) {
      button.addEventListener("click", function () { runAuthoring(button.dataset.author, button); });
    });
    try {
      var bootstrap = await window.pywebview.api.bootstrap();
      var defaults = safeObject(bootstrap.generation_defaults);
      if (defaults.seed) { byId("world-seed").value = defaults.seed; }
    } catch (_error) {
      showNotice("The local bridge is not ready.");
    }
    await refresh();
    window.setInterval(refresh, 2500);
  }

  window.addEventListener("pywebviewready", initialize);
  if (apiReady()) { initialize(); }
}());
