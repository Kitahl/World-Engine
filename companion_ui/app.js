(function () {
  "use strict";

  var MODES = [
    { id: "Story", icon: "✦", subtitle: "Accepted narration and choices" },
    { id: "Dialogue", icon: "❞", subtitle: "Text-only accepted conversation" },
    { id: "Explore", icon: "⌖", subtitle: "Your current place and nearby people" },
    { id: "Combat", icon: "⚔", subtitle: "Round, turn, and visible combatants" },
    { id: "Character", icon: "◈", subtitle: "Your sheet, resources, and inventory" },
    { id: "World Map", icon: "◇", subtitle: "Known and public-map geography" },
    { id: "Investigation", icon: "⌕", subtitle: "Visible quests and relationships" }
  ];
  var currentMode = "Story";
  var latest = null;
  var initialized = false;
  // Map navigation is deliberately a view-only convenience. It never calls
  // the bridge and therefore cannot move the character or disclose map data
  // beyond the already projected locations.
  var mapView = { scale: 1, x: 0, y: 0, selectedId: null };

  // Schemas this renderer understands. An unknown projection is refused rather
  // than drawn, so a downgraded or foreign payload can never be presented as if
  // it were authoritative.
  var SUPPORTED_SCHEMAS = ["WE-DESKTOP-5.1.0"];

  // pywebview resolves bridge calls on separate threads, so refreshes can land
  // out of order. A generation counter plus the last applied sequence means an
  // older snapshot that resolves late is discarded instead of overwriting newer
  // state; the pending flag keeps polls from overlapping and coalesces at most
  // one follow-up.
  var requestGeneration = 0;
  var appliedGeneration = -1;
  var appliedSequence = -1;
  var refreshInFlight = false;
  var refreshQueued = false;

  var lastSceneKey = null;
  var sceneOpenTimer = null;

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
  function numberText(value) {
    var numeric = Number(value || 0);
    return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2).replace(/\.?0+$/, "");
  }
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
  function shortText(value, limit) {
    var text = String(value || "").trim();
    return text.length > limit ? text.slice(0, Math.max(0, limit - 1)) + "…" : text;
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

  function renderDialogue(data, root) {
    var presentation = safeObject(data.presentation);
    if (!presentation.narration) {
      root.appendChild(emptyState("No accepted dialogue yet", "Dialogue uses the same exact player-safe narration and choices as Story."));
      return;
    }
    var card = node("article", "story-card dialogue-card");
    card.append(
      node("p", "eyebrow", "Accepted presentation"),
      node("p", "narration", presentation.narration),
      node("p", "fine-print", "Speaker identities and portraits are not inferred from narration.")
    );
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

    var environment = safeObject(data.environment);
    var weather = safeArray(environment.weather)[0];
    var effects = safeArray(environment.location_effects);
    if (weather || effects.length) {
      root.appendChild(sectionTitle("Environment"));
      var environmentCards = node("div", "card-grid");
      if (weather) {
        environmentCards.appendChild(dataCard("Weather", [
          String(weather.condition || "unknown"),
          "Temperature: " + numberText(weather.temperature_c) + " °C",
          "Visibility: " + numberText(weather.visibility)
        ]));
      }
      effects.forEach(function (effect) {
        environmentCards.appendChild(dataCard(String(effect.effect_type || "effect"), [
          "Intensity: " + numberText(effect.intensity),
          "Affected areas: " + numberText(effect.target_count)
        ]));
      });
      root.appendChild(environmentCards);
    }

    var settlement = safeObject(safeObject(data.population).settlement);
    if (Object.keys(settlement).length) {
      root.appendChild(sectionTitle("Settlement"));
      var settlementCards = node("div", "card-grid");
      settlementCards.append(
        dataCard(String(settlement.rank || settlement.settlement_type || "Settlement"), [
          "Population: " + numberText(settlement.population),
          "Prosperity: " + Math.round(Number(settlement.prosperity || 0) * 100) + "%",
          "Stability: " + Math.round(Number(settlement.stability || 0) * 100) + "%"
        ]),
        dataCard("Capacity", [
          "Food: " + numberText(settlement.food_capacity),
          "Housing: " + numberText(settlement.housing_capacity),
          "Water: " + numberText(settlement.water_capacity)
        ])
      );
      safeArray(settlement.service_gaps).slice(0, 4).forEach(function (gap) {
        settlementCards.appendChild(dataCard("Service gap · " + String(gap.service_kind || "unknown"), [
          "Shortfall: " + numberText(gap.gap)
        ]));
      });
      root.appendChild(settlementCards);
    }

    var economy = safeObject(data.economy);
    var markets = safeArray(economy.markets);
    var quotes = safeArray(economy.quotes);
    if (markets.length || quotes.length) {
      root.appendChild(sectionTitle("Local markets"));
      var marketCards = node("div", "card-grid");
      quotes.slice(0, 12).forEach(function (quote) {
        marketCards.appendChild(dataCard(String(quote.name || quote.item_id || "Item"), [
          "Stock: " + numberText(quote.stock),
          "Buy: " + numberText(quote.buy_price) + " " + String(quote.currency_key || ""),
          "Sell: " + numberText(quote.sell_price) + " " + String(quote.currency_key || "")
        ]));
      });
      if (!quotes.length) {
        markets.forEach(function (market) {
          marketCards.appendChild(dataCard(String(market.name || market.id), [
            numberText(market.item_count) + " listed items",
            "Currency: " + String(market.currency_key || "")
          ]));
        });
      }
      root.appendChild(marketCards);
    }

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
    root.appendChild(sectionTitle("Balances"));
    var balances = node("div", "tag-row");
    safeArray(data.balances).forEach(function (balance) {
      balances.appendChild(node("span", "tag", numberText(balance.amount) + " " + String(balance.currency_key || "")));
    });
    if (!balances.childNodes.length) { balances.appendChild(node("span", "muted", "No currency ledger entries.")); }
    root.appendChild(balances);
    if (safeArray(player.legacy_inventory).length && safeArray(player.inventory_ledger).length) {
      root.appendChild(sectionTitle("Legacy character notes"));
      var legacy = node("div", "tag-row");
      safeArray(player.legacy_inventory).forEach(function (item) { legacy.appendChild(node("span", "tag", inventoryLabel(item))); });
      root.appendChild(legacy);
    }
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
    var knownIds = locations.map(function (place) { return String(place.id); });
    if (knownIds.indexOf(String(mapView.selectedId)) === -1) {
      mapView.selectedId = String(world.current_location_id || locations[0].id);
    }
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "world-map");
    svg.setAttribute("viewBox", "0 0 900 520");
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", "Map of player-known World Engine locations. Use the location list after the map for keyboard selection.");
    svg.setAttribute("tabindex", "0");
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
    var viewport = document.createElementNS("http://www.w3.org/2000/svg", "g");
    viewport.setAttribute("class", "map-viewport");
    function applyMapView() {
      viewport.setAttribute("transform", "translate(" + mapView.x + " " + mapView.y + ") scale(" + mapView.scale + ")");
    }
    var mapNodes = {};
    safeArray(world.links).forEach(function (link) {
      if (!indexed[link.from_id] || !indexed[link.to_id]) { return; }
      var line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("class", "map-link");
      line.setAttribute("x1", px(indexed[link.from_id].x));
      line.setAttribute("y1", py(indexed[link.from_id].y));
      line.setAttribute("x2", px(indexed[link.to_id].x));
      line.setAttribute("y2", py(indexed[link.to_id].y));
      viewport.appendChild(line);
    });
    var selectedDetail = node("div", "map-selection", "");
    function selectLocation(place, focusMap) {
      mapView.selectedId = String(place.id);
      locations.forEach(function (candidate) {
        var isSelected = String(candidate.id) === mapView.selectedId;
        var mapNode = mapNodes[String(candidate.id)];
        if (mapNode) {
          mapNode.classList.toggle("selected", isSelected);
          mapNode.setAttribute("aria-pressed", isSelected ? "true" : "false");
        }
      });
      clear(selectedDetail);
      selectedDetail.append(node("strong", "", String(place.name || "Known location")));
      selectedDetail.append(node("span", "muted", String(place.region || "Public-map location")));
      if (focusMap) { svg.focus(); }
    }
    locations.forEach(function (place) {
      var circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      var selected = String(place.id) === mapView.selectedId;
      circle.setAttribute("class", "map-node" + (place.id === world.current_location_id ? " current" : "") + (selected ? " selected" : ""));
      circle.setAttribute("cx", px(place.x));
      circle.setAttribute("cy", py(place.y));
      circle.setAttribute("r", place.id === world.current_location_id ? "10" : "7");
      circle.setAttribute("data-location-id", String(place.id));
      circle.setAttribute("role", "button");
      circle.setAttribute("tabindex", "0");
      circle.setAttribute("aria-label", "Select " + String(place.name || "known location"));
      circle.setAttribute("aria-pressed", selected ? "true" : "false");
      circle.addEventListener("click", function () { selectLocation(place, false); });
      mapNodes[String(place.id)] = circle;
      circle.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectLocation(place, false);
        }
      });
      var label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("class", "map-label");
      label.setAttribute("x", px(place.x) + 13);
      label.setAttribute("y", py(place.y) + 4);
      label.textContent = place.name;
      label.setAttribute("aria-hidden", "true");
      viewport.append(circle, label);
    });
    applyMapView();
    svg.appendChild(viewport);
    var dragging = null;
    svg.addEventListener("pointerdown", function (event) {
      if (event.target && event.target.classList && event.target.classList.contains("map-node")) { return; }
      dragging = { x: event.clientX, y: event.clientY, originX: mapView.x, originY: mapView.y };
      svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener("pointermove", function (event) {
      if (!dragging) { return; }
      var box = svg.getBoundingClientRect();
      mapView.x = dragging.originX + ((event.clientX - dragging.x) * 900 / Math.max(1, box.width));
      mapView.y = dragging.originY + ((event.clientY - dragging.y) * 520 / Math.max(1, box.height));
      applyMapView();
    });
    function stopDragging(event) {
      if (dragging && svg.hasPointerCapture(event.pointerId)) { svg.releasePointerCapture(event.pointerId); }
      dragging = null;
    }
    svg.addEventListener("pointerup", stopDragging);
    svg.addEventListener("pointercancel", stopDragging);
    svg.addEventListener("wheel", function (event) {
      event.preventDefault();
      var nextScale = Math.max(0.65, Math.min(2.5, mapView.scale * (event.deltaY < 0 ? 1.12 : 0.89)));
      if (nextScale !== mapView.scale) { mapView.scale = nextScale; applyMapView(); }
    }, { passive: false });
    root.appendChild(svg);
    root.appendChild(node("p", "fine-print", "Drag to pan. Use the mouse wheel to zoom. Selecting a location only changes this local map view."));
    var locationList = node("div", "map-location-list");
    locationList.setAttribute("aria-label", "Known locations");
    locations.forEach(function (place) {
      var button = node("button", "map-location-button", String(place.name || "Known location"));
      button.type = "button";
      button.addEventListener("click", function () { selectLocation(place, true); });
      locationList.appendChild(button);
    });
    root.appendChild(locationList);
    selectLocation(indexed[mapView.selectedId], false);
    root.appendChild(selectedDetail);
    root.appendChild(node("p", "fine-print", "Only your current location and geography explicitly marked public-map are rendered."));
  }

  function renderInvestigation(data, root) {
    root.appendChild(sectionTitle("Executable quests"));
    var leads = node("div", "card-grid");
    safeArray(data.executable_quests || data.quests).forEach(function (quest) {
      var lines = safeArray(quest.objectives).map(function (objective) { return objective.text; });
      var activeNodes = safeArray(quest.nodes).filter(function (item) { return item.status === "active"; });
      if (activeNodes.length) { lines.push(activeNodes.length + " active executable step" + (activeNodes.length === 1 ? "" : "s")); }
      leads.appendChild(dataCard(quest.title, lines));
    });
    if (!leads.childNodes.length) { leads.appendChild(emptyState("No visible leads", "Private narrative constraints and unrevealed facts never appear here.")); }
    root.appendChild(leads);

    root.appendChild(sectionTitle("Incident journal"));
    var incidents = node("div", "card-grid");
    safeArray(safeObject(data.journal).incidents).forEach(function (incident) {
      incidents.appendChild(dataCard(String(incident.definition_id || incident.id || "Incident"), [
        String(incident.category || "world") + " · " + String(incident.status || "unknown"),
        String(incident.selected_world_time || "Time unknown")
      ]));
    });
    if (!incidents.childNodes.length) { incidents.appendChild(node("p", "muted", "No WORLD-visible incidents are recorded.")); }
    root.appendChild(incidents);

    root.appendChild(sectionTitle("Chronicle"));
    var chronicle = node("div", "card-grid");
    safeArray(safeObject(data.journal).presentations).forEach(function (entry) {
      var title = shortText(entry.title || entry.id || "Accepted presentation", 120);
      var when = shortText(entry.world_time || entry.accepted_at || "Time unknown", 120);
      var narration = shortText(entry.narration, 600);
      var lines = [when];
      if (narration) { lines.push(narration); }
      chronicle.appendChild(dataCard(title, lines));
    });
    if (!chronicle.childNodes.length) { chronicle.appendChild(node("p", "muted", "No accepted presentation history is available.")); }
    root.appendChild(chronicle);

    root.appendChild(sectionTitle("Available world actions"));
    var affordances = node("div", "card-grid");
    safeArray(safeObject(data.agency).available_affordances).forEach(function (item) {
      affordances.appendChild(dataCard(String(item.id || "Action"), [
        "Operator: " + String(item.operator_id || "unknown"),
        item.location_id ? "Location: " + item.location_id : "World scope"
      ]));
    });
    if (!affordances.childNodes.length) { affordances.appendChild(node("p", "muted", "No public executable affordances are currently available.")); }
    root.appendChild(affordances);

    var politics = safeObject(data.politics);
    if (Object.keys(politics).length) {
      root.appendChild(sectionTitle("Public politics"));
      var politicalCards = node("div", "card-grid");
      safeArray(politics.wars).forEach(function (war) {
        politicalCards.appendChild(dataCard("War · " + String(war.id || "unknown"), [
          String(war.attacker_faction_id || "?") + " ↔ " + String(war.defender_faction_id || "?"),
          "Status: " + String(war.status || "unknown")
        ]));
      });
      safeArray(politics.treaties).forEach(function (treaty) {
        politicalCards.appendChild(dataCard(String(treaty.name || treaty.id || "Treaty"), ["Status: " + String(treaty.status || "unknown")]));
      });
      safeArray(politics.projects).forEach(function (project) {
        politicalCards.appendChild(dataCard(String(project.name || project.id || "Project"), ["Status: " + String(project.status || "unknown")]));
      });
      if (!politicalCards.childNodes.length) { politicalCards.appendChild(node("p", "muted", "No public political changes are visible.")); }
      root.appendChild(politicalCards);
    }

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
    else if (currentMode === "Dialogue") { renderDialogue(data, root); }
    else if (currentMode === "Explore") { renderExplore(data, root); }
    else if (currentMode === "Combat") { renderCombat(data, root); }
    else if (currentMode === "Character") { renderCharacter(data, root); }
    else if (currentMode === "World Map") { renderMap(data, root); }
    else { renderInvestigation(data, root); }
  }

  function render(data) {
    latest = data;
    setAccent(data);
    renderRibbon(data);
    renderAlertTier(data);
    renderPlayer(data);
    renderChoices(data);
    renderInspector(data);
    renderStage(data);
    drawSceneArt(data);
    maybeOpenScene(data);
    if (safeObject(data.states).engine !== "READY") {
      showNotice("The desktop is ready, but the local campaign is not available yet.");
    } else {
      showNotice("");
    }
  }


  // --- one state-derived accent ------------------------------------------- //
  function worldHour(value) {
    var iso = /T(\d{2}):/.exec(String(value || ""));
    if (iso) { return Number(iso[1]); }
    var loose = /(\d{1,2}):(\d{2})/.exec(String(value || ""));
    return loose ? Number(loose[1]) : 12;
  }

  function stableHue(text) {
    var hash = 0;
    var source = String(text || "world-engine");
    for (var i = 0; i < source.length; i += 1) {
      hash = (hash * 31 + source.charCodeAt(i)) % 360;
    }
    return hash;
  }

  /* Hue from the public location identity, warmth from the hour, saturation
     from the weather. A storm literally drains the interface. Every input is
     already inside the public projection. */
  function setAccent(data) {
    var location = safeObject(data.location);
    var campaign = safeObject(data.campaign);
    var weather = String(campaign.weather || "").toLowerCase();
    var hour = worldHour(campaign.world_time);
    var hue = stableHue(location.id || data.campaign_id || "world-engine");
    var warm = (hour >= 5 && hour < 8) || (hour >= 17 && hour < 21);
    var night = hour < 5 || hour >= 21;
    if (warm) { hue = 20 + (hue % 50); } else if (night) { hue = 200 + (hue % 60); }
    var saturation = 40;
    var lightness = 66;
    if (weather.indexOf("storm") >= 0) { saturation = 14; lightness = 58; }
    else if (weather.indexOf("rain") >= 0 || weather.indexOf("fog") >= 0) { saturation = 22; lightness = 60; }
    else if (weather.indexOf("snow") >= 0) { saturation = 16; lightness = 76; }
    if (night && lightness > 60) { lightness = 60; }
    var root = document.documentElement.style;
    root.setProperty("--accent", "hsl(" + hue + " " + saturation + "% " + lightness + "%)");
    root.setProperty("--accent-dim", "hsl(" + hue + " " + Math.round(saturation * 0.6) + "% " + Math.round(lightness * 0.42) + "%)");
    root.setProperty("--accent-wash", "hsl(" + hue + " " + saturation + "% " + lightness + "% / 12%)");
  }

  // --- deterministic procedural scene art ---------------------------------- //
  /* Drawn locally from safe projected values only. This is PRESENTATION: the
     engine owns places and weather, not ground cover, and nothing drawn here is
     ever read back as state. No remote image is fetched or dereferenced. */
  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t0 = Math.imul(a ^ (a >>> 15), a | 1);
      t0 ^= t0 + Math.imul(t0 ^ (t0 >>> 7), t0 | 61);
      return ((t0 ^ (t0 >>> 14)) >>> 0) / 4294967296;
    };
  }

  function drawSceneArt(data) {
    var canvas = byId("scene-canvas");
    if (!canvas || !canvas.getContext) { return; }
    var box = canvas.parentNode.getBoundingClientRect();
    if (box.width < 8 || box.height < 8) { return; }
    canvas.width = Math.max(320, Math.floor(box.width));
    canvas.height = Math.max(110, Math.floor(box.height));
    var g = canvas.getContext("2d");
    var W = canvas.width;
    var H = canvas.height;

    var campaign = safeObject(data.campaign);
    var location = safeObject(data.location);
    var weather = String(campaign.weather || "clear").toLowerCase();
    var hour = worldHour(campaign.world_time);
    var night = hour < 5 || hour >= 21;
    var seedBase = Number(data.terrain_seed) || 1;
    var key = String(location.id || "nowhere") + "|" + weather;
    var mixed = seedBase ^ 0x9E3779B9;
    for (var i = 0; i < key.length; i += 1) {
      mixed = (Math.imul(mixed, 131) + key.charCodeAt(i)) >>> 0;
    }
    var rand = mulberry32(mixed || 1);

    var top = "#33465c";
    var bottom = "#7d90a0";
    if (night) { top = "#080a14"; bottom = "#141a2c"; }
    else if (hour < 8) { top = "#241f38"; bottom = "#8a5a44"; }
    else if (hour >= 17) { top = "#26304a"; bottom = "#93613e"; }
    if (weather.indexOf("storm") >= 0) { top = "#12141a"; bottom = "#2a2e36"; }
    else if (weather.indexOf("rain") >= 0) { top = "#1c222a"; bottom = "#3f4852"; }
    else if (weather.indexOf("fog") >= 0) { top = "#2e3130"; bottom = "#585d5b"; }
    else if (weather.indexOf("snow") >= 0) { top = "#2b3542"; bottom = "#8c98a6"; }

    var sky = g.createLinearGradient(0, 0, 0, H * 0.78);
    sky.addColorStop(0, top);
    sky.addColorStop(1, bottom);
    g.fillStyle = sky;
    g.fillRect(0, 0, W, H);

    if (weather.indexOf("fog") < 0 && weather.indexOf("storm") < 0) {
      g.fillStyle = night ? "#dfe6f2" : "#ffe6b0";
      g.beginPath();
      g.arc(W * (0.18 + rand() * 0.64), H * (0.16 + rand() * 0.2), Math.max(5, H * 0.05), 0, Math.PI * 2);
      g.fill();
      if (night) {
        g.fillStyle = "rgba(210,220,240,0.85)";
        for (var s = 0; s < 60; s += 1) {
          g.fillRect(Math.floor(rand() * W), Math.floor(rand() * H * 0.55), 1.6, 1.6);
        }
      }
    }

    var region = String(location.region || "").toLowerCase();
    var wooded = /forest|wood|green|vale/.test(region);
    var high = /hill|upland|mountain|peak|north/.test(region);
    var base = wooded ? [52, 80, 50] : (high ? [92, 86, 76] : [88, 106, 60]);
    var mix = night ? [16, 20, 38] : [122, 145, 168];

    var layers = 5;
    var nearLine = null;
    var nearColor = base;
    for (var L = 0; L < layers; L += 1) {
      var depth = L / (layers - 1);
      var yBase = H * (0.50 + depth * 0.30);
      var amp = H * (0.115 - 0.019 * L) * (high ? 1.6 : 1);
      var k = 0.55 + depth * 0.8;
      var phase = rand() * 100;
      var wash = (1 - depth) * 0.66;
      var color = base.map(function (channel, index) {
        var lit = channel * (0.28 + 0.78 * depth) * (1 - wash) + mix[index] * wash;
        return Math.max(6, Math.min(240, Math.round(lit)));
      });
      g.fillStyle = "rgb(" + color.join(",") + ")";
      g.beginPath();
      g.moveTo(0, H);
      var line = [];
      for (var x = 0; x <= W; x += 3) {
        var n = Math.sin(x * 0.0075 * k + phase) * 0.6
              + Math.sin(x * 0.021 * k + phase * 1.7) * 0.4
              + Math.sin(x * 0.052 * k + phase * 2.3) * 0.14;
        var y = yBase - n * amp;
        line.push(y);
        g.lineTo(x, y);
      }
      g.lineTo(W, H);
      g.closePath();
      g.fill();
      nearLine = line;
      nearColor = color;
    }

    var groundY = H * 0.88;
    var fg = base.map(function (c) { return Math.max(4, Math.round(c * 0.16)); });
    g.fillStyle = "rgb(" + fg.join(",") + ")";
    g.beginPath();
    g.moveTo(0, H);
    for (var gx = 0; gx <= W; gx += 4) {
      g.lineTo(gx, groundY + Math.sin(gx * 0.03 + seedBase) * 3);
    }
    g.lineTo(W, H);
    g.closePath();
    g.fill();

    if (nearLine) {
      var dark = nearColor.map(function (c) { return Math.max(4, Math.round(c * 0.5)); });
      g.fillStyle = "rgb(" + dark.join(",") + ")";
      var count = Math.floor(W / 28);
      for (var t2 = 0; t2 < count; t2 += 1) {
        var idx = Math.floor(rand() * (nearLine.length - 1));
        var tx = idx * 3;
        var ty = nearLine[idx];
        if (high && !wooded) {
          g.beginPath(); g.moveTo(tx - 8, ty); g.lineTo(tx, ty - 18 - rand() * 12); g.lineTo(tx + 8, ty); g.closePath(); g.fill();
        } else {
          var height = 12 + rand() * 20;
          g.fillRect(tx, ty - height, 2.5, height);
          g.beginPath(); g.moveTo(tx - 6, ty - height + 3); g.lineTo(tx + 1.2, ty - height - 10); g.lineTo(tx + 8, ty - height + 3); g.closePath(); g.fill();
        }
      }
    }

    if (weather.indexOf("rain") >= 0 || weather.indexOf("storm") >= 0) {
      g.strokeStyle = "rgba(170,195,225,0.34)";
      g.lineWidth = 1;
      for (var r2 = 0; r2 < W / 3; r2 += 1) {
        var rx = rand() * W;
        var ry = rand() * H;
        g.beginPath(); g.moveTo(rx, ry); g.lineTo(rx - 3, ry + 12); g.stroke();
      }
    }
    if (weather.indexOf("snow") >= 0) {
      g.fillStyle = "rgba(240,246,252,0.8)";
      for (var w2 = 0; w2 < W / 4; w2 += 1) { g.fillRect(Math.floor(rand() * W), Math.floor(rand() * H), 1.8, 1.8); }
    }
    if (weather.indexOf("fog") >= 0) {
      g.fillStyle = "rgba(170,175,172,0.26)";
      g.fillRect(0, 0, W, H);
    }

    byId("scene-place").textContent = location.name || (data.campaign_id || "Unplaced scene");
    byId("scene-when").textContent = [campaign.world_time || "", campaign.weather || ""].filter(Boolean).join(" \u00b7 ");
  }

  /* Scene opening fires only on a genuine public-location change; repeating it
     on every poll would turn a beat into a flicker. */
  function maybeOpenScene(data) {
    var location = safeObject(data.location);
    var key = String(location.id || "");
    if (!key || key === lastSceneKey) { return; }
    lastSceneKey = key;
    var overlay = byId("scene-open");
    byId("scene-open-title").textContent = location.name || key;
    byId("scene-open-kicker").textContent = data.mode === "COMBAT" ? "Battle" : "Arriving";
    overlay.classList.add("is-open");
    if (sceneOpenTimer) { window.clearTimeout(sceneOpenTimer); }
    sceneOpenTimer = window.setTimeout(function () { overlay.classList.remove("is-open"); }, 2400);
  }

  function renderAlertTier(data) {
    var pill = byId("alert-pill");
    if (!pill) { return; }
    var summary = safeObject(data.notification_summary);
    var tier = String(summary.tier || "normal");
    pill.dataset.tier = tier;
    var label = tier === "critical"
      ? "Critical " + (summary.critical || 0)
      : (tier === "warning" ? "Warning " + (summary.warning || 0) : "Normal");
    pill.textContent = label;
  }

  async function refresh() {
    if (!apiReady()) { return; }
    // Never let two polls overlap; coalesce at most one follow-up so a slow
    // bridge call cannot queue an unbounded backlog of stale renders.
    if (refreshInFlight) { refreshQueued = true; return; }
    refreshInFlight = true;
    requestGeneration += 1;
    var generation = requestGeneration;
    try {
      var data = await window.pywebview.api.snapshot();
      applySnapshot(data, generation);
    } catch (_error) {
      showNotice("The desktop is running, but the local engine did not answer.");
      var engine = byId("engine-state");
      engine.textContent = "Engine · offline";
      engine.className = "status-chip error";
    } finally {
      refreshInFlight = false;
      if (refreshQueued) { refreshQueued = false; window.setTimeout(refresh, 0); }
    }
  }

  /* Ordering gate. pywebview resolves on separate threads, so a slower earlier
     call can land after a newer one; applying it would flicker stale values
     back onto the screen. A snapshot is applied only when it is both newer than
     the last applied request AND not behind the last applied revision. */
  function applySnapshot(data, generation) {
    var payload = safeObject(data);
    var schema = String(payload.schema || "");
    if (SUPPORTED_SCHEMAS.indexOf(schema) === -1) {
      showNotice("This companion does not recognise the projection format reported by the engine.");
      return;
    }
    if (generation < appliedGeneration) { return; }
    var sequence = Number(payload.projection_sequence);
    // Generation tracks requests; sequence tracks authoritative engine state.
    // The latter must never move backwards, including for the most recently
    // requested call. -1 is the sentinel before the first successful render.
    if (Number.isFinite(sequence) && appliedSequence >= 0 && sequence < appliedSequence) {
      return;
    }
    appliedGeneration = generation;
    if (Number.isFinite(sequence)) { appliedSequence = sequence; }
    render(payload);
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
    wireChrome();
    await refresh();
    window.setInterval(refresh, 2500);
  }

  /* Collapsible rail and drawer. Both keep an accessible name and an
     aria-expanded state so an icon-only control is still announced, and the
     drawer keeps a labelled way back once hidden. */
  function wireChrome() {
    var cockpit = document.querySelector(".cockpit");
    if (!cockpit) { return; }
    cockpit.dataset.rail = "expanded";
    var compactQuery = window.matchMedia("(max-width: 980px)");
    cockpit.dataset.drawer = compactQuery.matches ? "hidden" : "shown";

    var railToggle = byId("rail-toggle");
    if (railToggle) {
      railToggle.addEventListener("click", function () {
        var collapsed = cockpit.dataset.rail === "collapsed";
        cockpit.dataset.rail = collapsed ? "expanded" : "collapsed";
        railToggle.setAttribute("aria-expanded", collapsed ? "true" : "false");
        railToggle.setAttribute("aria-label", collapsed ? "Collapse navigation rail" : "Expand navigation rail");
        railToggle.textContent = collapsed ? "‹" : "›";
        if (latest) { drawSceneArt(latest); }
      });
    }

    var drawerToggle = byId("drawer-toggle");
    var drawerRestore = byId("drawer-restore");
    function setDrawer(shown) {
      cockpit.dataset.drawer = shown ? "shown" : "hidden";
      if (drawerToggle) { drawerToggle.setAttribute("aria-expanded", shown ? "true" : "false"); }
      if (drawerRestore) { drawerRestore.hidden = shown; }
      if (latest) { drawSceneArt(latest); }
    }
    if (drawerToggle) { drawerToggle.addEventListener("click", function () { setDrawer(false); }); }
    if (drawerRestore) { drawerRestore.addEventListener("click", function () { setDrawer(true); }); }

    var resizeTimer = null;
    var wasCompact = compactQuery.matches;
    window.addEventListener("resize", function () {
      if (resizeTimer) { window.clearTimeout(resizeTimer); }
      resizeTimer = window.setTimeout(function () {
        var isCompact = compactQuery.matches;
        if (isCompact !== wasCompact) {
          // A side drawer must never suddenly cover the stage after a resize.
          setDrawer(!isCompact);
          wasCompact = isCompact;
        }
        if (latest) { drawSceneArt(latest); }
      }, 120);
    });
  }

  window.addEventListener("pywebviewready", initialize);
  if (apiReady()) { initialize(); }
}());
