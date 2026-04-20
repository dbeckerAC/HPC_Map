import maplibregl from "maplibre-gl";
import "./styles.css";

const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");
const TILESERVER_BASE = (import.meta.env.VITE_TILESERVER_BASE || "http://localhost:8080").replace(/\/$/, "");
const hoverEl = document.getElementById("hover");
const hpcToggle = document.getElementById("hpc-toggle");
const thresholdSelect = document.getElementById("threshold-select");
const appEl = document.getElementById("app");
const panelToggle = document.getElementById("panel-toggle");
const PANEL_STATE_KEY = "hpc_panel_collapsed";

let thresholdVariants = [];
let activeThreshold = "150";

const map = new maplibregl.Map({
  container: "map",
  center: [10.5, 51.1],
  zoom: 5.6,
  style: {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "&copy; OpenStreetMap contributors"
      }
    },
    layers: [
      {
        id: "osm",
        type: "raster",
        source: "osm",
        paint: {
          "raster-opacity": 0.62,
          "raster-saturation": -0.75,
          "raster-contrast": 0.15,
          "raster-brightness-max": 0.78
        }
      }
    ]
  }
});

function thresholdToken(value) {
  const numeric = Number(value);
  if (Number.isFinite(numeric) && Number.isInteger(numeric)) {
    return String(numeric);
  }
  return String(value).replace(".", "p");
}

async function loadConfig() {
  const response = await fetch(`${API_BASE}/config`);
  if (!response.ok) {
    throw new Error(`config request failed: ${response.status}`);
  }
  return response.json();
}

function buildThresholdVariants(config) {
  const analysis = config.analysis || {};
  const thresholds = Array.isArray(analysis.power_thresholds_kw) && analysis.power_thresholds_kw.length > 0
    ? analysis.power_thresholds_kw
    : [config.min_power_kw ?? 150];
  const distancePrefix = config.tiles?.distance_layer_prefix || "hpc_distance";
  const distanceLayer = config.tiles?.distance_layer_name || "hpc_distance";
  const hpcPrefix = config.tiles?.hpc_layer_prefix || "hpc_sites";
  const defaultThreshold = thresholdToken(
    analysis.default_power_threshold_kw ?? config.min_power_kw ?? thresholds[0]
  );
  const variants = thresholds.map((value) => {
    const token = thresholdToken(value);
    return {
      id: token,
      numericValue: Number(value),
      label: `${value}+ kW`,
      distanceSource: `distance-${token}`,
      distanceLayer,
      hpcSource: `hpc-${token}`,
      hpcHighlightSource: `hpc-highlight-${token}`,
      hpcGeojson: `${API_BASE}/layers/${hpcPrefix}_${token}.geojson`,
      tiles: [`${TILESERVER_BASE}/data/${distancePrefix}_${token}/{z}/{x}/{y}.pbf`]
    };
  });

  const direct = analysis.autobahn_direct_hpc || {};
  if (direct.enabled) {
    const token = "autobahn_direct_hpc";
    const minPower = Number(direct.min_power_kw ?? config.min_power_kw ?? 150);
    variants.push({
      id: token,
      numericValue: minPower,
      label: `Autobahn-direct HPC (${minPower}+ kW)`,
      distanceSource: `distance-${token}`,
      distanceLayer,
      hpcSource: `hpc-${token}`,
      hpcHighlightSource: `hpc-highlight-${token}`,
      hpcGeojson: `${API_BASE}/layers/${hpcPrefix}_${token}.geojson`,
      tiles: [`${TILESERVER_BASE}/data/${distancePrefix}_${token}/{z}/{x}/{y}.pbf`]
    });
  }

  return {
    defaultThreshold,
    variants
  };
}

function setPanelCollapsed(collapsed) {
  if (!appEl || !panelToggle) return;
  appEl.classList.toggle("panel-collapsed", collapsed);
  panelToggle.textContent = collapsed ? "Show info" : "Hide info";
  panelToggle.setAttribute("aria-expanded", String(!collapsed));
  window.localStorage.setItem(PANEL_STATE_KEY, collapsed ? "1" : "0");
  setTimeout(() => map.resize(), 120);
}

if (panelToggle) {
  const persisted = window.localStorage.getItem(PANEL_STATE_KEY) === "1";
  setPanelCollapsed(persisted);
  panelToggle.addEventListener("click", () => {
    setPanelCollapsed(!appEl?.classList.contains("panel-collapsed"));
  });
}

function distanceLayerIds(variant) {
  return [`hpc-distance-casing-${variant.id}`, `hpc-distance-${variant.id}`];
}

function hpcLayerIds(variant) {
  return [
    `hpc-clusters-${variant.id}`,
    `hpc-cluster-count-${variant.id}`,
    `hpc-sites-${variant.id}`,
    `hpc-nearest-highlight-${variant.id}`
  ];
}

function addDistanceLayer(variant) {
  map.addLayer({
    id: `hpc-distance-casing-${variant.id}`,
    type: "line",
    source: variant.distanceSource,
    layout: {
      "line-cap": "round",
      "line-join": "round"
    },
    paint: {
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 2.6, 12, 6.5],
      "line-color": "#0f141a",
      "line-opacity": 0.55
    },
    "source-layer": variant.distanceLayer
  });

  map.addLayer({
    id: `hpc-distance-${variant.id}`,
    type: "line",
    source: variant.distanceSource,
    layout: {
      "line-cap": "round",
      "line-join": "round"
    },
    paint: {
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1.8, 12, 4.6],
      "line-color": [
        "interpolate",
        ["linear"],
        ["coalesce", ["get", "distance_start_km"], 20],
        0,
        "#16a34a",
        8,
        "#facc15",
        14,
        "#f59e0b",
        20,
        "#dc2626"
      ],
      "line-opacity": 0.96
    },
    "source-layer": variant.distanceLayer
  });
}

function addHpcLayer(variant) {
  map.addLayer({
    id: `hpc-clusters-${variant.id}`,
    type: "circle",
    source: variant.hpcSource,
    filter: ["has", "point_count"],
    paint: {
      "circle-color": "#0b57d0",
      "circle-opacity": 0.82,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.0,
      "circle-radius": [
        "interpolate",
        ["linear"],
        ["get", "point_count"],
        10,
        12,
        50,
        17,
        200,
        22,
        1000,
        28
      ]
    }
  });

  map.addLayer({
    id: `hpc-cluster-count-${variant.id}`,
    type: "symbol",
    source: variant.hpcSource,
    filter: ["has", "point_count"],
    layout: {
      "text-field": ["to-string", ["get", "point_count_abbreviated"]],
      "text-size": 11
    },
    paint: {
      "text-color": "#ffffff"
    }
  });

  map.addLayer({
    id: `hpc-sites-${variant.id}`,
    type: "circle",
    source: variant.hpcSource,
    filter: ["!", ["has", "point_count"]],
    minzoom: 8.8,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.0, 12, 4.5],
      "circle-color": "#1155cc",
      "circle-opacity": 0.88,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 0.9
    }
  });

  map.addLayer({
    id: `hpc-nearest-highlight-${variant.id}`,
    type: "circle",
    source: variant.hpcHighlightSource,
    minzoom: 5,
    filter: ["==", ["get", "charger_id"], "__none__"],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 7, 10, 10, 14, 13],
      "circle-color": "#f59e0b",
      "circle-opacity": 0.95,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2.0
    }
  });
}

function setLayerVisibility(layerIds, visibility) {
  for (const layerId of layerIds) {
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", visibility);
    }
  }
}

function applyActiveThreshold() {
  const showHpc = !!hpcToggle?.checked;
  for (const variant of thresholdVariants) {
    const active = variant.id === activeThreshold;
    setLayerVisibility(distanceLayerIds(variant), active ? "visible" : "none");
    setLayerVisibility(
      [`hpc-clusters-${variant.id}`, `hpc-cluster-count-${variant.id}`, `hpc-sites-${variant.id}`],
      active && showHpc ? "visible" : "none"
    );
    setLayerVisibility([`hpc-nearest-highlight-${variant.id}`], active ? "visible" : "none");
  }
  const label = thresholdVariants.find((variant) => variant.id === activeThreshold)?.label || `${activeThreshold}+ kW`;
  hoverEl.textContent = `Hover a motorway segment | active threshold: ${label}`;
}

function bindHoverEventsForVariant(variant) {
  map.on("mousemove", `hpc-distance-${variant.id}`, (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const start = Number(feature.properties?.distance_start_km ?? NaN);
    const end = Number(feature.properties?.distance_end_km ?? NaN);
    const minPower = feature.properties?.min_power_kw ?? `${variant.numericValue}+`;
    const nearestHpcId = feature.properties?.nearest_hpc_id ?? "n/a";
    const avg = Number.isFinite(start) && Number.isFinite(end) ? ((start + end) / 2).toFixed(2) : "n/a";
    hoverEl.textContent = `Distance: ${avg} km | threshold: ${minPower} kW | nearest HPC: ${nearestHpcId}`;
    if (nearestHpcId && nearestHpcId !== "n/a" && map.getLayer(`hpc-nearest-highlight-${variant.id}`)) {
      map.setFilter(`hpc-nearest-highlight-${variant.id}`, ["==", ["get", "charger_id"], String(nearestHpcId)]);
    }
  });

  map.on("mouseleave", `hpc-distance-${variant.id}`, () => {
    if (map.getLayer(`hpc-nearest-highlight-${variant.id}`)) {
      map.setFilter(`hpc-nearest-highlight-${variant.id}`, ["==", ["get", "charger_id"], "__none__"]);
    }
    hoverEl.textContent = "Hover a motorway segment";
  });

  map.on("mouseenter", `hpc-sites-${variant.id}`, () => {
    map.getCanvas().style.cursor = "pointer";
  });

  map.on("mouseenter", `hpc-clusters-${variant.id}`, () => {
    map.getCanvas().style.cursor = "pointer";
  });

  map.on("mousemove", `hpc-sites-${variant.id}`, (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const chargerId = feature.properties?.charger_id ?? "n/a";
    const power = feature.properties?.power_kw ?? "n/a";
    const operator = feature.properties?.operator || "n/a";
    const status = feature.properties?.status || "n/a";
    hoverEl.textContent = `HPC ${chargerId} | ${power} kW | ${operator} | ${status}`;
  });

  map.on("mouseleave", `hpc-sites-${variant.id}`, () => {
    map.getCanvas().style.cursor = "";
    hoverEl.textContent = "Hover a motorway segment";
  });

  map.on("mousemove", `hpc-clusters-${variant.id}`, (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const count = feature.properties?.point_count_abbreviated ?? feature.properties?.point_count ?? "n/a";
    hoverEl.textContent = `HPC cluster: ${count} stations`;
  });

  map.on("mouseleave", `hpc-clusters-${variant.id}`, () => {
    map.getCanvas().style.cursor = "";
    hoverEl.textContent = "Hover a motorway segment";
  });
}

function populateThresholdSelect(variants) {
  if (!thresholdSelect) return;
  thresholdSelect.innerHTML = "";
  for (const variant of variants) {
    const option = document.createElement("option");
    option.value = variant.id;
    option.textContent = variant.label;
    thresholdSelect.appendChild(option);
  }
  thresholdSelect.value = activeThreshold;
}

map.on("load", async () => {
  try {
    const config = await loadConfig();
    const thresholdConfig = buildThresholdVariants(config);
    thresholdVariants = thresholdConfig.variants;
    activeThreshold = thresholdConfig.defaultThreshold;
    populateThresholdSelect(thresholdVariants);
    if (hpcToggle) {
      hpcToggle.checked = false;
    }

    for (const variant of thresholdVariants) {
      map.addSource(variant.distanceSource, {
        type: "vector",
        tiles: variant.tiles,
        minzoom: 4,
        maxzoom: 22
      });
      map.addSource(variant.hpcSource, {
        type: "geojson",
        data: variant.hpcGeojson,
        cluster: true,
        clusterRadius: 42,
        clusterMaxZoom: 8
      });
      map.addSource(variant.hpcHighlightSource, {
        type: "geojson",
        data: variant.hpcGeojson
      });
      addDistanceLayer(variant);
      addHpcLayer(variant);
      bindHoverEventsForVariant(variant);
    }

    applyActiveThreshold();

    if (hpcToggle) {
      hpcToggle.addEventListener("change", () => {
        applyActiveThreshold();
      });
    }
    if (thresholdSelect) {
      thresholdSelect.addEventListener("change", () => {
        activeThreshold = thresholdSelect.value;
        applyActiveThreshold();
      });
    }
  } catch (error) {
    console.error(error);
    hoverEl.textContent = "Failed to load threshold configuration";
  }
});
